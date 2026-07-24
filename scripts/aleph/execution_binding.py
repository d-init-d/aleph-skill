"""Bind semantic propagation rows to addressed engine trajectories."""

from __future__ import annotations

import math
import re
from typing import Any

from . import LEGACY_FORMULA_VERSION
from .engine import (
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    run_deterministic,
    sampled_edge_parameters,
    sampled_retention_factor,
    sampled_transform_parameters,
)
from .formula import evaluate_output_effect, nearly_equal
from .io import canonical_hash
from .issues import Issue, issue
from .schema import parse_time

RUN_REF_RE = re.compile(r"^run:(\d{1,20})$")
BINDING_V1 = "aleph-trace-execution-binding-v1"
BINDING_V2 = "aleph-trace-execution-binding-v2"


def _trajectory_value_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, int):
        return isinstance(actual, int) and not isinstance(actual, bool) and actual == expected
    if (
        isinstance(actual, bool)
        or not isinstance(actual, (int, float))
        or isinstance(expected, bool)
        or not isinstance(expected, (int, float))
    ):
        return False
    try:
        return nearly_equal(float(actual), float(expected))
    except (OverflowError, TypeError, ValueError):
        return False


def _target_is_blocked(model: ComputationalModel, target: str, tick: int) -> bool:
    """Return whether a set intervention suppresses incoming effects at a tick."""
    for intervention in model.interventions:
        if intervention.get("target") != target or intervention.get("op") != "set":
            continue
        start = intervention.get("start_tick", 0)
        end = intervention.get("end_tick")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and start <= tick
            and (end is None or (isinstance(end, int) and not isinstance(end, bool) and tick < end))
        ):
            return True
    return False


def _hysteresis_timeline(
    model: ComputationalModel,
    edge: ModelEdge,
    history: list[dict[str, Any]],
    *,
    sampled_strength: float,
    sampled_lag_ticks: int,
    resolved_parameters: dict[str, Any],
) -> list[tuple[bool, bool]]:
    """Reconstruct the once-per-tick hysteresis latch independently from the trace."""
    active = False
    timeline: list[tuple[bool, bool]] = []
    for tick, state in enumerate(history):
        before = active
        if sampled_lag_ticks == 0 and _target_is_blocked(model, edge.target, tick):
            after = before
        else:
            source_state = state.get(edge.source)
            if isinstance(source_state, bool) or not isinstance(source_state, (int, float)):
                raise ValueError(f"missing numeric source state for {edge.source} at tick {tick}")
            _, next_active = evaluate_output_effect(
                base_strength=sampled_strength,
                sign=edge.sign,
                context_mult=edge.context_multiplier,
                input_effect=float(source_state),
                transform=edge.transform,
                transform_parameters=resolved_parameters,
                saturation=edge.saturation,
                formula_version=model.formula_version,
                threshold_active=before,
            )
            if next_active is None:
                raise ValueError(f"edge {edge.id} did not produce a hysteresis latch transition")
            after = next_active
        timeline.append((before, after))
        active = after
    return timeline


