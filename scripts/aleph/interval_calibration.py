"""Causal rolling split-conformal prediction interval calibration.

Calibrates distribution-free prediction intervals strictly from prior out-of-sample
forecast residuals whose official release timestamp does not exceed each target's
forecast origin (zero lookahead), with optional Gibbs-Candes Adaptive Conformal
Inference (ACI) updates for non-stationary / volatility-clustering regimes.
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from aleph.calibration_lineage import utc
from aleph.io import canonical_hash, load_json_secure

DEFAULT_LEVELS: tuple[float, ...] = (0.80, 0.90, 0.95)


def parse_levels(raw: str | Sequence[float]) -> tuple[float, ...]:
    """Parse and validate confidence coverage levels in (0, 1)."""
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",") if part.strip()]
        values = [float(item) for item in items]
    else:
        if any(isinstance(item, bool) for item in raw):
            raise ValueError("coverage levels must be numbers")
        values = [float(item) for item in raw]
    if not values:
        raise ValueError("at least one confidence level is required")
    cleaned: list[float] = []
    for lvl in values:
        if not math.isfinite(lvl) or not (0.0 < lvl < 1.0):
            raise ValueError(f"coverage level must lie strictly in (0, 1), got {lvl!r}")
        cleaned.append(lvl)
    return tuple(sorted(set(cleaned)))


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def causal_conformal_quantile(residuals: Sequence[float], level: float) -> tuple[float, bool]:
    """Compute finite-sample conformal quantile ceil(level * (n + 1)) / n.

    Returns (quantile, finite_sample_saturated). When ceil(level * (n + 1)) > n,
    returns the maximum historical residual cleanly (no non-finite JSON values)
    and flags saturated=True.
    """
    if not residuals:
        raise ValueError("at least one residual is required for conformal quantile")
    ordered = sorted(float(r) for r in residuals)
    n = len(ordered)
    rank = math.ceil(float(level) * (n + 1))
    if rank <= 0:
        return 0.0, False
    if rank <= n:
        return ordered[rank - 1], False
    return ordered[-1], True


def load_evaluation_cases(evaluation_dir: Path) -> list[dict[str, Any]]:
    """Load hindcast cases from an evaluate_point_forecasts output directory."""
    hindcast_dir = evaluation_dir / "hindcast"
    if not hindcast_dir.is_dir():
        raise ValueError(f"missing hindcast directory under {evaluation_dir}")
    case_files = sorted(hindcast_dir.glob("case-*.json"))
    if not case_files:
        raise ValueError(f"no hindcast case-*.json files found in {hindcast_dir}")
    cases: list[dict[str, Any]] = []
    for path in case_files:
        payload, _ = load_json_secure(path)
        if not isinstance(payload, dict):
            raise ValueError(f"invalid hindcast case JSON object: {path.name}")
        cases.append(payload)
    return cases


def _level_key(lvl: float) -> str:
    if lvl == round(lvl, 2):
        return f"{lvl:.2f}"
    return str(lvl)


def calibrate_conformal_intervals(
    cases: Sequence[dict[str, Any]],
    *,
    levels: Sequence[float] = DEFAULT_LEVELS,
    window: int = 12,
    aci_gamma: float = 0.05,
    min_history: int = 1,
) -> dict[str, Any]:
    """Calibrate strictly causal prediction intervals across chronological cases."""
    sorted_levels = parse_levels(levels)
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError("window must be an integer >= 1")
    if isinstance(aci_gamma, bool) or not isinstance(aci_gamma, (int, float)) or not math.isfinite(float(aci_gamma)) or not (0.0 <= float(aci_gamma) <= 0.5):
        raise ValueError("aci_gamma must lie in [0, 0.5]")
    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history must be an integer >= 1")

    parsed_rows: list[tuple[datetime, datetime, str, dict[str, Any], float, float]] = []
    case_ids: set[str] = set()
    for item in cases:
        cid = str(item.get("case_id") or "")
        if not cid:
            raise ValueError("each case requires a non-empty case_id")
        if cid in case_ids:
            raise ValueError(f"duplicate case_id: {cid}")
        case_ids.add(cid)
        origin_dt = utc(item.get("forecast_origin"))
        release_dt = utc(item.get("official_release_date"))
        if release_dt <= origin_dt:
            raise ValueError(f"case {cid} official_release_date must post-date forecast_origin")
        pred = _finite_number(item.get("point_prediction"), f"{cid}.point_prediction")
        actual = _finite_number(item.get("actual_value"), f"{cid}.actual_value")
        parsed_rows.append((origin_dt, release_dt, cid, dict(item), pred, actual))

    parsed_rows.sort(key=lambda r: (r[0], str(r[3].get("target_period") or ""), r[2]))
    if len(parsed_rows) < 2:
        raise ValueError("at least 2 out-of-sample cases are required for causal interval calibration")

    alpha_state: dict[float, float] = {lvl: round(1.0 - lvl, 10) for lvl in sorted_levels}
    cov_counts: dict[str, dict[str, Any]] = {
        _level_key(lvl): {
            "target": lvl,
            "hits": 0,
            "evaluated": 0,
            "sum_width": 0.0,
            "post_warmup_hits": 0,
            "post_warmup_evaluated": 0,
        }
        for lvl in sorted_levels
    }

    interval_rows: list[dict[str, Any]] = []
    # Pick representative p05/p95 level (0.90 if present, else closest to 0.90)
    p90_lvl = min(sorted_levels, key=lambda lv: (abs(lv - 0.90), -lv))

    pending_updates: list[tuple[datetime, int, dict[float, bool]]] = []
    for idx, (origin_dt, release_dt, cid, raw_case, pred, actual) in enumerate(parsed_rows):
        while pending_updates and pending_updates[0][0] <= origin_dt:
            _, _, coverage_by_level = heapq.heappop(pending_updates)
            for lvl, covered in coverage_by_level.items():
                error = 0.0 if covered else 1.0
                alpha = alpha_state[lvl] + aci_gamma * ((1.0 - lvl) - error)
                alpha_state[lvl] = max(0.0, min(1.0, alpha))

        eligible_residuals: list[float] = []
        for j in range(idx):
            _j_origin, j_release, _j_cid, _j_raw, j_pred, j_actual = parsed_rows[j]
            if j_release <= origin_dt:
                eligible_residuals.append(abs(j_actual - j_pred))
        calib_pool = eligible_residuals[-window:]
        m_cnt = len(calib_pool)
        actual_resid = abs(actual - pred)

        row_levels: dict[str, Any] = {}
        calibrated = m_cnt >= min_history
        if calibrated:
            coverage_by_level = {}
            prev_q = 0.0
            for lvl in sorted_levels:
                lvl_key = _level_key(lvl)
                eff_level = max(0.0, min(1.0, 1.0 - alpha_state[lvl]))
                q, saturated = causal_conformal_quantile(calib_pool, eff_level)
                # Enforce non-crossing nested bounds across ascending levels
                if q < prev_q:
                    q = prev_q
                prev_q = q
                lower = pred - q
                upper = pred + q
                covered = lower <= actual <= upper
                width = upper - lower

                cov_counts[lvl_key]["evaluated"] += 1
                cov_counts[lvl_key]["sum_width"] += width
                if covered:
                    cov_counts[lvl_key]["hits"] += 1
                if m_cnt >= 5:
                    cov_counts[lvl_key]["post_warmup_evaluated"] += 1
                    if covered:
                        cov_counts[lvl_key]["post_warmup_hits"] += 1

                coverage_by_level[lvl] = covered

                row_levels[lvl_key] = {
                    "lower": round(lower, 6),
                    "upper": round(upper, 6),
                    "radius": round(q, 6),
                    "width": round(width, 6),
                    "covered": covered,
                    "saturated": saturated,
                }

            if aci_gamma > 0.0:
                heapq.heappush(pending_updates, (release_dt, idx, coverage_by_level))

        p90_key = _level_key(p90_lvl)
        p05 = row_levels[p90_key]["lower"] if calibrated else pred
        p95 = row_levels[p90_key]["upper"] if calibrated else pred
        interval_rows.append(
            {
                "case_id": cid,
                "forecast_origin": raw_case.get("forecast_origin"),
                "target_period": raw_case.get("target_period"),
                "point_prediction": pred,
                "actual_value": actual,
                "residual": round(actual_resid, 6),
                "calibration_count": m_cnt,
                "calibrated": calibrated,
                "p05": round(p05, 6),
                "p50": round(pred, 6),
                "p95": round(p95, 6),
                "levels": row_levels,
            }
        )

    coverage: dict[str, Any] = {}
    for lvl in sorted_levels:
        lvl_key = _level_key(lvl)
        stats = cov_counts[lvl_key]
        ev = stats["evaluated"]
        pw_ev = stats["post_warmup_evaluated"]
        coverage[lvl_key] = {
            "target": lvl,
            "empirical_coverage": round(stats["hits"] / ev, 6) if ev else None,
            "empirical": round(stats["hits"] / ev, 6) if ev else None,
            "covered_count": stats["hits"],
            "evaluated_count": ev,
            "mean_width": round(stats["sum_width"] / ev, 6) if ev else None,
            "post_warmup_empirical": round(stats["post_warmup_hits"] / pw_ev, 6) if pw_ev else None,
            "post_warmup_count": pw_ev,
        }

    bundle: dict[str, Any] = {
        "contract_version": "aleph-conformal-intervals-1",
        "status": "pass",
        "method": "causal_rolling_split_conformal_aci" if aci_gamma > 0.0 else "causal_rolling_split_conformal",
        "levels": list(sorted_levels),
        "window": window,
        "aci_gamma": float(aci_gamma),
        "min_history": min_history,
        "causal_cutoff_enforced": True,
        "case_count": len(parsed_rows),
        "coverage": coverage,
        "intervals": interval_rows,
    }
    bundle["bundle_hash"] = canonical_hash(bundle)
    return bundle
