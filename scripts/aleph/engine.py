"""Deterministic and Monte Carlo causal simulation engine.

The engine is deliberately stdlib-first.  All stochastic draws are addressed by
``(seed, run_id, edge_id, purpose)`` rather than mutable RNG state, so changing
worker partitioning cannot change a run.  A compiled model and an execution
configuration are hashed into every run contract.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .io import canonical_hash
from .issues import Issue, issue
from .rng import normal01, sample_triangular, sample_uniform, uniform01
from .schema import parse_duration_seconds


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
    existence_prob: float = 1.0
    effect_distribution: dict[str, Any] | None = None
    lag_distribution: dict[str, Any] | None = None
    context_multiplier: float = 1.0
    saturation: float | None = None


@dataclass
class ComputationalModel:
    variables: dict[str, Variable] = field(default_factory=dict)
    edges: list[ModelEdge] = field(default_factory=list)
    interventions: list[dict[str, Any]] = field(default_factory=list)


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


def _normalise_effect_distribution(value: Any) -> dict[str, Any] | None:
    """Validate an effect distribution without coercion or silent fallback."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("effect_distribution must be an object")
    distribution_name = value.get("distribution")
    type_name = value.get("type")
    if distribution_name is not None and type_name is not None and distribution_name != type_name:
        raise ValueError("effect distribution/type declarations disagree")
    kind = str(distribution_name if distribution_name is not None else type_name or "").lower()
    required_by_kind = {
        "fixed": {"value"},
        "uniform": {"min", "max"},
        "triangular": {"min", "mode", "max"},
        "normal": {"mean", "sd"},
    }
    if kind not in required_by_kind:
        raise ValueError(f"unsupported effect distribution: {kind or '<missing>'}")
    allowed = {"distribution", "type"} | required_by_kind[kind]
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown effect distribution fields: {', '.join(sorted(unknown))}")
    missing = required_by_kind[kind] - set(value)
    if missing:
        raise ValueError(f"effect distribution {kind} missing: {', '.join(sorted(missing))}")
    result: dict[str, Any] = {"distribution": kind}
    for key in required_by_kind[kind]:
        raw = value[key]
        if not _is_finite_number(raw):
            raise ValueError(f"effect distribution {key} must be finite")
        result[key] = float(raw)
    if kind == "uniform" and not result["min"] < result["max"]:
        raise ValueError("uniform effect distribution requires min < max")
    if kind == "triangular" and not result["min"] <= result["mode"] <= result["max"]:
        raise ValueError("triangular effect distribution requires min <= mode <= max")
    if kind == "triangular" and result["min"] == result["max"]:
        raise ValueError("degenerate triangular distribution must be declared fixed")
    if kind == "normal" and result["sd"] <= 0:
        raise ValueError("normal effect distribution requires sd > 0")
    return result


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
) -> ComputationalModel:
    """Compile public artifacts without treating simulated ``state_after`` as evidence.

    Baselines come from ``baseline`` or the observed ``state_before.value`` only.
    Interventions must be supplied explicitly (normally from the manifest or an
    interventions artifact).
    """
    model = ComputationalModel()
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
        model.variables[node_id] = Variable(
            id=node_id,
            role=str(node.get("role", "endogenous")),
            datatype=str(node.get("datatype", "continuous")),
            unit=str(node.get("unit", "")),
            scale=str(node.get("scale", "level")),
            baseline=baseline,
            bounds=bounds,
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
            if candidate.get("distribution"):
                distribution = candidate
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
        model.edges.append(
            ModelEdge(
                id=edge_id,
                source=source,
                target=target,
                sign=sign,
                strength=strength,
                lag_ticks=lag,
                lag_unit=lag_unit,
                transform=str(raw.get("transform", "linear")),
                existence_prob=min(1.0, max(0.0, _finite_number(raw.get("existence_prob", 1.0), 1.0))),
                effect_distribution=_normalise_effect_distribution(distribution),
                lag_distribution=lag_distribution,
                context_multiplier=context_multiplier,
                saturation=saturation,
            )
        )

    for raw in interventions or []:
        if not isinstance(raw, dict) or str(raw.get("target", "")) not in model.variables:
            continue
        op = str(raw.get("op", "set"))
        if op not in {"set", "add", "multiply"}:
            continue
        model.interventions.append(
            {
                "id": str(raw.get("id", f"intervention:{len(model.interventions)}")),
                "target": str(raw["target"]),
                "op": op,
                "value": _finite_number(raw.get("value", 0.0)),
                "start_tick": max(0, int(_finite_number(raw.get("start_tick", 0)))),
                "end_tick": (
                    max(0, int(_finite_number(raw["end_tick"]))) if raw.get("end_tick") is not None else None
                ),
            }
        )
    return model


def model_payload(model: ComputationalModel) -> dict[str, Any]:
    return {
        "variables": {
            key: {
                "id": var.id,
                "role": var.role,
                "datatype": var.datatype,
                "unit": var.unit,
                "scale": var.scale,
                "baseline": var.baseline,
                "bounds": list(var.bounds),
            }
            for key, var in sorted(model.variables.items())
        },
        "edges": [
            {
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
            for edge in sorted(model.edges, key=lambda value: value.id)
        ],
        "interventions": sorted(model.interventions, key=lambda value: str(value.get("id", ""))),
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


def _sample_strength(edge: ModelEdge, config: EngineConfig, run_id: int) -> float:
    distribution = edge.effect_distribution
    if config.mode != "monte_carlo" or not distribution:
        return edge.strength
    kind = str(distribution.get("distribution", distribution.get("type", "fixed"))).lower()
    if kind == "uniform":
        return sample_uniform(
            config.seed,
            _finite_number(distribution.get("min", edge.strength)),
            _finite_number(distribution.get("max", edge.strength)),
            run_id,
            edge.id,
            "effect",
        )
    if kind == "triangular":
        low = _finite_number(distribution.get("min", edge.strength))
        high = _finite_number(distribution.get("max", edge.strength))
        mode = _finite_number(distribution.get("mode", edge.strength))
        return sample_triangular(config.seed, low, mode, high, run_id, edge.id, "effect")
    if kind == "normal":
        mean = _finite_number(distribution.get("mean", edge.strength))
        sd = _finite_number(distribution.get("sd", 0.0))
        return mean + sd * normal01(config.seed, run_id, edge.id, "effect")
    if kind == "fixed":
        return float(distribution["value"])
    raise ValueError(f"unsupported effect distribution: {kind}")


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


def _edge_effect(edge: ModelEdge, strength: float, source_value: float) -> float:
    if edge.transform in {"linear", "elasticity"}:
        raw = edge.sign * strength * edge.context_multiplier * source_value
    elif edge.transform == "identity":
        raw = edge.sign * edge.context_multiplier * source_value
    elif edge.transform == "logistic":
        raw = edge.sign * strength * edge.context_multiplier * (1.0 / (1.0 + math.exp(-source_value)))
    else:
        raise ValueError(f"unsupported transform {edge.transform}")
    if edge.saturation is not None:
        raw = edge.saturation * math.tanh(raw / edge.saturation)
    return raw


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
    scheduled: dict[int, list[tuple[str, float, str]]] = {}
    events = 0
    unresolved = False
    event_storm = False
    edges: list[tuple[ModelEdge, float]] = []
    for edge in sorted(model.edges, key=lambda value: value.id):
        try:
            strength, lag_ticks, exists = sampled_edge_parameters(edge, config, run_id)
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
            sampled_edge = replace(edge, lag_ticks=lag_ticks)
            edges.append((sampled_edge, strength))
    if issues:
        return _invalid_run_result(model, config, ticks=ticks, run_id=run_id, issues=issues)

    for tick in range(max(0, ticks)):
        # Engine 2.0 uses discrete level equations.  Each tick starts from the
        # declared baseline, then applies interventions and effects emitted for
        # that tick.  Stock/flow integration is intentionally outside this
        # contract; it must not appear accidentally through cross-tick carry.
        base = {key: variable.baseline for key, variable in model.variables.items()}
        active = _active_interventions(model, tick)
        blocked = {str(value["target"]) for value in active if value.get("op") == "set"}
        for target, delta, _edge_id in scheduled.pop(tick, []):
            if target not in blocked and target in base:
                base[target] += delta
        for intervention in sorted(active, key=lambda value: str(value.get("id", ""))):
            target = str(intervention["target"])
            value = float(intervention["value"])
            if intervention["op"] == "set":
                base[target] = value
            elif intervention["op"] == "add":
                base[target] += value
            elif intervention["op"] == "multiply":
                base[target] *= value

        zero = [edge for edge, _ in edges if edge.lag_ticks == 0 and edge.target not in blocked]
        strengths = {edge.id: strength for edge, strength in edges}
        current = dict(base)
        for component in _component_order(sorted(model.variables), zero):
            internal = [edge for edge in zero if edge.source in component and edge.target in component]
            incoming = [edge for edge in zero if edge.target in component and edge.source not in component]
            component_base = {node: base[node] for node in component}
            for edge in incoming:
                try:
                    component_base[edge.target] += _edge_effect(edge, strengths[edge.id], current[edge.source])
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
                            candidate[edge.target] += _edge_effect(edge, strengths[edge.id], x[edge.source])
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

        # Delayed effects capture the source value at emission time.  They are
        # never recomputed from the future state at delivery time.
        for edge, strength in edges:
            if edge.lag_ticks <= 0:
                continue
            try:
                delta = _edge_effect(edge, strength, state[edge.source])
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
    digest = canonical_hash(payload)
    return {
        "ok": not unresolved and not event_storm,
        "payload": {**payload, "workers": config.workers},
        "history": history,
        "run_hash": digest,
        "issues": [value.to_dict() for value in issues],
        "exit_code": 4 if unresolved or event_storm else 0,
    }


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("empty values")
    position = probability * (len(values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return values[low]
    return values[low] * (high - position) + values[high] * (position - low)


def _signature(result: dict[str, float]) -> str:
    return "|".join(f"{key}:{0 if abs(value) < 1e-12 else (1 if value > 0 else -1)}" for key, value in sorted(result.items()))


def _branch_weights(results: list[dict[str, float]], total_runs: int) -> dict[str, float]:
    counts: dict[str, int] = {}
    for result in results:
        key = _signature(result)
        counts[key] = counts.get(key, 0) + 1
    return {key: count / total_runs for key, count in counts.items()} if total_runs else {}


def _cluster_relative(
    results: list[tuple[int, dict[str, float]]],
    total_runs: int,
    *,
    cap: int = 7,
) -> list[dict[str, Any]]:
    if not results or total_runs <= 0:
        return []
    buckets: dict[str, list[tuple[int, dict[str, float]]]] = {}
    for run_id, result in results:
        buckets.setdefault(_signature(result), []).append((run_id, result))
    ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    if len(ordered) > cap:
        kept = ordered[: cap - 1]
        remainder = [member for _, members in ordered[cap - 1 :] for member in members]
        ordered = kept + [("other", remainder)]
    branches: list[dict[str, Any]] = []
    for index, (signature, members) in enumerate(ordered):
        representative_run_id, representative = min(
            members,
            key=lambda value: (canonical_hash(value[1]), value[0]),
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
    valid_results: list[tuple[int, dict[str, float]]] = []
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
                valid_results.append((run_id, result["payload"]["final_state"]))
            else:
                invalid += 1
        n_runs = batch_end
        if n_runs < minimum:
            continue
        weights = _branch_weights([state for _, state in valid_results], n_runs)
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
            values = sorted(result[key] for _, result in valid_results if key in result)
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
