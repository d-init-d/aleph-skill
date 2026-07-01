from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from _lib import write_json


def read_trace(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
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
    base = abs(initial_change if initial_change is not None else float(rows[0].get("input_change", 1.0)))
    if base == 0:
        base = 1.0
    ratios: list[float] = []
    patterns: Counter[str] = Counter()
    max_row: dict[str, Any] | None = None
    max_ratio = -1.0
    for row in rows:
        ratio = row.get("amplification_ratio")
        if ratio is None:
            ratio = abs(float(row.get("output_effect", 0.0))) / base
        ratio = float(ratio)
        ratios.append(ratio)
        pattern = row.get("butterfly_pattern")
        if pattern:
            patterns[str(pattern)] += 1
        if ratio > max_ratio:
            max_ratio = ratio
            max_row = row
    return {
        "status": "pass",
        "steps": len(rows),
        "max_amplification_ratio": max_ratio,
        "classification": classify_ratio(max_ratio),
        "mean_amplification_ratio": sum(ratios) / len(ratios),
        "patterns": dict(patterns),
        "max_step": max_row,
        "warnings": ["Extreme amplification requires extra evidence"] if max_ratio >= 10.0 else [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score butterfly amplification from a propagation trace.")
    parser.add_argument("--trace", required=True, help="Path to propagation-trace.jsonl.")
    parser.add_argument("--initial-change", type=float, help="Override initial normalized change.")
    parser.add_argument("--out", help="Optional JSON output path.")
    args = parser.parse_args()
    result = score(read_trace(Path(args.trace).resolve()), args.initial_change)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.out:
        write_json(Path(args.out).resolve(), result)
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
