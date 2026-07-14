# Artifact contract 2.0

Use schema version `2.0.0`. Initialize from the bundled templates, keep every artifact inside the user workspace, and reject unknown fields. Migrate `1.2.0` workspaces to a sibling destination; never edit legacy input in place.

A migration is a loss-aware conversion, not a verification claim. `migration-report.json` and `manifest.migration.unresolved_fields` must enumerate every issue that still fails 2.0 draft validation. Re-run research, roleplay, numerical execution, and finalization as required; never treat a successfully copied tree as a validated 2.0 simulation.

## Invariants

- IDs are non-empty, unique, typed, and resolve to declared objects.
- Numbers are finite JSON numbers, never strings, `NaN`, or infinity.
- Artifact paths are workspace-relative regular files; absolute, drive, UNC, traversal, and symlink escape paths fail.
- Every persisted computational input has a SHA-256 and version. Every final artifact is indexed and receipt-bound.
- `fact`, `inference`, `assumption`, `simulation`, and `counterfactual` are epistemic labels, not interchangeable prose.
- `evidence_confidence` is separate from effect size and probability.

## Portable capability vocabulary

When a CLI, IDE, adapter, receipt, or conformance probe requests machine-readable capability values, emit the exact JSON values below. Do not translate, capitalize, shorten, coerce, or replace them with natural-language synonyms. These tokens summarize the normative rules in this contract and the referenced protocols; they do not relax any validation gate.

| Capability key | Canonical JSON value |
|---|---|
| `prospective_temporal_mode` | `"prospective_intervention"` |
| `computed_post_cutoff_label` | `"simulation"` |
| `uncalibrated_likelihood_mode` | `"relative_weight"` |
| `material_roleplay_input` | `"sealed_packet_only"` |
| `roleplay_may_emit_probability` | `false` |
| `numerical_trace_requires_execution_binding` | `true` |
| `invalid_monte_carlo_mass_may_be_renormalized` | `false` |
| `diagnostic_score_may_override_hard_gate` | `false` |
| `level_engine_implicitly_claims_stock_flow_dynamics` | `false` |
| `d_research_compatible_major` | `"3.x"` |
| `may_claim_single_certain_future` | `false` |

Use `"pass"` as a machine-readable overall result only when every applicable schema, semantic, privacy, numerical, calibration, integrity, and receipt hard gate passes; otherwise use `"fail"`. `sealed_packet_only` means one frozen, hash-bound temporal packet is the only material roleplay input. It does not permit the dossier, evidence ledger, excluded claim content, browsing, or external tools.

## Core artifacts

`simulation-manifest.json` declares temporal frame, change point, scope, execution state, simulation/likelihood modes, assurance tier, artifacts, assumptions, and hashes. The manifest is not completed until all hard gates pass.

`evidence-map.csv` contains atomic claims with source URL, tier, dates, access/retrieval methods, excerpt/value, evidence confidence, contradiction state, and notes. D Research raw provenance remains separately preserved.

`nodes.json` uses typed entity, event, factor, context, indicator, claim, and source nodes. Facts require evidence and cannot postdate the observation cutoff.

`edges.json` admits only declared transforms with mechanism, endpoints, relation/sign, effect parameter, evidence confidence, lag distribution, context modifiers, evidence or assumption, and status.

`actors.json` contains public-role dossiers. Every material actor declares a decision graph, sourced research claims, a sealed packet hash, at least two evidence-free roleplay hypotheses, and adjudicator-owned relative weights/calibrated likelihood.

`human-track-ledger.jsonl` contains exactly one research and one roleplay row per material actor. Rows bind agent/execution IDs, timestamps, hashed input/output, receipt chain, attestation class, and optional receipt artifact path.

`branch-ledger.json` declares one likelihood mode. Uncalibrated runs use normalized `relative_weight`; calibrated runs may use probability only with model/config hashes, calibration policy, hindcast report, sample count, and interval. Numerical ledgers declare one branch derivation: analyst-authored branches bind the trace but claim no run metadata; engine-derived branches exactly bind deterministic `run:0` or every Monte Carlo cluster.

`simulation-run.json` binds the compiled model hash, normalized execution configuration, numerical result hash, a `trace_contract` containing the workspace-relative trace path, raw SHA-256, and positive row count, and a `trace_execution_binding` digest produced by independently matching every trace row to the engine trajectory. Replay must reject missing, empty, changed, or self-consistent-but-fabricated trace content.

Hindcast evidence uses an order-independent canonical snapshot digest. A precommitted calibration policy maps each case ID to a commitment hash binding the cutoff, model hash, configuration hash, tick count, evidence snapshot, target IDs, and baselines. A boolean `precommitted` flag without a matching case commitment is not policy-locked and cannot support calibrated maturity.

`propagation-trace.jsonl` is ordered, formula-versioned, hash-chained, and replayable. It retains sample references and temporal endpoints. Numerical rows additionally bind one `run:N`, source/target states, source tick, and sampled strength to the reconstructed run.

`validation-report.json`, `quality-report.json`, artifact index, and final receipts record hard-gate results. Diagnostic quality score cannot elevate assurance.

## Human seal

Roleplay receives a packet, not a dossier or raw evidence ledger. Each admitted claim has valid `available_at` and established actor access no later than the cutoff. Excluded content stays outside the packet. Roleplay emits no evidence, facts, probability, confidence, or relative weight and can choose only decision-graph actions.

## Completion

Run draft validation, computational execution, replay, report rendering, finalization, final validation, and receipt verification. A completed manifest or high score does not override a failed schema, semantic, privacy, numerical, calibration, or integrity gate.

Workspace initialization deliberately creates a coherent `experimental` draft with a non-material example actor, an empty human-track ledger, and pending adaptive assessment. It is a structurally valid starting point, not completed research, roleplay, or assurance evidence.
