from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aleph import (
    EXIT_OK,
    EXIT_SEMANTIC,
    EXIT_USAGE,
    FORMULA_VERSION,
    LEGACY_FORMULA_VERSION,
    SUPPORTED_FORMULA_VERSIONS,
)
from aleph.engine import compile_model, model_hash, model_payload
from aleph.io import (
    canonical_hash,
    load_json_secure,
    load_jsonl_secure,
    sha256_file,
    write_json_atomic,
)
from aleph.issues import issue
from aleph.paths import output_alias_issues, resolve_in_workspace
from aleph.validator import manifest_integrity_payload


def _load_json_or_raise(path: Path) -> Any:
    data, issues = load_json_secure(path)
    if issues:
        detail = "; ".join(
            f"{value.code}: {value.message}" for value in issues
        )
        raise ValueError(f"cannot load {path}: {detail}")
    return data


def load_interventions(workspace: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    interventions: list[dict[str, Any]] = []
    raw_paths = manifest.get("artifact_paths")
    artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
    intervention_relative = str(artifact_paths.get("interventions", "interventions.json"))
    explicit, explicit_issues = resolve_in_workspace(
        workspace, intervention_relative, must_exist=False, require_file=False
    )
    if explicit_issues or explicit is None:
        raise ValueError("manifest-declared interventions path is invalid")
    if explicit.is_file():
        value = _load_json_or_raise(explicit)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError(f"{intervention_relative} entries must be objects")
                interventions.append(item)
        else:
            raise ValueError(f"{intervention_relative} must be an array")
    declared = manifest.get("interventions")
    if isinstance(declared, list):
        interventions.extend(item for item in declared if isinstance(item, dict))
    change = manifest.get("change_point")
    if isinstance(change, dict) and change.get("target") and (
        isinstance(change.get("value"), (int, float)) or isinstance(change.get("magnitude"), (int, float))
    ):
        interventions.append(
            {
                "id": str(change.get("id", "intervention:change-point")),
                "target": str(change["target"]),
                "op": str(change.get("op", "add")),
                "value": change.get("value", change.get("magnitude")),
                "start_tick": change.get("start_tick", 0),
                "end_tick": change.get("end_tick"),
                "release_policy": str(change.get("release_policy", "retain")),
            }
        )
    return interventions


def resolve_workspace_formula_version(workspace: Path, manifest: dict[str, Any]) -> str:
    """Resolve one formula generation from all existing workspace contracts."""
    raw_paths = manifest.get("artifact_paths")
    artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
    candidates: dict[str, str] = {}

    def add(source: str, value: Any) -> None:
        if value is None:
            return
        if value not in SUPPORTED_FORMULA_VERSIONS:
            raise ValueError(f"{source} declares unsupported formula version {value!r}")
        candidates[source] = str(value)

    add("simulation-manifest.json", manifest.get("formula_version"))

    json_artifacts = (
        (
            "computational_model",
            "simulation-model.json",
            "model_version",
            {"aleph-engine-2.0": LEGACY_FORMULA_VERSION, "aleph-engine-2.1": FORMULA_VERSION},
        ),
        (
            "run_ledger",
            "simulation-run.json",
            "run_contract_version",
            {"aleph-run-2.0": LEGACY_FORMULA_VERSION, "aleph-run-2.1": FORMULA_VERSION},
        ),
    )
    for key, default, generation_field, generation_map in json_artifacts:
        relative = str(artifact_paths.get(key, default))
        path, path_issues = resolve_in_workspace(
            workspace, relative, must_exist=False, require_file=False
        )
        if path_issues:
            raise ValueError(f"invalid {key} path while resolving formula version")
        if path is None or not path.is_file():
            continue
        value = _load_json_or_raise(path)
        if not isinstance(value, dict):
            raise ValueError(f"{relative} must be an object")
        declared = value.get("formula_version")
        if declared is None:
            generation = value.get(generation_field)
            declared = generation_map.get(generation) if isinstance(generation, str) else None
        add(relative.replace("\\", "/"), declared)

    trace_relative = str(artifact_paths.get("propagation_trace", "propagation-trace.jsonl"))
    trace_path, trace_path_issues = resolve_in_workspace(
        workspace, trace_relative, must_exist=False, require_file=False
    )
    if trace_path_issues:
        raise ValueError("invalid propagation trace path while resolving formula version")
    if trace_path is not None and trace_path.is_file():
        rows, row_issues = load_jsonl_secure(trace_path)
        if row_issues:
            detail = "; ".join(f"{value.code}: {value.message}" for value in row_issues)
            raise ValueError(f"cannot load {trace_path}: {detail}")
        versions = {
            row.get("formula_version")
            for row in rows
            if isinstance(row, dict) and row.get("formula_version") is not None
        }
        if len(versions) > 1:
            raise ValueError("propagation trace mixes formula versions")
        if versions:
            add(trace_relative.replace("\\", "/"), next(iter(versions)))

    unique = set(candidates.values())
    if len(unique) > 1:
        detail = ", ".join(f"{source}={version}" for source, version in sorted(candidates.items()))
        raise ValueError(f"workspace formula contracts disagree: {detail}")
    return next(iter(unique), FORMULA_VERSION)


def compile_workspace(workspace: Path, *, formula_version: str | None = None) -> dict[str, Any]:
    manifest_path = workspace / "simulation-manifest.json"
    manifest = _load_json_or_raise(manifest_path) if manifest_path.is_file() else {}
    if not isinstance(manifest, dict):
        raise ValueError("simulation-manifest.json must be an object")
    resolved_formula_version = (
        resolve_workspace_formula_version(workspace, manifest)
        if formula_version is None
        else formula_version
    )
    if resolved_formula_version not in SUPPORTED_FORMULA_VERSIONS:
        raise ValueError(f"unsupported formula version {resolved_formula_version}")
    raw_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
    node_relative = str(artifact_paths.get("nodes", "nodes.json"))
    edge_relative = str(artifact_paths.get("edges", "edges.json"))
    node_path, node_issues = resolve_in_workspace(workspace, node_relative, must_exist=True)
    edge_path, edge_issues = resolve_in_workspace(workspace, edge_relative, must_exist=True)
    if node_issues or edge_issues or node_path is None or edge_path is None:
        raise ValueError("manifest-declared node or edge artifact is unavailable")
    nodes = _load_json_or_raise(node_path)
    edges = _load_json_or_raise(edge_path)
    model = compile_model(
        nodes if isinstance(nodes, list) else [],
        edges if isinstance(edges, list) else [],
        load_interventions(workspace, manifest if isinstance(manifest, dict) else {}),
        formula_version=resolved_formula_version,
    )
    body = model_payload(model)
    source_hashes = {
        node_relative.replace("\\", "/"): sha256_file(node_path),
        edge_relative.replace("\\", "/"): sha256_file(edge_path),
    }
    intervention_relative = str(artifact_paths.get("interventions", "interventions.json"))
    intervention_path, intervention_issues = resolve_in_workspace(
        workspace, intervention_relative, must_exist=False, require_file=False
    )
    if intervention_issues or intervention_path is None:
        raise ValueError("manifest-declared interventions path is invalid")
    if intervention_path.is_file():
        source_hashes[intervention_relative.replace("\\", "/")] = sha256_file(intervention_path)
    if manifest_path.is_file() and isinstance(manifest, dict):
        source_hashes["simulation-manifest.json"] = canonical_hash(manifest_integrity_payload(manifest))
    return {
        "schema_version": "2.0.0",
        "model_version": (
            "aleph-engine-2.1"
            if resolved_formula_version != LEGACY_FORMULA_VERSION
            else "aleph-engine-2.0"
        ),
        "formula_version": resolved_formula_version,
        "model_hash": model_hash(model),
        **body,
        "source_hashes": source_hashes,
        "source_set_hash": canonical_hash(source_hashes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile evidence graph to a hashed computational model.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", help="must match manifest.artifact_paths.computational_model")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print("ERROR: workspace not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    manifest_path = workspace / "simulation-manifest.json"
    try:
        manifest = _load_json_or_raise(manifest_path) if manifest_path.is_file() else {}
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "INVALID_ARTIFACT"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    raw_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
    declared_out = str(artifact_paths.get("computational_model", "simulation-model.json"))
    if args.out is not None and args.out.replace("\\", "/") != declared_out.replace("\\", "/"):
        print(json.dumps({"ok": False, "code": "PATH_ALIAS", "error": "--out must match the manifest declaration"}, indent=2))
        raise SystemExit(EXIT_USAGE)
    out, issues = resolve_in_workspace(workspace, declared_out, must_exist=False, require_file=False)
    if out is not None and out.exists() and not out.is_file():
        issues.append(issue("TYPE", artifact=str(out), message="declared model output must be a regular file"))
    protected = [
        manifest_path,
        workspace / str(artifact_paths.get("nodes", "nodes.json")),
        workspace / str(artifact_paths.get("edges", "edges.json")),
        workspace / str(artifact_paths.get("interventions", "interventions.json")),
    ]
    if out is not None:
        issues.extend(output_alias_issues(out, protected))
    if issues or out is None:
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in issues]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    try:
        payload = compile_workspace(workspace)
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "MODEL_COMPILE"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    try:
        write_json_atomic(out, payload)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "ARTIFACT_COMMIT"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "model_hash": payload["model_hash"],
                "variables": len(payload["variables"]),
                "edges": len(payload["edges"]),
            },
            indent=2,
        )
    )
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
