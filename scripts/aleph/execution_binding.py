"""Bind semantic propagation rows to addressed engine trajectories."""

from __future__ import annotations

import math
import re
from typing import Any

from .engine import (
    ComputationalModel,
    EngineConfig,
    run_deterministic,
    sampled_edge_parameters,
)
from .formula import nearly_equal
from .io import canonical_hash
from .issues import Issue, issue
from .schema import parse_time

RUN_REF_RE = re.compile(r"^run:(\d{1,20})$")


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


def build_trace_execution_binding(
    rows: list[dict[str, Any]],
    model: ComputationalModel,
    config: EngineConfig,
    *,
    ticks: int,
    result: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Verify trace inputs against engine histories and return a hashed binding."""
    problems: list[Issue] = []
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
        strength, lag_ticks, exists = sampled_edge_parameters(edge, config, run_id)
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
            bound_rows.append(
                {
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
            )
    if problems:
        return None, problems
    body = {
        "version": "aleph-trace-execution-binding-v1",
        "mode": config.mode,
        "ticks": ticks,
        "rows": bound_rows,
    }
    return {**body, "binding_hash": canonical_hash(body)}, []
