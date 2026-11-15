"""Deterministic and Monte Carlo causal simulation engine.

The engine is deliberately stdlib-first.  All stochastic draws are addressed by
``(seed, run_id, edge_id, purpose)`` rather than mutable RNG state, so changing
worker partitioning cannot change a run.  A compiled model and an execution
configuration are hashed into every run contract.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any, cast

from . import (
    FORMULA_VERSION,
    FORMULA_VERSION_2_2,
    LEGACY_FORMULA_VERSION,
    SUPPORTED_FORMULA_VERSIONS,
)
from .formula import (
    compute_effective_input,
    evaluate_edge_gate,
    evaluate_output_effect,
    expected_output_effect,
)
from .io import canonical_hash, canonical_json_bytes
from .issues import Issue, issue
from .rng import normal01, sample_triangular, sample_uniform, uniform01
from .schema import (
    GATE_COMPARE_OPS,
    GATE_CONDITION_FIELDS,
    GATE_FIELDS,
    GATE_OPS,
    INPUT_MODES,
    RETENTION_UNITS,
    TRANSFORMS,
    parse_duration_seconds,
)


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
    scc_solver: str = "jacobi"
    divergence_threshold: float = 1e12


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
    retention_unit: str = "per_day"
    retention_distribution: dict[str, Any] | None = None
    decay_rate: float | None = None
    decay_rate_distribution: dict[str, Any] | None = None
    value: float = 0.0
    initial_value: float | None = None


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
    input_mode: str = "level"
    shock_group: str | None = None
    shock_correlation: float = 0.0
    gate: dict[str, Any] | None = None


@dataclass
class ComputationalModel:
    variables: dict[str, Variable] = field(default_factory=dict)
    edges: list[ModelEdge] = field(default_factory=list)
    interventions: list[dict[str, Any]] = field(default_factory=list)
    formula_version: str = FORMULA_VERSION

    def __post_init__(self) -> None:
        if self.formula_version == "2.2":
            self.formula_version = "2.2.0"
        elif self.formula_version == "2.1":
            self.formula_version = "2.1.0"
        elif self.formula_version == "2.0":
            self.formula_version = "2.0.0"


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
    if not isinstance(value, list):
        raise ValueError("context_modifiers must be an array")
    # An explicit empty list means that no context modulates the edge.
    # Its multiplicative identity is one, matching semantic validation.
    if not value:
        return 1.0
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


def _normalise_edge_gate(raw_gate: Any, edge_id: str, valid_nodes: set[str]) -> dict[str, Any] | None:
    """Validate and canonicalise an optional conjunction/veto edge gate."""
    if raw_gate is None:
        return None
    if not isinstance(raw_gate, Mapping):
        raise ValueError(f"edge {edge_id} gate must be an object")
    unknown = set(raw_gate) - GATE_FIELDS
    if unknown:
        raise ValueError(f"edge {edge_id} gate has unsupported fields {sorted(unknown)}")
    op = str(raw_gate.get("op", ""))
    if op not in GATE_OPS:
        raise ValueError(f"edge {edge_id} gate op must be one of {sorted(GATE_OPS)}")
    raw_conditions = raw_gate.get("conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise ValueError(f"edge {edge_id} gate requires a non-empty conditions array")
    normalised_conditions: list[dict[str, Any]] = []
    for index, cond in enumerate(raw_conditions):
        if not isinstance(cond, Mapping):
            raise ValueError(f"edge {edge_id} gate condition {index} must be an object")
        unknown_cond = set(cond) - GATE_CONDITION_FIELDS
        if unknown_cond:
            raise ValueError(f"edge {edge_id} gate condition {index} has unsupported fields {sorted(unknown_cond)}")
        node_id = str(cond.get("node", ""))
        if not node_id or node_id not in valid_nodes:
            raise ValueError(f"edge {edge_id} gate condition {index} node '{node_id}' must reference a model variable")
        cmp_op = str(cond.get("op", ""))
        if cmp_op not in GATE_COMPARE_OPS:
            raise ValueError(f"edge {edge_id} gate condition {index} op must be one of {sorted(GATE_COMPARE_OPS)}")
        raw_val = cond.get("value")
        if not _is_finite_number(raw_val):
            raise ValueError(f"edge {edge_id} gate condition {index} value must be finite")
        normalised_conditions.append({"node": node_id, "op": cmp_op, "value": float(cast(int | float, raw_val))})
    return {"op": op, "conditions": normalised_conditions}


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
        initial_value: float | None = None
        if "initial_value" in node and node["initial_value"] is not None:
            if not _is_finite_number(node["initial_value"]):
                raise ValueError(f"node {node_id} initial_value must be finite")
            initial_value = float(cast(int | float, node["initial_value"]))
            if (bounds[0] is not None and initial_value < bounds[0]) or (
                bounds[1] is not None and initial_value > bounds[1]
            ):
                raise ValueError(f"node {node_id} initial_value must lie within declared bounds")
        scale = str(node.get("scale", "level"))
        if scale not in {"level", "flow", "stock"}:
            raise ValueError(f"node {node_id} scale must be level, flow, or stock")
        if scale != "stock" and any(
            key in node
            for key in ("retention", "retention_unit", "retention_distribution", "decay_rate", "decay_rate_distribution")
        ):
            raise ValueError(f"node {node_id} stock dynamics are only valid for stock variables")
        if "retention" in node and "decay_rate" in node:
            raise ValueError(f"node {node_id} cannot declare both retention and decay_rate")
        retention_unit = str(node.get("retention_unit", "per_day"))
        if retention_unit not in RETENTION_UNITS:
            raise ValueError(f"node {node_id} retention_unit must be per_day or per_tick")
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
            retention_unit=retention_unit,
            retention_distribution=retention_distribution,
            decay_rate=decay_rate,
            decay_rate_distribution=decay_rate_distribution,
            value=initial_value if initial_value is not None else baseline,
            initial_value=initial_value,
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
        lag_unit = str(raw.get("lag_unit", lag_unit))
        if lag_unit not in {"days", "ticks"}:
            raise ValueError(f"edge {edge_id} lag_unit must be days or ticks")
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
        raw_shock_group = raw.get("shock_group")
        shock_group = (
            str(raw_shock_group).strip()
            if isinstance(raw_shock_group, str) and raw_shock_group.strip()
            else None
        )
        shock_correlation = _finite_number(raw.get("shock_correlation", 0.0), 0.0)
        if not -1.0 <= shock_correlation <= 1.0:
            raise ValueError(f"edge {edge_id} shock_correlation must be in [-1, 1]")
        raw_transform = raw.get("transform", "linear")
        edge_transform = "linear" if raw_transform is None else str(raw_transform)
        if model.formula_version == FORMULA_VERSION_2_2:
            default_input_mode = "log_ratio" if edge_transform == "elasticity" else "deviation"
        else:
            default_input_mode = "level"
        raw_input_mode = raw.get("input_mode")
        input_mode = default_input_mode if raw_input_mode is None else str(raw_input_mode)
        if input_mode not in INPUT_MODES:
            raise ValueError(f"edge {edge_id} input_mode must be one of {sorted(INPUT_MODES)}")
        if input_mode == "log_ratio":
            src_baseline = model.variables[source].baseline
            if not math.isfinite(src_baseline) or src_baseline <= 0.0:
                if model.formula_version == FORMULA_VERSION_2_2 and edge_transform == "elasticity" and raw_input_mode is None:
                    raise ValueError(
                        f"edge {edge_id} elasticity transform defaults to input_mode='log_ratio' in formula_version 2.2.0 and requires positive baseline on source {source}"
                    )
                raise ValueError(
                    f"edge {edge_id} log_ratio input_mode requires positive baseline on source {source}"
                )
        gate = _normalise_edge_gate(raw.get("gate"), edge_id, set(model.variables))
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
                input_mode=input_mode,
                shock_group=shock_group,
                shock_correlation=shock_correlation,
                gate=gate,
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
        if "op" not in raw or raw.get("op") is None:
            if model.formula_version == FORMULA_VERSION_2_2:
                raise ValueError(
                    f"intervention {intervention_id} op is required in formula_version 2.2.0 (set, add, or multiply)"
                )
            op = "set"
        else:
            op = str(raw.get("op"))
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
            isinstance(raw_end, bool) or not isinstance(raw_end, int) or raw_end <= raw_start
        ):
            raise ValueError(
                f"intervention {intervention_id} end_tick must be null or an integer greater than start_tick"
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
        if var.initial_value is not None:
            payload["initial_value"] = var.initial_value
        if var.scale == "stock":
            if var.retention_distribution is not None:
                payload["retention_distribution"] = var.retention_distribution
            elif var.retention is not None:
                payload["retention"] = var.retention
            if var.retention_unit != "per_day":
                payload["retention_unit"] = var.retention_unit
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
        if model.formula_version == FORMULA_VERSION_2_2 or edge.input_mode != "level":
            payload["input_mode"] = edge.input_mode
        if edge.shock_group is not None:
            payload["shock_group"] = edge.shock_group
        if edge.shock_correlation != 0.0:
            payload["shock_correlation"] = edge.shock_correlation
        if edge.gate is not None:
            payload["gate"] = edge.gate
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


def model_from_dict(
    payload: Mapping[str, Any],
    *,
    formula_version: str | None = None,
) -> ComputationalModel:
    """Reconstruct a ``ComputationalModel`` from a ``model_payload`` dictionary safely."""
    var_fields = {item.name for item in fields(Variable)}
    edge_fields = {item.name for item in fields(ModelEdge)}
    raw_variables = payload.get("variables")
    raw_edges = payload.get("edges")
    raw_interventions = payload.get("interventions")

    variables: dict[str, Variable] = {}
    if isinstance(raw_variables, Mapping):
        for key, raw_var in raw_variables.items():
            if not isinstance(raw_var, Mapping):
                continue
            cleaned = {k: v for k, v in raw_var.items() if k in var_fields}
            cleaned.setdefault("id", str(key))
            cleaned.setdefault("role", "endogenous")
            raw_bounds = cleaned.get("bounds")
            if isinstance(raw_bounds, list) and len(raw_bounds) == 2:
                cleaned["bounds"] = (raw_bounds[0], raw_bounds[1])
            elif not isinstance(raw_bounds, tuple):
                cleaned["bounds"] = (None, None)
            if "value" not in cleaned:
                init_val = cleaned.get("initial_value")
                cleaned["value"] = init_val if init_val is not None else cleaned.get("baseline", 0.0)
            variables[str(key)] = Variable(**cleaned)

    edges: list[ModelEdge] = []
    if isinstance(raw_edges, list):
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, Mapping):
                continue
            cleaned_edge = {k: v for k, v in raw_edge.items() if k in edge_fields}
            if "source" not in cleaned_edge and "from" in raw_edge:
                cleaned_edge["source"] = raw_edge["from"]
            if "target" not in cleaned_edge and "to" in raw_edge:
                cleaned_edge["target"] = raw_edge["to"]
            edges.append(ModelEdge(**cleaned_edge))

    interventions = (
        [dict(item) for item in raw_interventions if isinstance(item, Mapping)]
        if isinstance(raw_interventions, list)
        else []
    )
    resolved_formula = formula_version or str(payload.get("formula_version") or FORMULA_VERSION)
    return ComputationalModel(
        variables=variables,
        edges=edges,
        interventions=interventions,
        formula_version=resolved_formula,
    )


def model_hash(model: ComputationalModel) -> str:
    return canonical_hash(model_payload(model))


def config_payload(config: EngineConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload.pop("workers", None)
    if payload.get("scc_solver") == "jacobi":
        payload.pop("scc_solver", None)
    if payload.get("divergence_threshold") == 1e12:
        payload.pop("divergence_threshold", None)
    return payload



def semantic_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Remove executor-only metadata before comparing reproducible semantics."""
    normalised = dict(result)
    normalised.pop("scc_diagnostics", None)
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
    if getattr(config, "scc_solver", "jacobi") not in {"jacobi", "linear_auto", "bounded_affine"}:
        problems.append(
            issue(
                "ENUM",
                pointer="/config/scc_solver",
                actual=getattr(config, "scc_solver", None),
                expected=["jacobi", "linear_auto", "bounded_affine"],
            )
        )
    div_thresh = getattr(config, "divergence_threshold", 1e12)
    if not _is_finite_number(div_thresh) or div_thresh <= 0:
        problems.append(
            issue(
                "RANGE",
                pointer="/config/divergence_threshold",
                actual=div_thresh,
                message="finite positive value required",
            )
        )
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


