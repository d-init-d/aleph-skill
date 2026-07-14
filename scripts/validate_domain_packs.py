from __future__ import annotations

import argparse
import json
from pathlib import Path

from _lib import skill_root
from aleph import EXIT_OK, EXIT_SEMANTIC
from aleph.packs import validate_all_packs


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate bundled domain packs.")
    parser.add_argument("--root", help="Skill root (default: package root)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else skill_root()
    result = validate_all_packs(root)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
