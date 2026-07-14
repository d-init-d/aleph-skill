from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _lib import skill_root, write_json
from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.validator import validate_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Aleph simulation workspace artifacts.")
    parser.add_argument("--workspace", help="Simulation workspace directory.")
    parser.add_argument("--mode", default="final", choices=["draft", "final"])
    parser.add_argument("--require-report", action="store_true")
    parser.add_argument("--write-report", action="store_true", help="Write validation-report.json")
    parser.add_argument("--json", action="store_true", help="Machine-readable stdout")
    parser.add_argument("--examples", action="store_true", help="Validate package self-check only")
    args = parser.parse_args()

    if args.examples:
        # package structural self-check proxy
        root = skill_root()
        ok = (root / "SKILL.md").is_file() and (root / "scripts" / "aleph" / "validator.py").is_file()
        result = {"status": "pass" if ok else "fail", "package": str(root)}
        print(json.dumps(result, indent=2))
        raise SystemExit(EXIT_OK if ok else EXIT_SEMANTIC)

    if not args.workspace:
        print("ERROR: --workspace required", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    workspace = Path(args.workspace).resolve()
    result = validate_workspace(workspace, mode=args.mode, require_report=args.require_report or args.mode == "final")
    if args.write_report:
        write_json(workspace / "validation-report.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") != "pass":
        raise SystemExit(EXIT_SEMANTIC)
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
