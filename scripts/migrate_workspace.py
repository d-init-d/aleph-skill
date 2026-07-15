from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.migrate import (
    bind_bundled_d_research,
    migrate_dual_run_canonical,
    migrate_workspace,
    plan_migration,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Aleph workspace (schema 2.0) and bind bundled D Research.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", help="Sibling output directory (default: <source>-v2)")
    parser.add_argument("--check", action="store_true", help="Plan only; do not mutate source")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--backup-dir", help="Required for --in-place")
    parser.add_argument("--dual-run", action="store_true", help="Run twice and compare canonical output")
    parser.add_argument(
        "--dual-run-research",
        action="store_true",
        help="Alias for dual-run with research ledger awareness",
    )
    parser.add_argument(
        "--bind-bundled-d-research",
        action="store_true",
        help="Rewrite absolute D Research paths to aleph-component://d-research when equivalent",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_dir():
        print("ERROR: source not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    if args.bind_bundled_d_research:
        result = bind_bundled_d_research(source, check_only=args.check)
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)

    if args.dual_run or args.dual_run_research:
        out_a = Path(args.out).resolve() if args.out else source.parent / f"{source.name}-v2a"
        out_b = source.parent / f"{source.name}-v2b-dual"
        result = migrate_dual_run_canonical(source, out_a, out_b)
        print(json.dumps(result, indent=2))
        raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)

    if args.check:
        result = plan_migration(source)
        result["mode"] = "check"
        print(json.dumps(result, indent=2))
        raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)

    result = migrate_workspace(
        source,
        Path(args.out).resolve() if args.out else None,
        check_only=False,
        in_place=args.in_place,
        backup_dir=Path(args.backup_dir).resolve() if args.backup_dir else None,
    )
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
