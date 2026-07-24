"""Deterministic and Monte Carlo causal simulation engine.

The engine is deliberately stdlib-first.  All stochastic draws are addressed by
``(seed, run_id, edge_id, purpose)`` rather than mutable RNG state, so changing
worker partitioning cannot change a run.  A compiled model and an execution
configuration are hashed into every run contract.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, cast

from . import FORMULA_VERSION, LEGACY_FORMULA_VERSION, SUPPORTED_FORMULA_VERSIONS
from .formula import evaluate_output_effect, expected_output_effect
from .io import canonical_hash
from .issues import Issue, issue
from .rng import normal01, sample_triangular, sample_uniform, uniform01
from .schema import TRANSFORMS, parse_duration_seconds


@dataclass
class EngineConfig:
    seed: str = "0"
    mode: str = "deterministic"
    timestep: float = 1.0
    max_events: int = 1_000_000
    jacobi_abs_tol: float = 1e-9
    jacobi_rel_tol: float = 1e-7
    jacobi_relax: float = 0.5
    jacobi_max_iter: int = 100
    min_runs: int = 1000
    max_runs: int = 20000
    batch_size: int = 250
    stable_batches: int = 3
    branch_mass_tol: float = 0.01
    max_invalid_fraction: float = 0.01
    workers: int = 1


@dataclass
class Variable:
    id: str
    role: str
    datatype: str = "continuous"
    unit: str = ""
    scale: str = "level"
    baseline: float = 0.0
    bounds: tuple[float | None, float | None] = (None, None)
    retention: float | None = None
    retention_distribution: dict[str, Any] | None = None
    decay_rate: float | None = None
    decay_rate_distribution: dict[str, Any] | None = None
    value: float = 0.0


@dataclass
class ModelEdge:
    id: str
    source: str
    target: str
    sign: int
    strength: float
    lag_ticks: int = 0
    lag_unit: str = "ticks"
    transform: str = "linear"
    transform_parameters: dict[str, Any] = field(default_factory=dict)
    existence_prob: float = 1.0
    effect_distribution: dict[str, Any] | None = None
    lag_distribution: dict[str, Any] | None = None
    context_multiplier: float = 1.0
    saturation: float | None = None
    integration: str = "impulse"


@dataclass
class ComputationalModel:
    variables: dict[str, Variable] = field(default_factory=dict)
    edges: list[ModelEdge] = field(default_factory=list)
    interventions: list[dict[str, Any]] = field(default_factory=list)
    formula_version: str = FORMULA_VERSION


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _finite_number(value: Any, default: float = 0.0) -> float:
    if not _is_finite_number(value):
        return default
    return float(value)


def _normalise_distribution(value: Any, *, label: str) -> dict[str, Any] | None:
    """Validate a scalar distribution without coercion or silent fallback."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{label} distribution must be an object")
    distribution_name = value.get("distribution")
    type_name = value.get("type")
    if distribution_name is not None and type_name is not None and distribution_name != type_name:
        raise ValueError(f"{label} distribution/type declarations disagree")
    kind = str(distribution_name if distribution_name is not None else type_name or "").lower()
    required_by_kind = {
        "fixed": {"value"},
        "uniform": {"min", "max"},
        "triangular": {"min", "mode", "max"},
        "normal": {"mean", "sd"},
    }
    if kind not in required_by_kind:
        raise ValueError(f"unsupported {label} distribution: {kind or '<missing>'}")
    allowed = {"distribution", "type"} | required_by_kind[kind]
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {label} distribution fields: {', '.join(sorted(unknown))}")
    missing = required_by_kind[kind] - set(value)
    if missing:
        raise ValueError(f"{label} distribution {kind} missing: {', '.join(sorted(missing))}")
    result: dict[str, Any] = {"distribution": kind}
    for key in required_by_kind[kind]:
        raw = value[key]
        if not _is_finite_number(raw):
            raise ValueError(f"{label} distribution {key} must be finite")
        result[key] = float(raw)
    if kind == "uniform" and not result["min"] < result["max"]:
        raise ValueError(f"uniform {label} distribution requires min < max")
    if kind == "triangular" and not result["min"] <= result["mode"] <= result["max"]:
        raise ValueError(f"triangular {label} distribution requires min <= mode <= max")
    if kind == "triangular" and result["min"] == result["max"]:
        raise ValueError(f"degenerate triangular {label} distribution must be declared fixed")
    if kind == "normal" and result["sd"] <= 0:
        raise ValueError(f"normal {label} distribution requires sd > 0")
    return result


def _normalise_effect_distribution(value: Any) -> dict[str, Any] | None:
    return _normalise_distribution(value, label="effect")


def _representative_parameter(distribution: dict[str, Any]) -> float:
    kind = str(distribution["distribution"])
    if kind == "fixed":
        return float(distribution["value"])
    if kind == "uniform":
        return (float(distribution["min"]) + float(distribution["max"])) / 2.0
    if kind == "triangular":
        return float(distribution["mode"])
    return float(distribution["mean"])


def _normalise_scalar_parameter(value: Any, *, label: str) -> float | dict[str, Any]:
    if _is_finite_number(value):
        return float(value)
    distribution = _normalise_distribution(value, label=label)
    if distribution is None:
        raise ValueError(f"{label} must be a finite number or distribution")
    return distribution


def _duration_ticks(value: Any) -> int:
    if _is_finite_number(value):
        if value < 0:
            raise ValueError("lag duration must be non-negative")
        return int(math.ceil(float(value)))
    seconds = parse_duration_seconds(value)
    if seconds is None:
        raise ValueError(f"invalid lag duration: {value!r}")
    return int(math.ceil(seconds / 86400.0))


