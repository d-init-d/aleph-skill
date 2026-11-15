# Offline Historical-Cutoff Backtest & Conformal Calibration Guide

Aleph provides deterministic retrospective evaluation and conformal interval calibration for timestamped CSVs and monthly JSON archives. The temporal gate uses the availability timestamps in the input.

## 1. Point Forecast Evaluation (`evaluate_point_forecasts.py`)

Evaluates genuine Aleph compiled causal models (`compile_model` + `run_deterministic`) across chronological out-of-sample origins where every input observation satisfies `max(official_release_date, vintage_date) <= forecast_origin`:

```bash
python3 "$ALEPH_SKILL_ROOT/scripts/evaluate_point_forecasts.py" \
  --data path/to/series.csv \
  --out /tmp/eval_brent \
  --model adaptive \
  --fit-window 8
```

Available `--model` choices:
- `adaptive`: Causal Ridge-regularized AR(1) first-difference shrinkage with strictly pre-cutoff regime screening against mean reversion. Recommended default for commodities, macro rates, and price series.
- `damped_trend`: Damped 1-step change propagation.
- `mean_reversion`: Trailing window deviation model for stationary series.
- `momentum`: Legacy trailing 3-difference mean rule (retained for backward compatibility).

## 2. Causal Conformal Prediction Intervals (`calibrate_intervals.py`)

Produces finite-sample distribution-free prediction intervals (`p05` / `p50` / `p95`) and empirical coverage statistics for target coverage levels (`0.80,0.90,0.95`) strictly from past residuals with `official_release_date <= forecast_origin`, using rolling Adaptive Conformal Inference (ACI) to preserve coverage under volatility shifts:

```bash
python3 "$ALEPH_SKILL_ROOT/scripts/calibrate_intervals.py" \
  --evaluation-dir /tmp/eval_brent \
  --levels 0.80,0.90,0.95 \
  --out /tmp/eval_brent/conformal-intervals.json
```

## 3. Turn-Key Batch Backtest (`run_offline_backtest.py`)

Runs both causal point forecasting and conformal interval calibration across a CSV or multi-series archive (e.g., `packs/economics/data/us_macro_2024_2026.json`):

```bash
python3 "$ALEPH_SKILL_ROOT/scripts/run_offline_backtest.py" \
  --data "$ALEPH_SKILL_ROOT/packs/economics/data/us_macro_2024_2026.json" \
  --series brent,cpi_u,fedfunds \
  --model adaptive \
  --out-dir /tmp/macro_backtest
```

Use timestamped CSV inputs with the documented release and vintage times for point-in-time analysis. The bundled macro sample uses an estimated next-month release date where timestamps are absent. The batch summary reports that fallback as `release_date_basis: includes_estimates`. The bundled economics hindcasts are retrospective reproducibility fixtures.

Outputs record causal lineage digests (`model_hash`, `result_hash`, `bundle_hash`, `report_hash`) and keep offline/retrospective provenance transparency explicit (historical vintage and preregistration remain subject to independent host review).
