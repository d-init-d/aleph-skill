# Branch management

Branching is mandatory for timeline simulation because uncertainty compounds.

## Minimum branch set

Produce at least:

- most likely branch,
- plausible alternative branch,
- stress or downside branch,
- optional black-swan branch when low-probability high-impact paths exist.

If the user asks for a deterministic historical replay, provide one trace and state that branching was intentionally disabled.

## Branch triggers

Create branches at:

- uncertain causal edges,
- human decision points,
- threshold crossings,
- context activation,
- feedback-loop dominance,
- contested evidence,
- exogenous shocks that materially alter the outcome.

## Probability rules

- Probabilities must sum to `1.0`.
- The largest branch should not exceed `0.60` for open future or counterfactual work.
- Black-swan branches usually sit between `0.05` and `0.15`.
- If probabilities are qualitative, state the basis and mark confidence lower.

## Pruning

Prune a branch when:

- effect size stays below threshold,
- the path relies on an inadmissible edge,
- the branch duplicates another branch after merging,
- evidence is too thin and the branch is not useful as a warning scenario.

Record pruned branches in the audit if they were plausible enough to consider.

## Merging

Merge branches when different paths converge to the same end state. Preserve the distinct causal traces as alternatives under that branch.

## Branch object

Each branch should include:

```json
{
  "name": "branch name",
  "probability": 0.0,
  "summary": "",
  "causal_trace": [],
  "key_decision_points": [],
  "end_state": {},
  "evidence_ids": [],
  "confidence": 0.0,
  "warnings": []
}
```

## Sensitivity

Report which assumptions would move probability mass between branches. Highlight brittle edges and actor decisions.
