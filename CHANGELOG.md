# Changelog

## 2.4.0

### Added
- Added the additive workspace artifact schema `2.1.0` next to `2.0.0` with full dual-read validation: optional type-discriminated node `details`, namespaced node `extensions`, explicit `actor_basis` authoring, and an explicit sibling-only `--upgrade-schema-2-1` migration that is idempotent, transactional, byte-preserving, and audit-trailed. `2.0.0` remains the write default; `init_simulation_workspace.py` gains `--schema-version {2.0.0,2.1.0}`.
- Added a deterministic `aleph-skill-runtime-v<version>.zip` artifact next to the unchanged capability-complete full artifact: an allowlisted transitive closure carrying the entire locked D Research component with its interop contract, self-described by an embedded `runtime-manifest.json`, with fail-closed closure, extraction, and tamper verification outside Git.
- Added audit-stream persistence to the ledger import CLI: a `d-research-import-audit` artifact keeps `lead`, `process`, and `blocker` partitions plus full per-row 37-column source provenance, bound into the import receipt by `audit_ref`/`audit_sha256` with deterministic receipt hashes.
- Added capability-contract discovery: the bundled report surfaces the component's machine-checkable `templates/interop-contract.json` (header widths, record types, canonicalization/signature identifiers, routes, entrypoints, artifact profiles) beside the importer's own ledger contract, and compatibility is decided from declared capability when a contract is present, with the historical major-3 acceptance retained for contract-less candidates.
- Added gateway routes for the new D Research 3.4.0 surfaces — `research:package-metadata`, `research:check-doc-examples`, and `research:build-artifacts` — while keeping new library modules explicitly reported as non-dispatchable entries.
- Added `research:runtime-self-test`; the historical `research:package-check` and inventory alias now select the source-package checker or official runtime closure self-test from the attested component profile, preserving the old route while making it valid for both profiles.
- Added canonical minimal node/edge fixtures (`tests/fixtures/canonical/minimal-graph.json`) as the CI-validated source of truth embedded byte-exactly in `references/node-builder.md` and `references/causal-edge-protocol.md`, with fenced-JSON parsing checks over both docs.
- Added a machine-readable schema `2.1.0` catalog and timeline-node schema, plus semantic validation and end-to-end compilation for every fenced node/edge JSON example.

### Changed
- Updated the locked internal D Research component from `3.3.0` to `3.4.0`.
- Advanced Aleph package and validator versions to `2.4.0`; workspace schema `2.0.0` artifacts and formula `2.0.0`/`2.1.0` contracts are unchanged.
- Rewrote the host-policy statement repository-wide (docs, adapter generator, generated adapters): Aleph imposes no additional content restrictions of its own; mandatory host, system, and developer policy and applicable law always take precedence; within that scope the simulation completes to the maximum permitted extent with explicit `fact`/`inference`/`assumption`/`simulation`/`counterfactual` labels. The Vatican regression is retitled to a labeled-simulation regression and keeps its causal branches.
- Edge validation accepts an explicit `context_modifiers: []` declaration, normalizes the `confidence` alias to canonical `evidence_confidence` (non-fatal `CONFIDENCE_ALIAS` warning on divergence), and treats a lone `confidence` as satisfying the required-field contract; missing `actor_basis` keeps its inference with a warning-severity annotation.
- D Research discovery now verifies the complete declared interop contract for bundled and external candidates. A healthy bundle remains the default; an explicit external path replaces it only when the caller also opts in, and the selected candidate's interop/importer contracts remain visible in the result.
- Import validation now matches the upstream semantic validator across all exact 14/19/22/23/37-column contracts, while preserving the additive Aleph prototype contract and accepting upstream-valid unspecified confidence conservatively as `0.0`.
- New audit-bearing import receipts replay the exact deterministic audit bytes and counts during assurance verification; legacy receipts without the additive audit fields remain verifiable.
- The frozen v2.3.1 monotonic baseline now records all 16 adapter IDs and 140 argparse option/default contracts, and current-surface capture includes readable schema `2.1.0`.
- Validation reports resolve `formula_version` from the workspace instead of hardcoding the current formula; an unsaturated partial that self-claims `verified`/`calibrated` is normalized with a `PARTIAL_ASSURANCE_NORMALIZED` warning while final-mode saturation gates stay hard.
- Release archives use deterministic DEFLATE after a byte-identical double-build check, with STORED as the declared fallback; adapter version labels (Continue frontmatter and contract header) derive from the canonical package version.
- Updated gateway acceptance reconciliation for the D Research 3.4.0 matrix: repository-only case `23_unsafe_runtime_config` is reconciled only against the exact locked identity, while the 3.3.0 and 3.2.1 branches remain unchanged.

