from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.engine import (
    EngineConfig,
    compile_model,
    run_deterministic,
    run_monte_carlo,
    semantic_result_payload,
)
from aleph.execution_binding import build_trace_execution_binding
from aleph.formula import formula_version
from aleph.io import canonical_hash, load_json_secure, sha256_file, write_json_atomic
from aleph.issues import Issue, issue
from aleph.paths import output_alias_issues, resolve_in_workspace
from aleph.trace_contract import validate_declared_trace
from compile_model import compile_workspace, load_interventions


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a saved simulation-run.json contract.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run", help="workspace-relative recorded contract; must match the manifest")
    parser.add_argument("--out", help="workspace-relative replay report; must match the manifest")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print("ERROR: workspace not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    manifest = (
        _load_json_or_exit(workspace / "simulation-manifest.json")
        if (workspace / "simulation-manifest.json").is_file()
        else {}
    )
    artifact_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    node_relative = artifact_paths.get("nodes", "nodes.json") if isinstance(artifact_paths, dict) else "nodes.json"
    edge_relative = artifact_paths.get("edges", "edges.json") if isinstance(artifact_paths, dict) else "edges.json"
    declared_run = artifact_paths.get("run_ledger", "simulation-run.json") if isinstance(artifact_paths, dict) else "simulation-run.json"
    declared_report = artifact_paths.get("replay_report", "replay-report.json") if isinstance(artifact_paths, dict) else "replay-report.json"
    if (
        args.run is not None
        and str(args.run).replace("\\", "/") != str(declared_run).replace("\\", "/")
        or args.out is not None
        and str(args.out).replace("\\", "/") != str(declared_report).replace("\\", "/")
    ):
        print(json.dumps({"ok": False, "error": "run/report paths must match manifest declarations", "code": "PATH_ALIAS"}, indent=2))
        raise SystemExit(EXIT_USAGE)
    run_path, run_path_issues = resolve_in_workspace(workspace, str(declared_run), must_exist=True)
    out_path, out_path_issues = resolve_in_workspace(workspace, str(declared_report), must_exist=False, require_file=False)
    node_path, node_path_issues = resolve_in_workspace(workspace, str(node_relative), must_exist=True)
    edge_path, edge_path_issues = resolve_in_workspace(workspace, str(edge_relative), must_exist=True)
    if out_path is not None:
        if out_path.exists() and not out_path.is_file():
            out_path_issues.append(
                issue("TYPE", artifact=str(out_path), message="declared replay output must be a regular file")
            )
        out_path_issues.extend(
            output_alias_issues(
                out_path,
                [
                    value
                    for value in (
                        run_path,
                        node_path,
                        edge_path,
                        workspace / "simulation-manifest.json",
                        workspace / str(artifact_paths.get("propagation_trace", "propagation-trace.jsonl"))
                        if isinstance(artifact_paths, dict)
                        else workspace / "propagation-trace.jsonl",
                    )
                    if value is not None
                ],
            )
        )
    if run_path_issues or out_path_issues or node_path_issues or edge_path_issues or run_path is None or out_path is None or node_path is None or edge_path is None:
        issues = run_path_issues + out_path_issues + node_path_issues + edge_path_issues
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in issues]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    recorded = _load_json_or_exit(run_path)
    if (
        not isinstance(recorded, dict)
        or recorded.get("schema_version") != "2.0.0"
        or recorded.get("run_contract_version") != "aleph-run-2.0"
        or recorded.get("mode") not in {"deterministic", "monte_carlo"}
        or not isinstance(recorded.get("ticks"), int)
        or isinstance(recorded.get("ticks"), bool)
        or recorded.get("ticks", -1) < 0
        or not isinstance(recorded.get("config"), dict)
        or recorded["config"].get("mode") != recorded.get("mode")
        or not isinstance(recorded.get("result"), dict)
    ):
        print(json.dumps({"ok": False, "error": "invalid or unsupported run contract"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)

    declared_contract_hash = recorded.get("contract_hash")
    contract_body = {key: value for key, value in recorded.items() if key != "contract_hash"}
    contract_hash_ok = declared_contract_hash == canonical_hash(contract_body)
    raw_config = recorded.get("config")
    try:
        compiled = compile_workspace(workspace)
        config = EngineConfig(**raw_config) if isinstance(raw_config, dict) else EngineConfig()
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "MODEL_COMPILE"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    model_hash_ok = recorded.get("model_hash") == compiled["model_hash"]
    config_hash_ok = recorded.get("config_hash") == canonical_hash(raw_config if isinstance(raw_config, dict) else {})

    nodes = _load_json_or_exit(node_path)
    edges = _load_json_or_exit(edge_path)
    if not isinstance(manifest, dict) or manifest.get("simulation_mode") != recorded.get("mode"):
        print(json.dumps({"ok": False, "error": "manifest and run modes differ", "code": "MODE_MISMATCH"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)
    try:
        model = compile_model(
            nodes if isinstance(nodes, list) else [],
            edges if isinstance(edges, list) else [],
            load_interventions(workspace, manifest if isinstance(manifest, dict) else {}),
        )
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "MODEL_COMPILE"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    ticks = recorded["ticks"]
    if recorded.get("mode") == "monte_carlo":
        replay = run_monte_carlo(model, config, ticks=ticks)
        replay_hash = replay["summary"]["canonical_hash"]
    else:
        replay = run_deterministic(model, config, ticks=ticks, run_id=0)
        replay_hash = replay["run_hash"]
    result_hash_ok = recorded.get("result_hash") == replay_hash
    saved_result_ok = semantic_result_payload(recorded["result"]) == semantic_result_payload(replay)

    trace_issues = []
    trace_rows = 0
    validated_rows: list[dict[str, Any]] = []
    recorded_trace = recorded.get("trace_contract")
    trace_contract_ok = False
    current_trace_hash = None
    artifact_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    declared_trace = (
        artifact_paths.get("propagation_trace")
        if isinstance(artifact_paths, dict)
        else "propagation-trace.jsonl"
    )
    trace_path: Path | None = None
    if recorded_trace is None:
        trace_issues.append(
            issue(
                "REPLAY_MISMATCH",
                artifact=str(declared_trace),
                message="run contract did not bind the declared propagation trace",
            )
        )
    elif isinstance(recorded_trace, dict):
        relative = recorded_trace.get("path")
        if str(relative).replace("\\", "/") != str(declared_trace).replace("\\", "/"):
            trace_issues.append(
                issue(
                    "REPLAY_MISMATCH",
                    pointer="/trace_contract/path",
                    expected=declared_trace,
                    actual=relative,
                    message="run contract references a different trace than the manifest",
                )
            )
        resolved_trace, path_issues = resolve_in_workspace(
            workspace,
            str(relative),
            must_exist=True,
            require_file=True,
        )
        trace_issues.extend(path_issues)
        if resolved_trace is not None:
            trace_path = resolved_trace
            current_trace_hash = sha256_file(trace_path)
            expected_hash = recorded_trace.get("sha256")
            expected_rows = recorded_trace.get("row_count")
            trace_contract_ok = (
                isinstance(expected_hash, str)
                and current_trace_hash == expected_hash
                and isinstance(expected_rows, int)
                and not isinstance(expected_rows, bool)
                and expected_rows > 0
                and not any(value.severity == "error" for value in trace_issues)
            )
            if current_trace_hash != expected_hash:
                trace_issues.append(
                    issue(
                        "REPLAY_MISMATCH",
                        artifact=str(relative),
                        pointer="sha256",
                        expected=expected_hash,
                        actual=current_trace_hash,
                        message="trace digest changed",
                    )
                )
    else:
        trace_issues.append(
            issue("TYPE", pointer="/trace_contract", message="trace_contract must be object or null")
        )
    if isinstance(recorded_trace, dict) and trace_path is not None and trace_path.is_file():
        _, rows, semantic_issues = validate_declared_trace(
            workspace,
            manifest if isinstance(manifest, dict) else {},
            nodes if isinstance(nodes, list) else [],
            edges if isinstance(edges, list) else [],
        )
        trace_issues.extend(semantic_issues)
        trace_rows = len(rows)
        validated_rows = rows
        expected_rows = recorded_trace.get("row_count")
        if trace_rows != expected_rows:
            trace_contract_ok = False
            trace_issues.append(
                issue(
                    "REPLAY_MISMATCH",
                    artifact=str(recorded_trace.get("path")),
                    pointer="row_count",
                    expected=expected_rows,
                    actual=trace_rows,
                    message="trace row count changed",
                )
            )
    trace_ok = trace_contract_ok and not any(value.severity == "error" for value in trace_issues)
    current_execution_binding = None
    binding_issues: list[Issue] = []
    if trace_ok:
        current_execution_binding, binding_issues = build_trace_execution_binding(
            validated_rows,
            model,
            config,
            ticks=ticks,
            result=replay,
            manifest=manifest if isinstance(manifest, dict) else {},
        )
        trace_issues.extend(binding_issues)
    recorded_execution_binding = recorded.get("trace_execution_binding")
    trace_execution_binding_ok = (
        current_execution_binding is not None
        and current_execution_binding == recorded_execution_binding
        and not any(value.severity == "error" for value in binding_issues)
    )
    if not trace_execution_binding_ok:
        trace_issues.append(
            issue(
                "REPLAY_MISMATCH",
                pointer="/trace_execution_binding",
                expected=recorded_execution_binding,
                actual=current_execution_binding,
                message="trace-to-engine execution binding differs from replay",
            )
        )
    trace_ok = trace_ok and trace_execution_binding_ok
    matched = (
        contract_hash_ok
        and model_hash_ok
        and config_hash_ok
        and result_hash_ok
        and saved_result_ok
        and replay.get("ok", False)
        and trace_ok
    )
    report = {
        "schema_version": "2.0.0",
        "formula_version": formula_version(),
        "recorded_contract_hash": declared_contract_hash,
        "contract_hash_ok": contract_hash_ok,
        "recorded_model_hash": recorded.get("model_hash"),
        "current_model_hash": compiled["model_hash"],
        "model_hash_ok": model_hash_ok,
        "config_hash_ok": config_hash_ok,
        "recorded_result_hash": recorded.get("result_hash"),
        "replay_result_hash": replay_hash,
        "result_hash_ok": result_hash_ok,
        "saved_result_ok": saved_result_ok,
        "trace_rows": trace_rows,
        "trace_contract_ok": trace_contract_ok,
        "trace_execution_binding_ok": trace_execution_binding_ok,
        "recorded_trace_hash": recorded_trace.get("sha256") if isinstance(recorded_trace, dict) else None,
        "current_trace_hash": current_trace_hash,
        "trace_ok": trace_ok,
        "issues": [value.to_dict() for value in trace_issues],
        "match": matched,
    }
    report["report_hash"] = canonical_hash(report)
    try:
        write_json_atomic(out_path, report)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "code": "ARTIFACT_COMMIT", "error": str(exc)}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(EXIT_OK if matched else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
