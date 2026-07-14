from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.engine import compile_model, model_hash, model_payload
from aleph.io import canonical_hash, load_json_secure, sha256_file, write_json_atomic
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
    explicit = workspace / "interventions.json"
    if explicit.is_file():
        value = _load_json_or_raise(explicit)
        if isinstance(value, list):
            interventions.extend(item for item in value if isinstance(item, dict))
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
                "start_tick": int(change.get("start_tick", 0)),
                "end_tick": change.get("end_tick"),
            }
        )
    return interventions


def compile_workspace(workspace: Path) -> dict[str, Any]:
    manifest_path = workspace / "simulation-manifest.json"
    manifest = _load_json_or_raise(manifest_path) if manifest_path.is_file() else {}
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
    )
    body = model_payload(model)
    source_hashes = {
        node_relative.replace("\\", "/"): sha256_file(node_path),
        edge_relative.replace("\\", "/"): sha256_file(edge_path),
    }
    if (workspace / "interventions.json").is_file():
        source_hashes["interventions.json"] = sha256_file(workspace / "interventions.json")
    if manifest_path.is_file() and isinstance(manifest, dict):
        source_hashes["simulation-manifest.json"] = canonical_hash(manifest_integrity_payload(manifest))
    return {
        "schema_version": "2.0.0",
        "model_version": "aleph-engine-2.0",
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
        workspace / "interventions.json",
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
