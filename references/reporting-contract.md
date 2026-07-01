# Reporting contract

Use this structure for final simulation reports.

## Required sections

1. Change point and assumptions.
2. Baseline world state.
3. Evidence ledger summary.
4. Node and causal graph summary.
5. Propagation highlights.
6. Timeline branch distribution.
7. Human decision points, including research/roleplay separation.
8. Validation and audit.
9. Warnings, uncertainty, and next steps.

## Branch table

Include:

| Branch | Probability | Summary | Key divergence | Confidence |
|---|---:|---|---|---:|

Probabilities must sum to `1.0`. If they do not, fix the branch ledger before reporting.

## Mechanism excerpts

For major effects, include short mechanism summaries with evidence IDs. Do not include every hop unless the user requests a full trace.

## Human-node appendix

For each material human actor, report:

- whether a Human Research track was completed,
- whether a Human Roleplay track was used,
- which roleplay outputs were accepted, downgraded, or rejected,
- which alternative actions became branches,
- why private or unsupported information was excluded.
- the execution mode and distinct research/roleplay agent references from `human-track-ledger.jsonl`.

## Audit appendix

Report:

- unresolved evidence gaps,
- contested claims,
- low-confidence critical edges,
- high-sensitivity assumptions,
- pruned branches,
- blocked sources,
- privacy/safety constraints,
- validation commands run.

## Language discipline

Use cautious wording:

- "This branch is estimated at..."
- "Under these assumptions..."
- "The strongest mechanism is..."
- "The weakest link is..."

Avoid:

- "This will happen."
- "This proves."
- "The person would definitely."
- "The true timeline is."

## File outputs

When writing artifacts, include paths to:

- manifest,
- evidence map,
- propagation trace,
- branch ledger,
- validation report,
- final report.

Before calling the report complete, run final validation with `--require-report` and run `scripts/evaluate_simulation_quality.py --threshold 85 --enforce`. A validator pass confirms structural/audit gates; the quality score additionally checks evidence strength, human-track execution, and efficiency.
