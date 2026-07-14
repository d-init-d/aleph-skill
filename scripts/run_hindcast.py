from __future__ import annotations

import argparse
import json
from pathlib import Path

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.io import canonical_hash, load_json_secure, write_json_atomic
from aleph.issues import Issue
from aleph.packs import evaluate_hindcast_case
from aleph.paths import output_alias_issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute a pre-cutoff, model-backed hindcast case.")
    parser.add_argument("--case", required=True)
    parser.add_argument("--policy", help="precommitted calibration policy JSON")
    parser.add_argument("--out", help="write a hashed hindcast report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    case_path = Path(args.case).resolve()
    if not case_path.is_file():
        print(json.dumps({"ok": False, "error": "case not found"}, indent=2))
        raise SystemExit(EXIT_USAGE)
    case, case_issues = load_json_secure(case_path)
    policy = None
    policy_issues: list[Issue] = []
    if args.policy:
        policy, policy_issues = load_json_secure(Path(args.policy).resolve())
    if case_issues or policy_issues:
        problems = [value.to_dict() for value in [*case_issues, *policy_issues]]
        print(json.dumps({"ok": False, "issues": problems}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)
    if not isinstance(case, dict) or (policy is not None and not isinstance(policy, dict)):
        print(json.dumps({"ok": False, "error": "case/policy must be JSON objects"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)
    result = evaluate_hindcast_case(case, policy=policy)
    result["case_hash"] = canonical_hash(case)
    if policy is not None:
        result["policy_hash"] = canonical_hash(policy)
    result["report_hash"] = canonical_hash(result)
    if args.out:
        output_path = Path(args.out).resolve()
        inputs = [case_path]
        if args.policy:
            inputs.append(Path(args.policy).resolve())
        alias_issues = output_alias_issues(output_path, inputs)
        if alias_issues:
            print(json.dumps({"ok": False, "issues": [value.to_dict() for value in alias_issues]}, indent=2))
            raise SystemExit(EXIT_USAGE)
        write_json_atomic(output_path, result)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(EXIT_OK if result.get("ok") else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
