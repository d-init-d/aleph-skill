from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from aleph import (
    EXIT_NUMERICAL,
    EXIT_OK,
    EXIT_SEMANTIC,
    EXIT_USAGE,
    FORMULA_VERSION_2_2,
    LEGACY_FORMULA_VERSION,
)
from aleph.engine import (
    EngineConfig,
    compile_model,
    config_payload,
    generate_numerical_execution_trace,
    inspect_execution_health,
    run_deterministic,
    run_monte_carlo,
)
from aleph.execution_binding import build_trace_execution_binding
from aleph.io import (
    canonical_hash,
    load_json_secure,
    sha256_file,
    write_json_atomic,
)
from aleph.issues import issue
from aleph.paths import output_alias_issues, resolve_in_workspace
from aleph.schema import parse_time, parse_timestep_seconds
from aleph.trace_contract import validate_declared_trace
from compile_model import (
    compile_workspace,
    load_interventions,
    resolve_workspace_formula_version,
)


def _load_json_or_exit(path: Path) -> Any:
    data, issues = load_json_secure(path)
    if issues:
        print(
            json.dumps(
                {"ok": False, "code": "INVALID_ARTIFACT", "issues": [value.to_dict() for value in issues]},
                indent=2,
            )
        )
        raise SystemExit(EXIT_SEMANTIC)
    return data


