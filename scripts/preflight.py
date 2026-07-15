from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

from _lib import skill_root
from aleph.component_registry import COMPONENT_URI, verify_component_lock
from aleph.discovery import discover_d_research
from aleph.packs import validate_all_packs


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    allow_external = bool(getattr(args, "allow_external", False))
    external = getattr(args, "external_d_research", None) or getattr(args, "d_research", None)
    if external and str(external).strip() == COMPONENT_URI:
        allow_external = False
        explicit: Path | str | None = COMPONENT_URI
    elif external:
        explicit = Path(str(external)).expanduser().resolve()
    else:
        explicit = None
    d_research = discover_d_research(
        explicit=explicit,
        allow_external=allow_external,
        require_bundled=not allow_external,
        skill_root=skill_root(),
    )
    verification = verify_component_lock(skill_root=skill_root())
    python_ready = sys.version_info >= (3, 10)
    numpy_ok = importlib.util.find_spec("numpy") is not None
    scipy_ok = importlib.util.find_spec("scipy") is not None
    packs = validate_all_packs(skill_root())

    recommendations: list[str] = []
    if d_research.get("status") == "unavailable":
        recommendations.append(
            "Bundled D Research unavailable. Continue limited if host research tools exist; do not fabricate ledgers."
        )
    elif d_research.get("status") == "incompatible":
        recommendations.append(
            "D Research failed identity/version checks. Refuse integration until a genuine compatible 3.x install is selected."
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
            "resolved_path": d_research.get("resolved_path"),
            "source": d_research.get("source"),
            "source_kind": d_research.get("source_kind"),
            "package_major": d_research.get("package_major"),
            "compatible": d_research.get("compatible"),
            "identity_verified": d_research.get("identity_verified"),
            "package_version": d_research.get("package_version"),
            "status": d_research.get("status"),
            "component_uri": d_research.get("component_uri"),
            "component_binding": d_research.get("component_binding"),
            "component_lock_sha256": d_research.get("component_lock_sha256"),
            "component_tree_sha256": d_research.get("component_tree_sha256"),
            "lock_ok": verification.ok,
            "lock_error": verification.error_code,
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
    parser = argparse.ArgumentParser(description="Preflight checks for Aleph Skill 2.1.")
    parser.add_argument("--d-research", help="Deprecated alias; prefer bundled component or --external-d-research.")
    parser.add_argument(
        "--external-d-research",
        help="Explicit external D Research path; requires --allow-external.",
    )
    parser.add_argument("--allow-external", action="store_true", help="Permit external D Research discovery.")
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