### Compatibility
- Every v2.3.1 command, route, flag, default, adapter, domain pack, schema, ledger width, and valid input remains accepted; the repaired frozen capability baseline enforces the superset from the actual v2.3.1 source tree.
- Existing exact 14-, 19-, 22-, 23-, and 37-column ledgers remain canonicalizable, signable, verifiable, and importable with unchanged canonicalization and HMAC semantics.
- Existing schema `2.0.0` workspaces and fixtures remain valid without migration; the `2.1.0` upgrade is opt-in, sibling-only, and never rewrites a source workspace in place.
- No new mandatory Python or Node dependency is introduced. Playwright remains locked upstream and browsers are never bundled or auto-installed.

### Security and provenance
- Leads, process rows, and blocker rows persist to the audit artifact and are never promoted to evidence; prohibited policy rows continue to fail closed.
- Pinned GitHub-verified annotated tag object `65b5b788f841f40b1de50269c5dbf3fd2b51e4d9`, commit `9f9ad30346f3921ab544014b2a15a8fe5734e2e9`, and Git tree `899e955dea529fad79892d5750d77075c26aecc3`. Provenance binds both the raw reproducible Git archive tar SHA-256 `19ebdb0281a6b24aa6cc252f4d8404f052b6f671366818eb913454eab3779795` and the release-workflow tar.gz SHA-256 `7de771335576bebb93b66e3f89aad87a6355b175c11e63d4b508feeddbd5d366`.
- Vendored the official 210-file runtime profile with component-tree SHA-256 `7c382b677cce9b228bb81e053fc6897e4c4dca49288a73577ef205c1aee18cfc`; excluded 545 exact source/development, release-evidence, and hostile-fixture paths, and applied only the attested runtime `package.json` projection.

## 2.3.1

### Release integrity
- Added targeted regression coverage for the D Research 3.3 policy-ledger validation paths so the branch-aware release gate passes without lowering its threshold.
- Aligned importer behavior with the upstream ledger validator by rejecting `record_type=lead` outside the exact 37-column policy contract.
- Kept the public `v2.3.0` tag immutable after its unpublished workflow run stopped at the coverage gate; `v2.3.1` is the first published package in the 2.3 line.

### Compatibility
- Advanced package and validator versions to `2.3.1`. Workspace schema `2.0.0`, formula contracts, and the locked D Research `3.3.0` component remain unchanged. Existing exact 23-column ledgers remain supported, but `lead` rows require the 37-column policy schema as specified upstream.

## 2.3.0

### Added
- Added the D Research 3.3 investigative-policy surface through the locked `research:policy` gateway route, including workspace-confined scope initialization, validation, authorization binding, and research-plan policy binding.
- Added exact 37-column ledger interoperability with byte-parity canonicalization, a `d-research-policy-37` import contract, and separate preservation of `lead` rows.
- Added policy self-testing to Aleph's component-aware verification without executing repository-only checks from the vendored snapshot.

### Changed
- Updated the locked internal D Research component from `3.2.1` to `3.3.0`.
- Advanced Aleph package and validator versions to `2.3.0`; workspace schema remains `2.0.0`, new workspaces continue to use formula `2.1.0`, and formula `2.0.0` artifacts remain replayable.
- Updated gateway acceptance reconciliation for the D Research 3.3 matrix: repository-only case `23` is reconciled only against exact snapshot exclusions, while unrelated failures remain fatal.

### Compatibility
- Existing exact 14-, 19-, 22-, and 23-column ledgers remain canonicalizable, signable, verifiable, and importable. New policy-aware ledgers use the exact 37-column header.
- No workspace schema or D Research plan migration is required. Existing component bindings, import receipts, finalization receipts, and validation receipts remain version-bound; preserve the original workspace and regenerate affected receipts on a sibling copy.
- No new mandatory Python or Node dependency is introduced. Playwright remains locked upstream and browsers are never bundled or auto-installed.

