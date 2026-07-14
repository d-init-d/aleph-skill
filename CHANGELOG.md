# Changelog

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