def _reserve_sibling(path: Path, label: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.{label}-", dir=path.parent)
    os.close(descriptor)
    candidate = Path(name)
    candidate.unlink()
    return candidate


def _commit_json_pair(artifacts: list[tuple[Path, dict[str, Any]]]) -> None:
    """Stage and commit related JSON artifacts with exception-safe rollback."""
    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    preserve_backups = False
    try:
        for target, payload in artifacts:
            temporary = _reserve_sibling(target, "stage")
            write_json_atomic(temporary, payload)
            staged.append((temporary, target))
        for target, _payload in artifacts:
            if target.exists():
                backup = _reserve_sibling(target, "backup")
                os.replace(target, backup)
                backups[target] = backup
        for temporary, target in staged:
            os.replace(temporary, target)
            committed.append(target)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                target.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {target}: {rollback_exc}")
        for target, backup in backups.items():
            try:
                if backup.exists():
                    os.replace(backup, target)
            except OSError as rollback_exc:
                rollback_errors.append(f"restore {target}: {rollback_exc}")
        if rollback_errors:
            preserve_backups = True
            detail = "; ".join(rollback_errors)
            raise RuntimeError(f"artifact commit failed and rollback was incomplete: {detail}") from exc
        raise
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        if not preserve_backups:
            for backup in backups.values():
                backup.unlink(missing_ok=True)


def _parse_timestep_days(raw: Any, *, source: str) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        if not math.isfinite(val) or val <= 0.0:
            raise ValueError(f"{source} timestep must be finite and positive")
        return val
    if isinstance(raw, str):
        seconds = parse_timestep_seconds(raw)
        if seconds is not None:
            return seconds / 86400.0
        try:
            val = float(raw.strip())
        except ValueError as exc:
            raise ValueError(f"{source} timestep {raw!r} is not a valid duration or number of days") from exc
        if not math.isfinite(val) or val <= 0.0:
            raise ValueError(f"{source} timestep must be finite and positive")
        return val
    raise ValueError(f"{source} timestep must be an ISO duration string or positive number")


def _config(workspace: Path, args: argparse.Namespace, manifest: dict[str, Any] | None = None) -> tuple[EngineConfig, dict[str, Any]]:
    values: dict[str, Any] = {}
    raw_cfg: dict[str, Any] = {}
    allowed = {item.name for item in fields(EngineConfig)}
    if isinstance(manifest, dict):
        raw_ec = manifest.get("engine_config")
        if isinstance(raw_ec, dict):
            for key, value in raw_ec.items():
                if key in allowed and value is not None:
                    values[key] = value
            if raw_ec.get("timestep") is not None:
                values["timestep"] = _parse_timestep_days(raw_ec["timestep"], source="manifest.engine_config")
    path = workspace / "simulation-config.json"
    if path.is_file():
        raw = _load_json_or_exit(path)
        if isinstance(raw, dict):
            raw_cfg = raw
            values.update({key: value for key, value in raw.items() if key in allowed})
            if "timestep" in raw and raw["timestep"] is not None:
                values["timestep"] = _parse_timestep_days(raw["timestep"], source="simulation-config.json")
    if "timestep" not in values and isinstance(manifest, dict):
        frame = manifest.get("temporal_frame")
        if isinstance(frame, dict) and frame.get("timestep") is not None:
            values["timestep"] = _parse_timestep_days(frame["timestep"], source="manifest.temporal_frame")
    if getattr(args, "timestep", None) is not None:
        values["timestep"] = _parse_timestep_days(args.timestep, source="--timestep")
    overrides = {
        "mode": args.mode,
        "seed": args.seed,
        "workers": args.workers,
        "min_runs": args.runs,
        "max_runs": args.max_runs,
        "batch_size": args.batch_size,
    }
    values.update({key: value for key, value in overrides.items() if value is not None})
    return EngineConfig(**values), raw_cfg


def _run_contract_version(formula_version: str) -> str:
    if formula_version == LEGACY_FORMULA_VERSION:
        return "aleph-run-2.0"
    if formula_version == FORMULA_VERSION_2_2:
        return "aleph-run-2.2"
    return "aleph-run-2.1"


def _formula_version(workspace: Path, manifest: dict[str, Any]) -> str:
    return resolve_workspace_formula_version(workspace, manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a hashed deterministic or Monte Carlo simulation contract.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=["deterministic", "monte_carlo"])
    parser.add_argument("--seed")
    parser.add_argument("--ticks", type=int)
    parser.add_argument("--timestep", help="timestep duration such as P1M, P7D, 1d, or numeric days")
    parser.add_argument("--runs", type=int, help="minimum Monte Carlo runs")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", help="workspace-relative run contract; must match the manifest declaration")
    parser.add_argument("--no-sync-branches", action="store_true", help="skip updating branch-ledger trace_hash after cold-start trace generation")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print("ERROR: workspace not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    manifest_path = workspace / "simulation-manifest.json"
    manifest = _load_json_or_exit(manifest_path) if manifest_path.is_file() else {}
    artifact_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    node_relative = artifact_paths.get("nodes", "nodes.json") if isinstance(artifact_paths, dict) else "nodes.json"
    edge_relative = artifact_paths.get("edges", "edges.json") if isinstance(artifact_paths, dict) else "edges.json"
    node_path, node_issues = resolve_in_workspace(workspace, str(node_relative), must_exist=True)
    edge_path, edge_issues = resolve_in_workspace(workspace, str(edge_relative), must_exist=True)
    if node_issues or edge_issues or node_path is None or edge_path is None:
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in [*node_issues, *edge_issues]]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    nodes = _load_json_or_exit(node_path)
    edges = _load_json_or_exit(edge_path)
    declared_run = (
        artifact_paths.get("run_ledger", "simulation-run.json")
        if isinstance(artifact_paths, dict)
        else "simulation-run.json"
    )
    if args.out is not None and str(args.out).replace("\\", "/") != str(declared_run).replace("\\", "/"):
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "PATH_ALIAS",
                    "error": "--out must match manifest.artifact_paths.run_ledger",
                    "expected": declared_run,
                    "actual": args.out,
                },
                indent=2,
            )
        )
        raise SystemExit(EXIT_USAGE)
    declared_model = (
        artifact_paths.get("computational_model", "simulation-model.json")
        if isinstance(artifact_paths, dict)
        else "simulation-model.json"
    )
    declared_trace = (
        artifact_paths.get("execution_trace")
        or artifact_paths.get("propagation_trace")
        or "execution-trace.json"
        if isinstance(artifact_paths, dict)
        else "execution-trace.json"
    )
    out, out_issues = resolve_in_workspace(
        workspace, str(declared_run), must_exist=False, require_file=False
    )
    model_out, model_issues = resolve_in_workspace(
        workspace, str(declared_model), must_exist=False, require_file=False
    )
    trace_out, trace_out_issues = resolve_in_workspace(
        workspace, str(declared_trace), must_exist=False, require_file=False
    )
    path_issues = [*out_issues, *model_issues, *trace_out_issues]
    for candidate in (out, model_out, trace_out):
        if candidate is not None and candidate.exists() and not candidate.is_file():
            path_issues.append(
                issue("TYPE", artifact=str(candidate), message="declared output must be a regular file")
            )
    protected = [
        node_path,
        edge_path,
        manifest_path,
        workspace
        / str(
            artifact_paths.get("interventions", "interventions.json")
            if isinstance(artifact_paths, dict)
            else "interventions.json"
        ),
    ]
    if out is not None and model_out is not None and trace_out is not None:
        path_issues.extend(output_alias_issues(out, [model_out, trace_out, *protected]))
        path_issues.extend(output_alias_issues(model_out, [out, trace_out, *protected]))
        path_issues.extend(output_alias_issues(trace_out, [out, model_out, *protected]))
    if path_issues or out is None or model_out is None or trace_out is None:
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in path_issues]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    try:
        formula_version = _formula_version(workspace, manifest if isinstance(manifest, dict) else {})
        config, raw_cfg = _config(workspace, args, manifest if isinstance(manifest, dict) else {})
        shocks = (
            raw_cfg.get("stochastic_shocks")
            or (manifest.get("stochastic_shocks") if isinstance(manifest, dict) else None)
        )
        model = compile_model(
            nodes if isinstance(nodes, list) else [],
            edges if isinstance(edges, list) else [],
            load_interventions(workspace, manifest if isinstance(manifest, dict) else {}),
            formula_version=formula_version,
            stochastic_shocks=shocks if isinstance(shocks, list) else None,
        )
        ticks = args.ticks
        if ticks is None:
            if "ticks" in raw_cfg:
                raw_ticks = raw_cfg["ticks"]
                if not isinstance(raw_ticks, int) or isinstance(raw_ticks, bool):
                    raise ValueError("simulation ticks must be a non-negative integer")
                ticks = raw_ticks
            else:
                frame = manifest.get("temporal_frame") if isinstance(manifest, dict) else None
                raw_ec = manifest.get("engine_config") if isinstance(manifest, dict) else None
                has_explicit_timestep = (
                    getattr(args, "timestep", None) is not None
                    or "timestep" in raw_cfg
                    or (isinstance(raw_ec, dict) and raw_ec.get("timestep") is not None)
                    or (isinstance(frame, dict) and frame.get("timestep") is not None)
                )
                start_dt = parse_time(frame.get("simulation_start")) if isinstance(frame, dict) else None
                end_dt = parse_time(frame.get("simulation_end")) if isinstance(frame, dict) else None
                if has_explicit_timestep and start_dt is not None and end_dt is not None and end_dt > start_dt:
                    ticks = max(1, round((end_dt - start_dt).total_seconds() / (float(config.timestep) * 86400.0)))
                else:
                    ticks = 10
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "MODEL_COMPILE"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    if ticks < 0:
        print(json.dumps({"ok": False, "error": "ticks must be non-negative"}, indent=2))
        raise SystemExit(EXIT_USAGE)
    manifest_mode = manifest.get("simulation_mode") if isinstance(manifest, dict) else None
    if manifest_mode not in {"deterministic", "monte_carlo"} or config.mode != manifest_mode:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "MODE_MISMATCH",
                    "error": "manifest, run, and engine configuration modes must match",
                    "manifest_mode": manifest_mode,
                    "config_mode": config.mode,
                },
                indent=2,
            )
        )
        raise SystemExit(EXIT_SEMANTIC)

    if config.mode == "deterministic":
        result = run_deterministic(model, config, ticks=ticks, run_id=0)
        result_hash = result["run_hash"]
    else:
        result = run_monte_carlo(model, config, ticks=ticks)
        result_hash = result["summary"]["canonical_hash"]
    if result.get("exit_code", 0) != 0:
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(EXIT_NUMERICAL)
    try:
        compiled = compile_workspace(workspace, formula_version=formula_version)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "MODEL_COMPILE"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    trace_path, trace_rows, trace_issues = validate_declared_trace(
        workspace,
        manifest if isinstance(manifest, dict) else {},
        nodes if isinstance(nodes, list) else [],
        edges if isinstance(edges, list) else [],
    )
    if trace_path is not None and trace_path.is_file() and trace_path.stat().st_size and trace_issues:
        print(json.dumps({"ok": False, "code": "TRACE_EMPTY",
                          "error": "existing trace failed validation; repair it explicitly before rerunning",
                          "issues": [value.to_dict() for value in trace_issues]}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)
    is_json_trace = str(declared_trace).endswith(".json")
    is_cold_start = (
        trace_path is None
        or not trace_path.is_file()
        or trace_path.stat().st_size == 0
        or (not is_json_trace and not trace_rows)
    )
    execution_warnings = inspect_execution_health(model, config, ticks=ticks, result=result)

    if is_cold_start:
        if not is_json_trace:
            print(json.dumps({"ok": False, "code": "TRACE_FORMAT",
                              "error": "engine-derived traces require a .json artifact; declare artifact_paths.execution_trace as execution-trace.json",
                              "path": str(declared_trace)}, indent=2))
            raise SystemExit(EXIT_SEMANTIC)
        # Engine-derived numerical execution trace (R06 / F07 cold start)
        model_id = manifest.get("model_id", compiled.get("model_id", "model:compiled")) if isinstance(manifest, dict) else "model:compiled"
        trace_data = generate_numerical_execution_trace(
            model,
            config,
            ticks=ticks,
            run_id=0,
            model_id=str(model_id),
            formula_version=formula_version,
        )
        trace_rows = trace_data["steps"]
        trace_path = trace_out
        trace_execution_binding, binding_issues = build_trace_execution_binding(
            trace_data,
            model,
            config,
            ticks=ticks,
            result=result,
            manifest=manifest if isinstance(manifest, dict) else {},
        )
        if binding_issues or trace_execution_binding is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "generated execution trace could not be bound to engine trajectory",
                        "code": "TRACE_EXECUTION_BINDING",
                        "issues": [value.to_dict() for value in binding_issues],
                    },
                    indent=2,
                )
            )
            raise SystemExit(EXIT_SEMANTIC)
        trace_bytes = json.dumps(trace_data, indent=2, ensure_ascii=False, sort_keys=False, allow_nan=False).encode("utf-8") + b"\n"
        trace_digest = hashlib.sha256(trace_bytes).hexdigest()
        trace_contract = {
            "path": str(declared_trace).replace("\\", "/"),
            "sha256": trace_digest,
            "row_count": len(trace_rows),
            "replay_hash": trace_data.get("replay_hash"),
        }
        contract = {
            "schema_version": "2.0.0",
            "run_contract_version": _run_contract_version(formula_version),
            "mode": config.mode,
            "ticks": ticks,
            "model_hash": compiled["model_hash"],
            "config": config_payload(config),
            "config_hash": canonical_hash(config_payload(config)),
            "result_hash": result_hash,
            "result": result,
            "trace_contract": trace_contract,
            "trace_execution_binding": trace_execution_binding,
        }
        if execution_warnings:
            contract["warnings"] = execution_warnings
        if formula_version != LEGACY_FORMULA_VERSION:
            contract["formula_version"] = formula_version
        contract["contract_hash"] = canonical_hash(contract)
        artifacts_to_commit = [(model_out, compiled), (out, contract), (trace_out, trace_data)]
        if not getattr(args, "no_sync_branches", False):
            branch_rel = (
                artifact_paths.get("branch_ledger", "branch-ledger.json")
                if isinstance(artifact_paths, dict)
                else "branch-ledger.json"
            )
            branch_path, branch_path_issues = resolve_in_workspace(workspace, str(branch_rel), must_exist=False, require_file=True)
            if not branch_path_issues and branch_path is not None and branch_path.is_file():
                branch_doc, branch_issues = load_json_secure(branch_path)
                if not branch_issues and isinstance(branch_doc, dict) and isinstance(branch_doc.get("branches"), list):
                    changed_branch = False
                    for branch_item in branch_doc["branches"]:
                        if isinstance(branch_item, dict):
                            if branch_item.get("trace_hash") != trace_digest:
                                branch_item["trace_hash"] = trace_digest
                                changed_branch = True
                            if branch_item.get("derivation") not in {"analyst_authored", "engine_derived"}:
                                branch_item["derivation"] = (
                                    "engine_derived"
                                    if (
                                        branch_item.get("representative_run") is not None
                                        or branch_item.get("engine_cluster_id") is not None
                                        or branch_item.get("member_count") is not None
                                    )
                                    else "analyst_authored"
                                )
                                changed_branch = True
                    if changed_branch:
                        artifacts_to_commit.append((branch_path, branch_doc))
    else:
        if trace_issues or trace_path is None or (not is_json_trace and not trace_rows):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "declared propagation trace failed semantic validation",
                        "code": "TRACE_EMPTY",
                        "issues": [value.to_dict() for value in trace_issues],
                    },
                    indent=2,
                )
            )
            raise SystemExit(EXIT_SEMANTIC)
        trace_binding_input: Any = trace_rows
        if is_json_trace:
            loaded_json = _load_json_or_exit(trace_path)
            if isinstance(loaded_json, dict):
                trace_binding_input = loaded_json
                trace_fv = loaded_json.get("formula_version")
                if trace_fv and trace_fv != formula_version:
                    print(
                        json.dumps(
                            {
                                "ok": False,
                                "error": "trace and run formula versions differ",
                                "code": "FORMULA_VERSION_MISMATCH",
                                "expected": formula_version,
                                "actual": [str(trace_fv)],
                            },
                            indent=2,
                        )
                    )
                    raise SystemExit(EXIT_SEMANTIC)
        elif {row.get("formula_version") for row in trace_rows if isinstance(row, dict) and row.get("formula_version")} not in [{formula_version}, set()]:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "trace and run formula versions differ",
                        "code": "FORMULA_VERSION_MISMATCH",
                        "expected": formula_version,
                        "actual": sorted({str(row.get("formula_version")) for row in trace_rows if isinstance(row, dict)}),
                    },
                    indent=2,
                )
            )
            raise SystemExit(EXIT_SEMANTIC)
        if is_json_trace and isinstance(trace_binding_input, dict):
            model_id = (
                manifest.get("model_id", compiled.get("model_id", "model:compiled"))
                if isinstance(manifest, dict)
                else "model:compiled"
            )
            expected_trace = generate_numerical_execution_trace(
                model,
                config,
                ticks=ticks,
                run_id=0,
                model_id=str(model_id),
                formula_version=formula_version,
            )
            if trace_binding_input.get("parameters_hash") != expected_trace.get("parameters_hash"):
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "existing execution trace parameters_hash does not match compiled model/config; remove stale execution-trace.json to cold-start regenerate",
                            "code": "PARAMETERS_HASH_MISMATCH",
                            "expected": expected_trace.get("parameters_hash"),
                            "actual": trace_binding_input.get("parameters_hash"),
                        },
                        indent=2,
                    )
                )
                raise SystemExit(EXIT_SEMANTIC)
            if trace_binding_input.get("replay_hash") != expected_trace.get("replay_hash"):
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "existing execution trace replay_hash differs from deterministic replay; remove stale execution-trace.json to cold-start regenerate",
                            "code": "REPLAY_MISMATCH",
                            "expected": expected_trace.get("replay_hash"),
                            "actual": trace_binding_input.get("replay_hash"),
                        },
                        indent=2,
                    )
                )
                raise SystemExit(EXIT_SEMANTIC)
        trace_execution_binding, binding_issues = build_trace_execution_binding(
            trace_binding_input,
            model,
            config,
            ticks=ticks,
            result=result,
            manifest=manifest if isinstance(manifest, dict) else {},
        )
        if binding_issues or trace_execution_binding is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "declared propagation trace is not bound to the engine trajectory",
                        "code": "TRACE_EXECUTION_BINDING",
                        "issues": [value.to_dict() for value in binding_issues],
                    },
                    indent=2,
                )
            )
            raise SystemExit(EXIT_SEMANTIC)
        trace_contract = {
            "path": str(declared_trace).replace("\\", "/"),
            "sha256": sha256_file(trace_path),
            "row_count": len(trace_rows),
        }
        contract = {
            "schema_version": "2.0.0",
            "run_contract_version": _run_contract_version(formula_version),
            "mode": config.mode,
            "ticks": ticks,
            "model_hash": compiled["model_hash"],
            "config": config_payload(config),
            "config_hash": canonical_hash(config_payload(config)),
            "result_hash": result_hash,
            "result": result,
            "trace_contract": trace_contract,
            "trace_execution_binding": trace_execution_binding,
        }
        if execution_warnings:
            contract["warnings"] = execution_warnings
        if formula_version != LEGACY_FORMULA_VERSION:
            contract["formula_version"] = formula_version
        contract["contract_hash"] = canonical_hash(contract)
        artifacts_to_commit = [(model_out, compiled), (out, contract)]

    try:
        _commit_json_pair(artifacts_to_commit)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "code": "ARTIFACT_COMMIT", "error": str(exc)}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    print(json.dumps(contract, indent=2, default=str))
    raise SystemExit(EXIT_OK if result.get("exit_code", 0) == 0 else EXIT_NUMERICAL)


if __name__ == "__main__":
    main()
