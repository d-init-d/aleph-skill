from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.finalize import finalize_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Atomically finalize workspace with receipts.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        print("ERROR: workspace not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    result = finalize_workspace(ws)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
