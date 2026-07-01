from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path
from typing import Any

from _lib import common_d_research_candidates, first_existing, print_json


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


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    d_research_path = (
        Path(args.d_research).expanduser().resolve()
        if args.d_research
        else first_existing(common_d_research_candidates())
    )
    python_ready = sys.version_info >= (3, 10)
    d_research = inspect_d_research(d_research_path)
    recommendations: list[str] = []
    if not d_research.get("ready"):
        recommendations.append(
            "D Research was not found. On skill invocation, ask the user once whether "
            "they want to install or enable d-research-skill; continue in limited mode "
            "if they decline."
        )

    return {
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "ready": python_ready,
            "requires": ">=3.10",
        },
        "d_research": d_research,
        "recommendations": recommendations,
        "status": "pass" if python_ready else "fail",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for Aleph Skill.")
    parser.add_argument("--d-research", help="Local D Research skill path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()

    report = build_report(args)
    if args.json:
        print_json(report)
    else:
        print("Aleph Skill preflight")
        print(f"Python: {report['python']['version']} ({'ready' if report['python']['ready'] else 'not ready'})")
        print(f"D Research: {'ready' if report['d_research'].get('ready') else 'not ready'}")
        for item in report["recommendations"]:
            print(f"- {item}")
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
