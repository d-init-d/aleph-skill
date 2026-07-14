# Reporting contract

Use this structure for final simulation reports.

## Required professional-report sections

1. Executive summary.
2. Methodology, adaptive complexity, and temporal framing.
3. Baseline world state and change point.
4. Evidence and source-quality assessment.
5. Causal architecture and propagation.
6. Timeline branch distribution and likelihood mode.
7. Future monitoring and likelihood updates for prospective/hybrid work.
8. Human decision points, including research/roleplay separation.
9. Sensitivity, contradictions, and limitations.
10. Validation and audit.
11. Source appendix.
12. Warnings and next steps.

## Branch table

Include:

| Branch | Relative weight / calibrated probability | Summary | Key divergence | Evidence confidence |
|---|---:|---|---|---:|

Name the likelihood mode. Uncalibrated weights must be labeled `relative_weight`, never probability. Calibrated probabilities must cite the calibration policy, hindcast report, sample count, interval, model hash, and config hash.

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

- "Under the relative-weight model, this branch receives..."
- "Under the cited calibration policy, this branch is estimated at..."
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

Before calling the report complete, run final validation with `--require-report`, replay, finalization, receipt verification, and assurance evaluation. A diagnostic score is informative only and cannot override a failed hard gate or elevate `experimental|limited` output to `verified|calibrated`.
