# Propagation engine

This skill uses Aleph-style propagation. The formula is a modeling convention, not a guarantee.

## Per-hop formula

```text
effect(A -> B) =
  change_in_A
  * sign
  * base_strength
  * context_multiplier
  * confidence
  * time_decay
  * noise_sample
  * saturation
```

Definitions:

- `change_in_A`: normalized perturbation from the source node.
- `sign`: `1` for positive/increasing relations, `-1` for negative/decreasing relations.
- `base_strength`: estimated effect size at peak lag.
- `context_multiplier`: product of active context modifiers.
- `confidence`: reliability of the edge.
- `time_decay`: lag-dependent arrival and fade.
- `noise_sample`: Monte Carlo uncertainty, usually centered near `1.0`.
- `saturation`: diminishing returns for extreme perturbations.

## Trace requirement

Every propagated hop should record:

- `time`,
- `from`,
- `to`,
- `input_change`,
- `sign`,
- `base_strength`,
- `context_multiplier`,
- `confidence`,
- `time_decay`,
- `noise`,
- `saturation`,
- `output_effect`,
- `lag_applied`,
- `mechanism`,
- `evidence_ids`,
- `amplification_ratio`,
- `butterfly_pattern`.

Use `templates/propagation-trace.jsonl`.

## Amplification patterns

Flag these patterns:

- Cascade: multiple high-confidence hops create compounding effects.
- Threshold breach: a factor crosses a level that unlocks new effects.
- Feedback loop: a cycle reinforces or stabilizes the perturbation.
- Context creation: a propagated effect activates a new context.
- Human catalyst: an actor response creates disproportionate downstream effects.

## Cycle handling

For positive feedback cycles:

- allow at most three full cycles before damping,
- apply damping after the cap,
- report the cap and sensitivity.

For negative feedback cycles:

- allow convergence,
- report stabilizing nodes and equilibrium direction.

## Threshold handling

When a factor crosses a threshold:

1. record the crossing,
2. create or activate resulting event/context nodes,
3. branch if the threshold value is uncertain,
4. lower confidence if the threshold is weakly sourced.

## Monte Carlo guidance

Use multiple runs when the graph has many uncertain edges or human decision points. Cluster end states into branches. If no deterministic engine is available, approximate manually but state that clustering was qualitative.

## Warning conditions

Add warnings for:

- extreme perturbations above normalized magnitude `2.0`,
- context multipliers above `3.0`,
- confidence below `0.30` on critical edges,
- roleplay-driven paths without external evidence,
- missing observed-history validation,
- circular amplification that dominates the result.
