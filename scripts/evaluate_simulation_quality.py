from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from _lib import write_json
from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.quality import evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Aleph workspace diagnostic quality and assurance tier.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", help="Optional JSON output path.")
    parser.add_argument("--threshold", type=float, default=90.0)
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--actor-receipt-hmac-key-env", default="ALEPH_RECEIPT_KEY")
    parser.add_argument("--d-research-hmac-key-env", default="D_RESEARCH_LEDGER_KEY")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print("ERROR: workspace not found", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    if not 0.0 <= args.threshold <= 100.0:
        print("ERROR: --threshold must be within [0, 100]", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    actor_key = os.environ.get(args.actor_receipt_hmac_key_env)
    research_key = os.environ.get(args.d_research_hmac_key_env)
    result = evaluate(
        workspace,
        actor_receipt_hmac_key=actor_key.encode("utf-8") if actor_key else None,
        d_research_hmac_key=research_key.encode("utf-8") if research_key else None,
    )
    if args.out:
        write_json(Path(args.out).resolve(), result)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.enforce and (
        result.get("validation_status") != "pass"
        or result.get("assurance_status") == "failed"
        or result.get("diagnostic_score", 0) < args.threshold
    ):
        raise SystemExit(EXIT_SEMANTIC)
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
