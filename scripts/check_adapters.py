from __future__ import annotations

import argparse
import json

from _lib import skill_root
from aleph import EXIT_OK, EXIT_SEMANTIC
from aleph.adapters_registry import check_adapter_drift, write_generated_adapters


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or regenerate portable adapters.")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = skill_root()
    if args.generate:
        result = write_generated_adapters(root)
        print(json.dumps(result, indent=2))
        raise SystemExit(EXIT_OK)
    result = check_adapter_drift(root)
    print(json.dumps(result, indent=2))
    raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