### Security and provenance
- A `lead`, lead-only disposition, raw-leak pointer, or prohibited policy row cannot enter the Aleph evidence map; 37-column claims import only from the `evidence` field.
- Pinned GitHub-verified annotated tag object `050d4060182a38c9383365896941b9cbeffee5f4`, commit `f097505c1f3a76428d381581c2ac9310553b6823`, Git tree `03469107c036438077eab8d00448e3001999eeb9`, and canonical archive SHA-256 `427a96e0a02a1a94b47f5ec482947f9cebe8b218804012e6b46c8c1e5286d300`.
- Locked 209 component files with component-tree SHA-256 `6f34464f149dab246f51c082aaa886688133f80db4d5589526b3625329490bfa`; excluded 531 exact repository-only paths.

## 2.2.0

### Creative simulation
- Made scenario execution a first-class invariant: Aleph has no content refusal based on topic, actor, motive, conflict, violence, religion, politics, ideology, current relevance, or speculation.
- Replaced privacy refusal intake with provenance classification. Public, private, minor, fictional, and unknown actors can be simulated with `actor_basis: evidence|mixed|assumption`; unsupported details remain explicit assumptions or simulation output.
- Added sealed assumption-only actor packets with distinct assumptions and unknowns, no fabricated research, byte-verified retained artifacts, and a single unchained roleplay receipt; evidence-backed tracks retain research/roleplay separation.
- Added a verbatim regression for the Vatican internal-conflict and attempted-overthrow scenario, together with sensitive-content and actor-provenance coverage.

### Numerical engine
- Advanced new runs to `formula_version: 2.1.0` while retaining deterministic replay support for formula `2.0.0` workspaces.
- Corrected transform semantics with centered logistic response, log-change elasticity, identity passthrough, and explicit `above`, `below`, `deadband`, and `hysteresis` thresholds.
- Added timestep-invariant stock decay, explicit flow-to-stock `rate|impulse` integration, and `do(set)` release policies `retain|reset_baseline`.
- Extended Monte Carlo execution to sample transform parameters and stock dynamics, and made branch clustering sensitive to trajectory and magnitude rather than endpoint sign alone.
- Added execution-binding v2 over formula version, sampled parameters, integration mode, and dynamics hashes; v1 bindings remain replayable for legacy formula runs.
- Advanced hindcast commitments to v3 so calibration precommitments bind `formula_version`; regenerated all bundled formula 2.1 case commitments. Custom v2 policies must declare each case's intended formula version, recompute its commitment hash, and verify the copied v3 policy before reuse.

### Compatibility and packaging
- Advanced package and validator versions to `2.2.0`; workspace schema remains `2.0.0` and bundled D Research remains locked at `3.2.1`.
- Preserved legacy serialization defaults where required so unchanged formula 2.0.0 workspaces retain stable hashes.
- Regenerated portable adapters and the exact distribution manifest, and made the required release-note path derive from `PACKAGE_VERSION` to avoid stale release metadata.
- Added formula 2.1 contract and engine-dynamics regression suites alongside full lifecycle, replay, package, provenance, and release-gate coverage.
- Published a schema-cataloged intervention contract and strengthened scalar-distribution and hysteresis schema parity.
- Clarified that numerical traces are authored, audited inputs (the CLI never fabricates a placeholder) and documented the baseline-plus-delta meaning of numeric change-point magnitudes.

### Review scope
- Converted reproducible findings from the requested manual multi-model advisory review into deterministic regression tests.
- Model reviews remain advisory only; replay checks, package validation, reproducible assets, and release CI are authoritative.

## 2.1.1

### Changed
- Updated the locked internal D Research component from `3.2.0` to `3.2.1`.
- Added upstream semantic retrieval with optional `sentence-transformers` auto-selection and deterministic `local-hashing` fallback.
- Added richer fail-closed BibTeX/CSL metadata handling and optional local `langdetect` support with the existing trigram fallback.
- Advanced Aleph package and validator versions to `2.1.1`; workspace schema and numerical formula versions remain `2.0.0`.

### Compatibility
- D Research workspaces require no schema migration and gain no new mandatory runtime dependency.
- Existing Aleph finalization, validation, quality, and D Research component-binding receipts are version-bound; preserve the original workspace, rebind or re-import on a sibling copy as applicable, then validate and finalize again.
- Optional embedding and language-detection backends are never bundled or auto-installed. Missing extras retain deterministic local fallbacks.