def build_trace_execution_binding(
    rows: list[dict[str, Any]],
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: int,
    result: dict[str, Any],
    manifest: dict[str, Any],
    binding_version: str | None = None,
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Verify trace inputs against engine histories and return a hashed binding."""
    problems: list[Issue] = []
    expected_version = BINDING_V1 if model.formula_version == LEGACY_FORMULA_VERSION else BINDING_V2
    version = binding_version or expected_version
    if version not in {BINDING_V1, BINDING_V2}:
        return None, [issue("TRACE_EXECUTION_BINDING", message="unsupported execution binding version", actual=version)]
    if version != expected_version:
        return None, [
            issue(
                "TRACE_EXECUTION_BINDING",
                pointer="/trace_execution_binding/version",
                message="execution binding version does not match formula version",
                expected=expected_version,
                actual=version,
            )
        ]
    frame = manifest.get("temporal_frame")
    start = parse_time(frame.get("simulation_start")) if isinstance(frame, dict) else None
    if start is None:
        return None, [issue("TRACE_EXECUTION_BINDING", message="simulation_start is required")]
    try:
        seconds_per_tick = float(config.timestep) * 86400.0
    except (OverflowError, TypeError, ValueError):
        seconds_per_tick = math.inf
    if not math.isfinite(seconds_per_tick) or seconds_per_tick <= 0:
        return None, [issue("TRACE_EXECUTION_BINDING", message="positive finite timestep required")]
    edge_by_id = {edge.id: edge for edge in model.edges}
    runs: dict[int, dict[str, Any]] = {}
    if config.mode == "deterministic":
        runs[0] = result
        n_runs = 1
    else:
        summary = result.get("summary")
        n_runs = int(summary.get("n_runs", 0)) if isinstance(summary, dict) else 0

    bound_rows: list[dict[str, Any]] = []
    hysteresis_timelines: dict[tuple[int, str], list[tuple[bool, bool]]] = {}
    for index, row in enumerate(rows):
        pointer = f"/propagation_trace/{index}"
        refs = row.get("sample_refs")
        run_refs = [RUN_REF_RE.fullmatch(str(value)) for value in refs] if isinstance(refs, list) else []
        run_ids = [int(match.group(1)) for match in run_refs if match is not None]
        declared_run_id = row.get("run_id")
        if (
            not isinstance(refs, list)
            or len(refs) != 1
            or len(run_ids) != 1
            or not isinstance(declared_run_id, int)
            or isinstance(declared_run_id, bool)
            or declared_run_id != run_ids[0]
        ):
            problems.append(
                issue(
                    "TRACE_EXECUTION_BINDING",
                    pointer=f"{pointer}/sample_refs",
                    message="exactly one run:N reference matching run_id is required",
                )
            )
            continue
        run_id = run_ids[0]
        if run_id < 0 or run_id >= n_runs:
            problems.append(issue("TRACE_EXECUTION_BINDING", pointer=f"{pointer}/run_id", actual=run_id))
            continue
        edge = edge_by_id.get(str(row.get("edge_id")))
        if edge is None:
            problems.append(issue("UNKNOWN_REF", pointer=f"{pointer}/edge_id", actual=row.get("edge_id")))
            continue
        row_formula_version = row.get("formula_version")
        if row_formula_version is not None and row_formula_version != model.formula_version:
            problems.append(
                issue(
                    "TRACE_EXECUTION_BINDING",
                    pointer=f"{pointer}/formula_version",
                    message="trace row formula version differs from the engine run",
                    expected=model.formula_version,
                    actual=row_formula_version,
                )
            )
            continue
        try:
            strength, lag_ticks, exists = sampled_edge_parameters(edge, config, run_id)
            resolved_parameters = sampled_transform_parameters(edge, config, run_id)
            target_retention_factor = sampled_retention_factor(model.variables[edge.target], config, run_id)
        except (OverflowError, TypeError, ValueError, ZeroDivisionError) as exc:
            problems.append(issue("TRACE_EXECUTION_BINDING", pointer=pointer, message=f"parameter sampling failed: {exc}"))
            continue
        if not exists:
            problems.append(issue("TRACE_EXECUTION_BINDING", pointer=pointer, message="trace references an absent sampled edge"))
            continue
        if run_id not in runs:
            runs[run_id] = run_deterministic(model, config, ticks=ticks, run_id=run_id)
        run = runs[run_id]
        history = run.get("history")
        if not run.get("ok") or not isinstance(history, list):
            problems.append(issue("TRACE_EXECUTION_BINDING", pointer=pointer, message="trace references an invalid run"))
            continue
        timestamp = parse_time(row.get("time"))
        if timestamp is None:
            problems.append(issue("TRACE_EXECUTION_BINDING", pointer=f"{pointer}/time"))
            continue
        tick_float = (timestamp - start).total_seconds() / seconds_per_tick
        if not math.isfinite(tick_float):
            problems.append(
                issue(
                    "TRACE_EXECUTION_BINDING",
                    pointer=f"{pointer}/time",
                    message="time cannot be represented on the engine timestep grid",
                )
            )
            continue
        effect_tick = int(round(tick_float))
        source_tick = effect_tick - lag_ticks
        if not nearly_equal(tick_float, float(effect_tick), abs_tol=1e-9, rel_tol=0.0):
            problems.append(issue("TRACE_EXECUTION_BINDING", pointer=f"{pointer}/time", message="time is off the engine timestep grid"))
            continue
        if _target_is_blocked(model, edge.target, effect_tick):
            problems.append(
                issue(
                    "TRACE_EXECUTION_BINDING",
                    pointer=pointer,
                    message="trace claims an edge effect suppressed by a set intervention",
                )
            )
            continue
        if not (0 <= source_tick < len(history) and 0 <= effect_tick < len(history)):
            problems.append(
                issue(
                    "TRACE_EXECUTION_BINDING",
                    pointer=pointer,
                    message="trace time/lag is outside the retained engine trajectory",
                    actual={"source_tick": source_tick, "effect_tick": effect_tick, "ticks": len(history)},
                )
            )
            continue
        source_state = history[source_tick].get(edge.source)
        target_state = history[effect_tick].get(edge.target)
        checks = (
            ("tick", row.get("tick"), effect_tick),
            ("source_tick", row.get("source_tick"), source_tick),
            ("source_state", row.get("source_state"), source_state),
            ("target_state", row.get("target_state"), target_state),
            ("sampled_strength", row.get("sampled_strength"), strength),
            ("input_effect", row.get("input_effect"), source_state),
            ("noise", row.get("noise"), 0.0),
        )
        row_ok = True
        for field, actual, expected in checks:
            equal = _trajectory_value_equal(actual, expected)
            if not equal:
                row_ok = False
                problems.append(
                    issue(
                        "TRACE_EXECUTION_BINDING",
                        pointer=f"{pointer}/{field}",
                        expected=expected,
                        actual=actual,
                        message="trace value differs from addressed engine trajectory",
                    )
                )
        if row_ok:
            bound_row: dict[str, Any] = {
                "step": row.get("step"),
                "edge_id": edge.id,
                "run_id": run_id,
                "tick": effect_tick,
                "source_tick": source_tick,
                "source_state": source_state,
                "target_state": target_state,
                "sampled_strength": strength,
                "run_hash": run.get("run_hash"),
                "history_hash": (run.get("payload") or {}).get("history_hash"),
            }
            if version == BINDING_V2:
                declared_parameters = row.get("resolved_transform_parameters")
                if declared_parameters is not None and declared_parameters != resolved_parameters:
                    problems.append(
                        issue(
                            "TRACE_EXECUTION_BINDING",
                            pointer=f"{pointer}/resolved_transform_parameters",
                            expected=resolved_parameters,
                            actual=declared_parameters,
                        )
                    )
                    continue
                if model.variables[edge.target].scale == "stock":
                    declared_retention = row.get("target_retention_factor")
                    if declared_retention is not None and not _trajectory_value_equal(
                        declared_retention, target_retention_factor
                    ):
                        problems.append(
                            issue(
                                "TRACE_EXECUTION_BINDING",
                                pointer=f"{pointer}/target_retention_factor",
                                expected=target_retention_factor,
                                actual=declared_retention,
                            )
                        )
                        continue
                integration_factor = (
                    float(config.timestep)
                    if model.variables[edge.target].scale == "stock" and edge.integration == "rate"
                    else 1.0
                )
                declared_integrated = row.get("integrated_effect")
                expected_integrated = float(row.get("output_effect", 0.0)) * integration_factor
                if declared_integrated is not None and not _trajectory_value_equal(
                    declared_integrated, expected_integrated
                ):
                    problems.append(
                        issue(
                            "TRACE_EXECUTION_BINDING",
                            pointer=f"{pointer}/integrated_effect",
                            expected=expected_integrated,
                            actual=declared_integrated,
                        )
                    )
                    continue
                bound_row.update(
                    {
                        "formula_version": model.formula_version,
                        "sampled_lag_ticks": lag_ticks,
                        "resolved_transform_parameters": resolved_parameters,
                        "target_scale": model.variables[edge.target].scale,
                        "target_retention_factor": target_retention_factor,
                        "integration": edge.integration,
                        "integration_factor": integration_factor,
                        "integrated_effect": expected_integrated,
                        "dynamics_hash": (run.get("payload") or {}).get("dynamics_hash"),
                    }
                )
                is_hysteresis = (
                    edge.transform == "threshold"
                    and resolved_parameters.get("mode", "above") == "hysteresis"
                )
                if is_hysteresis:
                    cache_key = (run_id, edge.id)
                    try:
                        timeline = hysteresis_timelines.get(cache_key)
                        if timeline is None:
                            timeline = _hysteresis_timeline(
                                model,
                                edge,
                                history,
                                sampled_strength=strength,
                                sampled_lag_ticks=lag_ticks,
                                resolved_parameters=resolved_parameters,
                            )
                            hysteresis_timelines[cache_key] = timeline
                        expected_before, expected_after = timeline[source_tick]
                    except (IndexError, OverflowError, TypeError, ValueError) as exc:
                        problems.append(
                            issue(
                                "TRACE_EXECUTION_BINDING",
                                pointer=pointer,
                                message=f"hysteresis reconstruction failed: {exc}",
                            )
                        )
                        continue
                    latch_mismatch = False
                    for field, expected in (
                        ("threshold_active_before", expected_before),
                        ("threshold_active_after", expected_after),
                    ):
                        actual = row.get(field)
                        if not isinstance(actual, bool) or actual != expected:
                            latch_mismatch = True
                            problems.append(
                                issue(
                                    "TRACE_EXECUTION_BINDING",
                                    pointer=f"{pointer}/{field}",
                                    message="trace hysteresis latch differs from the addressed engine trajectory",
                                    expected=expected,
                                    actual=actual,
                                )
                            )
                    if latch_mismatch:
                        continue
                    bound_row.update(
                        {
                            "threshold_active_before": expected_before,
                            "threshold_active_after": expected_after,
                        }
                    )
            bound_rows.append(bound_row)
    if problems:
        return None, problems
    body = {
        "version": version,
        "mode": config.mode,
        "ticks": ticks,
        "rows": bound_rows,
    }
    if version == BINDING_V2:
        body["formula_version"] = model.formula_version
        body["dynamics_hashes"] = {
            f"run:{run_id}": (run.get("payload") or {}).get("dynamics_hash")
            for run_id, run in sorted(runs.items())
        }
        body["resolved_parameters_hash"] = canonical_hash(
            [
                {
                    "edge_id": row["edge_id"],
                    "run_id": row["run_id"],
                    "sampled_strength": row["sampled_strength"],
                    "sampled_lag_ticks": row["sampled_lag_ticks"],
                    "transform_parameters": row["resolved_transform_parameters"],
                    "target_retention_factor": row["target_retention_factor"],
                }
                for row in bound_rows
            ]
        )
    return {**body, "binding_hash": canonical_hash(body)}, []
