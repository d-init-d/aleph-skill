from __future__ import annotations

import argparse
import json
from pathlib import Path

from _lib import common_aleph_candidates, first_existing, print_json, run_command
from preflight import REQUIRED_ALEPH_FILES, inspect_aleph


def locate_aleph(path: str | None) -> Path | None:
    if path:
        candidate = Path(path).expanduser().resolve()
        return candidate if candidate.exists() else candidate
    return first_existing(common_aleph_candidates())


def command_check(args: argparse.Namespace) -> int:
    aleph = locate_aleph(args.aleph)
    report = inspect_aleph(aleph)
    report["required_files"] = REQUIRED_ALEPH_FILES
    print_json(report)
    return 0 if report.get("ready") else 1


def command_validate(args: argparse.Namespace) -> int:
    aleph = locate_aleph(args.aleph)
    report = inspect_aleph(aleph)
    if not aleph or not report.get("ready"):
        print_json({"status": "fail", "reason": "Aleph repo is not ready", "aleph": report})
        return 1
    command = ["python", "scripts/validate.py", "--paths", args.paths, "--strict"]
    result = run_command(command, cwd=aleph, timeout=args.timeout)
    print_json({"status": "pass" if result["ok"] else "fail", "command_result": result})
    return 0 if result["ok"] else 1


def command_run_scenario(args: argparse.Namespace) -> int:
    aleph = locate_aleph(args.aleph)
    report = inspect_aleph(aleph)
    if not aleph or not report.get("ready"):
        print_json({"status": "fail", "reason": "Aleph repo is not ready", "aleph": report})
        return 1
    scenario = Path(args.scenario).resolve()
    if not scenario.exists():
        print_json({"status": "fail", "reason": f"Scenario file not found: {scenario}"})
        return 1
    command = ["python", "scripts/run_scenario_v2.py", "--scenario", str(scenario)]
    if args.out:
        command.extend(["--out", str(Path(args.out).resolve())])
    if args.dry_run:
        print_json({"status": "dry-run", "cwd": str(aleph), "command": command})
        return 0
    result = run_command(command, cwd=aleph, timeout=args.timeout)
    print_json({"status": "pass" if result["ok"] else "fail", "command_result": result})
    return 0 if result["ok"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge helper for the external Aleph repo.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Check Aleph repo readiness.")
    check.add_argument("--aleph", help="Local Aleph repository path.")
    check.set_defaults(func=command_check)

    validate = subparsers.add_parser("validate", help="Run Aleph strict validation.")
    validate.add_argument("--aleph", help="Local Aleph repository path.")
    validate.add_argument("--paths", default="kb", help="Aleph path passed to validate.py.")
    validate.add_argument("--timeout", type=int, default=120)
    validate.set_defaults(func=command_validate)

    scenario = subparsers.add_parser("run-scenario", help="Run or dry-run Aleph scenario v2.")
    scenario.add_argument("--aleph", help="Local Aleph repository path.")
    scenario.add_argument("--scenario", required=True, help="Scenario YAML/JSON file.")
    scenario.add_argument("--out", help="Output path.")
    scenario.add_argument("--dry-run", action="store_true", help="Print command without executing.")
    scenario.add_argument("--timeout", type=int, default=300)
    scenario.set_defaults(func=command_run_scenario)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
