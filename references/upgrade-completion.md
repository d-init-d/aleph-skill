# Upgrade workflow contract

This `2.5.0` stable release pins the published D Research `3.6.0`
runtime profile to its immutable annotated tag, source commit, Git tree,
reproducible source archive and full/runtime artifact digests. CI verifies the
projection against upstream Git objects. Stable release provenance establishes the locked source identity; it does
not establish empirical calibration or live-provider availability.

Development snapshots can still use `provenance.mode = local_candidate`.
That policy verifies artifact/recipe/snapshot integrity without asserting an
upstream release. `--require-upstream` refuses such local snapshots.

## Evidence to model

`auto_causal_inducer.py propose` creates a bilingual heuristic proposal. A causal
verb is a discovery hint, not proof of causation. Missing node measurements,
units, stock retention, mechanisms, lags and effect sizes stay unknown/gaps.
Evidence confidence is separate from effect strength. Do not infer a shared
baseline for both endpoints from one number in a claim.

JSON evidence rows may supply `node_measurements`, keyed by exact node name or
node ID. Each measurement has `baseline`, `unit`, `time`, and, for stocks,
`retention`. Supply actual per-node observations, with traceable source anchors.
CSV ledgers without these fields intentionally require a reviewed enrichment.

`review` without decisions is deterministic screening, with reviewer identity
`deterministic:causal-screening`. It cannot approve execution. Complete explicit
review requires `--reviewer human:<id>` or `agent:<id>` and `--decisions` containing
`node_decisions` and `edge_decisions` for every candidate, each with a decision
and rationale. Revise and rehash the proposal when new evidence resolves gaps;
do not remove gaps merely to obtain approval. Optional reviewed `interventions`
are an explicit array in the decisions artifact, never copied from templates.

`admit`/`apply` requires a fresh or empty target. Partial review writes only
candidate artifacts, proposal/review and assumptions; it creates no executable
nodes/model/run. A ready export verifies the numerical model; the normal Aleph
research workflow must still supply evidence mapping, research receipts and all
final scenario artifacts before full final-workspace validation. Approved inputs
must have complete parameters and substantive
mechanisms. The standard disk compiler, run CLI and replay CLI must agree before
a single directory rename publishes the complete workspace. Existing traces,
models or unrelated files are refused regardless of their content.

## Simulation and roleplay

`linear_auto` can solve eligible nonsingular linear instantaneous components.
It does not guarantee dynamic stability or repair nonlinear/nonconvergent
models. Numerical errors remain errors; no hidden delay or edge pruning occurs.

Interactive roleplay binds model, engine config and spec before packet issuance
or execution. Replay checks commits, allowlists, actor/round identities, derives
interventions from declared actions, recomputes state/payoffs and checks final
receipt summaries. Empty visibility exposes no state. Hashes detect divergence;
they do not authenticate a malicious writer who can replace all artifacts.
The host remains responsible for sealed offline actors and existing human-track
research receipts. No simulation output becomes empirical evidence.

## Verification

Run from the skill root:

```bash
python3 -m unittest discover -s tests -p test_upgrade_completion.py -v
python3 scripts/lock_bundled_component.py
python3 scripts/acceptance.py
python3 scripts/release_gate.py
```

The release notes record compatibility, verification commands and operating
limits. Inspect the tagged CI and release workflow results before adoption.
