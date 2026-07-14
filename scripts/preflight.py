from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

from _lib import skill_root
from aleph.discovery import discover_d_research
from aleph.packs import validate_all_packs


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    explicit = Path(args.d_research).expanduser().resolve() if args.d_research else None
    d_research = discover_d_research(explicit=explicit)
    python_ready = sys.version_info >= (3, 10)
    numpy_ok = importlib.util.find_spec("numpy") is not None
    scipy_ok = importlib.util.find_spec("scipy") is not None
    packs = validate_all_packs(skill_root())

    recommendations: list[str] = []
    if d_research.get("status") == "unavailable":
        recommendations.append(
            "D Research not found. Ask once to install d-research-skill; continue limited if declined."
        )
    elif d_research.get("status") == "incompatible":
        recommendations.append(
            "Configured D Research failed identity/version checks. Refuse integration until a genuine compatible 3.x install is selected."
        )
    if not numpy_ok:
        recommendations.append("NumPy absent: Sobol and scientific extras degraded; qualitative/deterministic remain available.")

    d_research_incompatible = d_research.get("status") == "incompatible" or (
        d_research.get("status") == "available" and not d_research.get("compatible")
    )
    status = "pass" if python_ready and not d_research_incompatible else "fail"
    assurance_cap = "limited"
    if python_ready and d_research.get("status") == "available" and d_research.get("compatible") is True and packs.get("all_validated"):
        assurance_cap = "verified"  # capability ceiling, not automatic claim
    if not numpy_ok:
        # scientific soft-degrade does not crash
        pass

    return {
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "ready": python_ready,
            "requires": ">=3.10",
        },
        "extras": {
            "numpy": numpy_ok,
            "scipy": scipy_ok,
        },
        "d_research": {
            "found": d_research.get("status") in {"available", "incompatible"},
            "ready": d_research.get("status") == "available" and d_research.get("compatible") is True,
            "path": d_research.get("path"),
            "source": d_research.get("source"),
            "package_major": d_research.get("package_major"),
            "compatible": d_research.get("compatible"),
            "identity_verified": d_research.get("identity_verified"),
            "package_version": d_research.get("package_version"),
            "status": d_research.get("status"),
        },
        "domain_packs": {
            "ok": packs.get("ok"),
            "all_validated": packs.get("all_validated"),
            "count": packs.get("count"),
        },
        "capabilities": {
            "simulation_modes": ["qualitative", "deterministic"] + (["monte_carlo"] if True else []),
            "sensitivity": ["oat", "morris", "conditional"] + (["sobol"] if numpy_ok else []),
            "assurance_cap": assurance_cap,
        },
        "recommendations": recommendations,
        "status": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for Aleph Skill 2.0.")
    parser.add_argument("--d-research", help="Local D Research skill path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only on stdout.")
    args = parser.parse_args()

    report = build_report(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("Aleph Skill preflight", file=sys.stderr)
        print(f"Python: {report['python']['version']} ({'ready' if report['python']['ready'] else 'not ready'})", file=sys.stderr)
        print(f"D Research: {report['d_research']['status']}", file=sys.stderr)
        print(f"NumPy: {report['extras']['numpy']}", file=sys.stderr)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
