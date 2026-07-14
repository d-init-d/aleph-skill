from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, cast

from _lib import write_json
from aleph.formula import amplification_ratio
from aleph.io import load_json_secure, load_jsonl_secure
from aleph.paths import resolve_in_workspace
from aleph.schema import is_number
from aleph.validator import validate_workspace


def read_trace(path: Path) -> list[dict[str, Any]]:
    rows, issues = load_jsonl_secure(path)
    if issues:
        raise ValueError(json.dumps({"status": "fail", "issues": [item.to_dict() for item in issues]}))
    return rows


def classify_ratio(value: float) -> str:
    if value < 1.0:
        return "dampening"
    if value < 2.0:
        return "proportional"
    if value < 5.0:
        return "butterfly amplification"
    if value < 10.0:
        return "strong butterfly effect"
    return "extreme butterfly effect"


def score(rows: list[dict[str, Any]], initial_change: float | None = None) -> dict[str, Any]:
    if not rows:
        return {"status": "fail", "reason": "empty trace"}
    initial = initial_change if initial_change is not None else rows[0].get("input_change", rows[0].get("input_effect"))
    if not is_number(initial):
        return {"status": "fail", "reason": "initial change must be a finite number", "code": "TYPE"}
    base = abs(float(cast(int | float, initial)))
    if base == 0:
        base = 1.0
    ratios: list[float] = []
    patterns: Counter[str] = Counter()
    max_row: dict[str, Any] | None = None
    max_ratio = -1.0
    for row in rows:
        # Always recompute from output_effect / base — do not trust artifact amplification
        if not is_number(row.get("output_effect")):
            return {"status": "fail", "reason": f"row {len(ratios) + 1} output_effect is not finite", "code": "NON_FINITE"}
        out = float(row["output_effect"])
        ratio = amplification_ratio(out, base, 1.0)
        if not math.isfinite(ratio):
            return {"status": "fail", "reason": "non-finite amplification", "code": "NON_FINITE"}
        ratios.append(float(ratio))
        pattern = row.get("butterfly_pattern")
        if pattern:
            patterns[str(pattern)] += 1
        if ratio > max_ratio:
            max_ratio = float(ratio)
            max_row = row
    return {
        "status": "pass",
        "steps": len(rows),
        "max_amplification_ratio": max_ratio,
        "classification": classify_ratio(max_ratio),
        "mean_amplification_ratio": sum(ratios) / len(ratios),
        "patterns": dict(patterns),
        "max_step": max_row,
        "recomputed": True,
        "warnings": ["Extreme amplification requires extra evidence"] if max_ratio >= 10.0 else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score butterfly amplification from a propagation trace.")
    parser.add_argument("--trace", help="Path to propagation-trace.jsonl (absolute or with --workspace).")
    parser.add_argument("--workspace", required=True, help="Workspace whose trace has passed semantic replay.")
    parser.add_argument("--initial-change", type=float, help="Override initial normalized change.")
    parser.add_argument("--out", help="Optional JSON output path.")
    args = parser.parse_args()
    ws = Path(args.workspace).resolve()
    validation = validate_workspace(ws, mode="draft", require_report=False)
    trace_check = (validation.get("check_results") or {}).get("trace") or {}
    if trace_check.get("status") != "pass":
        result = {
            "status": "fail",
            "code": "REPLAY_MISMATCH",
            "reason": "butterfly scoring requires a replay-pass trace",
            "issues": trace_check.get("issues", []),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        raise SystemExit(1)
    manifest, manifest_issues = load_json_secure(ws / "simulation-manifest.json")
    if manifest_issues or not isinstance(manifest, dict):
        result = {
            "status": "fail",
            "code": "INVALID_ARTIFACT",
            "issues": [value.to_dict() for value in manifest_issues],
        }
        print(json.dumps(result, indent=2))
        raise SystemExit(1)
    declared = (manifest.get("artifact_paths") or {}).get("propagation_trace", "propagation-trace.jsonl")
    requested = args.trace or declared
    if requested != declared:
        result = {"status": "fail", "code": "PATH_ESCAPE", "reason": "--trace must match manifest propagation_trace"}
        print(json.dumps(result, indent=2))
        raise SystemExit(3)
    path, issues = resolve_in_workspace(ws, requested, must_exist=True, require_file=True)
    if issues or path is None:
        print(json.dumps({"status": "fail", "issues": [i.to_dict() for i in issues]}, indent=2))
        raise SystemExit(3)
    result = score(read_trace(path), args.initial_change)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.out:
        write_json(Path(args.out).resolve(), result)
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