def _normalise_lag_distribution(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("lag_distribution must be an object")
    kind = str(value.get("type", value.get("distribution", ""))).lower()
    if kind not in {"fixed", "uniform", "triangular", "truncated_exponential"}:
        raise ValueError(f"unsupported lag distribution: {kind or '<missing>'}")
    result: dict[str, Any] = {"type": kind}
    for key in ("fixed", "min", "mode", "max", "mean"):
        if value.get(key) is not None:
            result[key] = _duration_ticks(value[key])
    if value.get("rate") is not None:
        rate = _finite_number(value["rate"], -1.0)
        if rate <= 0:
            raise ValueError("lag distribution rate must be positive")
        result["rate"] = rate
    if kind == "fixed" and "fixed" not in result:
        raise ValueError("fixed lag distribution requires fixed")
    if kind == "uniform" and not {"min", "max"} <= set(result):
        raise ValueError("uniform lag distribution requires min and max")
    if kind == "triangular" and not {"min", "mode", "max"} <= set(result):
        raise ValueError("triangular lag distribution requires min, mode, and max")
    low = int(result.get("min", 0))
    high = int(result.get("max", low))
    mode = int(result.get("mode", low))
    if low > high or (kind == "triangular" and not low <= mode <= high):
        raise ValueError("lag distribution requires min <= mode <= max")
    if kind == "truncated_exponential" and "rate" not in result and int(result.get("mean", 0)) <= 0:
        raise ValueError("truncated_exponential lag requires positive rate or mean")
    return result


def _context_multiplier(value: Any) -> float:
    if value is None:
        return 1.0
    if not isinstance(value, list) or not value:
        raise ValueError("context_modifiers must be a non-empty array")
    multiplier = 1.0
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("context modifier must be an object")
        if raw.get("active", True) is False:
            continue
        factor = raw.get("multiplier")
        factor_number = _finite_number(factor, math.nan)
        if not math.isfinite(factor_number) or factor_number <= 0:
            raise ValueError("context multiplier must be finite and positive")
        multiplier *= factor_number
        if not math.isfinite(multiplier):
            raise ValueError("combined context multiplier must remain finite")
    return multiplier


def _normalise_transform_parameters(transform: str, value: Any) -> dict[str, Any]:
    if transform not in TRANSFORMS:
        raise ValueError(f"unsupported transform {transform}")
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("transform_parameters must be an object")
    allowed_by_transform = {
        "linear": set(),
        "elasticity": set(),
        "identity": set(),
        "logistic": {"midpoint", "steepness"},
        "threshold": {"mode", "threshold", "deadband", "theta_on", "theta_off"},
    }
    unknown = set(value) - allowed_by_transform[transform]
    if unknown:
        raise ValueError(
            f"transform {transform} has unsupported parameters: {', '.join(sorted(unknown))}"
        )
    parameters: dict[str, Any] = {}
    for key, raw in value.items():
        if key == "mode":
            parameters[key] = str(raw)
            continue
        parameters[key] = _normalise_scalar_parameter(raw, label=f"transform parameter {key}")
    if transform == "logistic":
        parameters.setdefault("midpoint", 0.0)
        parameters.setdefault("steepness", 1.0)
        representative = (
            _representative_parameter(parameters["steepness"])
            if isinstance(parameters["steepness"], dict)
            else float(parameters["steepness"])
        )
        if representative <= 0:
            raise ValueError("logistic steepness must be positive")
    if transform == "threshold":
        mode = str(parameters.get("mode", "above"))
        if mode not in {"above", "below", "deadband", "hysteresis"}:
            raise ValueError("threshold mode must be above, below, deadband, or hysteresis")
        parameters["mode"] = mode
        parameters.setdefault("threshold", 0.0)
        if mode == "deadband":
            parameters.setdefault("deadband", 0.0)
        if mode == "hysteresis" and not {"theta_on", "theta_off"} <= set(parameters):
            raise ValueError("hysteresis requires theta_on and theta_off")
        representatives = {
            key: _representative_parameter(raw) if isinstance(raw, dict) else float(raw)
            for key, raw in parameters.items()
            if key != "mode"
        }
        if representatives.get("deadband", 0.0) < 0:
            raise ValueError("threshold deadband must be non-negative")
        if mode == "hysteresis" and not (
            representatives["theta_on"] >= representatives["theta_off"] >= 0
        ):
            raise ValueError("hysteresis requires theta_on >= theta_off >= 0")
    return parameters


def _representative_lag(distribution: dict[str, Any]) -> int:
    kind = str(distribution["type"])
    if kind == "fixed":
        return int(distribution["fixed"])
    if kind == "uniform":
        return int(math.floor((int(distribution["min"]) + int(distribution["max"])) / 2 + 0.5))
    if kind == "triangular":
        return int(distribution["mode"])
    mean = float(distribution["mean"]) if "mean" in distribution else 1.0 / float(distribution["rate"])
    low = int(distribution.get("min", 0))
    high = int(distribution.get("max", math.ceil(mean)))
    return max(low, min(high, math.ceil(mean)))


def compile_model(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    interventions: list[dict[str, Any]] | None = None,
    *,
    formula_version: str = FORMULA_VERSION,
) -> ComputationalModel:
    """Compile public artifacts without treating simulated ``state_after`` as evidence.

    Baselines come from ``baseline`` or the observed ``state_before.value`` only.
    Interventions must be supplied explicitly (normally from the manifest or an
    interventions artifact).
    """
    if formula_version not in SUPPORTED_FORMULA_VERSIONS:
        raise ValueError(f"unsupported formula version {formula_version}")
    model = ComputationalModel(formula_version=formula_version)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        if not node_id or node_id in model.variables:
            continue
        raw_before = node.get("state_before")
        before: dict[str, Any] = raw_before if isinstance(raw_before, dict) else {}
        raw_baseline = node.get("baseline", before.get("value", 0.0))
        baseline = _finite_number(raw_baseline)
        raw_bounds = node.get("bounds")
        bounds: tuple[float | None, float | None] = (None, None)
        if isinstance(raw_bounds, list) and len(raw_bounds) == 2:
            lo = _finite_number(raw_bounds[0]) if raw_bounds[0] is not None else None
            hi = _finite_number(raw_bounds[1]) if raw_bounds[1] is not None else None
            bounds = (lo, hi)
        scale = str(node.get("scale", "level"))
        if scale not in {"level", "flow", "stock"}:
            raise ValueError(f"node {node_id} scale must be level, flow, or stock")
        if scale != "stock" and any(
            key in node for key in ("retention", "retention_distribution", "decay_rate", "decay_rate_distribution")
        ):
            raise ValueError(f"node {node_id} stock dynamics are only valid for stock variables")
        if "retention" in node and "decay_rate" in node:
            raise ValueError(f"node {node_id} cannot declare both retention and decay_rate")
        retention: float | None = None
        retention_distribution: dict[str, Any] | None = None
        decay_rate: float | None = None
        decay_rate_distribution: dict[str, Any] | None = None
        if scale == "stock" and "retention" in node:
            normalised_retention = _normalise_scalar_parameter(node["retention"], label=f"node {node_id} retention")
            if isinstance(normalised_retention, dict):
                retention_distribution = normalised_retention
                representative_retention = _representative_parameter(normalised_retention)
            else:
                retention = normalised_retention
                representative_retention = retention
            if not 0.0 <= representative_retention <= 1.0:
                raise ValueError(f"node {node_id} retention must be in [0,1]")
        if scale == "stock" and "decay_rate" in node:
            normalised_decay = _normalise_scalar_parameter(node["decay_rate"], label=f"node {node_id} decay_rate")
            if isinstance(normalised_decay, dict):
                decay_rate_distribution = normalised_decay
                representative_decay = _representative_parameter(normalised_decay)
            else:
                decay_rate = normalised_decay
                representative_decay = decay_rate
            if representative_decay < 0:
                raise ValueError(f"node {node_id} decay_rate must be non-negative")
        model.variables[node_id] = Variable(
            id=node_id,
            role=str(node.get("role", "endogenous")),
            datatype=str(node.get("datatype", "continuous")),
            unit=str(node.get("unit", "")),
            scale=scale,
            baseline=baseline,
            bounds=bounds,
            retention=retention,
            retention_distribution=retention_distribution,
            decay_rate=decay_rate,
            decay_rate_distribution=decay_rate_distribution,
            value=baseline,
        )

    for raw in edges:
        if not isinstance(raw, dict):
            continue
        edge_id = str(raw.get("id", ""))
        source = str(raw.get("from", raw.get("source", "")))
        target = str(raw.get("to", raw.get("target", "")))
        if not edge_id or source not in model.variables or target not in model.variables:
            continue
        strength = _finite_number(raw.get("base_strength", raw.get("strength", raw.get("effect_size", 0.0))))
        lag_distribution = _normalise_lag_distribution(raw.get("lag_distribution"))
        raw_lag = raw.get("lag_ticks", 0)
        lag = _representative_lag(lag_distribution) if lag_distribution else _duration_ticks(raw_lag)
        lag_unit = "days" if lag_distribution is not None or isinstance(raw_lag, str) else "ticks"
        distribution = raw.get("effect_distribution")
        if distribution is None and isinstance(raw.get("effect_parameter"), dict):
            candidate = raw["effect_parameter"]
            candidate_distribution = candidate.get("distribution")
            if isinstance(candidate_distribution, dict):
                distribution = candidate_distribution
        sign_raw = raw.get("sign", 1)
        if sign_raw not in (-1, 1):
            raise ValueError(f"edge {edge_id} sign must be -1 or 1")
        sign = int(sign_raw)
        context_multiplier = _context_multiplier(raw.get("context_modifiers"))
        saturation_raw = raw.get("saturation")
        saturation: float | None = None
        if saturation_raw is not None:
            saturation = _finite_number(saturation_raw, -1.0)
            if saturation <= 0:
                raise ValueError(f"edge {edge_id} saturation must be finite and positive")
        transform = str(raw.get("transform", "linear"))
        transform_parameters = _normalise_transform_parameters(
            transform, raw.get("transform_parameters")
        )
        effect_distribution = _normalise_effect_distribution(distribution)
        if transform == "identity" and effect_distribution is not None:
            raise ValueError(f"edge {edge_id} identity transform cannot use effect_distribution")
        integration = str(
            raw.get(
                "integration",
                "rate" if model.variables[source].scale == "flow" and model.variables[target].scale == "stock" else "impulse",
            )
        )
        if integration not in {"rate", "impulse"}:
            raise ValueError(f"edge {edge_id} integration must be rate or impulse")
        if integration == "rate" and model.variables[target].scale != "stock":
            raise ValueError(f"edge {edge_id} rate integration requires a stock target")
        model.edges.append(
            ModelEdge(
                id=edge_id,
                source=source,
                target=target,
                sign=sign,
                strength=strength,
                lag_ticks=lag,
                lag_unit=lag_unit,
                transform=transform,
                transform_parameters=transform_parameters,
                existence_prob=min(1.0, max(0.0, _finite_number(raw.get("existence_prob", 1.0), 1.0))),
                effect_distribution=effect_distribution,
                lag_distribution=lag_distribution,
                context_multiplier=context_multiplier,
                saturation=saturation,
                integration=integration,
            )
        )

    intervention_ids: set[str] = set()
    for index, raw in enumerate(interventions or []):
        if not isinstance(raw, dict):
            raise ValueError(f"intervention {index} must be an object")
        target = str(raw.get("target", ""))
        if target not in model.variables:
            raise ValueError(f"intervention {index} target must reference a model variable")
        intervention_id = str(raw.get("id", ""))
        if not intervention_id:
            raise ValueError(f"intervention {index} id is required")
        if intervention_id in intervention_ids:
            raise ValueError(f"duplicate intervention id {intervention_id}")
        intervention_ids.add(intervention_id)
        op = str(raw.get("op", "set"))
        if op not in {"set", "add", "multiply"}:
            raise ValueError(f"intervention {intervention_id} op must be set, add, or multiply")
        raw_value = raw.get("value")
        if not _is_finite_number(raw_value):
            raise ValueError(f"intervention {intervention_id} value must be finite")
        raw_start = raw.get("start_tick", 0)
        raw_end = raw.get("end_tick")
        if isinstance(raw_start, bool) or not isinstance(raw_start, int) or raw_start < 0:
            raise ValueError(f"intervention {intervention_id} start_tick must be a non-negative integer")
        if raw_end is not None and (
            isinstance(raw_end, bool) or not isinstance(raw_end, int) or raw_end < raw_start
        ):
            raise ValueError(
                f"intervention {intervention_id} end_tick must be null or an integer at least start_tick"
            )
        release_policy = str(raw.get("release_policy", "retain"))
        if release_policy not in {"retain", "reset_baseline"}:
            raise ValueError("intervention release_policy must be retain or reset_baseline")
        if release_policy == "reset_baseline" and (
            op != "set" or model.variables[target].scale != "stock"
        ):
            raise ValueError("reset_baseline release_policy requires a set intervention on a stock")
        model.interventions.append(
            {
                "id": intervention_id,
                "target": target,
                "op": op,
                "value": float(cast(int | float, raw_value)),
                "start_tick": raw_start,
                "end_tick": raw_end,
                "release_policy": release_policy,
            }
        )
    return model


def model_payload(model: ComputationalModel) -> dict[str, Any]:
    variables: dict[str, dict[str, Any]] = {}
    for key, var in sorted(model.variables.items()):
        payload: dict[str, Any] = {
            "id": var.id,
            "role": var.role,
            "datatype": var.datatype,
            "unit": var.unit,
            "scale": var.scale,
            "baseline": var.baseline,
            "bounds": list(var.bounds),
        }
        if var.scale == "stock":
            if var.retention_distribution is not None:
                payload["retention_distribution"] = var.retention_distribution
            elif var.retention is not None:
                payload["retention"] = var.retention
            if var.decay_rate_distribution is not None:
                payload["decay_rate_distribution"] = var.decay_rate_distribution
            elif var.decay_rate is not None:
                payload["decay_rate"] = var.decay_rate
        variables[key] = payload

    edges: list[dict[str, Any]] = []
    for edge in sorted(model.edges, key=lambda value: value.id):
        payload = {
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "sign": edge.sign,
            "strength": edge.strength,
            "lag_ticks": edge.lag_ticks,
            "lag_unit": edge.lag_unit,
            "transform": edge.transform,
            "existence_prob": edge.existence_prob,
            "effect_distribution": edge.effect_distribution,
            "lag_distribution": edge.lag_distribution,
            "context_multiplier": edge.context_multiplier,
            "saturation": edge.saturation,
        }
        if edge.transform_parameters:
            payload["transform_parameters"] = edge.transform_parameters
        if model.formula_version != LEGACY_FORMULA_VERSION and model.variables[edge.target].scale == "stock":
            payload["integration"] = edge.integration
        edges.append(payload)

    interventions_payload: list[dict[str, Any]] = []
    for intervention in sorted(model.interventions, key=lambda value: str(value.get("id", ""))):
        payload = dict(intervention)
        if payload.get("release_policy") == "retain":
            payload.pop("release_policy", None)
        interventions_payload.append(payload)

    return {
        "variables": variables,
        "edges": edges,
        "interventions": interventions_payload,
    }


def model_hash(model: ComputationalModel) -> str:
    return canonical_hash(model_payload(model))


def config_payload(config: EngineConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload.pop("workers", None)
    return payload


def semantic_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Remove executor-only metadata before comparing reproducible semantics."""
    normalised = dict(result)
    payload = normalised.get("payload")
    if isinstance(payload, dict):
        normalised["payload"] = {key: value for key, value in payload.items() if key != "workers"}
    summary = normalised.get("summary")
    if isinstance(summary, dict):
        normalised["summary"] = {key: value for key, value in summary.items() if key != "workers"}
    return normalised


def _diagnostic_hash(value: Any) -> str:
    """Hash invalid diagnostic data after explicitly tagging non-finite floats."""
    if isinstance(value, float) and not math.isfinite(value):
        normalised: Any = {"non_finite": repr(value)}
    elif isinstance(value, dict):
        normalised = {str(key): _diagnostic_value(item) for key, item in value.items()}
    elif isinstance(value, list):
        normalised = [_diagnostic_value(item) for item in value]
    else:
        normalised = value
    return canonical_hash(normalised)


def _diagnostic_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {"non_finite": repr(value)}
    if isinstance(value, dict):
        return {str(key): _diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_diagnostic_value(item) for item in value]
    return value


def _apply_bounds(variable: Variable, value: float) -> float:
    low, high = variable.bounds
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _active_interventions(model: ComputationalModel, tick: int) -> list[dict[str, Any]]:
    return [
        value
        for value in model.interventions
        if int(value.get("start_tick", 0)) <= tick
        and (value.get("end_tick") is None or tick < int(value["end_tick"]))
    ]


def _reset_releases(model: ComputationalModel, tick: int) -> set[str]:
    return {
        str(value["target"])
        for value in model.interventions
        if value.get("op") == "set"
        and value.get("release_policy") == "reset_baseline"
        and value.get("end_tick") == tick
    }


def _integrate_edge_output(
    model: ComputationalModel,
    edge: ModelEdge,
    output: float,
    config: EngineConfig,
) -> float:
    if (
        model.formula_version != LEGACY_FORMULA_VERSION
        and model.variables[edge.target].scale == "stock"
        and edge.integration == "rate"
    ):
        return output * float(config.timestep)
    return output


def _config_errors(config: EngineConfig) -> list[Issue]:
    problems: list[Issue] = []
    if config.mode not in {"deterministic", "monte_carlo"}:
        problems.append(issue("ENUM", pointer="/config/mode", actual=config.mode))
    numeric_positive = {
        "timestep": config.timestep,
        "jacobi_abs_tol": config.jacobi_abs_tol,
        "jacobi_rel_tol": config.jacobi_rel_tol,
    }
    for name, value in numeric_positive.items():
        is_numeric = _is_finite_number(value)
        invalid_range = (
            not is_numeric
            or (value <= 0 if name == "timestep" else value < 0)
        )
        if invalid_range:
            message = "finite positive value required" if name == "timestep" else "finite non-negative value required"
            problems.append(issue("RANGE", pointer=f"/config/{name}", actual=value, message=message))
    if not _is_finite_number(config.jacobi_relax) or not 0 < config.jacobi_relax <= 1:
        problems.append(issue("RANGE", pointer="/config/jacobi_relax", actual=config.jacobi_relax, message="must be in (0,1]"))
    if isinstance(config.max_events, bool) or not isinstance(config.max_events, int) or config.max_events < 0:
        problems.append(issue("RANGE", pointer="/config/max_events", actual=config.max_events, message="non-negative integer required"))
    for name in ("jacobi_max_iter", "min_runs", "max_runs", "batch_size", "stable_batches", "workers"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            problems.append(issue("RANGE", pointer=f"/config/{name}", actual=value, message="positive integer required"))
    for name in ("branch_mass_tol", "max_invalid_fraction"):
        value = getattr(config, name)
        if not _is_finite_number(value) or not 0 <= value <= 1:
            problems.append(issue("RANGE", pointer=f"/config/{name}", actual=value, message="must be in [0,1]"))
    if isinstance(config.min_runs, int) and isinstance(config.max_runs, int) and config.max_runs < config.min_runs:
        problems.append(issue("RANGE", pointer="/config/max_runs", actual=config.max_runs, expected=config.min_runs, message="must be at least min_runs"))
    return problems


def _sample_distribution(
    distribution: dict[str, Any],
    config: EngineConfig,
    run_id: int,
    object_id: str,
    purpose: str,
) -> float:
    if config.mode != "monte_carlo":
        return _representative_parameter(distribution)
    kind = str(distribution.get("distribution", distribution.get("type", "fixed"))).lower()
    if kind == "uniform":
        return sample_uniform(
            config.seed,
            float(distribution["min"]),
            float(distribution["max"]),
            run_id,
            object_id,
            purpose,
        )
    if kind == "triangular":
        return sample_triangular(
            config.seed,
            float(distribution["min"]),
            float(distribution["mode"]),
            float(distribution["max"]),
            run_id,
            object_id,
            purpose,
        )
    if kind == "normal":
        return float(distribution["mean"]) + float(distribution["sd"]) * normal01(
            config.seed, run_id, object_id, purpose
        )
    if kind == "fixed":
        return float(distribution["value"])
    raise ValueError(f"unsupported scalar distribution: {kind}")


def _sample_strength(edge: ModelEdge, config: EngineConfig, run_id: int) -> float:
    # ``base_strength`` is the deterministic estimate in both formula
    # generations. Effect distributions describe Monte Carlo uncertainty and
    # must not silently replace that estimate during deterministic replay.
    if config.mode != "monte_carlo" or not edge.effect_distribution:
        return edge.strength
    return _sample_distribution(edge.effect_distribution, config, run_id, edge.id, "effect")


def sampled_transform_parameters(
    edge: ModelEdge,
    config: EngineConfig,
    run_id: int,
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in edge.transform_parameters.items():
        if isinstance(value, dict):
            resolved[key] = _sample_distribution(value, config, run_id, edge.id, f"transform:{key}")
        else:
            resolved[key] = value
    if edge.transform == "logistic" and float(resolved.get("steepness", 1.0)) <= 0:
        raise ValueError("sampled logistic steepness must be positive")
    if edge.transform == "threshold":
        mode = str(resolved.get("mode", "above"))
        if float(resolved.get("deadband", 0.0)) < 0:
            raise ValueError("sampled threshold deadband must be non-negative")
        if mode == "hysteresis" and not (
            float(resolved.get("theta_on", math.nan))
            >= float(resolved.get("theta_off", math.nan))
            >= 0
        ):
            raise ValueError("sampled hysteresis requires theta_on >= theta_off >= 0")
    return resolved


def sampled_retention_factor(
    variable: Variable,
    config: EngineConfig,
    run_id: int,
) -> float:
    """Resolve timestep-invariant stock decay for one addressed run."""
    if variable.scale != "stock":
        return 1.0
    decay_rate: float | None
    if variable.decay_rate_distribution is not None:
        decay_rate = _sample_distribution(
            variable.decay_rate_distribution, config, run_id, variable.id, "decay_rate"
        )
    else:
        decay_rate = variable.decay_rate
    if decay_rate is not None:
        if not math.isfinite(decay_rate) or decay_rate < 0:
            raise ValueError(f"sampled decay rate for {variable.id} must be non-negative")
        return math.exp(-decay_rate * float(config.timestep))
    retention: float | None
    if variable.retention_distribution is not None:
        retention = _sample_distribution(
            variable.retention_distribution, config, run_id, variable.id, "retention"
        )
    else:
        retention = variable.retention
    if retention is None:
        return 1.0
    if not math.isfinite(retention) or not 0 <= retention <= 1:
        raise ValueError(f"sampled retention for {variable.id} must be in [0,1]")
    return float(retention ** float(config.timestep))


def _lag_days_to_ticks(days: int | float, timestep: int | float) -> int:
    try:
        ratio = float(days) / float(timestep)
    except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("lag/timestep ratio must be finite and positive") from exc
    if not math.isfinite(ratio) or ratio < 0:
        raise ValueError("lag/timestep ratio must be finite and non-negative")
    return int(math.ceil(ratio))


def _sample_lag(edge: ModelEdge, config: EngineConfig, run_id: int) -> int:
    distribution = edge.lag_distribution
    if config.mode != "monte_carlo" or not distribution:
        deterministic_lag = edge.lag_ticks
        return (
            _lag_days_to_ticks(deterministic_lag, config.timestep)
            if edge.lag_unit == "days"
            else deterministic_lag
        )
    kind = str(distribution["type"])
    if kind == "fixed":
        fixed = int(distribution["fixed"])
        return _lag_days_to_ticks(fixed, config.timestep) if edge.lag_unit == "days" else fixed
    low = int(distribution.get("min", 0))
    default_high = (
        math.ceil(float(distribution.get("mean", 1.0 / float(distribution.get("rate", 1.0)))) * 10)
        if kind == "truncated_exponential"
        else low
    )
    high = int(distribution.get("max", max(low, default_high)))
    sampled: float
    if kind == "uniform":
        sampled = sample_uniform(config.seed, low, high, run_id, edge.id, "lag")
    elif kind == "triangular":
        sampled = sample_triangular(
            config.seed,
            low,
            float(distribution["mode"]),
            high,
            run_id,
            edge.id,
            "lag",
        )
    else:
        rate = (
            float(distribution["rate"])
            if "rate" in distribution
            else 1.0 / float(distribution["mean"])
        )
        u = uniform01(config.seed, run_id, edge.id, "lag")
        sampled = -math.log(max(1e-15, 1.0 - u)) / rate
        sampled = min(high, max(low, sampled))
    sampled_value = max(0, int(math.floor(sampled + 0.5)))
    return (
        _lag_days_to_ticks(sampled_value, config.timestep)
        if edge.lag_unit == "days"
        else sampled_value
    )


def sampled_edge_parameters(
    edge: ModelEdge,
    config: EngineConfig,
    run_id: int,
) -> tuple[float, int, bool]:
    """Return the addressed strength, lag, and existence decision for one run."""
    exists = (
        config.mode != "monte_carlo"
        or uniform01(config.seed, run_id, edge.id, "exist") < edge.existence_prob
    )
    return _sample_strength(edge, config, run_id), _sample_lag(edge, config, run_id), exists


def _edge_effect(
    edge: ModelEdge,
    strength: float,
    source_value: float,
    *,
    formula_version: str,
    threshold_active: bool | None = None,
) -> tuple[float, bool | None]:
    return evaluate_output_effect(
        base_strength=strength,
        sign=edge.sign,
        context_mult=edge.context_multiplier,
        input_effect=source_value,
        transform=edge.transform,
        transform_parameters=edge.transform_parameters,
        saturation=edge.saturation,
        formula_version=formula_version,
        threshold_active=threshold_active,
    )


def _edge_effect_value(edge: ModelEdge, strength: float, source_value: float, *, formula_version: str) -> float:
    return expected_output_effect(
        base_strength=strength,
        sign=edge.sign,
        context_mult=edge.context_multiplier,
        input_effect=source_value,
        transform=edge.transform,
        transform_parameters=edge.transform_parameters,
        saturation=edge.saturation,
        formula_version=formula_version,
    )


def _tarjan(nodes: list[str], edges: list[ModelEdge]) -> list[list[str]]:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        graph[edge.source].append(edge.target)
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            result.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return result


def _component_order(nodes: list[str], edges: list[ModelEdge]) -> list[list[str]]:
    components = _tarjan(nodes, edges)
    owner = {node: idx for idx, component in enumerate(components) for node in component}
    outgoing: dict[int, set[int]] = {idx: set() for idx in range(len(components))}
    indegree = {idx: 0 for idx in range(len(components))}
    for edge in edges:
        source, target = owner[edge.source], owner[edge.target]
        if source != target and target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1
    ready = sorted((idx for idx, degree in indegree.items() if degree == 0), key=lambda idx: components[idx])
    order: list[list[str]] = []
    while ready:
        current = ready.pop(0)
        order.append(components[current])
        for target in sorted(outgoing[current], key=lambda idx: components[idx]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=lambda idx: components[idx])
    return order


def _invalid_run_result(
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: Any,
    run_id: int,
    issues: list[Issue],
) -> dict[str, Any]:
    configuration = config_payload(config)
    payload = {
        "model_hash": model_hash(model),
        "config_hash": _diagnostic_hash(configuration),
        "final_state": {key: value.baseline for key, value in sorted(model.variables.items())},
        "ticks": ticks,
        "events": 0,
        "unresolved": True,
        "event_storm": False,
        "run_id": run_id,
        "seed": config.seed,
        "history_hash": canonical_hash([]),
    }
    if model.formula_version != LEGACY_FORMULA_VERSION:
        payload["formula_version"] = model.formula_version
        payload["dynamics_hash"] = canonical_hash([])
    return {
        "ok": False,
        "payload": {**payload, "workers": config.workers},
        "history": [],
        "run_hash": _diagnostic_hash(payload),
        "issues": [value.to_dict() for value in issues],
        "exit_code": 4,
    }


def run_deterministic(
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: int = 10,
    run_id: int = 0,
) -> dict[str, Any]:
    """Execute one deterministic trajectory or one addressed MC sample."""
    issues: list[Issue] = _config_errors(config)
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 0:
        issues.append(issue("RANGE", pointer="/ticks", actual=ticks, message="non-negative integer required"))
    if issues:
        return _invalid_run_result(model, config, ticks=ticks, run_id=run_id, issues=issues)
    state = {key: var.baseline for key, var in model.variables.items()}
    history: list[dict[str, float]] = []
    dynamics_history: list[dict[str, Any]] = []
    scheduled: dict[int, list[tuple[str, float, str]]] = {}
    events = 0
    unresolved = False
    event_storm = False
    edges: list[tuple[ModelEdge, float]] = []
    retention_factors: dict[str, float] = {}
    edge_gate_state: dict[str, bool] = {}
    try:
        retention_factors = {
            key: sampled_retention_factor(variable, config, run_id)
            for key, variable in sorted(model.variables.items())
            if variable.scale == "stock"
        }
    except (OverflowError, TypeError, ValueError) as exc:
        issues.append(issue("RANGE", pointer="/variables", message=f"stock dynamics sampling failed: {exc}"))
    for edge in sorted(model.edges, key=lambda value: value.id):
        try:
            strength, lag_ticks, exists = sampled_edge_parameters(edge, config, run_id)
            transform_parameters = sampled_transform_parameters(edge, config, run_id)
        except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
            issues.append(
                issue(
                    "RANGE",
                    pointer=f"/edges/{edge.id}/lag_distribution",
                    message=f"edge sampling failed: {exc}",
                )
            )
            continue
        if exists:
            sampled_edge = replace(
                edge,
                lag_ticks=lag_ticks,
                transform_parameters=transform_parameters,
            )
            edges.append((sampled_edge, strength))
    if issues:
        return _invalid_run_result(model, config, ticks=ticks, run_id=run_id, issues=issues)

    for tick in range(max(0, ticks)):
        # Formula 2.1 uses end-of-step history: stock carry/decay is applied on
        # every tick, then delayed inputs, interventions, and zero-lag rates.
        # Formula 2.0 retains the released level-equation behavior.
        released_resets = _reset_releases(model, tick)
        base: dict[str, float] = {}
        stock_audit: dict[str, dict[str, Any]] = {}
        for key, variable in sorted(model.variables.items()):
            if model.formula_version != LEGACY_FORMULA_VERSION and variable.scale == "stock":
                previous_state = state[key]
                retention_factor = retention_factors.get(key, 1.0)
                reset = key in released_resets
                retained_state = variable.baseline if reset else previous_state * retention_factor
                base[key] = retained_state
                stock_audit[key] = {
                    "tick": tick,
                    "node_id": key,
                    "previous_state": previous_state,
                    "retention_factor": retention_factor,
                    "retained_state": retained_state,
                    "delayed_input": 0.0,
                    "zero_lag_input": 0.0,
                    "release_policy": "reset_baseline" if reset else None,
                    "interventions": [],
                }
            else:
                base[key] = variable.baseline
        active = _active_interventions(model, tick)
        blocked = {str(value["target"]) for value in active if value.get("op") == "set"}
        for target, delta, edge_id in scheduled.pop(tick, []):
            if target not in blocked and target in base:
                base[target] += delta
                if target in stock_audit:
                    stock_audit[target]["delayed_input"] += delta
                    stock_audit[target].setdefault("delayed_edges", []).append(edge_id)
        for intervention in sorted(active, key=lambda value: str(value.get("id", ""))):
            target = str(intervention["target"])
            value = float(intervention["value"])
            if intervention["op"] == "set":
                base[target] = value
            elif intervention["op"] == "add":
                base[target] += value
            elif intervention["op"] == "multiply":
                base[target] *= value
            if target in stock_audit:
                stock_audit[target]["interventions"].append(
                    {
                        "id": intervention.get("id"),
                        "op": intervention.get("op"),
                        "value": value,
                    }
                )

        zero = [edge for edge, _ in edges if edge.lag_ticks == 0 and edge.target not in blocked]
        strengths = {edge.id: strength for edge, strength in edges}
        gate_before = dict(edge_gate_state)
        current = dict(base)
        for component in _component_order(sorted(model.variables), zero):
            internal = [edge for edge in zero if edge.source in component and edge.target in component]
            incoming = [edge for edge in zero if edge.target in component and edge.source not in component]
            component_base = {node: base[node] for node in component}
            for edge in incoming:
                try:
                    output, _ = _edge_effect(
                        edge,
                        strengths[edge.id],
                        current[edge.source],
                        formula_version=model.formula_version,
                        threshold_active=gate_before.get(edge.id),
                    )
                    component_base[edge.target] += _integrate_edge_output(model, edge, output, config)
                except (OverflowError, ValueError) as exc:
                    unresolved = True
                    issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))
            cyclic = len(component) > 1 or any(edge.source == edge.target for edge in internal)
            if cyclic:
                x = {node: current[node] for node in component}
                converged = False
                for _iteration in range(max(1, config.jacobi_max_iter)):
                    candidate = dict(component_base)
                    try:
                        for edge in internal:
                            output, _ = _edge_effect(
                                edge,
                                strengths[edge.id],
                                x[edge.source],
                                formula_version=model.formula_version,
                                threshold_active=gate_before.get(edge.id),
                            )
                            candidate[edge.target] += _integrate_edge_output(model, edge, output, config)
                    except (OverflowError, ValueError):
                        break
                    residual = max(
                        (abs(candidate[node] - x[node]) for node in component),
                        default=0.0,
                    )
                    scale = max(
                        1.0,
                        *(abs(value) for value in x.values()),
                        *(abs(value) for value in candidate.values()),
                    )
                    if residual <= config.jacobi_abs_tol + config.jacobi_rel_tol * scale:
                        x = candidate
                        converged = True
                        break
                    for node in component:
                        relaxed = (1.0 - config.jacobi_relax) * x[node] + config.jacobi_relax * candidate[node]
                        x[node] = relaxed
                if not converged:
                    unresolved = True
                    issues.append(issue("NONCONVERGENCE", actual=component, message="zero-lag SCC did not converge"))
                for node in component:
                    current[node] = x[node]
            else:
                current[component[0]] = component_base[component[0]]

        for intervention in active:
            if intervention["op"] == "set":
                current[str(intervention["target"])] = float(intervention["value"])
        for node, value in list(current.items()):
            if not math.isfinite(value):
                unresolved = True
                issues.append(issue("NON_FINITE", pointer=node, actual=value, message="non-finite state"))
                value = model.variables[node].baseline
            current[node] = _apply_bounds(model.variables[node], value)
        state = current

        # Latches advance once from the converged source state, never once per
        # Jacobi iteration. The same pass records realized zero-lag stock flow.
        for edge in zero:
            try:
                output, next_gate = _edge_effect(
                    edge,
                    strengths[edge.id],
                    state[edge.source],
                    formula_version=model.formula_version,
                    threshold_active=gate_before.get(edge.id),
                )
                if next_gate is not None:
                    edge_gate_state[edge.id] = next_gate
                if edge.target in stock_audit:
                    stock_audit[edge.target]["zero_lag_input"] += _integrate_edge_output(
                        model, edge, output, config
                    )
            except (OverflowError, ValueError) as exc:
                unresolved = True
                issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))

        # Delayed effects capture the source value at emission time.  They are
        # never recomputed from the future state at delivery time.
        for edge, strength in edges:
            if edge.lag_ticks <= 0:
                continue
            try:
                output, next_gate = _edge_effect(
                    edge,
                    strength,
                    state[edge.source],
                    formula_version=model.formula_version,
                    threshold_active=edge_gate_state.get(edge.id),
                )
                if next_gate is not None:
                    edge_gate_state[edge.id] = next_gate
                delta = _integrate_edge_output(model, edge, output, config)
            except (OverflowError, ValueError) as exc:
                unresolved = True
                issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))
                continue
            due = tick + edge.lag_ticks
            scheduled.setdefault(due, []).append((edge.target, delta, edge.id))
            events += 1
        events += len(zero)
        if events > config.max_events:
            event_storm = True
            issues.append(issue("EVENT_STORM", actual=events, expected=config.max_events, message="event limit exceeded"))
        history.append(dict(sorted(state.items())))
        for node_id, audit in sorted(stock_audit.items()):
            audit["final_state"] = state[node_id]
            low, high = model.variables[node_id].bounds
            audit["bounded"] = bool(
                low is not None and math.isclose(state[node_id], low)
                or high is not None and math.isclose(state[node_id], high)
            )
            dynamics_history.append(audit)
        if event_storm:
            break

    model_digest = model_hash(model)
    configuration = config_payload(config)
    config_digest = canonical_hash(configuration)
    payload = {
        "model_hash": model_digest,
        "config_hash": config_digest,
        "final_state": dict(sorted(state.items())),
        "ticks": ticks,
        "events": events,
        "unresolved": unresolved,
        "event_storm": event_storm,
        "run_id": run_id,
        "seed": config.seed,
        "history_hash": canonical_hash(history),
    }
    if model.formula_version != LEGACY_FORMULA_VERSION:
        payload["formula_version"] = model.formula_version
        payload["dynamics_hash"] = canonical_hash(dynamics_history)
    digest = canonical_hash(payload)
    response = {
        "ok": not unresolved and not event_storm,
        "payload": {**payload, "workers": config.workers},
        "history": history,
        "run_hash": digest,
        "issues": [value.to_dict() for value in issues],
        "exit_code": 4 if unresolved or event_storm else 0,
    }
    if model.formula_version != LEGACY_FORMULA_VERSION:
        response["dynamics_history"] = dynamics_history
    return response


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("empty values")
    position = probability * (len(values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return values[low]
    return values[low] * (high - position) + values[high] * (position - low)


def _regime(value: float, scale: float) -> str:
    threshold = max(1e-12, abs(scale) * 1e-9)
    if abs(value) <= threshold:
        return "0"
    magnitude = int(math.floor(math.log2(max(abs(value) / max(abs(scale), 1e-12), 1e-12))))
    magnitude = max(-40, min(40, magnitude))
    return f"{'+' if value > 0 else '-'}{magnitude}"


def _signature(
    result: dict[str, float],
    model: ComputationalModel | None = None,
    history: list[dict[str, float]] | None = None,
    *,
    timestep: float = 1.0,
) -> str:
    tokens: list[str] = []
    for key, value in sorted(result.items()):
        baseline = model.variables[key].baseline if model is not None and key in model.variables else 0.0
        scale = max(1.0, abs(baseline))
        tokens.append(f"{key}:final={_regime(value - baseline, scale)}")
        if model is not None and history and model.variables.get(key) is not None and model.variables[key].scale == "stock":
            sampled_deltas = [float(row[key]) - baseline for row in history if key in row]
            if sampled_deltas:
                # Include the initial state in extrema and integrate exposure
                # over physical time so clustering is not an artifact of tick
                # resolution. History contains end-of-step samples.
                extrema_deltas = [0.0, *sampled_deltas]
                exposure = sum(abs(item) for item in sampled_deltas) * float(timestep)
                tokens.extend(
                    (
                        f"{key}:peak={_regime(max(extrema_deltas), scale)}",
                        f"{key}:trough={_regime(min(extrema_deltas), scale)}",
                        f"{key}:exposure={_regime(exposure, scale)}",
                    )
                )
    return "|".join(tokens)


def _branch_weights(signatures: list[str], total_runs: int) -> dict[str, float]:
    counts: dict[str, int] = {}
    for signature in signatures:
        counts[signature] = counts.get(signature, 0) + 1
    return {key: count / total_runs for key, count in counts.items()} if total_runs else {}


def _cluster_relative(
    results: list[tuple[int, dict[str, float], str]],
    total_runs: int,
) -> list[dict[str, Any]]:
    if not results or total_runs <= 0:
        return []
    buckets: dict[str, list[tuple[int, dict[str, float]]]] = {}
    for run_id, result, signature in results:
        buckets.setdefault(signature, []).append((run_id, result))
    ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    branches: list[dict[str, Any]] = []
    for index, (signature, members) in enumerate(ordered):
        medians = {
            key: _quantile(sorted(value[key] for _, value in members), 0.5)
            for key in sorted(members[0][1])
        }
        representative_run_id, representative = min(
            members,
            key=lambda value: (
                sum(
                    abs(value[1][key] - median) / max(1.0, abs(median))
                    for key, median in medians.items()
                ),
                value[0],
            ),
        )
        branches.append(
            {
                "id": f"branch:cluster-{index}",
                "relative_weight": len(members) / total_runs,
                "probability": None,
                "member_count": len(members),
                "representative": representative,
                "representative_run": f"run:{representative_run_id}",
                "signature": signature,
            }
        )
    return branches


def run_monte_carlo(model: ComputationalModel, config: EngineConfig, *, ticks: int = 10) -> dict[str, Any]:
    """Run adaptive deterministic batches and preserve unresolved probability mass."""
    config_issues = _config_errors(config)
    if config.mode != "monte_carlo":
        config_issues.append(issue("ENUM", pointer="/config/mode", actual=config.mode, expected="monte_carlo"))
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 0:
        config_issues.append(issue("RANGE", pointer="/ticks", actual=ticks, message="non-negative integer required"))
    if config_issues:
        invalid_summary = {
            "model_hash": model_hash(model),
            "config_hash": _diagnostic_hash(config_payload(config)),
            "n_runs": 0,
            "invalid_runs": 0,
            "unresolved_mass": 1.0,
            "valid_mass": 0.0,
            "mass_balance_error": 0.0,
            "converged_batches": 0,
            "quantiles": {},
            "branches": [],
            "likelihood_mode": "relative_weight",
            "seed": config.seed,
            "workers": config.workers,
        }
        invalid_summary["canonical_hash"] = _diagnostic_hash(
            {**invalid_summary, "workers": None, "issues": [value.to_dict() for value in config_issues]}
        )
        return {
            "ok": False,
            "summary": invalid_summary,
            "issues": [value.to_dict() for value in config_issues],
            "exit_code": 4,
        }
    minimum = max(1, config.min_runs)
    maximum = max(minimum, config.max_runs)
    batch_size = max(1, config.batch_size)
    valid_results: list[tuple[int, dict[str, float], str]] = []
    hashes: list[str] = []
    invalid = 0
    n_runs = 0
    stable = 0
    previous_weights: dict[str, float] | None = None
    while n_runs < maximum:
        batch_end = min(maximum, n_runs + batch_size)
        for run_id in range(n_runs, batch_end):
            result = run_deterministic(model, config, ticks=ticks, run_id=run_id)
            hashes.append(result["run_hash"])
            if result["ok"]:
                final_state = result["payload"]["final_state"]
                valid_results.append(
                    (
                        run_id,
                        final_state,
                        _signature(
                            final_state,
                            model,
                            result.get("history"),
                            timestep=config.timestep,
                        ),
                    )
                )
            else:
                invalid += 1
        n_runs = batch_end
        if n_runs < minimum:
            continue
        weights = _branch_weights([signature for _, _, signature in valid_results], n_runs)
        if previous_weights is not None:
            keys = set(weights) | set(previous_weights)
            change = max((abs(weights.get(key, 0.0) - previous_weights.get(key, 0.0)) for key in keys), default=0.0)
            stable = stable + 1 if change <= config.branch_mass_tol else 0
        previous_weights = weights
        if stable >= max(1, config.stable_batches):
            break

    unresolved_mass = invalid / n_runs
    valid_mass = len(valid_results) / n_runs
    quantiles: dict[str, dict[str, float]] = {}
    if valid_results:
        for key in sorted(valid_results[0][1]):
            values = sorted(result[key] for _, result, _signature_value in valid_results if key in result)
            quantiles[key] = {"p05": _quantile(values, 0.05), "p50": _quantile(values, 0.5), "p95": _quantile(values, 0.95)}
    branches = _cluster_relative(valid_results, n_runs)
    weight_sum = sum(float(branch["relative_weight"]) for branch in branches)
    mass_error = abs((weight_sum + unresolved_mass) - 1.0)
    threshold_exceeded = unresolved_mass > config.max_invalid_fraction
    summary = {
        "model_hash": model_hash(model),
        "config_hash": canonical_hash(config_payload(config)),
        "n_runs": n_runs,
        "invalid_runs": invalid,
        "unresolved_mass": unresolved_mass,
        "valid_mass": valid_mass,
        "mass_balance_error": mass_error,
        "converged_batches": stable,
        "quantiles": quantiles,
        "branches": branches,
        "likelihood_mode": "relative_weight",
        "seed": config.seed,
        "workers": config.workers,
    }
    summary["canonical_hash"] = canonical_hash({**summary, "workers": None, "run_hashes": hashes})
    issues: list[dict[str, Any]] = []
    if threshold_exceeded:
        issues.append(
            issue(
                "UNRESOLVED_MASS",
                actual=unresolved_mass,
                expected=config.max_invalid_fraction,
                message="invalid run fraction exceeds configured hard gate",
            ).to_dict()
        )
    if mass_error > 1e-12:
        issues.append(issue("UNRESOLVED_MASS", actual=mass_error, expected=0.0, message="branch mass is not conserved").to_dict())
    return {"ok": not issues, "summary": summary, "issues": issues, "exit_code": 4 if issues else 0}


def hand_calc_three_node_chain() -> dict[str, Any]:
    model = ComputationalModel()
    for variable_id, baseline in (("factor:A", 1.0), ("factor:B", 0.0), ("factor:C", 0.0)):
        model.variables[variable_id] = Variable(id=variable_id, role="endogenous", baseline=baseline, value=baseline)
    model.edges = [
        ModelEdge(id="edge:ab", source="factor:A", target="factor:B", sign=1, strength=0.5),
        ModelEdge(id="edge:bc", source="factor:B", target="factor:C", sign=1, strength=0.4),
    ]
    return {"model": model, "expected": {"factor:A": 1.0, "factor:B": 0.5, "factor:C": 0.2}}
