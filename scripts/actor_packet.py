from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from _lib import load_json
from aleph import EXIT_OK, EXIT_SECURITY, EXIT_SEMANTIC, EXIT_USAGE
from aleph.io import write_json_atomic
from aleph.packets import (
    build_knowledge_packet,
    dossier_contract_hash,
    scenario_contract_hash,
)
from aleph.paths import output_alias_issues, resolve_in_workspace, validate_relative_artifact_path
from aleph.privacy import privacy_intake


def _safe_actor_token(actor_id: str) -> str:
    label = re.sub(r"[^a-z0-9-]+", "-", actor_id.lower()).strip("-")[:48] or "actor"
    digest = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()[:12]
    return f"{label}-{digest}"


def _allowed_actions(actor: dict[str, object]) -> list[str]:
    graph = actor.get("decision_graph")
    values = graph.get("allowed_actions") or graph.get("actions") or [] if isinstance(graph, dict) else graph or []
    actions: list[str] = []
    if not isinstance(values, list):
        return actions
    for value in values:
        if isinstance(value, str) and value.strip():
            actions.append(value.strip())
        elif isinstance(value, dict) and isinstance(value.get("action"), str):
            actions.append(value["action"].strip())
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze a public-role dossier and build a sealed temporal packet.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--decision-id", default="decision:main")
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--out", help="Workspace-relative packet output path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ws = Path(args.workspace).resolve()
    manifest = load_json(ws / "simulation-manifest.json")
    raw_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
    actors_path, actor_path_issues = resolve_in_workspace(
        ws, str(artifact_paths.get("actors", "actors.json")), must_exist=True
    )
    if actor_path_issues or actors_path is None:
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in actor_path_issues]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    actors = load_json(actors_path)
    if not isinstance(actors, list):
        print("ERROR: actors artifact must be an array", file=sys.stderr)
        raise SystemExit(EXIT_SEMANTIC)
    actor = next((value for value in actors if isinstance(value, dict) and value.get("id") == args.actor_id), None)
    if not actor:
        print("ERROR: actor not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    intake = privacy_intake(
        subject_class=actor.get("subject_class") or "unknown",
        living_status=actor.get("living_status") or "unknown",
        request_text=str(actor.get("scope_note") or ""),
        public_role_anchor=actor.get("public_role"),
        evidence_ids=actor.get("evidence_ids") or [],
        payload=actor,
    )
    if not intake.get("allowed"):
        print(json.dumps(intake, indent=2))
        raise SystemExit(EXIT_SECURITY)

    dossier_hash = dossier_contract_hash(actor)
    claims = []
    for claim in (actor.get("research_track") or {}).get("claims") or []:
        if isinstance(claim, dict):
            claims.append(
                {
                    "id": claim.get("id"),
                    "text": claim.get("claim"),
                    "available_at": claim.get("available_at"),
                    "actor_access": claim.get("actor_access") or claim.get("access_basis"),
                    "access_basis": claim.get("access_basis") or "",
                }
            )
    packet_result = build_knowledge_packet(
        actor_id=args.actor_id,
        decision_id=args.decision_id,
        decision_time=args.decision_time,
        knowledge_cutoff=args.cutoff,
        dossier_hash=dossier_hash,
        scenario_hash=scenario_contract_hash(manifest),
        claims=claims,
        institutional_constraints=list(actor.get("institutional_constraints") or []),
        allowed_actions=_allowed_actions(actor),
        unknowns=list(actor.get("uncertainty_factors") or []),
    )
    if not packet_result.get("ok"):
        print(json.dumps(packet_result, indent=2, ensure_ascii=False))
        raise SystemExit(EXIT_SEMANTIC)

    relative_out = args.out or f"packets/{_safe_actor_token(args.actor_id)}.json"
    path_issues = validate_relative_artifact_path(relative_out)
    if path_issues:
        print(json.dumps({"ok": False, "issues": [item.to_dict() for item in path_issues]}, indent=2))
        raise SystemExit(EXIT_SECURITY)
    output_path, path_issues = resolve_in_workspace(ws, relative_out, must_exist=False, require_file=False)
    if output_path is not None:
        path_issues.extend(
            output_alias_issues(
                output_path,
                [actors_path, ws / "simulation-manifest.json"],
            )
        )
    if path_issues or output_path is None:
        print(json.dumps({"ok": False, "issues": [item.to_dict() for item in path_issues]}, indent=2))
        raise SystemExit(EXIT_SECURITY)
    packet = packet_result["packet"]
    write_json_atomic(output_path, packet)
    out = {
        "schema_version": "2.0.0",
        "dossier_hash": dossier_hash,
        "packet": packet,
        "packet_hash": packet["packet_hash"],
        # Content-free exclusions remain with the adjudicator, outside the seal.
        "exclusion_ledger": packet_result["exclusion_ledger"],
        "privacy": intake,
        "output_path": str(output_path.relative_to(ws)).replace("\\", "/"),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