### Security and provenance
- Pinned the upstream verified annotated tag object `4f797f0cb0f75539edfc9bc9332ca4dd041e881c`, commit `dc07d4902361ddf15ff0dd093faa0784b2fd47ab`, Git tree `4943fec2d4fa6bda1f4beecfd9177a630af0ab8b`, and canonical archive SHA-256 `e91837e6d2d38cde4f055f54d382e58c7b2094220b87e77dff695f92a63cdd71`.
- Locked 201 runtime files with component-tree SHA-256 `0276b3f4b6230b65e75c664edd642b9b2b7ce30e534a88a8328899b3d6fc7edd`; excluded all 529 tracked repository-only CI, release, and dogfood-evidence paths.
- Retained bundled-first discovery, gateway-only subprocess routing, external HMAC refusal, sealed roleplay isolation, and fail-closed component verification.
- Forces Hugging Face and Transformers cache-only mode for gateway operations unless the caller explicitly grants network access.

### Verification scope
- This patch retains the complete deterministic Aleph release gate and cross-platform CI matrix.
- Per maintainer direction, no new model dogfood or manual multi-model forward-test campaign is part of the `2.1.1` release claim.

## 2.1.0

### Added
- Bundled D Research component under `components/d-research` with `component-lock.json` digests.
- `scripts/aleph/component_registry.py` resolve/verify/discovery (bundled-first).
- `scripts/research_gateway.py` as the sole Aleph-to-D Research subprocess dispatch path.
- Portable import `component_binding` and quality re-verify against lock digests.
- `references/bundled-research-routing.md` and `THIRD_PARTY_NOTICES.md`.
- Migration `--bind-bundled-d-research` / `--dual-run-research`.

### Changed
- Discovery no longer lets `D_RESEARCH_SKILL` override the bundle.
- External compatibility mode now requires the explicit path and a separate `--allow-external` opt-in at every CLI boundary.
- Adapter registry `d_research_discovery` is `bundled-component-gateway`.
- Package version `2.1.0`; workspace schema/formula remain `2.0.0`.

### Security
- Component path traversal, symlink/reparse, bytecode/cache, one-byte tamper, missing/extra file hard fails.
- Roleplay uses a sealed packet/receipt boundary and requires host-enforced network/tool denial; filtered environment variables are defense in depth and are not claimed as an OS sandbox.
- Release CI fetches the pinned annotated D Research tag and verifies its tag object, commit, Git tree, reproducible archive, snapshot recipe, and bundled bytes before publication. Provenance archives explicitly disable `core.autocrlf`, force LF and `tar.umask=0002`, failures remain visible in CI logs, and the full research-acceptance process receives 300 seconds on slower macOS runners without skipping checks.

## v2.0.1 - 2026-07-14

### Compatibility and protocol correctness

- Updated host discovery to use current native Agent Skills locations for supported CLIs and IDEs; generated rule fallbacks are opt-in and never apply globally.
- Corrected GitHub Copilot and JetBrains project paths and removed stale generated adapters that could activate Aleph for unrelated tasks.
- Documented an explicit installed skill root for every script invocation, removing the process-working-directory assumption across CLI and IDE hosts.
- Reconciled all workflow, evaluation, and forward-test language with the likelihood contract: uncalibrated output uses `relative_weight`; probability requires calibrated assurance and hindcast evidence.
- Enforced mode-exact likelihood fields and normalization across branch ledgers, actor adjudication, and predicted responses, including exact action-set and hypothesis-reference binding.
- Added a host-native research fallback with source-level provenance, an explicit lack of signed D Research import receipts, and an assurance ceiling of `limited`.
- Bound `imported` and `verified` D Research states to existing preserved-ledger and import-receipt artifacts instead of trusting manifest strings.
- Added resumable adaptive-research checkpoints and an unsaturated partial handoff for host execution boundaries without introducing fixed source or elapsed-time caps.
- Hardened sealed roleplay artifacts against cross-scenario/dossier replay, duplicate JSON keys, extra receipt inputs, execution mismatches, and hash-valid but semantically invalid packet/output bytes.
- Extended nested privacy refusal to medical/diagnostic fields and content, including deeply nested dossier values.
- Clarified that external CLI profiles are adapter contracts rather than turnkey orchestration claims.

