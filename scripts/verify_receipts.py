from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from _lib import load_json
from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.packets import verify_receipt_chain


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify actor execution receipt chain.")
    parser.add_argument("--receipts", required=True, help="JSON file with receipts array")
    parser.add_argument("--research-id", required=True)
    parser.add_argument("--roleplay-id", required=True)
    parser.add_argument("--hmac-key-env", default="ALEPH_RECEIPT_KEY")
    parser.add_argument("--allow-unsigned", action="store_true", help="Diagnostic only; cannot support verified assurance")
    args = parser.parse_args()
    path = Path(args.receipts).resolve()
    if not path.is_file():
        print("ERROR: receipts file missing", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    data = load_json(path)
    receipts = data if isinstance(data, list) else data.get("receipts") or []
    key_text = os.environ.get(args.hmac_key_env)
    result = verify_receipt_chain(
        receipts,
        research_id=args.research_id,
        roleplay_id=args.roleplay_id,
        hmac_key=key_text.encode("utf-8") if key_text else None,
        require_hmac=not args.allow_unsigned,
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