def _normal_cdf(z: float) -> float:
    """Standard normal cumulative distribution function using stdlib math.erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _inverse_triangular_cdf(u: float, low: float, mode: float, high: float) -> float:
    """Evaluate inverse CDF of a triangular distribution on [low, high] at quantile u."""
    if high <= low:
        return low
    u_clamped = min(1.0 - 1e-15, max(0.0, u))
    split = (mode - low) / (high - low)
    if u_clamped < split:
        return low + math.sqrt(u_clamped * (high - low) * (mode - low))
    return high - math.sqrt((1.0 - u_clamped) * (high - low) * (high - mode))


def _sample_strength(edge: ModelEdge, config: EngineConfig, run_id: int) -> float:
    # ``base_strength`` is the deterministic estimate in both formula
    # generations. Effect distributions describe Monte Carlo uncertainty and
    # must not silently replace that estimate during deterministic replay.
    if config.mode != "monte_carlo" or not edge.effect_distribution:
        return edge.strength
    if edge.shock_group and abs(edge.shock_correlation) > 0.0:
        rho = max(-1.0, min(1.0, float(edge.shock_correlation)))
        z_group = normal01(config.seed, run_id, "shock_group", edge.shock_group)
        z_edge = normal01(config.seed, run_id, edge.id, "effect")
        z_corr = rho * z_group + math.sqrt(max(0.0, 1.0 - rho * rho)) * z_edge
        dist = edge.effect_distribution
        kind = str(dist.get("distribution", dist.get("type", "fixed"))).lower()
        if kind == "normal":
            return float(dist["mean"]) + float(dist["sd"]) * z_corr
        if kind == "uniform":
            u = _normal_cdf(z_corr)
            return float(dist["min"]) + (float(dist["max"]) - float(dist["min"])) * u
        if kind == "triangular":
            return _inverse_triangular_cdf(
                _normal_cdf(z_corr),
                float(dist["min"]),
                float(dist["mode"]),
                float(dist["max"]),
            )
        if kind == "fixed":
            return float(dist["value"])
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
    per_tick = getattr(variable, "retention_unit", "per_day") == "per_tick"
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
        return math.exp(-decay_rate) if per_tick else math.exp(-decay_rate * float(config.timestep))
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
    return float(retention) if per_tick else float(retention ** float(config.timestep))


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


def _edge_input_nodes(edge: ModelEdge) -> list[str]:
    nodes = [edge.source]
    if isinstance(edge.gate, Mapping):
        conditions = edge.gate.get("conditions")
        if isinstance(conditions, list):
            for cond in conditions:
                if isinstance(cond, Mapping):
                    node_id = cond.get("node")
                    if isinstance(node_id, str) and node_id not in nodes:
                        nodes.append(node_id)
    return nodes


def _edge_effect(
    edge: ModelEdge,
    strength: float,
    source_value: float,
    *,
    source_baseline: float = 0.0,
    formula_version: str,
    threshold_active: bool | None = None,
    state_map: Mapping[str, float] | None = None,
) -> tuple[float, bool | None]:
    effective = compute_effective_input(edge.input_mode, source_value, source_baseline)
    gate_mult = evaluate_edge_gate(edge.gate, state_map) if edge.gate is not None else 1.0
    return evaluate_output_effect(
        base_strength=strength,
        sign=edge.sign,
        context_mult=edge.context_multiplier,
        input_effect=effective,
        transform=edge.transform,
        transform_parameters=edge.transform_parameters,
        saturation=edge.saturation,
        formula_version=formula_version,
        threshold_active=threshold_active,
        gate_multiplier=gate_mult,
    )


def _edge_effect_value(
    edge: ModelEdge,
    strength: float,
    source_value: float,
    *,
    source_baseline: float = 0.0,
    formula_version: str,
    state_map: Mapping[str, float] | None = None,
) -> float:
    effective = compute_effective_input(edge.input_mode, source_value, source_baseline)
    gate_mult = evaluate_edge_gate(edge.gate, state_map) if edge.gate is not None else 1.0
    return expected_output_effect(
        base_strength=strength,
        sign=edge.sign,
        context_mult=edge.context_multiplier,
        input_effect=effective,
        transform=edge.transform,
        transform_parameters=edge.transform_parameters,
        saturation=edge.saturation,
        formula_version=formula_version,
        gate_multiplier=gate_mult,
    )


def _tarjan(nodes: list[str], edges: list[ModelEdge]) -> list[list[str]]:
    graph: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        for src in _edge_input_nodes(edge):
            if src in graph and edge.target in graph:
                graph[src].append(edge.target)
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
        target = owner[edge.target]
        for dep in _edge_input_nodes(edge):
            if dep not in owner:
                continue
            source = owner[dep]
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
        "final_state": {
            key: (value.initial_value if value.initial_value is not None else value.baseline)
            for key, value in sorted(model.variables.items())
        },
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



def _solve_linear_system_gaussian(

    A: list[list[float]],
    b: list[float],
    *,
    pivot_tol: float = 1e-12,
) -> tuple[list[float] | None, str | None]:
    """Solve A x = b using Gaussian elimination with partial pivoting in pure stdlib Python."""
    n = len(A)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for k in range(n):
        max_val = abs(M[k][k])
        max_row = k
        for r in range(k + 1, n):
            val = abs(M[r][k])
            if val > max_val:
                max_val = val
                max_row = r
        if max_val < pivot_tol or not math.isfinite(max_val):
            return None, f"singular matrix or pivot too small ({max_val} < {pivot_tol})"
        if max_row != k:
            M[k], M[max_row] = M[max_row], M[k]
        pivot = M[k][k]
        for r in range(k + 1, n):
            factor = M[r][k] / pivot
            M[r][k] = 0.0
            for c in range(k + 1, n + 1):
                M[r][c] -= factor * M[k][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        pivot = M[i][i]
        if abs(pivot) < pivot_tol or not math.isfinite(pivot):
            return None, f"zero or non-finite diagonal element at index {i}"
        sum_known = sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = (M[i][n] - sum_known) / pivot
        if not math.isfinite(x[i]):
            return None, f"non-finite solution value at index {i}"
    return x, None


def _is_affine_eligible(
    component: list[str],
    internal: list[ModelEdge],
    model: ComputationalModel,
    *,
    allow_bounds: bool = False,
) -> tuple[bool, str]:
    """Check whether a zero-lag SCC satisfies affine or bounded-affine solve criteria."""
    for node in component:
        var = model.variables.get(node)
        if var is None:
            return False, f"node {node} missing from model variables"
        if var.scale != "level":
            return False, f"node {node} scale '{var.scale}' is not 'level'"
        if var.datatype != "continuous":
            return False, f"node {node} datatype '{var.datatype}' is not 'continuous'"
        if not allow_bounds and var.bounds != (None, None):
            return False, f"node {node} has finite bounds {var.bounds}"
    for edge in internal:
        if edge.input_mode == "log_ratio":
            return False, f"edge {edge.id} has non-linear input_mode '{edge.input_mode}'"
        if edge.transform not in {"linear", "identity"}:
            return False, f"edge {edge.id} has non-linear transform '{edge.transform}'"
        if edge.saturation is not None:
            return False, f"edge {edge.id} has finite saturation {edge.saturation}"
        if edge.gate is not None:
            return False, f"edge {edge.id} has conditional gate"
    return True, "affine_eligible"


def _build_affine_scc_system(
    component: list[str],
    internal: list[ModelEdge],
    component_base: dict[str, float],
    strengths: dict[str, float],
    model: ComputationalModel | None = None,
) -> tuple[list[list[float]], list[float]]:
    """Build linear system matrix W and RHS b for x = b + W x within an SCC."""
    n = len(component)
    idx_map = {node: i for i, node in enumerate(component)}
    W = [[0.0] * n for _ in range(n)]
    b = [component_base[node] for node in component]
    for edge in internal:
        src_idx = idx_map[edge.source]
        tgt_idx = idx_map[edge.target]
        coeff = 1.0 if edge.transform == "identity" else strengths[edge.id]
        weight = float(edge.sign) * float(coeff) * float(edge.context_multiplier)
        W[tgt_idx][src_idx] += weight
        if edge.input_mode == "deviation" and model is not None and edge.source in model.variables:
            b[tgt_idx] -= weight * float(model.variables[edge.source].baseline)
    return W, b


def _solve_scc_bounded_affine(
    component: list[str],
    internal: list[ModelEdge],
    component_base: dict[str, float],
    current: dict[str, float],
    strengths: dict[str, float],
    model: ComputationalModel,
    config: EngineConfig,
    *,
    tick: int,
    sample_id: int | str,
) -> tuple[dict[str, float], bool, list[Issue], dict[str, Any]]:
    """Solve box-constrained linear zero-lag SCC via active-set Newton + damped projection."""
    W, b = _build_affine_scc_system(component, internal, component_base, strengths, model=model)
    n = len(component)

    def clamp_idx(idx: int, val: float) -> float:
        return _apply_bounds(model.variables[component[idx]], val)

    def eval_residual(vec: list[float]) -> tuple[list[float], list[float], float, float]:
        raw = [b[i] + sum(W[i][j] * vec[j] for j in range(n)) for i in range(n)]
        proj = [clamp_idx(i, raw[i]) for i in range(n)]
        res = max((abs(vec[i] - proj[i]) for i in range(n)), default=0.0)
        d_scale = max(1.0, *(abs(v) for v in vec), *(abs(v) for v in proj))
        tol = config.jacobi_abs_tol + config.jacobi_rel_tol * d_scale
        return raw, proj, res, tol

    active: dict[int, float] = {}
    last_err = "bounded active-set did not converge"
    for switch_iter in range(24):
        free = [i for i in range(n) if i not in active]
        cand = [0.0] * n
        for idx, bound_val in active.items():
            cand[idx] = bound_val
        if free:
            A_f = [[(1.0 - W[i][j]) if i == j else -W[i][j] for j in free] for i in free]
            rhs_f = [b[i] + sum(W[i][j] * active[j] for j in active) for i in free]
            sol_f, err_msg = _solve_linear_system_gaussian(A_f, rhs_f, pivot_tol=1e-12)
            if sol_f is None or any(not math.isfinite(v) for v in sol_f):
                last_err = err_msg or "singular bounded subsystem"
                break
            for pos, i in enumerate(free):
                cand[i] = sol_f[pos]
        raw, proj, res, tol = eval_residual(cand)
        if res <= tol and all(math.isfinite(v) for v in proj):
            return {component[i]: proj[i] for i in range(n)}, True, [], {
                "tick": tick,
                "sample_id": sample_id,
                "component": component,
                "internal_edges": [e.id for e in internal],
                "solver": "bounded_affine",
                "method": "bounded_active_set",
                "iterations": switch_iter + 1,
                "residual": res,
                "tolerance": tol,
                "converged": True,
            }
        next_active: dict[int, float] = {}
        for i in range(n):
            low, high = model.variables[component[i]].bounds
            if low is not None and raw[i] < low:
                next_active[i] = float(low)
            elif high is not None and raw[i] > high:
                next_active[i] = float(high)
        if next_active == active:
            break
        active = next_active

    max_proj_iters = max(200, config.jacobi_max_iter)
    best_res = float("inf")
    for alpha in (0.5, 0.25, 0.1):
        vec = [clamp_idx(i, current[component[i]]) for i in range(n)]
        for it in range(max_proj_iters):
            raw, proj, res, tol = eval_residual(vec)
            best_res = res
            if res <= tol and all(math.isfinite(v) for v in proj):
                return {component[i]: proj[i] for i in range(n)}, True, [], {
                    "tick": tick,
                    "sample_id": sample_id,
                    "component": component,
                    "internal_edges": [e.id for e in internal],
                    "solver": "bounded_affine",
                    "method": "bounded_damped_projection",
                    "iterations": it + 1,
                    "residual": res,
                    "tolerance": tol,
                    "converged": True,
                }
            vec = [clamp_idx(i, (1.0 - alpha) * vec[i] + alpha * raw[i]) for i in range(n)]

    issues_out = [
        issue(
            "NONCONVERGENCE",
            actual=component,
            message=f"bounded_affine solve failed: {last_err} (residual={best_res})",
        )
    ]
    return {node: clamp_idx(i, current[node]) for i, node in enumerate(component)}, False, issues_out, {
        "tick": tick,
        "sample_id": sample_id,
        "component": component,
        "internal_edges": [e.id for e in internal],
        "solver": "bounded_affine",
        "converged": False,
        "error": "NONCONVERGENCE",
        "residual": best_res,
    }


def _solve_scc(
    component: list[str],
    internal: list[ModelEdge],
    component_base: dict[str, float],
    current: dict[str, float],
    strengths: dict[str, float],
    model: ComputationalModel,
    config: EngineConfig,
    active_formula: str,
    gate_before: dict[str, bool],
    *,
    tick: int = 0,
    sample_id: int | str = 0,
) -> tuple[dict[str, float], bool, list[Issue], dict[str, Any]]:
    """Unified zero-lag SCC solver supporting Jacobi iteration and direct linear solve."""
    cyclic = len(component) > 1 or any(
        src == edge.target for edge in internal for src in _edge_input_nodes(edge)
    )
    if not cyclic:
        node = component[0]
        val = component_base[node]
        return {node: val}, True, [], {
            "tick": tick,
            "sample_id": sample_id,
            "component": component,
            "solver": "direct",
            "converged": True,
            "iterations": 0,
        }

    x = {node: current[node] for node in component}
    converged = False
    issues: list[Issue] = []
    final_residual = 0.0
    scale_val = 1.0
    iterations_used = 0

    # 1. Primary method: Jacobi iteration
    for _iteration in range(max(1, config.jacobi_max_iter)):
        iterations_used = _iteration + 1
        candidate = dict(component_base)
        eval_state = {**current, **x}
        try:
            for edge in internal:
                output, _ = _edge_effect(
                    edge,
                    strengths[edge.id],
                    x[edge.source],
                    source_baseline=model.variables[edge.source].baseline,
                    formula_version=active_formula,
                    threshold_active=gate_before.get(edge.id),
                    state_map=eval_state,
                )
                candidate[edge.target] += _integrate_edge_output(model, edge, output, config)
        except (OverflowError, ValueError):
            break
        if any(not math.isfinite(val) for val in candidate.values()):
            break

        residual = max((abs(candidate[node] - x[node]) for node in component), default=0.0)
        final_residual = residual
        scale_val = max(1.0, *(abs(val) for val in x.values()), *(abs(val) for val in candidate.values()))
        if residual <= config.jacobi_abs_tol + config.jacobi_rel_tol * scale_val:
            x = candidate
            converged = True
            break
        for node in component:
            x[node] = (1.0 - config.jacobi_relax) * x[node] + config.jacobi_relax * candidate[node]

    if converged:
        return x, True, [], {
            "tick": tick,
            "sample_id": sample_id,
            "component": component,
            "internal_edges": [e.id for e in internal],
            "solver": "jacobi",
            "iterations": iterations_used,
            "residual": final_residual,
            "tolerance": config.jacobi_abs_tol + config.jacobi_rel_tol * scale_val,
            "converged": True,
        }

    # 2. If Jacobi failed and linear_auto / bounded_affine is configured, attempt affine solve
    solver_choice = getattr(config, "scc_solver", "jacobi")
    if solver_choice in {"linear_auto", "bounded_affine"}:
        eligible_unbounded, reason = _is_affine_eligible(component, internal, model, allow_bounds=False)
        if solver_choice == "linear_auto" and eligible_unbounded:
            W, b = _build_affine_scc_system(component, internal, component_base, strengths, model=model)
            n = len(component)
            A = [[0.0] * n for _ in range(n)]
            for i in range(n):
                for j in range(n):
                    A[i][j] = (1.0 - W[i][j]) if i == j else -W[i][j]

            x_direct, err_msg = _solve_linear_system_gaussian(A, b, pivot_tol=1e-12)
            if x_direct is not None:
                residuals = []
                for i in range(n):
                    calc_i = b[i] + sum(W[i][j] * x_direct[j] for j in range(n))
                    residuals.append(abs(x_direct[i] - calc_i))
                direct_residual = max(residuals, default=0.0)
                d_scale = max(
                    1.0,
                    *(abs(v) for v in x_direct),
                    *(abs(b[i] + sum(W[i][j] * x_direct[j] for j in range(n))) for i in range(n)),
                )
                tol = config.jacobi_abs_tol + config.jacobi_rel_tol * d_scale
                if direct_residual <= tol and all(math.isfinite(v) for v in x_direct):
                    sol_dict = {component[i]: x_direct[i] for i in range(n)}
                    return sol_dict, True, [], {
                        "tick": tick,
                        "sample_id": sample_id,
                        "component": component,
                        "internal_edges": [e.id for e in internal],
                        "solver": "linear_auto",
                        "method": "linear_direct",
                        "iterations": 1,
                        "residual": direct_residual,
                        "tolerance": tol,
                        "converged": True,
                    }
                else:
                    err_msg = f"direct solve residual too large ({direct_residual} > {tol})"

            err_code = "SINGULAR" if "singular" in str(err_msg) else "NONCONVERGENCE"
            issues.append(
                issue("NONCONVERGENCE", actual=component, message=f"linear_auto direct solve failed: {err_msg}")
            )
            return x, False, issues, {
                "tick": tick,
                "sample_id": sample_id,
                "component": component,
                "internal_edges": [e.id for e in internal],
                "solver": "linear_auto",
                "converged": False,
                "error": err_code,
                "detail": err_msg,
            }

        eligible_bounded, bounded_reason = _is_affine_eligible(component, internal, model, allow_bounds=True)
        if eligible_bounded:
            return _solve_scc_bounded_affine(
                component,
                internal,
                component_base,
                current,
                strengths,
                model,
                config,
                tick=tick,
                sample_id=sample_id,
            )

        issues.append(
            issue(
                "NONCONVERGENCE",
                actual=component,
                message=f"{solver_choice} ineligible for nonlinear SCC: {bounded_reason or reason}",
            )
        )
        return x, False, issues, {
            "tick": tick,
            "sample_id": sample_id,
            "component": component,
            "internal_edges": [e.id for e in internal],
            "solver": solver_choice,
            "converged": False,
            "error": "UNSUPPORTED_TRANSFORM",
            "reason": bounded_reason or reason,
        }

    # Standard Jacobi non-convergence
    issues.append(issue("NONCONVERGENCE", actual=component, message="zero-lag SCC did not converge"))
    return x, False, issues, {
        "tick": tick,
        "sample_id": sample_id,
        "component": component,
        "internal_edges": [e.id for e in internal],
        "solver": "jacobi",
        "iterations": iterations_used,
        "residual": final_residual,
        "converged": False,
        "error": "ITERATION_LIMIT_EXCEEDED",
    }


def _divergence_limit(model: ComputationalModel, config: EngineConfig) -> float:
    configured = float(getattr(config, "divergence_threshold", 1e12))
    if configured != 1e12:
        return configured
    scale_floor = max(
        [
            1.0,
            *(abs(var.baseline) for var in model.variables.values() if math.isfinite(var.baseline)),
            *(
                abs(float(item["value"]))
                for item in model.interventions
                if isinstance(item, dict)
                and item.get("op") in {"set", "add"}
                and _is_finite_number(item.get("value"))
            ),
        ]
    )
    if scale_floor > 1e295:
        return float("inf")
    limit = 1e12 * scale_floor
    return limit if math.isfinite(limit) else float("inf")


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
    state = {
        key: (var.initial_value if var.initial_value is not None else var.baseline)
        for key, var in model.variables.items()
    }
    history: list[dict[str, float]] = []
    dynamics_history: list[dict[str, Any]] = []
    scheduled: dict[int, list[tuple[str, float, str]]] = {}
    events = 0
    unresolved = False
    event_storm = False
    scc_diagnostics: list[dict[str, Any]] = []
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
                base[key] = (
                    variable.initial_value
                    if tick == 0 and variable.initial_value is not None
                    else variable.baseline
                )
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
                        source_baseline=model.variables[edge.source].baseline,
                        formula_version=model.formula_version,
                        threshold_active=gate_before.get(edge.id),
                        state_map=current,
                    )
                    component_base[edge.target] += _integrate_edge_output(model, edge, output, config)
                except (OverflowError, ValueError) as exc:
                    unresolved = True
                    issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))
            comp_sol, comp_ok, comp_issues, comp_diag = _solve_scc(
                component=component,
                internal=internal,
                component_base=component_base,
                current=current,
                strengths=strengths,
                model=model,
                config=config,
                active_formula=model.formula_version,
                gate_before=gate_before,
                tick=tick,
                sample_id=run_id,
            )
            if not comp_ok:
                unresolved = True
            issues.extend(comp_issues)
            for node, val in comp_sol.items():
                current[node] = val
            if comp_diag.get("solver") != "direct":
                scc_diagnostics.append(comp_diag)


        for intervention in active:
            if intervention["op"] == "set":
                current[str(intervention["target"])] = float(intervention["value"])
        divergence_limit = _divergence_limit(model, config)
        diverged = False
        for node, value in list(current.items()):
            if not math.isfinite(value):
                unresolved = True
                diverged = True
                issues.append(issue("NON_FINITE", pointer=node, actual=value, message="non-finite state"))
                value = model.variables[node].baseline
            else:
                value = _apply_bounds(model.variables[node], value)
                if math.isfinite(divergence_limit) and abs(value) > divergence_limit:
                    unresolved = True
                    diverged = True
                    issues.append(
                        issue(
                            "STATE_DIVERGENCE",
                            pointer=node,
                            actual=value,
                            expected={"limit": divergence_limit},
                            message=f"state magnitude {value} exceeds divergence limit {divergence_limit}",
                        )
                    )
            current[node] = value
        state = current

        # Latches advance once from the converged source state, never once per
        # Jacobi iteration. The same pass records realized zero-lag stock flow.
        for edge in zero:
            try:
                output, next_gate = _edge_effect(
                    edge,
                    strengths[edge.id],
                    state[edge.source],
                    source_baseline=model.variables[edge.source].baseline,
                    formula_version=model.formula_version,
                    threshold_active=gate_before.get(edge.id),
                    state_map=state,
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
                    source_baseline=model.variables[edge.source].baseline,
                    formula_version=model.formula_version,
                    threshold_active=edge_gate_state.get(edge.id),
                    state_map=state,
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
        if event_storm or diverged:
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
    if scc_diagnostics:
        response["scc_diagnostics"] = scc_diagnostics


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


def _regime(value: float, scale: float, *, legacy: bool = False) -> str:
    threshold = max(1e-12, abs(scale) * 1e-9)
    if abs(value) <= threshold:
        return "0"
    ratio = abs(value) / max(abs(scale), 1e-12)
    magnitude = int(math.floor(math.log2(max(ratio, 1e-12))))
    magnitude = max(-40, min(40, magnitude))
    prefix = "+" if value > 0 else "-"
    if magnitude < 0 and not legacy:
        if magnitude >= -2:
            bucket = "sub_mid"
        elif magnitude >= -5:
            bucket = "sub_low"
        else:
            bucket = "sub_micro"
        return f"{prefix}{bucket}"
    return f"{prefix}{magnitude}"


def _signature(
    result: dict[str, float],
    model: ComputationalModel | None = None,
    history: list[dict[str, float]] | None = None,
    *,
    timestep: float = 1.0,
) -> str:
    legacy = model is not None and model.formula_version != FORMULA_VERSION_2_2
    tokens: list[str] = []
    for key, value in sorted(result.items()):
        var = model.variables.get(key) if model is not None else None
        baseline = var.baseline if var is not None else 0.0
        init_delta = (var.initial_value - baseline) if (var is not None and var.initial_value is not None) else 0.0
        scale = max(1.0, abs(baseline))
        tokens.append(f"{key}:final={_regime(value - baseline, scale, legacy=legacy)}")
        if model is not None and history and var is not None and var.scale == "stock":
            sampled_deltas = [float(row[key]) - baseline for row in history if key in row]
            if sampled_deltas:
                # Include the initial state in extrema and integrate exposure
                # over physical time so clustering is not an artifact of tick
                # resolution. History contains end-of-step samples.
                extrema_deltas = [init_delta, *sampled_deltas]
                exposure = sum(abs(item) for item in sampled_deltas) * float(timestep)
                tokens.extend(
                    (
                        f"{key}:peak={_regime(max(extrema_deltas), scale, legacy=legacy)}",
                        f"{key}:trough={_regime(min(extrema_deltas), scale, legacy=legacy)}",
                        f"{key}:exposure={_regime(exposure, scale, legacy=legacy)}",
                    )
                )
    return "|".join(tokens)


def _branch_weights(signatures: list[str], total_runs: int) -> dict[str, float]:
    counts: dict[str, int] = {}
    for signature in signatures:
        counts[signature] = counts.get(signature, 0) + 1
    return {key: count / total_runs for key, count in counts.items()} if total_runs else {}


def _select_branch_representative(members: list[tuple[int, dict[str, float]]]) -> tuple[int, dict[str, float]]:
    medians = {
        key: _quantile(sorted(value[key] for _, value in members), 0.5)
        for key in sorted(members[0][1])
    }
    return min(
        members,
        key=lambda value: (
            sum(
                abs(value[1][key] - median) / max(1.0, abs(median))
                for key, median in medians.items()
            ),
            value[0],
        ),
    )


def _cluster_relative(
    results: list[tuple[int, dict[str, float], str]],
    total_runs: int,
    *,
    max_branches: int | None = 12,
) -> list[dict[str, Any]]:
    if not results or total_runs <= 0:
        return []
    buckets: dict[str, list[tuple[int, dict[str, float]]]] = {}
    for run_id, result, signature in results:
        buckets.setdefault(signature, []).append((run_id, result))
    ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    branches: list[dict[str, Any]] = []
    limit = len(ordered) if max_branches is None else max(1, max_branches)
    head_buckets = ordered if len(ordered) <= limit else ordered[: max(0, limit - 1)]
    tail_buckets = [] if len(ordered) <= limit else ordered[max(0, limit - 1) :]
    for index, (signature, members) in enumerate(head_buckets):
        representative_run_id, representative = _select_branch_representative(members)
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
    if tail_buckets:
        tail_members = [member for _, members in tail_buckets for member in members]
        representative_run_id, representative = _select_branch_representative(tail_members)
        branches.append(
            {
                "id": f"branch:cluster-{len(head_buckets)}",
                "relative_weight": len(tail_members) / total_runs,
                "probability": None,
                "member_count": len(tail_members),
                "representative": representative,
                "representative_run": f"run:{representative_run_id}",
                "signature": f"tail_consolidated:{len(tail_buckets)}_regimes",
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
    branches = _cluster_relative(
        valid_results,
        n_runs,
        max_branches=12 if model.formula_version == FORMULA_VERSION_2_2 else None,
    )
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


VALID_TRACE_TRANSFORM_MODES = frozenset(
    {
        "linear",
        "threshold",
        "logistic",
        "elasticity",
        "identity",
        "saturation",
        "above",
        "below",
        "deadband",
        "hysteresis",
    }
)


def _populate_trace_step_details(
    step_dict: dict[str, Any],
    *,
    model: ComputationalModel,
    edge: ModelEdge,
    output: float,
    target_state_before: float,
    target_state_after: float,
    retention_factors: dict[str, float],
    config: EngineConfig,
    gate_before_step: bool,
    gate_after_step: bool,
    active_formula: str | None = None,
    gate_multiplier: float | None = None,
) -> None:
    """Attach stock-flow integration metrics and transform mode metadata to a trace step."""
    formula = active_formula or model.formula_version
    if edge.gate is not None and gate_multiplier is not None:
        step_dict["gate_multiplier"] = float(gate_multiplier)
    if formula == FORMULA_VERSION_2_2 or edge.input_mode != "level":
        source_baseline = float(model.variables[edge.source].baseline) if edge.source in model.variables else 0.0
        step_dict["input_mode"] = edge.input_mode
        step_dict["source_baseline"] = source_baseline
        try:
            step_dict["input_effect"] = compute_effective_input(
                edge.input_mode,
                float(step_dict.get("source_state", source_baseline)),
                source_baseline,
            )
        except ValueError:
            step_dict["input_effect"] = 0.0
    if edge.target in model.variables and model.variables[edge.target].scale == "stock":
        ret_factor = float(retention_factors.get(edge.target, 1.0))
        explicit_decay = model.variables[edge.target].decay_rate
        if explicit_decay is not None:
            decay_val = float(explicit_decay)
        elif 0.0 < ret_factor < 1.0:
            decay_val = round(1.0 - ret_factor, 6)
        else:
            decay_val = 0.0
        outflow_rate = target_state_before * (1.0 - ret_factor) / float(config.timestep)
        step_dict["stock_flow_integrations"] = {
            "stock_variable": edge.target,
            "inflow_rate": output,
            "outflow_rate": outflow_rate,
            "retention_factor": ret_factor,
            "decay_constant": decay_val,
            "dt": float(config.timestep),
            "integrated_level": target_state_after,
        }
    if edge.transform in VALID_TRACE_TRANSFORM_MODES - {"linear", "identity"}:
        raw_mode = (
            str(edge.transform_parameters.get("mode", edge.transform))
            if edge.transform == "threshold"
            else str(edge.transform)
        )
        mode = raw_mode if raw_mode in VALID_TRACE_TRANSFORM_MODES else "linear"
        step_dict["transform_applied"] = {
            "mode": mode,
            "threshold_active_before": bool(gate_before_step),
            "threshold_active_after": bool(gate_after_step),
        }


def generate_numerical_execution_trace(
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: int = 10,
    run_id: int = 0,
    model_id: str = "model:compiled",
    execution_timestamp: str | None = None,
    formula_version: str | None = None,
) -> dict[str, Any]:
    """Execute model deterministically and construct a schema-compliant numerical execution trace."""
    active_formula = formula_version or model.formula_version
    ticks_int = max(0, int(ticks))

    param_payload = {
        "model": model_payload(model),
        "config": config_payload(config),
        "interventions": sorted(model.interventions, key=lambda v: str(v.get("id", ""))),
        "ticks": ticks_int,
    }
    parameters_hash = f"sha256:{canonical_hash(param_payload)}"

    issues: list[Issue] = _config_errors(config)
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 0:
        issues.append(issue("RANGE", pointer="/ticks", actual=ticks, message="non-negative integer required"))

    state = {
        key: (var.initial_value if var.initial_value is not None else var.baseline)
        for key, var in sorted(model.variables.items())
    }
    history: list[dict[str, float]] = []
    scheduled: dict[int, list[dict[str, Any]]] = {}
    unresolved = False
    event_storm = False
    events = 0
    steps: list[dict[str, Any]] = []
    step_counter = 1

    retention_factors: dict[str, float] = {}
    try:
        retention_factors = {
            key: sampled_retention_factor(variable, config, run_id)
            for key, variable in sorted(model.variables.items())
            if variable.scale == "stock"
        }
    except (OverflowError, TypeError, ValueError) as exc:
        unresolved = True
        issues.append(issue("RANGE", pointer="/variables", message=f"stock dynamics sampling failed: {exc}"))

    sampled_edges: list[ModelEdge] = []
    edge_strengths: dict[str, float] = {}
    for edge in sorted(model.edges, key=lambda value: value.id):
        try:
            strength, lag_ticks, exists = sampled_edge_parameters(edge, config, run_id)
            transform_params = sampled_transform_parameters(edge, config, run_id)
        except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
            unresolved = True
            issues.append(issue("RANGE", pointer=f"/edges/{edge.id}/lag_distribution", message=f"edge sampling failed: {exc}"))
            continue
        if exists:
            s_edge = replace(edge, lag_ticks=lag_ticks, transform_parameters=transform_params)
            sampled_edges.append(s_edge)
            edge_strengths[edge.id] = strength

    edge_gate_state: dict[str, bool] = {}
    divergence_limit = _divergence_limit(model, config)

    for tick in range(ticks_int):
        tick_steps_start = len(steps)
        released_resets = _reset_releases(model, tick)
        base: dict[str, float] = {}
        for key, variable in sorted(model.variables.items()):
            if active_formula != LEGACY_FORMULA_VERSION and variable.scale == "stock":
                previous_state = state[key]
                retention_factor = retention_factors.get(key, 1.0)
                reset = key in released_resets
                retained_state = variable.baseline if reset else previous_state * retention_factor
                base[key] = retained_state
            else:
                base[key] = (
                    variable.initial_value
                    if tick == 0 and variable.initial_value is not None
                    else variable.baseline
                )

        active = _active_interventions(model, tick)
        blocked = {str(value["target"]) for value in active if value.get("op") == "set"}
        interventions_by_target = {str(value["target"]): value for value in active}

        # Check releases from previous tick
        previous_active = _active_interventions(model, tick - 1) if tick > 0 else []
        previous_targets = {str(value["target"]) for value in previous_active}
        current_targets = {str(value["target"]) for value in active}
        released_targets = previous_targets - current_targets

        # Deliver scheduled delayed edges arriving at tick
        for delayed_item in scheduled.pop(tick, []):
            target = delayed_item["target"]
            delta = delayed_item["delta"]
            edge = delayed_item["edge"]
            strength = delayed_item["strength"]
            emission_tick = delayed_item["emission_tick"]
            source_state = delayed_item["source_state"]
            output = delayed_item["output"]
            gate_before_step = delayed_item["gate_before"]
            gate_after_step = delayed_item["gate_after"]
            delayed_gate_multiplier = delayed_item.get("gate_multiplier")

            target_blocked = target in blocked
            actual_delta = 0.0 if target_blocked else delta
            target_state_before = base.get(target, 0.0)
            if not target_blocked and target in base:
                base[target] += actual_delta
            target_state_after = base.get(target, 0.0)

            interv_type = "do_set" if target_blocked else interventions_by_target.get(target, {}).get("op", "none")
            if target in released_targets and not target_blocked:
                interv_type = "release"
            elif interv_type in {"add", "multiply", "set"}:
                interv_type = f"do_{interv_type}"
            if interv_type not in {"none", "do_set", "do_add", "do_multiply", "release"}:
                interv_type = "none"

            norm_edge_id = edge.id if edge.id.startswith(("causal:", "edge:")) else f"edge:{edge.id}"
            step_dict = {
                "step": step_counter,
                "sample_id": run_id,
                "tick": tick,
                "emission_tick": emission_tick,
                "delivery_tick": tick,
                "source_node_id": edge.source,
                "node_id": target,
                "edge_id": norm_edge_id,
                "drawn_strength": strength,
                "sampled_parameters": {
                    "lag": edge.lag_ticks,
                    "strength": strength,
                    **edge.transform_parameters,
                },
                "source_state": source_state,
                "target_state_before": target_state_before,
                "target_state_after": target_state_after,
                "state_transitions": {
                    "delta": actual_delta,
                    "previous_value": target_state_before,
                    "new_value": target_state_after,
                    "mechanism": edge.transform or "linear",
                    "intervention_type": interv_type,
                },
            }
            _populate_trace_step_details(
                step_dict,
                model=model,
                edge=edge,
                output=0.0 if target_blocked else output,
                target_state_before=target_state_before,
                target_state_after=target_state_after,
                retention_factors=retention_factors,
                config=config,
                gate_before_step=gate_before_step,
                gate_after_step=gate_after_step,
                active_formula=active_formula,
                gate_multiplier=delayed_gate_multiplier,
            )
            steps.append(step_dict)
            step_counter += 1

        # Apply direct interventions to base
        for intervention in sorted(active, key=lambda value: str(value.get("id", ""))):
            target = str(intervention["target"])
            value = float(intervention["value"])
            if intervention["op"] == "set":
                base[target] = value
            elif intervention["op"] == "add":
                base[target] += value
            elif intervention["op"] == "multiply":
                base[target] *= value

        zero = [edge for edge in sampled_edges if edge.lag_ticks == 0 and edge.target not in blocked]
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
                        edge_strengths[edge.id],
                        current[edge.source],
                        source_baseline=model.variables[edge.source].baseline,
                        formula_version=active_formula,
                        threshold_active=gate_before.get(edge.id),
                        state_map=current,
                    )
                    component_base[edge.target] += _integrate_edge_output(model, edge, output, config)
                except (OverflowError, ValueError) as exc:
                    unresolved = True
                    issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))
            comp_sol, comp_ok, comp_issues, _ = _solve_scc(
                component=component,
                internal=internal,
                component_base=component_base,
                current=current,
                strengths=edge_strengths,
                model=model,
                config=config,
                active_formula=active_formula,
                gate_before=gate_before,
                tick=tick,
                sample_id="trace",
            )
            if not comp_ok:
                unresolved = True
            issues.extend(comp_issues)
            for node, val in comp_sol.items():
                current[node] = val


        for intervention in active:
            if intervention["op"] == "set":
                current[str(intervention["target"])] = float(intervention["value"])
        diverged = False
        for node, val in list(current.items()):
            if not math.isfinite(val):
                unresolved = True
                diverged = True
                issues.append(issue("NON_FINITE", pointer=node, actual=val, message="non-finite state"))
                val = model.variables[node].baseline
            else:
                val = _apply_bounds(model.variables[node], val)
                if math.isfinite(divergence_limit) and abs(val) > divergence_limit:
                    unresolved = True
                    diverged = True
                    issues.append(
                        issue(
                            "STATE_DIVERGENCE",
                            pointer=node,
                            actual=val,
                            expected={"limit": divergence_limit},
                            message=f"state magnitude {val} exceeds divergence limit {divergence_limit}",
                        )
                    )
            current[node] = val
        state = current

        # Record zero-lag edges
        all_zero = [edge for edge in sampled_edges if edge.lag_ticks == 0]
        for edge in all_zero:
            edge_is_blocked = edge.target in blocked
            edge_gate_mult: float | None = None
            try:
                if edge.gate is not None:
                    edge_gate_mult = evaluate_edge_gate(edge.gate, state)
                if edge_is_blocked:
                    output = 0.0
                    delta = 0.0
                    next_gate = None
                else:
                    output, next_gate = _edge_effect(
                        edge,
                        edge_strengths[edge.id],
                        state[edge.source],
                        source_baseline=model.variables[edge.source].baseline,
                        formula_version=active_formula,
                        threshold_active=gate_before.get(edge.id),
                        state_map=state,
                    )
                    if next_gate is not None:
                        edge_gate_state[edge.id] = next_gate
                    delta = _integrate_edge_output(model, edge, output, config)
            except (OverflowError, ValueError) as exc:
                unresolved = True
                issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))
                output = 0.0
                delta = 0.0

            target_state_before = base.get(edge.target, 0.0)
            target_state_after = state.get(edge.target, 0.0)
            interv_type = "do_set" if edge_is_blocked else interventions_by_target.get(edge.target, {}).get("op", "none")
            if edge.target in released_targets and not edge_is_blocked:
                interv_type = "release"
            elif interv_type in {"add", "multiply", "set"}:
                interv_type = f"do_{interv_type}"
            if interv_type not in {"none", "do_set", "do_add", "do_multiply", "release"}:
                interv_type = "none"

            norm_edge_id = edge.id if edge.id.startswith(("causal:", "edge:")) else f"edge:{edge.id}"
            step_dict = {
                "step": step_counter,
                "sample_id": run_id,
                "tick": tick,
                "emission_tick": tick,
                "delivery_tick": tick,
                "source_node_id": edge.source,
                "node_id": edge.target,
                "edge_id": norm_edge_id,
                "drawn_strength": edge_strengths[edge.id],
                "sampled_parameters": {
                    "lag": 0,
                    "strength": edge_strengths[edge.id],
                    **edge.transform_parameters,
                },
                "source_state": state[edge.source],
                "target_state_before": target_state_before,
                "target_state_after": target_state_after,
                "state_transitions": {
                    "delta": delta,
                    "previous_value": target_state_before,
                    "new_value": target_state_after,
                    "mechanism": edge.transform or "linear",
                    "intervention_type": interv_type,
                },
            }
            _populate_trace_step_details(
                step_dict,
                model=model,
                edge=edge,
                output=output,
                target_state_before=target_state_before,
                target_state_after=target_state_after,
                retention_factors=retention_factors,
                config=config,
                gate_before_step=bool(gate_before.get(edge.id, False)),
                gate_after_step=bool(edge_gate_state.get(edge.id, False)),
                active_formula=active_formula,
                gate_multiplier=edge_gate_mult,
            )
            steps.append(step_dict)
            step_counter += 1

        # Synchronize all steps delivered on this tick to the realized end-of-tick
        # state (covers delayed arrivals followed by zero-lag updates, interventions,
        # multi-edge fan-in, and bounds clipping).
        for step_entry in steps[tick_steps_start:]:
            target_id = step_entry.get("node_id")
            if isinstance(target_id, str) and target_id in state:
                final_val = state[target_id]
                step_entry["target_state_after"] = final_val
                if isinstance(step_entry.get("state_transitions"), dict):
                    step_entry["state_transitions"]["new_value"] = final_val
                if isinstance(step_entry.get("stock_flow_integrations"), dict):
                    step_entry["stock_flow_integrations"]["integrated_level"] = final_val

        # Schedule delayed edges for future ticks
        for edge in sampled_edges:
            if edge.lag_ticks <= 0:
                continue
            gate_before_step = edge_gate_state.get(edge.id, False)
            edge_gate_mult = evaluate_edge_gate(edge.gate, state) if edge.gate is not None else None
            try:
                output, next_gate = _edge_effect(
                    edge,
                    edge_strengths[edge.id],
                    state[edge.source],
                    source_baseline=model.variables[edge.source].baseline,
                    formula_version=active_formula,
                    threshold_active=edge_gate_state.get(edge.id),
                    state_map=state,
                )
                if next_gate is not None:
                    edge_gate_state[edge.id] = next_gate
                delta = _integrate_edge_output(model, edge, output, config)
            except (OverflowError, ValueError) as exc:
                unresolved = True
                issues.append(issue("NONCONVERGENCE", pointer=edge.id, message=str(exc)))
                continue
            due = tick + edge.lag_ticks
            scheduled.setdefault(due, []).append(
                {
                    "target": edge.target,
                    "delta": delta,
                    "edge": edge,
                    "strength": edge_strengths[edge.id],
                    "emission_tick": tick,
                    "source_state": state[edge.source],
                    "output": output,
                    "gate_before": gate_before_step,
                    "gate_after": edge_gate_state.get(edge.id, False),
                    "gate_multiplier": edge_gate_mult,
                }
            )
            events += 1
        events += len(zero)
        if events > config.max_events:
            event_storm = True
            unresolved = True
            issues.append(issue("EVENT_STORM", actual=events, expected=config.max_events, message="event limit exceeded"))
            break
        history.append(dict(sorted(state.items())))
        if diverged:
            break

    # Calculate hash_chain
    prev_hash = "0" * 64
    for step in steps:
        step_data = {k: v for k, v in step.items() if k != "hash_chain"}
        step_bytes = canonical_json_bytes(step_data)
        hasher = hashlib.sha256()
        hasher.update(prev_hash.encode("utf-8") + b":" + step_bytes)
        current_hash = hasher.hexdigest()
        step["hash_chain"] = current_hash
        prev_hash = current_hash

    replay_hash = prev_hash
    invalid_mass = 1.0 if (unresolved or event_storm) else 0.0

    raw_seed = config.seed
    try:
        seed_int = int(raw_seed)
    except (TypeError, ValueError):
        seed_int = abs(hash(str(raw_seed))) % (2**31)

    trace = {
        "schema_version": "2.1.0" if active_formula in {FORMULA_VERSION, FORMULA_VERSION_2_2} else "2.0.0",
        "run_id": f"run:{run_id}",
        "model_id": model_id,
        "formula_version": active_formula,
        "generated_by": "aleph.engine.numerical",
        "generation_mode": "engine_derived",
        "execution_timestamp": execution_timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": seed_int,
        "parameters_hash": parameters_hash,
        "total_ticks": max(1, ticks_int),
        "total_samples": 1,
        "invalid_mass": invalid_mass,
        "steps": steps,
        "final_state_vector": dict(sorted(state.items())),
        "replay_hash": replay_hash,
    }
    return trace


def stream_numerical_execution_trace(
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: int = 10,
    run_id: int = 0,
    model_id: str = "model:compiled",
    execution_timestamp: str | None = None,
    formula_version: str | None = None,
) -> dict[str, Any]:
    """Execute simulation with performance telemetry for large workloads."""
    import time

    start_time = time.perf_counter()
    trace = generate_numerical_execution_trace(
        model,
        config,
        ticks=ticks,
        run_id=run_id,
        model_id=model_id,
        execution_timestamp=execution_timestamp,
        formula_version=formula_version,
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    trace["telemetry"] = {
        "elapsed_ms": elapsed_ms,
        "step_count": len(trace["steps"]),
        "steps_per_second": (len(trace["steps"]) / (elapsed_ms / 1000.0)) if elapsed_ms > 0 else 0.0,
    }
    return trace


def inspect_execution_health(
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: int,
    run_id: int = 0,
    result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Inspect model/config/result for structural execution warnings without altering core semantics."""
    warnings: list[dict[str, Any]] = []
    if ticks <= 0:
        return warnings
    lags: list[tuple[str, int]] = []
    for edge in model.edges:
        try:
            _strength, lag_ticks, exists = sampled_edge_parameters(edge, config, run_id)
        except (OverflowError, TypeError, ValueError, ZeroDivisionError):
            continue
        if not exists:
            continue
        lags.append((edge.id, int(lag_ticks)))
        if lag_ticks >= ticks:
            warnings.append(
                issue(
                    "LAG_EXCEEDS_HORIZON",
                    pointer=f"/edges/{edge.id}/lag_ticks",
                    severity="warning",
                    actual=int(lag_ticks),
                    expected={"ticks": int(ticks)},
                    message=f"edge lag_ticks ({lag_ticks}) >= simulation ticks ({ticks}); causal signal never lands within horizon",
                ).to_dict()
            )
    if lags and all(lag >= ticks for _, lag in lags):
        warnings.append(
            issue(
                "ENGINE_FROZEN",
                pointer="/edges",
                severity="warning",
                actual={"ticks": int(ticks), "min_lag_ticks": min(lag for _, lag in lags)},
                message="every active causal edge has lag_ticks >= ticks so no edge effect can arrive",
            ).to_dict()
        )
    baseline_result = result
    if (
        model.interventions
        and model.formula_version == FORMULA_VERSION_2_2
        and len(model.variables) <= 64
        and ticks <= 120
    ):
        baseline_model = ComputationalModel(
            variables=dict(model.variables),
            edges=list(model.edges),
            interventions=[],
            formula_version=model.formula_version,
        )
        try:
            baseline_result = run_deterministic(
                baseline_model,
                config,
                ticks=int(ticks),
                run_id=int(run_id),
            )
        except (OverflowError, TypeError, ValueError, ZeroDivisionError):
            baseline_result = None
    has_explicit_initial_offset = any(
        var.initial_value is not None
        and not math.isclose(float(var.initial_value), float(var.baseline), rel_tol=1e-9, abs_tol=1e-12)
        for var in model.variables.values()
    )
    if (
        (not model.interventions or model.formula_version == FORMULA_VERSION_2_2)
        and isinstance(baseline_result, dict)
        and not has_explicit_initial_offset
    ):
        payload = baseline_result.get("payload")
        raw_state = payload.get("final_state") if isinstance(payload, dict) else None
        final_state = raw_state if isinstance(raw_state, dict) else {}
        drifting = [
            node_id
            for node_id, var in sorted(model.variables.items())
            if node_id in final_state
            and _is_finite_number(final_state[node_id])
            and abs(float(final_state[node_id]) - float(var.baseline)) > 1e-6 * max(1.0, abs(float(var.baseline)))
        ]
        if drifting and model.formula_version == FORMULA_VERSION_2_2:
            warnings.append(
                issue(
                    "ZERO_INTERVENTION_DRIFT",
                    pointer="/variables",
                    severity="warning",
                    actual=drifting,
                    message="non-intervention run drifted from declared baselines under Formula 2.2.0",
                ).to_dict()
            )
    return warnings

