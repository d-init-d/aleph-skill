from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from _lib import (
    common_aleph_candidates,
    common_d_research_candidates,
    first_existing,
    print_json,
    run_command,
)


REQUIRED_ALEPH_FILES = [
    "schemas/scenario.schema.json",
    "schemas/forecast.schema.json",
    "schemas/causal-relation.schema.json",
    "schemas/entity.schema.json",
    "schemas/event.schema.json",
    "schemas/factor.schema.json",
    "scripts/run_scenario_v2.py",
    "scripts/validate.py",
    "scripts/kb_audit.py",
    "scripts/build_graph.py",
]


def inspect_aleph(path: Path | None) -> dict[str, Any]:
    if not path:
        return {"found": False, "path": None, "missing": REQUIRED_ALEPH_FILES}
    missing = [rel for rel in REQUIRED_ALEPH_FILES if not (path / rel).exists()]
    return {
        "found": path.exists(),
        "path": str(path),
        "missing": missing,
        "ready": path.exists() and not missing,
    }


def inspect_d_research(path: Path | None) -> dict[str, Any]:
    if not path:
        return {"found": False, "path": None, "ready": False}
    skill_file = path / "SKILL.md"
    return {
        "found": path.exists(),
        "path": str(path),
        "skill_file": str(skill_file),
        "ready": skill_file.exists(),
    }


def inspect_gh() -> dict[str, Any]:
    gh_path = shutil.which("gh")
    if not gh_path:
        return {"found": False, "ready": False, "message": "gh CLI not found"}
    result = run_command(
        [
            gh_path,
            "repo",
            "view",
            "d-init-d/Aleph",
            "--json",
            "nameWithOwner,isPrivate,defaultBranchRef,pushedAt,url",
        ],
        timeout=20,
    )
    return {
        "found": True,
        "path": gh_path,
        "ready": result["ok"],
        "repo_view": result,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    aleph_path = Path(args.aleph).expanduser().resolve() if args.aleph else first_existing(common_aleph_candidates())
    d_research_path = (
        Path(args.d_research).expanduser().resolve()
        if args.d_research
        else first_existing(common_d_research_candidates())
    )
    python_ready = sys.version_info >= (3, 10)
    report = {
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "ready": python_ready,
            "requires": ">=3.10",
        },
        "gh": inspect_gh(),
        "aleph": inspect_aleph(aleph_path),
        "d_research": inspect_d_research(d_research_path),
        "recommendations": [],
    }
    if not report["aleph"].get("ready"):
        report["recommendations"].append("Provide --aleph <local Aleph repo path> before running Aleph scripts.")
    if not report["d_research"].get("ready"):
        report["recommendations"].append("Install or provide d-research-skill for deep evidence ledgers.")
    if not report["gh"].get("ready"):
        report["recommendations"].append("Authenticate gh CLI if private Aleph repo access is needed.")
    report["status"] = "pass" if python_ready else "fail"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for Aleph Timeline Simulator.")
    parser.add_argument("--aleph", help="Local Aleph repository path.")
    parser.add_argument("--d-research", help="Local D Research skill path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()

    report = build_report(args)
    if args.json:
        print_json(report)
    else:
        print("Aleph Timeline Simulator preflight")
        print(f"Python: {report['python']['version']} ({'ready' if report['python']['ready'] else 'not ready'})")
        print(f"gh: {'ready' if report['gh'].get('ready') else 'not ready'}")
        print(f"Aleph: {'ready' if report['aleph'].get('ready') else 'not ready'}")
        print(f"D Research: {'ready' if report['d_research'].get('ready') else 'not ready'}")
        for item in report["recommendations"]:
            print(f"- {item}")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
