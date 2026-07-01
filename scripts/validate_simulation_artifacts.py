from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _lib import load_csv_rows, load_json, load_optional_yaml, skill_root, utc_now, write_json


NODE_REQUIRED = {
    "time",
    "state_before",
    "trigger",
    "mechanism",
    "state_after",
    "lag",
    "evidence_ids",
    "status",
    "probability",
    "confidence",
    "alternative_explanations",
    "sensitivity",
}
EDGE_REQUIRED = {"id", "from", "to", "relation", "sign", "base_strength", "confidence", "mechanism", "lag_distribution", "evidence", "status"}
ACTOR_REQUIRED = {
    "id",
    "person_node",
    "public_role",
    "scope_note",
    "evidence_ids",
    "research_track",
    "roleplay_track",
    "adjudication",
    "decision_patterns",
    "predicted_responses",
}


def load_structured(path: Path) -> Any:
    if path.suffix.lower() == ".json":
        return load_json(path)
    if path.suffix.lower() in {".yaml", ".yml"}:
        return load_optional_yaml(path)
    raise ValueError(f"Unsupported structured file type: {path}")


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def validate_node(node: dict[str, Any], index: int, errors: list[str]) -> None:
    missing = NODE_REQUIRED - set(node)
    if missing:
        errors.append(f"node[{index}] missing required fields: {sorted(missing)}")
    if not 0 <= float(node.get("confidence", 0)) <= 1:
        errors.append(f"node[{index}] confidence must be within [0, 1]")
    if not 0 <= float(node.get("probability", 0)) <= 1:
        errors.append(f"node[{index}] probability must be within [0, 1]")


def validate_edge(edge: dict[str, Any], index: int, errors: list[str], warnings: list[str]) -> None:
    missing = EDGE_REQUIRED - set(edge)
    if missing:
        errors.append(f"edge[{index}] missing required fields: {sorted(missing)}")
    mechanism = str(edge.get("mechanism", ""))
    if len(mechanism.split()) < 20:
        warnings.append(f"edge[{index}] mechanism is short; target at least 20 words")
    for field in ["base_strength", "confidence"]:
        if not 0 <= float(edge.get(field, 0)) <= 1:
            errors.append(f"edge[{index}] {field} must be within [0, 1]")


def validate_actor(actor: dict[str, Any], index: int, errors: list[str]) -> None:
    missing = ACTOR_REQUIRED - set(actor)
    if missing:
        errors.append(f"actor[{index}] missing required fields: {sorted(missing)}")
    research_notes = str(actor.get("research_track", {}).get("notes", "")).lower()
    roleplay_notes = str(actor.get("roleplay_track", {}).get("notes", "")).lower()
    if "must not roleplay" not in research_notes:
        errors.append(f"actor[{index}] research track must explicitly forbid roleplay")
    if "simulation" not in roleplay_notes or "not evidence" not in roleplay_notes:
        errors.append(f"actor[{index}] roleplay track must be marked as simulation, not evidence")
    for response in actor.get("predicted_responses", []):
        probability = float(response.get("probability", 0))
        if probability > 0.8:
            errors.append(f"actor[{index}] predicted response probability exceeds 0.80 cap")


def validate_branch_ledger(data: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    branches = data.get("branches", [])
    if len(branches) < 3:
        errors.append("branch ledger must contain at least 3 branches")
    total = sum(float(branch.get("probability", 0)) for branch in branches)
    if abs(total - 1.0) > 0.0001:
        errors.append(f"branch probabilities must sum to 1.0, got {total:.6f}")
    for branch in branches:
        if float(branch.get("probability", 0)) > 0.60:
            warnings.append(f"{branch.get('id', 'branch')} exceeds 0.60 probability cap")


def validate_trace(path: Path, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"missing trace file: {path}")
        return
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"trace line {line_no} invalid JSON: {exc}")
            continue
        for key in ["time", "from", "to", "output_effect", "mechanism", "evidence_ids"]:
            if key not in row:
                errors.append(f"trace line {line_no} missing {key}")


def validate_workspace(workspace: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = workspace / "simulation-manifest.json"
    if not manifest_path.exists():
        errors.append(f"missing manifest: {manifest_path}")
        return {"status": "fail", "errors": errors, "warnings": warnings}
    manifest = load_json(manifest_path)
    artifact_paths = manifest.get("artifact_paths", {})

    nodes = ensure_list(load_structured(workspace / artifact_paths.get("nodes", "nodes.json")))
    edges = ensure_list(load_structured(workspace / artifact_paths.get("edges", "edges.json")))
    actors = ensure_list(load_structured(workspace / artifact_paths.get("actors", "actors.json")))
    branch_ledger = load_json(workspace / artifact_paths.get("branch_ledger", "branch-ledger.json"))
    evidence_rows = load_csv_rows(workspace / artifact_paths.get("evidence_map", "evidence-map.csv"))

    if not evidence_rows:
        errors.append("evidence map must contain at least one row")
    for index, node in enumerate(nodes):
        validate_node(node, index, errors)
    for index, edge in enumerate(edges):
        validate_edge(edge, index, errors, warnings)
    for index, actor in enumerate(actors):
        validate_actor(actor, index, errors)
    validate_branch_ledger(branch_ledger, errors, warnings)
    validate_trace(workspace / artifact_paths.get("propagation_trace", "propagation-trace.jsonl"), errors)

    return {
        "schema_version": "1.0.0",
        "validated_at": utc_now(),
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
    }


def validate_examples() -> dict[str, Any]:
    root = skill_root()
    workspace = root / "templates"
    temp_manifest = load_json(workspace / "simulation-manifest.json")
    temp_manifest["artifact_paths"] = {
        "nodes": "timeline-node.json",
        "edges": "causal-edge.json",
        "actors": "actor-dossier.json",
        "evidence_map": "evidence-map.csv",
        "branch_ledger": "branch-ledger.json",
        "propagation_trace": "propagation-trace.jsonl",
    }
    temp_path = workspace / ".simulation-manifest.generated.json"
    write_json(temp_path, temp_manifest)
    try:
        original = workspace / "simulation-manifest.json"
        original_text = original.read_text(encoding="utf-8")
        original.write_text(temp_path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        return validate_workspace(workspace)
    finally:
        (workspace / "simulation-manifest.json").write_text(original_text, encoding="utf-8", newline="\n")
        if temp_path.exists():
            temp_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Aleph Skill simulation artifacts.")
    parser.add_argument("--workspace", help="Simulation workspace directory.")
    parser.add_argument("--examples", action="store_true", help="Validate bundled templates as examples.")
    parser.add_argument("--write-report", action="store_true", help="Write validation-report.json into workspace.")
    args = parser.parse_args()

    if args.examples:
        result = validate_examples()
    elif args.workspace:
        result = validate_workspace(Path(args.workspace).resolve())
        if args.write_report:
            write_json(Path(args.workspace).resolve() / "validation-report.json", result)
    else:
        parser.error("Provide --workspace or --examples")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
