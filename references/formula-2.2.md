# Formula 2.2.0 Specification

`formula_version: "2.2.0"` introduces baseline-deviation and log-ratio edge input modes while preserving bit-exact `2.0.0` and `2.1.0` replay and model hashing.

## Edge Input Modes (`input_mode`)

Every causal edge resolves an effective input `x_eff` from the source node's state `x_src` and declared baseline `x_0`:

| `input_mode` | Default for | Effective Input `x_eff` | Domain Constraint |
| --- | --- | --- | --- |
| `level` | `2.0.0`, `2.1.0` | `x_src` | finite float |
| `deviation` | `2.2.0` (non-`elasticity` transforms) | `x_src - x_0` | finite float |
| `log_ratio` | `2.2.0` `elasticity` transform (or explicit opt-in) | `ln(x_src / x_0)` | `x_0 > 0` at compile time, `x_src > 0` at runtime |

Under `deviation` mode, a zero-intervention system sitting at declared baselines `x = x_0` produces `x_eff = 0` on every edge, yielding exact zero baseline drift regardless of absolute unit scale (e.g. `CPI = 300`, `FEDFUNDS = 4.5`, `VND = 1e16`).

Under `2.2.0`, any edge with `transform: "elasticity"` and omitted `input_mode` defaults to `"log_ratio"` (requiring positive `source.baseline > 0`, failing compilation cleanly otherwise) rather than `"deviation"`. Explicitly forcing `input_mode: "deviation"` on an `elasticity` edge passes raw unnormalized deltas into `expm1(coeff * x_eff)`, which can explode on non-unit scales (e.g., `+10` USD oil shock yielding `expm1(0.5 * 10) = +147.41`); both `validator.py` and `repair_model_schema.py` emit a non-fatal warning when `elasticity` edges explicitly configure `input_mode: "deviation"`. Under `2.0.0` and `2.1.0`, omitted `input_mode` still defaults to `"level"` for bit-exact replay.

Under `log_ratio` mode, relative shocks combine naturally with `elasticity` or `linear` transforms (`sign * strength * context_mult * ln(x_src / x_0)`). Compiling an edge with `log_ratio` whose source node has `baseline <= 0` fails closed; a runtime trajectory that drives a `log_ratio` source `<= 0` fails with typed numerical non-convergence (`exit_code: 4`, `invalid_mass: 1.0`).

## Intervention Operators (`op`)

Explicit interventions in `interventions.json` under `formula_version: "2.2.0"` require an explicit `"op"` (`"set"`, `"add"`, or `"multiply"`), matching `schemas/interventions.schema.json`. Legacy `2.0.0` / `2.1.0` replay preserves the historical default (`"set"` when `"op"` is omitted).

## Stock Retention Units (`retention_unit`)

Stock nodes support optional `retention_unit`:
- `per_day` (default, preserving 2.0/2.1 hashes): retention over step `dt` days is `retention ** dt` (or `exp(-decay_rate * dt)`).
- `per_tick`: retention per discrete simulation tick is `retention` directly (or `exp(-decay_rate)`), preventing accidental `retention ** 30` collapse on monthly (`timestep: "P1M"`) runs.

When `timestep >= 7` days (for example `P1W`, `P1M`) and a `scale: "stock"` node sets `retention` (or `decay_rate`) with omitted `retention_unit`, defaulting to `per_day` causes `retention ** dt` decay over `dt` days and scales incoming `rate` edges by `dt` (for `P1M` / `30` days, `retention = 0.9` collapses to `0.9 ** 30 ≈ 0.0424` per tick). Both `validate_simulation_artifacts` and `repair_model_schema.py` emit explicit non-fatal warnings whenever a `stock` node specifies retention/decay without `retention_unit` under `timestep >= 7` days, flagging values with `retention < 0.98` and recommending explicit `"retention_unit": "per_tick"` vs `"per_day"`.

## State Divergence Guard

`EngineConfig.divergence_threshold` (default scale-aware `1e12 * max(1, max_abs_baseline, max_abs_set_add_intervention)`) halts unbounded geometric explosions cleanly with `STATE_DIVERGENCE` (`exit_code: 4`) before floating-point overflow without tripping valid large nominal baselines (e.g. `1e16` VND).

## Contracts & Bindings

- Compiled model contract: `aleph-engine-2.2`
- Run contract: `aleph-run-2.2`
- Trace-to-engine binding: `aleph-trace-execution-binding-v3` (adds `input_mode`, `source_baseline`, `input_effect`, and optional conditional `gate_multiplier` per step)

## v2.8.0 Additive Dynamics (`initial_value` & `gate`)

- **Node `initial_value`**: Optional finite numeric starting state at `tick = 0` distinct from equilibrium reference `baseline`. Under `deviation` or `log_ratio` inputs, setting `initial_value != baseline` models ongoing pre-cutoff disequilibrium momentum without flagging `ZERO_INTERVENTION_DRIFT`.
- **Edge `gate`**: Optional conditional block (`{"op": "all" | "any" | "veto", "conditions": [{"node": "<id>", "op": "gt" | "gte" | "lt" | "lte", "value": <number>}]}`). Evaluates against `history[source_tick]` to yield `gate_multiplier` in `[0.0, 1.0]`, blocking instant SCC affine simplification whenever present.
