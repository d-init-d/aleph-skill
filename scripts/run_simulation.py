from __future__ import annotations

import argparse
import json
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
    LEGACY_FORMULA_VERSION,
)
from aleph.engine import (
    EngineConfig,
    compile_model,
    config_payload,
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


def _config(workspace: Path, args: argparse.Namespace) -> EngineConfig:
    values: dict[str, Any] = {}
    path = workspace / "simulation-config.json"
    if path.is_file():
        raw = _load_json_or_exit(path)
        if isinstance(raw, dict):
            allowed = {item.name for item in fields(EngineConfig)}
            values.update({key: value for key, value in raw.items() if key in allowed})
    overrides = {
        "mode": args.mode,
        "seed": args.seed,
        "workers": args.workers,
        "min_runs": args.runs,
        "max_runs": args.max_runs,
        "batch_size": args.batch_size,
    }
    values.update({key: value for key, value in overrides.items() if value is not None})
    return EngineConfig(**values)


def _formula_version(workspace: Path, manifest: dict[str, Any]) -> str:
    return resolve_workspace_formula_version(workspace, manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a hashed deterministic or Monte Carlo simulation contract.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=["deterministic", "monte_carlo"])
    parser.add_argument("--seed")
    parser.add_argument("--ticks", type=int)
    parser.add_argument("--runs", type=int, help="minimum Monte Carlo runs")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", help="workspace-relative run contract; must match the manifest declaration")
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
    out, out_issues = resolve_in_workspace(
        workspace, str(declared_run), must_exist=False, require_file=False
    )
    model_out, model_issues = resolve_in_workspace(
        workspace, str(declared_model), must_exist=False, require_file=False
    )
    path_issues = [*out_issues, *model_issues]
    for candidate in (out, model_out):
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
    if out is not None and model_out is not None:
        path_issues.extend(output_alias_issues(out, [model_out, *protected]))
        path_issues.extend(output_alias_issues(model_out, [out, *protected]))
    if path_issues or out is None or model_out is None:
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in path_issues]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    try:
        formula_version = _formula_version(workspace, manifest if isinstance(manifest, dict) else {})
        model = compile_model(
            nodes if isinstance(nodes, list) else [],
            edges if isinstance(edges, list) else [],
            load_interventions(workspace, manifest if isinstance(manifest, dict) else {}),
            formula_version=formula_version,
        )
        config = _config(workspace, args)
        ticks = args.ticks
        if ticks is None:
            raw_cfg = (
                _load_json_or_exit(workspace / "simulation-config.json")
                if (workspace / "simulation-config.json").is_file()
                else {}
            )
            raw_ticks = raw_cfg.get("ticks", 10) if isinstance(raw_cfg, dict) else 10
            if not isinstance(raw_ticks, int) or isinstance(raw_ticks, bool):
                raise ValueError("simulation ticks must be a non-negative integer")
            ticks = raw_ticks
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
    declared_trace = (
        artifact_paths.get("propagation_trace")
        if isinstance(artifact_paths, dict)
        else "propagation-trace.jsonl"
    )
    trace_path, trace_rows, trace_issues = validate_declared_trace(
        workspace,
        manifest if isinstance(manifest, dict) else {},
        nodes if isinstance(nodes, list) else [],
        edges if isinstance(edges, list) else [],
    )
    if trace_issues or trace_path is None or not trace_rows:
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
    if {row.get("formula_version") for row in trace_rows} != {formula_version}:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "trace and run formula versions differ",
                    "code": "FORMULA_VERSION_MISMATCH",
                    "expected": formula_version,
                    "actual": sorted({str(row.get("formula_version")) for row in trace_rows}),
                },
                indent=2,
            )
        )
        raise SystemExit(EXIT_SEMANTIC)
    trace_execution_binding, binding_issues = build_trace_execution_binding(
        trace_rows,
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
        "run_contract_version": "aleph-run-2.0" if formula_version == LEGACY_FORMULA_VERSION else "aleph-run-2.1",
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
    if formula_version != LEGACY_FORMULA_VERSION:
        contract["formula_version"] = formula_version
    contract["contract_hash"] = canonical_hash(contract)
    try:
        _commit_json_pair([(model_out, compiled), (out, contract)])
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "code": "ARTIFACT_COMMIT", "error": str(exc)}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    print(json.dumps(contract, indent=2, default=str))
    raise SystemExit(EXIT_OK if result.get("exit_code", 0) == 0 else EXIT_NUMERICAL)


if __name__ == "__main__":
    main()