### Quality and release integrity

- Fixed strict mypy violations in schema and validator paths and made strict checking an explicit project contract.
- Integrated initializer draft validation plus the complete compile-to-finalize lifecycle acceptance scenario into the release gate.
- Added regression coverage for portable host paths, non-global adapter activation, protocol wording, release packaging, and quality-gate behavior.
- Added mutation sweeps that require manifest, actor, branch, packet, and roleplay validators to return structured failures rather than throw on malformed scalar/container substitutions.
- Hardened all shared JSON/JSONL/CSV readers against duplicate fields, non-finite values, lone Unicode surrogates, resource bombs, and parser differentials; Windows alternate data streams are refused portably.
- Added a deterministic, manifest-exact release builder. The official ZIP contains only attested files and can pass both copy and symlink installer preflight after extraction.
- Kept development release checks non-mutating by disabling Ruff's project cache and routing mypy and coverage state to disposable external paths, so a self-tested ZIP remains eligible for symlink installation.
- Made installer machine status internally consistent: every refused or failed symlink transaction now returns `ok: false`, including operating-system privilege failures, while preserving rollback and receipt details.
- Added a tag-gated GitHub Release workflow with locked dependencies, commit-pinned actions, a second reproducibility build, SHA-256 assets, and GitHub build-provenance attestations; repository-level immutable releases protect the published tag and assets.
- Made release-tag verification resilient to `actions/checkout` peeling annotated tags: a two-phase verifier refetches the remote tag object into an isolated namespace, binds it to the checked-out commit and `main`, then refuses publication if the tag object or target moved during the build. Git-topology integration tests cover annotated, lightweight, mismatched, moved, side-branch, and malformed tags.
- Forward-checked the reproducible ZIP with Grok Build, the requested GLM/Kimi/MiniMax models through OpenCode, and every Cline Pass model exposed by Cline 3.0.40; all passed both the semantic safety-contract audit and packaged fixture, while host/model-specific strict-output-format variability remains explicitly documented.

### Versioning

- Package and validator versions advance to `2.0.1`.
- Schema and numerical formula identifiers remain `2.0.0`; there is no schema-number migration, but previously accepted 2.0.0 workspaces must be revalidated and may require packet/report/numerical artifact regeneration under the stricter v2.0.1 contracts.
- Existing finalization receipts require regeneration whenever validator-version binding or repaired source artifacts make them stale.
- All bundled domain packs remain honestly labeled `experimental`; this release does not enable probability claims for them.

## v2.0.0 - 2026-07-14

### Breaking

- Schema writes are `2.0.0` only; `1.2.0` workspaces migrate via `sim:migrate` (default sibling output).
- Assurance tiers are `experimental | limited | verified | calibrated`; diagnostic score cannot override hard gates; `excellent` is legacy display only.
- Uncalibrated branches use `relative_weight` (not bare `probability`); calibrated probability requires method, sample count, interval, and policy ref.
- Trace rows are recomputed (formula, lag/context, amplification); forged effects fail closed.
- Installer uses distribution allowlist, refuses `source==destination`/nested paths, and never deletes real trees for symlink mode.

### Added

- Exact machine-readable portability vocabulary so CLI/IDE adapters do not paraphrase temporal, roleplay, engine-limit, assurance, or D Research compatibility values.
- Secure shared path resolver and streaming loader (size/depth limits).
- Typed check results with stable public issue codes.
- Atomic finalization + `STALE_ARTIFACT` detection.
- D Research discovery (no hardcoded developer paths) and claim-only ledger import with HMAC hard-fail.
- Privacy intake, knowledge packets, sealed roleplay rules, receipt chain helpers.
- Deterministic / Monte Carlo engine with counter-based RNG; sensitivity (OAT/Morris; optional Sobol).
- Seven data-only domain packs with semantic validation and model-backed hindcast fixtures.
- Portable adapter registry + generated instruction adapters; drift check.
- npm script surface aligned with Upgrade plan (`sim:*`, `research:import`, `packs:validate`, `acceptance`, etc.).
- Full portable lifecycle acceptance: initialize, compile, run, replay, sensitivity, hindcast, finalize, and strict revalidation.
- Published JSON Schema contracts for manifests, nodes, edges, branches, roleplay receipts, and research imports.
- Run contracts bind a non-empty propagation trace by declared path, raw SHA-256, row count, and semantic replay.
- Project-scoped IDE/external-CLI adapters install the same verified core and emit one bundle receipt.
- Numerical trace execution binding independently matches source/target states, ticks, sampled strengths, interventions, and run identity to a reconstructed engine trajectory.
- Numerical branch ledgers explicitly distinguish analyst-authored scenarios from engine-derived deterministic or Monte Carlo clusters.

