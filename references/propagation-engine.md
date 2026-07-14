# Propagation engine

Aleph 2.0 uses auditable discrete level equations. The numerical result is a modeling convention, not a factual guarantee.

## Level equation

For each tick, every variable starts from its declared baseline. Aleph then applies active interventions, delayed emissions due at that tick, and zero-lag causal effects:

```text
target[t] = baseline[target]
          + intervention[target, t]
          + sum(saturate(transform(source[t - sampled_lag])
                         * sign * sampled_strength * context_multiplier))
```

`do(set)` interventions replace the target level and block incoming effects while active. `add` and `multiply` interventions apply on every active tick. When an intervention ends, the target returns to the level implied by the baseline and active causal inputs.

The engine does not implicitly carry a previous level into the next tick. That prevents a static input from creating accidental linear growth. Stock/flow integration, decay kernels, and continuous-time dynamics are outside the 2.0 engine contract and must not be claimed from a level-model run.

## Edge semantics

- `sign` must be exactly `1` or `-1`.
- `base_strength` is the deterministic reference effect.
- `effect_distribution` is sampled once per Monte Carlo run.
- Effect distributions fail compilation unless their kind and finite parameters are complete and ordered (`uniform min < max`, `triangular min <= mode <= max`, `normal sd > 0`). Invalid distributions never fall back to `base_strength`.
- `lag_distribution` is preserved in the compiled model and sampled once per edge and run. Deterministic runs use `fixed`, the triangular mode, the uniform midpoint, or the truncated-exponential mean.
- ISO-8601 lags use the supported duration subset and are rounded up to days, then divided by the positive `timestep` (days per tick) with ceiling. Invalid durations fail compilation rather than becoming zero-lag edges.
- `existence_prob` controls edge admission per Monte Carlo run.
- Evidence confidence is epistemic metadata. It is not silently multiplied into the numerical effect.

Supported transforms are `linear`, `elasticity`, `identity`, and `logistic` in the engine API. Shipped causal-edge artifacts currently admit `linear` and `elasticity`.

## Cycles

Zero-lag strongly connected components use a relaxed Jacobi fixed-point solve. Convergence is decided from the unrelaxed equation residual, never from the smaller relaxation step. Convergence tolerances, relaxation, and iteration limits are part of the hashed execution configuration. A component that does not converge is a numerical hard failure; Aleph does not return its state as a valid branch.

Delayed cycles are resolved through emission snapshots. A delayed effect captures its source value when emitted and is delivered after the sampled lag.

## Monte Carlo

Counter-addressed RNG binds every draw to the seed, run ID, edge ID, and purpose. Length-prefixed typed fields prevent ambiguous counter addresses. Worker count does not alter the run hash.

Invalid runs are excluded from branch clusters and remain unresolved simulation mass. The run fails when the invalid fraction exceeds its configured hard gate. Valid relative-weight mass plus unresolved mass must equal one; this accounting identity does not turn uncalibrated weights into probabilities.

## Trace requirement

Every material propagated hop records its source and target, input effect, sign, strength, context multiplier, noise, output effect, lag, mechanism, evidence IDs, and hash-chain linkage. The saved run contract binds the raw trace SHA-256 and positive row count. Replay requires both to match before formula replay; an absent, empty, or substituted trace fails. Formula replay recomputes values from source artifacts; it never trusts an artifact-provided amplification ratio.

Numerical traces have a stronger contract than qualitative traces. Every numerical row must identify exactly one bounded `run:N` sample, the source tick, source state, target state, and sampled edge strength. The execution-binding validator independently reconstructs that run and requires the row to match the engine trajectory, active intervention window, sampled edge admission, lag, and zero-noise engine semantics. The run ledger stores the resulting execution-binding digest, and replay recomputes it. Rehashing a fabricated but internally consistent row is therefore insufficient.

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
- requested stock/flow behavior that the level engine cannot represent.
