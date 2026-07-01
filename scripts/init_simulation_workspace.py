from __future__ import annotations

import argparse
import re
from pathlib import Path

from _lib import load_json, skill_root, utc_now, write_json


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "simulation-run"


def build_workspace(args: argparse.Namespace) -> Path:
    root = skill_root()
    templates = root / "templates"
    slug = slugify(args.slug or args.change_point or "simulation-run")
    out_base = Path(args.out_dir).resolve() if args.out_dir else (Path.cwd() / "simulation-output").resolve()
    resolved_root = root.resolve()
    if out_base == resolved_root or resolved_root in out_base.parents:
        raise ValueError("Simulation output cannot be created inside the installed skill. Pass --out-dir pointing to the user workspace.")
    workspace = out_base / slug
    workspace.mkdir(parents=True, exist_ok=args.force)

    manifest = load_json(templates / "simulation-manifest.json")
    manifest["simulation_id"] = f"sim-{slug}"
    manifest["created_at"] = utc_now()
    manifest["status"] = "draft"
    manifest["change_point"]["description"] = args.change_point
    manifest["change_point"]["time"] = args.time
    manifest["scope"]["horizon"] = args.horizon
    manifest["scope"]["domain"] = args.domain
    manifest["scope"]["depth"] = args.depth
    profile_budgets = {
        "quick": {"min_sources": 4, "max_sources": 8, "max_repair_loops": 1},
        "standard": {"min_sources": 6, "max_sources": 12, "max_repair_loops": 2},
        "deep": {"min_sources": 12, "max_sources": 25, "max_repair_loops": 3},
    }
    quality_by_profile = {"quick": "basic", "standard": "standard", "deep": "deep"}
    manifest["execution"]["profile"] = args.profile
    manifest["execution"]["research_quality"] = quality_by_profile[args.profile]
    manifest["execution"]["research_budget"] = profile_budgets[args.profile]
    manifest["execution"]["repair_loops_used"] = 0
    manifest["execution"]["d_research"] = {"status": "unknown", "invoked": False}
    manifest["execution"]["subagents"] = {
        "status": "unknown",
        "tool": None,
        "detection_method": "pending runtime capability check",
        "fallback_reason": "",
    }
    manifest["execution"]["checkpoints"] = {
        "initialized": True,
        "baseline_researched": False,
        "human_tracks_completed": False,
        "graph_built": False,
        "propagated": False,
        "branched": False,
        "validated": False,
    }

    node = load_json(templates / "timeline-node.json")
    edge = load_json(templates / "causal-edge.json")
    actor = load_json(templates / "actor-dossier.json")

    write_json(workspace / "simulation-manifest.json", manifest)
    write_json(workspace / "nodes.json", [node])
    write_json(workspace / "edges.json", [edge])
    write_json(workspace / "actors.json", [actor])
    (workspace / "evidence-map.csv").write_text(
        (templates / "evidence-map.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "propagation-trace.jsonl").write_text(
        (templates / "propagation-trace.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "human-track-ledger.jsonl").write_text(
        (templates / "human-track-ledger.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    write_json(workspace / "branch-ledger.json", load_json(templates / "branch-ledger.json"))
    write_json(workspace / "validation-report.json", load_json(templates / "validation-report.json"))
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an Aleph Skill simulation workspace from templates.")
    parser.add_argument("--slug", help="Workspace slug.")
    parser.add_argument("--change-point", default="Example change point", help="Change point description.")
    parser.add_argument("--time", default="2026-06-01", help="Change point date.")
    parser.add_argument("--horizon", default="P24M", help="Simulation horizon.")
    parser.add_argument("--domain", default="mixed", help="Simulation domain.")
    parser.add_argument("--depth", default="medium", choices=["shallow", "medium", "deep"])
    parser.add_argument("--profile", default="standard", choices=["quick", "standard", "deep"], help="Execution and research budget profile.")
    parser.add_argument("--out-dir", help="Output base directory outside the installed skill. Defaults to ./simulation-output.")
    parser.add_argument("--force", action="store_true", help="Allow using an existing workspace directory.")
    args = parser.parse_args()
    workspace = build_workspace(args)
    print(str(workspace))


if __name__ == "__main__":
    main()
