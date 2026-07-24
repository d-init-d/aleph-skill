# Causal edge protocol

Edges are directed causal hypotheses. Every edge must be traceable and auditable.

## Required edge fields

```json
{
  "id": "causal:from-verb-to",
  "from": "node:id",
  "to": "node:id",
  "relation": "increases | decreases | enables | prevents | amplifies | dampens",
  "sign": 1,
  "base_strength": 0.0,
  "confidence": 0.0,
  "mechanism": "",
  "lag_distribution": {
    "type": "triangular",
    "min": "P1M",
    "mode": "P3M",
    "max": "P12M"
  },
  "context_modifiers": [],
  "evidence": [],
  "status": "proposed"
}
```

## Transform selection

- Use `linear` for proportional effects. Use `elasticity` only when `input_effect` is a log-change; formula 2.1 converts `base_strength * input_effect` with `expm1`.
- Use `identity` for direct passthrough without a strength coefficient.
- Use `logistic` for a bounded S-shaped response; set `transform_parameters.midpoint` and positive `steepness` when defaults `0` and `1` are not appropriate.
- Use `threshold` with `mode: above|below|deadband|hysteresis`. Declare `theta_on` and `theta_off` for hysteresis; numeric parameters may use scalar distributions.
- Use optional positive `saturation` when the final edge contribution needs a symmetric hardening cap.
- For edges into a stock, use `integration: rate` for a per-day flow integrated by the timestep or `integration: impulse` for an instantaneous increment. Flow→stock defaults to `rate`.

## Mechanism test

Before admitting an edge, answer:

1. What is the transmission channel?
2. How does the change physically, socially, institutionally, financially, or psychologically reach the target?
3. Why is the relation causal rather than merely correlated?
4. When does the effect arrive? If accumulation or fade matters, is the target declared as a stock with `decay_rate`/`retention` and the edge integration mode?
5. Which contexts amplify or dampen it?

If any answer is missing, mark the edge `incomplete` and do not use it as a strong propagation path.

## Strength scoring

Use conservative defaults:

- 0.90-0.99: accounting identity or near-mechanical relation.
- 0.70-0.89: direct mechanical or institutional link.
- 0.50-0.69: well-established empirical channel.
- 0.30-0.49: moderate empirical channel.
- 0.15-0.29: indirect or second-order channel.
- 0.05-0.14: theoretical or weak channel.

Avoid `1.0`.

## Confidence caps

- One weak source: cap at 0.30.
- One credible source: cap at 0.50.
- Two or three independent credible sources: cap at 0.70.
- Multiple independent sources plus historical validation: cap at 0.85.
- Passing backtest in Aleph: cap at 0.90.

New unbacktested AI-proposed edges should normally stay at or below 0.50.

## Context modifiers

Each modifier needs:

```json
{
  "context": "context:id",
  "multiplier": 1.0,
  "rationale": "why the context changes this edge"
}
```

Use caps to avoid runaway context multiplication: below 0.1 or above 3.0 requires a warning.

## Lag distributions

Use:

- `fixed` for accounting or immediate institutional effects,
- `triangular` when timing has a plausible minimum/mode/maximum,
- `uniform` when timing is uncertain within a range,
- `truncated_exponential` for uncertain hazard-like arrival time bounded by the declared range.

Aleph samples arrival lag and supports explicit stock accumulation with timestep-invariant exponential decay. Declare a limitation when the scenario needs an arbitrary non-exponential memory kernel.

## Admission decision

Admit an edge into propagation only if:

- it passes the mechanism test,
- it has at least one evidence or assumption reference,
- strength and confidence are both present,
- lag is explicit,
- status is not `deprecated`,
- every unsupported premise is explicitly labeled as an assumption or simulation.
