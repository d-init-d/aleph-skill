# Changelog

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
