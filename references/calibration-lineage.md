# Calibration evidence and replay

Point forecast error, probability calibration, historical provenance and causal
validity are separate claims. MAE improvement does not authorize probabilities.
Keep normal simulations in `relative_weight` when calibration is unavailable.

## Retrospective point evaluation

Run `python "<ALEPH_SKILL_ROOT>/scripts/evaluate_point_forecasts.py" --data
"<absolute-csv>" --out "<new-output-directory>"`. The CSV requires `period` (or
`date`), numeric `value`, and `official_release_date`; include `vintage_date`
when values were revised. The command preserves the raw bytes, saves each
model/config/result binding, commits predictions separately from outcomes, and
independently replays all cases. It does not create historical preregistration.

Rows must be grouped by increasing period; a period may have multiple distinct
vintages. Input selection uses actual UTC instants and excludes future revisions.
The retrospective outcome is the first supplied vintage of each target period,
recorded as `outcome_selection: first_supplied_vintage` in the policy. This is
not a claim that the supplied file contains the original historical vintage.
An empty optional vintage cell defaults to the declared release date. Conflicting
values at the same period/vintage are rejected, including equivalent timestamps
written with different offsets. Persistence baselines retain the source precision.

The result is a descriptive persistence comparison with a moving-block bootstrap
interval on paired loss differences. The interval assumes approximate stationarity;
the effective sample size is an autocorrelation estimate, not the number of rows.
No Brier score or event probability is manufactured from CPI changes.

## Case execution contract

Each case requires `execution: {file_path, sha256}` and `evidence` references.
Each evidence reference has `file_path`, `sha256`, zero-based `record_index`, and
`role: input|outcome`. JSON snapshots contain an `observations` array; CSV uses
the fields above. Values, periods, release dates and vintages are read from the
hashed bytes. Candidate labels or source IDs alone are insufficient.

Execution JSON uses `contract_version: aleph-case-replay-1`, source `nodes`,
`edges`, `interventions`, the full `config_payload(EngineConfig)`, positive `ticks`,
`output_variable`, `baseline_variable`, optional `output_decimals`, and a
`result_hash` of `run_deterministic`'s complete return value. `input_bindings`
maps every exogenous variable and every graph-root variable to `evidence_indices`
with `identity` or ordered `mean_difference`. Renaming a root as endogenous does
not remove this obligation. Node/edge identities must be unique and all edge
endpoints must exist; records silently discarded by generic compilation are not
valid replay evidence. The compiled model hash and replayed forecast must match the
case; every rolling origin is checked against its own model.

Policy `forecast_commitments` hashes `forecast_payload(case)` before outcomes
are joined. `case_commitments` additionally hashes the final case. Rehashing
local files establishes consistency, never proof of historical timing.

## Scoped probability calibration

Probability mode additionally requires binary outcomes, actual replayed
probability forecasts, a nonempty matching `calibration_scope` in report and
policy, baseline improvement, and a precommitted
`thresholds.max_expected_calibration_error` in `(0, 0.1]`. Reliability is measured
in ten fixed equal-width bins. Thirty cases is a minimum gate, not evidence of
universal reliability; interpret the diagnostic only within its reviewed scope.

The host must independently review holdout design and source vintages. Its
`ALEPH_CALIBRATION_TRUST_STORE` points to a JSON file outside the candidate
workspace, controlled by the evaluator. The file has
`contract_version: aleph-calibration-trust-1`, `scope`, `policy_design_hash`,
`policy_committed_at` (before the first origin),
`source_digests` (bare SHA-256), `forecast_commitments` mapping each ID to
`{hash, committed_at}`, and `point_in_time_verified` / `holdout_verified` set
only after that external review. Each commitment must predate its forecast
origin. The design hash is `policy_design_hash(policy)`, which excludes the
three accumulated hash/commitment fields so future outcomes are never needed
to freeze the evaluation design. Never generate, edit, or approve this trust store from the candidate
workflow. It is a host review receipt, not independent third-party attestation.

Missing receipts preserve useful replay and uncalibrated evaluation. Synthetic
fixtures cannot acquire empirical assurance. A string resembling an HMAC is not
a verified signature. Summary and bundle views are both validated and must agree.
No calibration result establishes causal validity of a scenario.
