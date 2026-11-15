"""Turn-key historical-cutoff offline backtest CLI combining causal point forecasting and conformal intervals."""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.interval_calibration import (
    calibrate_conformal_intervals,
    load_evaluation_cases,
    parse_levels,
)
from aleph.io import ResourceLimitError, canonical_hash, load_json_secure, write_json_atomic
from evaluate_point_forecasts import evaluate as evaluate_points


def _next_month_release(ym: str) -> str:
    year, month = int(ym[:4]), int(ym[5:7])
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    return f"{next_year:04d}-{next_month:02d}-10T13:30:00Z"


def _write_series_csv_from_monthly(
    rows: list[dict[str, Any]], column: str, dst: Path,
) -> tuple[int, bool]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    estimated_dates = False
    with dst.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["period", "value", "official_release_date"])
        for row in rows:
            val = row.get(column)
            ym = row.get("ym") or row.get("period")
            if val is None or not ym:
                continue
            ym_str = str(ym)
            period = ym_str if len(ym_str) > 7 else f"{ym_str}-01"
            release_date = row.get("official_release_date")
            estimated_dates |= not bool(release_date)
            rel = str(release_date or _next_month_release(ym_str[:7]))
            writer.writerow([period, float(val), rel])
            count += 1
    return count, estimated_dates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Input CSV (period,value,official_release_date) or monthly JSON archive")
    parser.add_argument("--out-dir", type=Path, required=True, help="Fresh output directory for offline backtest evidence")
    parser.add_argument("--series", type=str, default="brent,cpi_u,fedfunds", help="Comma-separated columns when input is multi-series JSON")
    parser.add_argument("--model", choices=["momentum", "damped_trend", "mean_reversion", "adaptive"], default="adaptive")
    parser.add_argument("--fit-window", type=int, default=8)
    parser.add_argument("--levels", type=str, default="0.80,0.90,0.95")
    parser.add_argument("--window", type=int, default=12)
    parser.add_argument("--gamma", type=float, default=0.05)
    args = parser.parse_args(argv)

    src = args.data.resolve()
    out_dir = args.out_dir.resolve()
    if not src.is_file():
        print(json.dumps({"ok": False, "error": f"input file not found: {src}"}))
        return EXIT_USAGE
    if out_dir.exists() and any(out_dir.iterdir()):
        print(json.dumps({"ok": False, "error": f"out-dir must be new or empty: {out_dir}"}))
        return EXIT_USAGE

    try:
        levels = parse_levels(args.levels)
        series_inputs: list[tuple[str, Path, bool]] = []
        if src.suffix.lower() == ".csv":
            series_inputs.append((src.stem, src, False))
        else:
            payload, issues = load_json_secure(src)
            if issues or not isinstance(payload, dict) or not isinstance(payload.get("monthly"), list):
                raise ValueError("JSON input must be an object containing a 'monthly' array")
            cols = [c.strip() for c in args.series.split(",") if c.strip()]
            if not cols:
                raise ValueError("at least one series column is required for multi-series JSON")
            if any(
                not re.fullmatch(r"[A-Za-z0-9_-]+", col)
                or col.lower() == "inputs"
                or PureWindowsPath(f"{col}.csv").is_reserved()
                for col in cols
            ) or len({col.lower() for col in cols}) != len(cols):
                raise ValueError("series names must be unique, portable output names")
            staging_csv_dir = out_dir / "inputs"
            for col in cols:
                csv_path = staging_csv_dir / f"{col}.csv"
                cnt, estimated_dates = _write_series_csv_from_monthly(payload["monthly"], col, csv_path)
                if cnt >= 5:
                    series_inputs.append((col, csv_path, estimated_dates))
            if not series_inputs:
                raise ValueError("no requested series had at least 5 non-null observations")

        results: dict[str, Any] = {}
        all_passed = True
        for name, csv_path, estimated_dates in series_inputs:
            eval_dir = out_dir / name
            point_report = evaluate_points(
                csv_path,
                eval_dir,
                model_type=args.model,
                fit_window=args.fit_window,
            )
            cases = load_evaluation_cases(eval_dir)
            intervals = calibrate_conformal_intervals(
                cases,
                levels=levels,
                window=args.window,
                aci_gamma=args.gamma,
            )
            write_json_atomic(eval_dir / "conformal-intervals.json", intervals)
            passed = point_report.get("status") == "pass" and intervals.get("status") == "pass"
            all_passed = all_passed and passed
            metrics = point_report.get("metrics") or {}
            cand = metrics.get("candidate_metrics") or {}
            base = metrics.get("baseline_metrics") or {}
            results[name] = {
                "ok": passed,
                "case_count": len(cases),
                "release_date_basis": "includes_estimates" if estimated_dates else "input",
                "execution_verified": bool((point_report.get("lineage") or {}).get("execution_verified")),
                "beats_baseline": bool(metrics.get("beats_baseline")),
                "candidate_mae": cand.get("mae"),
                "candidate_rmse": cand.get("rmse"),
                "baseline_mae": base.get("mae"),
                "baseline_rmse": base.get("rmse"),
                "coverage": intervals.get("coverage"),
                "interval_bundle_hash": intervals.get("bundle_hash"),
            }

        summary: dict[str, Any] = {
            "contract_version": "aleph-offline-backtest-1",
            "ok": all_passed,
            "model": args.model,
            "fit_window": args.fit_window,
            "levels": [round(lvl, 4) for lvl in levels],
            "window": args.window,
            "aci_gamma": args.gamma,
            "offline_provisional_notice": (
                "Retrospective computational replay only; external vintage/preregistration review "
                "must be completed before empirical calibration claims."
            ),
            "series": results,
        }
        summary["report_hash"] = canonical_hash(summary)
        write_json_atomic(out_dir / "backtest-summary.json", summary)
    except (ResourceLimitError, ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return EXIT_SEMANTIC

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return EXIT_OK if all_passed else EXIT_SEMANTIC


if __name__ == "__main__":
    raise SystemExit(main())