### Correctness

- Engine uses discrete level equations, explicit intervention release, SCC convergence gates, context multipliers, saturation, and deterministic or sampled lag distributions.
- Counter RNG uses typed length-prefixed framing so distinct counter tuples cannot alias.
- Monte Carlo preserves invalid/unresolved mass and cannot silently renormalize failed runs.
- Migrator records a complete 1.x source digest, materializes declared assumptions, writes the canonical 2.0 migration contract, and refuses overlapping or ambiguous trees.
- Every published JSON schema is parsed in the release suite, and schema/template/fixture assumption contracts are regression-tested.
- Strict sensitivity inputs reject booleans, strings, non-finite values, and malformed bounds.
- SCC convergence uses the unrelaxed fixed-point residual; day-based lags honor timestep in deterministic and Monte Carlo modes.
- Effect distributions fail compilation on unknown, incomplete, unordered, or non-finite parameters instead of falling back silently.
- Hindcast calibration commitments bind cutoff, model/config/ticks, evidence snapshot, targets, and baselines; OAT stays inside declared bounds.
- Knowledge packets and roleplay outputs are closed and recursively scanned; offline execution flags are mandatory.
- Run and replay honor manifest-declared nodes, edges, model, ledger, report, and trace paths; aliased output paths fail closed.
- Replay rejects malformed contracts, mode/config drift, changed saved results, and worker-only metadata drift; run/replay/compile entry points use bounded secure JSON loading.
- Numerical branch validation binds deterministic output or every Monte Carlo cluster exactly; analyst-authored branches cannot claim engine metadata.
- Extreme timestep, lag, numeric, and run-reference inputs fail with typed issues instead of coercion, overflow, or traceback.
- Model and run artifacts stage as one exception-safe pair and restore both targets when staging or promotion fails.
- Workspace initialization emits a coherent draft with no fabricated completed human-track receipts.

### Security

- Path traversal / UNC / drive / symlink escape blocked for artifacts.
- `.env`, credential-like files, coverage output, caches, and generated package metadata are excluded from install distribution.
- Secret scanning streams complete files, including files larger than 2 MiB.
- Symlink installation requires a fully attested clean tree and refuses any unmanifested exposure.
- Adapter writes use exclusive randomized temporary files, flush to disk, verify digests, and roll back on failure.
- Copy installs and single-file adapters require a current distribution manifest; unverified or secret-bearing sources fail closed.
- D Research verified assurance requires a signed preserved ledger, a hashed import receipt, exact regenerated evidence CSV, and a reverified compatible 3.x package identity.
- Roleplay Tier A requires referenced HMAC receipt bodies; self-attested receipt strings cannot support verified assurance.
- Distribution-manifest parsing and hashing use the same bounded byte buffer; installer commits recheck reparse-point parents and roll back copy, symlink, adapter, and combined bundle mutations on receipt failure.
- Reparse detection inspects filesystem attributes per component, preserving junction defenses without misclassifying Windows 8.3 path aliases.
- Stable administrator-owned POSIX root aliases (for example macOS `/var -> /private/var`) are canonicalized while every lower path component remains reparse-checked.
- Linked-worktree `.git` pointer files are pruned as administrative metadata, matching ordinary `.git` directory handling without entering the distribution.
- Successful install transactions discard rollback backups only after their receipt or combined bundle receipt is durable, preventing duplicate hidden skills from contaminating host discovery.

### Verification

- Python 3.10-3.13 and Linux/macOS/Windows CI matrix declared.
- Adversarial validator tests derive their tampered workspace from committed fixtures and do not depend on developer-local output directories.
- Distribution fixtures use repository-enforced LF bytes so manifest hashes remain identical across Git checkouts on Windows, macOS, and Linux.
- The v2.0.0 gate ran 100+ regression tests, Ruff, project-configured mypy, package validation, adapter drift, deterministic replay, and adversarial rejection; lifecycle acceptance was verified separately. v2.0.1 promotes actual `mypy --strict` and lifecycle acceptance into the gate.
- Domain packs remain `experimental`; probability output stays disabled until real calibration and hindcast evidence satisfy the calibrated gate.

