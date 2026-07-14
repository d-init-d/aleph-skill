# Branch likelihood and calibration

Create branches at uncertain mechanisms, actor decisions, thresholds, context changes, feedback dominance, contested evidence, and exogenous shocks. Keep materially distinct end states; merge duplicates while preserving their traces. Include a central, plausible alternative, and stress branch unless deterministic replay is explicitly required.

## Likelihood modes

Use exactly one mode per ledger:

- `relative_weight`: default for uncalibrated scenarios. Weights are non-negative and normalized for comparison but are not probabilities.
- `calibrated_probability`: allowed only when a domain/decision calibration policy, model/config hashes, evidence snapshot, sufficient hindcast cases, calibration metrics, sample count, and intervals all pass.
- `deterministic`: one replay trace; do not invent branch likelihood.

Never convert roleplay confidence or evidence confidence into branch probability. The main adjudicator may use evidence and base rates to assign relative weights. A roleplay execution assigns neither.

## Branch object

Each branch records ID/name, likelihood mode, `relative_weight` or calibrated `probability`, summary, trace, decision points, end state, indicators, disconfirming conditions, evidence IDs, uncertainty/warnings, derivation, trace hash, and unresolved mass.

Numerical branch ledgers use exactly one derivation contract:

- `analyst_authored` means an adjudicator created the scenario interpretation. Every branch binds the actual propagation-trace digest and must leave `representative_run`, `engine_cluster_id`, and `member_count` absent or null. It must not imply that an engine emitted the narrative branch.
- `engine_derived` means the ledger is a direct rendering of numerical output. A deterministic run has exactly one branch bound to `run:0` with weight `1.0`. A Monte Carlo ledger covers every returned cluster exactly once and matches its cluster ID, representative run, member count, relative weight, trace digest, and unresolved mass.

Do not mix derivations in one numerical ledger. Qualitative draft ledgers may omit numerical binding fields until an executable run exists, but final numerical validation requires them.

Do not silently renormalize after invalid/nonconvergent runs. Report valid and unresolved mass. Future/hybrid branches require observable update triggers and a new simulation when observations change; never retroactively change what an actor knew.

Prune only duplicates, inadmissible paths, or effects below a declared threshold. Record meaningful pruned/stress paths in the audit.
