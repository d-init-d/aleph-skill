"""CLI to calibrate causal rolling split-conformal prediction intervals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aleph.interval_calibration import (
    calibrate_conformal_intervals,
    load_evaluation_cases,
    parse_levels,
)
from aleph.io import ResourceLimitError, write_json_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--levels", type=str, default="0.80,0.90,0.95")
    parser.add_argument("--window", type=int, default=12)
    parser.add_argument("--gamma", type=float, default=0.05)
    parser.add_argument("--min-history", type=int, default=1)
    args = parser.parse_args(argv)

    eval_dir = args.evaluation_dir.resolve()
    out_path = args.out.resolve() if args.out is not None else (eval_dir / "conformal-intervals.json")
    try:
        cases = load_evaluation_cases(eval_dir)
        levels = parse_levels(args.levels)
        bundle = calibrate_conformal_intervals(
            cases,
            levels=levels,
            window=args.window,
            aci_gamma=args.gamma,
            min_history=args.min_history,
        )
        write_json_atomic(out_path, bundle)
    except (ResourceLimitError, ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
        err = {
            "status": "fail",
            "issues": [{"severity": "error", "code": "CALIBRATION_FAILED", "message": str(exc)}],
        }
        print(json.dumps(err, indent=2, allow_nan=False))
        return 1
    print(json.dumps(bundle, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