## v1.2.0 - 2026-07-02

### Changed

- Removed user/model-selected `quick`, `standard`, and `deep` execution profiles and all fixed source/repair caps.
- Replaced budget-based completion with adaptive complexity assessment and evidence-saturation gates modeled on the D Research workflow.
- Research now expands in waves according to temporal span, domain/geographic breadth, actor density, causal depth, evidence uncertainty, and stakes.

### Added

- Automatic retrospective, prospective, and hybrid temporal modes.
- Present-day intervention → future branch simulation with strict post-cutoff fact boundaries.
- Past divergence → alternate present → future projection workflow.
- Required future leading indicators, disconfirming conditions, and monitoring guidance.
- Professional decision-grade report renderer with executive summary, methodology, evidence quality, causal architecture, branches, sensitivity, audit, and source appendix.
- Adaptive source-quality thresholds that rise with causal complexity without limiting how long research may run.
- Validator rejection of legacy execution profiles/caps and non-schema research-quality aliases.
- Exact professional-report section parity between `SKILL.md`, the renderer, and final validation.

### Verification

- Passed the portable Agent Skills validator and the full `npm run self-test` release gate with 20 unit tests.
- Re-ran read-only OpenCode regressions with DeepSeek V4 Flash, GLM 5.2, Kimi K2.7 Code, MiniMax M3 on Ollama Cloud, and Qwen 3.7 Max on Cline Pass.
- Added deterministic gates for omissions observed in weaker or non-deterministic model outputs; a run is not complete merely because a model produced plausible prose.

## v1.1.0 - 2026-07-02

### Added

- Execution profiles with bounded source and repair-loop budgets.
- Strict schema `1.1.0` with cross-artifact reference integrity and source-quality metadata.
- Auditable `human-track-ledger.jsonl` for distinct Human Research and Human Roleplay executions.
- Mandatory subagent use when a task/subagent tool is exposed, with isolated-pass fallback only when unavailable.
- Final-report validation and a 100-point simulation quality evaluator.

### Improved

- Validator now rejects unresolved references, weakly labeled evidence, unchecked contradictions, incomplete human tracks, unnormalized actor hypotheses, missing context/lag data, and branch-cap violations.
- Workspace initialization now starts checkpoints immediately and applies `quick`, `standard`, or `deep` budgets.
- Report rendering now summarizes evidence access quality and human-track execution.
- Workflow now checkpoints artifacts before research expands and caps repair loops to prevent runaway context/time use.

### Forward testing

- Tested through OpenCode with DeepSeek V4 Flash, GLM 5.2, Kimi K2.7 Code, MiniMax M3 on Ollama Cloud, and Qwen 3.7 Max on Cline Pass.
- The baseline exposed inconsistent source quality, missing human subagent separation, and long repair loops; v1.1 gates were designed from those observed failures.

## v1.0.0 - 2026-07-01

Initial production release.

### Added

- Portable skill core with `SKILL.md` frontmatter limited to `name` and `description`.
- Runtime guidance for Codex, Claude Code, OpenCode, and generic `.agents` skill directories.
- D Research integration guide for evidence ledgers, source discovery, contradiction checks, and public-role actor research.
- Mandatory Human Research / Human Roleplay split for material human decision nodes.
- Seven-phase timeline simulation workflow: define, research, construct, link, propagate, branch, validate.
- Node, edge, human-node, propagation, branch, safety, and reporting reference guides.
- JSON/CSV/JSONL templates for simulation manifests, nodes, causal edges, actor dossiers, evidence maps, branch ledgers, propagation traces, and validation reports.
- Stdlib-first helper scripts for package validation, workspace initialization, artifact validation, butterfly scoring, report rendering, preflight checks, and adapter installation.
- Local release-gate self-test workflow documented in README.

### Verification

- `npm run self-test`
- `python scripts/validate_skill_package.py .`
- `python scripts/validate_simulation_artifacts.py --examples`
- end-to-end workspace lifecycle smoke test.
