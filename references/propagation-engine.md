# Propagation engine

Aleph formula 2.1 uses auditable discrete equations while retaining formula 2.0 replay for existing run contracts. New artifacts emit `formula_version: 2.1.0`; a run and trace may never mix formula versions. Formula 2.0 replay preserves its legacy level equation: a legacy `scale: stock|flow` label does not acquire formula 2.1 decay, retention, or rate-integration semantics. Rebuild the workspace under formula 2.1 before using those dynamics; do not add 2.1-only fields to a legacy run. The numerical result is a modeling convention, not a factual guarantee.

## Level equation

For each tick, `level` and `flow` variables start from their declared baseline. A `stock` carries its prior end-of-step state through timestep-invariant decay, then receives impulses and integrated rates:

```text
retention_factor = exp(-decay_rate_per_day * timestep_days)
# equivalent retention form: retention_per_day ** timestep_days

level_or_flow[t+1] = bounds(baseline + interventions + sum(edge_output))

stock[t+1] = bounds(retention_factor * stock[t]
                    + interventions
                    + sum(impulse_edge_output)
                    + timestep_days * sum(rate_edge_output))
```

`do(set)` interventions replace the target and block incoming effects while active. A stock `set` declares `release_policy: retain|reset_baseline`; omitted policy is canonical `retain` for compatibility. `add` and `multiply` interventions apply on every active tick. Bounds are applied last to the computed end-of-step state.

For a manifest `change_point` with a numeric `magnitude`, the value is an
intervention delta, not the desired final level: the first step starts from the
node's declared baseline and applies that delta. To request a final level,
use an explicit `set` operation in `interventions.json` (or document the
baseline and delta separately). This distinction is part of the compiled
model hash and should never be inferred from prose after the run.

The engine never carries prior state implicitly. Cross-tick accumulation happens only for `scale: stock`. Prefer non-negative `decay_rate`; optional `retention` is a per-day coefficient in `[0,1]`. Either field may be a scalar distribution, but a node cannot declare both. An edge into a stock uses `integration: rate|impulse`; flow→stock defaults to `rate`, while other stock inputs default to `impulse`.

## Edge semantics

- `sign` must be exactly `1` or `-1`.
- `base_strength` is the deterministic reference effect.
- `effect_distribution` is sampled once per Monte Carlo run; deterministic execution always uses `base_strength` and never substitutes a distribution midpoint or mode. `identity` rejects an effect distribution because identity ignores strength.
- Effect distributions fail compilation unless their kind and finite parameters are complete and ordered (`uniform min < max`, `triangular min <= mode <= max`, `normal sd > 0`). Invalid distributions never fall back to `base_strength`.
- `lag_distribution` is preserved in the compiled model and sampled once per edge and run. Deterministic runs use `fixed`, the triangular mode, the uniform midpoint, or the truncated-exponential mean.
- ISO-8601 lags use the supported duration subset and are rounded up to days, then divided by the positive `timestep` (days per tick) with ceiling. Invalid durations fail compilation rather than becoming zero-lag edges.
- `existence_prob` controls edge admission per Monte Carlo run.
- Evidence confidence is epistemic metadata. It is not silently multiplied into the numerical effect.

Supported artifact and engine transforms are:

- `linear`: proportional response;
- `elasticity`: formula 2.1 treats the input as log-change and returns `expm1(base_strength * input)` before sign/context; formula 2.0 replays the legacy linear interpretation;
- `identity`: passes the source through with sign and context but without `base_strength`;
- `logistic`: bounded centered response with optional `midpoint` and positive `steepness`;
- `threshold`: supports `mode: above|below|deadband|hysteresis`. Hysteresis requires `theta_on >= theta_off >= 0` and advances its latch exactly once per tick after convergence.

Optional edge `saturation` applies a final symmetric tanh cap to every transform.

## Cycles

Zero-lag strongly connected components use a relaxed Jacobi fixed-point solve. Convergence is decided from the unrelaxed equation residual, never from the smaller relaxation step. Convergence tolerances, relaxation, and iteration limits are part of the hashed execution configuration. A component that does not converge is a numerical hard failure; Aleph does not return its state as a valid branch.

Delayed cycles are resolved through emission snapshots. A delayed effect captures its source value when emitted and is delivered after the sampled lag.

## Monte Carlo

Counter-addressed RNG binds every draw to the seed, run ID, node/edge ID, and purpose. It samples strength, lag, existence, decay/retention, and numeric transform parameters with distinct purposes. Length-prefixed typed fields prevent ambiguous counter addresses. Worker count does not alter the run hash.

Invalid runs are excluded from branch clusters and remain unresolved simulation mass. The run fails when the invalid fraction exceeds its configured hard gate. Valid relative-weight mass plus unresolved mass must equal one; this accounting identity does not turn uncalibrated weights into probabilities. Branch signatures use baseline-relative magnitude regimes and add initial-state-aware peak/trough plus timestep-integrated exposure for stocks, so materially different same-sign paths are not collapsed and equivalent physical horizons do not split merely because tick resolution changed.

## Trace requirement

Every material propagated hop records its source and target, input effect, sign, strength, context multiplier, noise, output effect, lag, mechanism, evidence IDs, and hash-chain linkage. The saved run contract binds the raw trace SHA-256 and positive row count. Replay requires both to match before formula replay; an absent, empty, or substituted trace fails. Formula replay recomputes values from source artifacts; it never trusts an artifact-provided amplification ratio.

The numerical trace is an authored, auditable input to a run, not a placeholder
that the engine silently invents. Replace the illustrative row copied by
workspace initialization with rows derived from the admitted edges and the
addressed run plan before calling `run_simulation.py`; the CLI deliberately
fails closed when the trace is missing or does not bind to the resulting
trajectory. A host may provide a trace generator, but it must preserve the
same row schema, hash chain, run references, and execution-binding checks.

Numerical traces have a stronger contract than qualitative traces. Every numerical row identifies exactly one bounded `run:N` sample, the source tick, source state, target state, and sampled edge strength. Execution binding v2 additionally binds formula version, sampled lag, resolved transform parameters, target scale/retention, rate integration factor, integrated effect, and the per-run stock dynamics hash. Binding v1 remains readable only for formula-2.0 run contracts. Rehashing a fabricated but internally consistent row is therefore insufficient.

Manifest-declared paths are authoritative for nodes, edges, the compiled model, run ledger, replay report, and propagation trace. CLI output overrides must resolve to the same non-aliased file as the declared artifact path.

OAT sensitivity perturbations are clamped to declared parameter bounds and report the actual perturbed values and deltas. Boundary parameters therefore use one-sided or zero-distance contrasts rather than silently evaluating an invalid parameter value.

## Warning conditions

Add warnings for:

- extreme normalized perturbations,
- context multipliers above the declared domain range,
- weak evidence on critical edges,
- roleplay-driven paths without external evidence,
- missing hindcast validation,
- non-convergent or dominant feedback,
- requested arbitrary non-exponential memory behavior that the discrete engine cannot represent.
