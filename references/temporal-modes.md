# Temporal modes

Infer the mode from the change-point date, observation cutoff, and simulation end. Do not ask the user to select a mode when the dates make it determinable.

## Retrospective counterfactual

Condition: `simulation_end <= observation_cutoff`.

Use when asking how a past change would alter a later historical state. Reconstruct the observed baseline at the change point, apply the divergence, and propagate to the requested historical end. Use observed post-change history only for contrast, mechanism calibration, and backtesting; actors in the simulated branch cannot know it in advance.

## Prospective intervention

Condition: `change_point >= observation_cutoff` and `simulation_end > observation_cutoff`.

Use when changing the present or a scheduled future action and projecting consequences. Treat facts through the observation cutoff as the shared baseline. Every post-cutoff state is `simulation` or `counterfactual`, never `fact`.

Each branch must include:

- an end time and end state,
- observable leading indicators,
- disconfirming conditions,
- probability-update triggers,
- sensitivity to intervention magnitude and actor response.

Do not call a branch a forecast certainty. State the observation cutoff prominently.

## Hybrid projection

Condition: `change_point < observation_cutoff < simulation_end`.

Use when a past divergence creates an alternate present and the user also wants its future. Split the work into two linked segments:

1. reconstruct the alternate timeline from change point to observation cutoff;
2. treat that alternate present as the baseline for prospective branches beyond the cutoff.

Do not silently import facts from the observed post-divergence world into the alternate present. Every retained real-world fact must have a mechanism explaining why it survives the divergence.

## Timeline labels

Every node declares one label:

- `shared_baseline`: observed before the divergence and available to all branches;
- `observed_baseline`: real-world contrast/calibration data that is not part of the simulated branch;
- `simulated_branch`: counterfactual or projected state produced by the model.

No node after the observation cutoff may be labeled `fact`. A `simulated_branch` node is never a fact even when it resembles observed history.

## Temporal knowledge

Actors know only information available and accessible at their roleplay `knowledge_cutoff`. For future projections, update relative weights or calibrated probabilities through a new simulation run; do not retroactively rewrite what actors knew.
