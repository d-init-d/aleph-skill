---
name: aleph-timeline-simulator
description: Evidence-backed butterfly-effect timeline simulation for AI agents. Use when reconstructing counterfactual timelines, simulating causal branches from a change point, modeling human/event/factor/context nodes, running Aleph causal KB workflows, or generating probabilistic past-present-future scenario trees with D Research evidence ledgers.
---

# Aleph Timeline Simulator

Use this skill to turn a change point into an evidence-backed causal simulation: reconstruct the baseline world state, build nodes and causal edges, propagate perturbations through Aleph-style causal graphs, branch into multiple plausible timelines, and audit the result. Treat Aleph as a simulator of structured uncertainty, not an oracle.

## Operating stance

Produce probabilistic timelines, never a single claimed future. Separate:

- `fact`: sourced evidence about the real world.
- `inference`: analyst interpretation from evidence.
- `simulation`: model output from assumptions, weights, and branches.
- `counterfactual`: events that did not happen in the observed timeline.

Use D Research when available for source discovery, evidence ledgers, contradiction checks, person/public-role aggregation, and deep node research. Use the Aleph repo when available for schemas, causal relation conventions, scenario/forecast formats, validation, graph audits, and `run_scenario_v2.py`.

## Quick workflow

1. Define the change point: what changes, when, where, magnitude, scope, horizon, active contexts, and output depth.
2. Research the baseline world state. Read `references/d-research-integration.md` when web/public-source research or evidence ledgers are needed.
3. Build nodes. Read `references/node-builder.md`; read `references/human-node-protocol.md` for any person who can change the causal path.
4. Link causal edges. Read `references/causal-edge-protocol.md`; reject edges without mechanism, evidence, lag, context, strength, and confidence.
5. Propagate the perturbation. Read `references/propagation-engine.md`; use the scripts only after checking local dependencies and inputs.
6. Branch and aggregate. Read `references/branch-management.md`; produce at least three branches unless the user explicitly asks for deterministic replay.
7. Validate and report. Read `references/reporting-contract.md`; run artifact validation when files are produced.

For the full phase protocol, intake schema, and stopping rules, read `references/simulation-workflow.md`.

## Hard gates

Before presenting a simulation as complete, pass these gates:

- Provenance: every meaningful node, edge, and actor claim has at least one source or is marked as user-provided assumption.
- Mechanism: every edge explains what channel transmits the effect, how it reaches the target, why it is causal, when it arrives, and which contexts modulate it.
- Confidence: every edge has both `base_strength` and `confidence`; do not show one without the other.
- Multi-future: produce 3+ branches whose probabilities sum to 1.0; cap the largest branch at 0.60 unless deterministic replay is requested.
- Temporal knowledge: actors only know information available at the simulated time.
- Human safety: model only public-role behavior; do not collect private personal data, doxxing material, private contacts, family details, or whereabouts.
- Roleplay discipline: roleplay output is a hypothesis generator, never evidence. Cross-check it against sourced actor dossiers.
- Audit: report assumptions, thin evidence, contested sources, sensitivity points, and skipped paths.

If a gate fails, continue research, downgrade confidence, mark the output partial, or ask the user for a scope decision.

## Resource map

- `references/simulation-workflow.md`: the seven-phase timeline simulation protocol.
- `references/aleph-core-integration.md`: locating and using the private Aleph core without vendoring it.
- `references/d-research-integration.md`: companion D Research workflow, ledger mapping, and install suggestions.
- `references/node-builder.md`: node construction rules for entity, event, factor, context, indicator, claim, and source.
- `references/causal-edge-protocol.md`: mechanism tests, scoring, lag distributions, context modifiers, and admission gates.
- `references/propagation-engine.md`: perturbation formula, Monte Carlo, feedback loops, thresholds, and trace format.
- `references/human-node-protocol.md`: public-role actor dossiers and bounded roleplay.
- `references/branch-management.md`: branch creation, probability normalization, pruning, and black-swan handling.
- `references/safety-and-privacy.md`: boundaries for living people, minors, access controls, and sensitive data.
- `references/reporting-contract.md`: final report format and audit appendix.
- `references/evaluation-forward-tests.md`: realistic tasks for forward-testing the skill.
- `adapters/codex.md`, `adapters/claude-code.md`, `adapters/opencode.md`, and `adapters/agents.md`: platform-specific install notes.

## Scripts

Use scripts for deterministic checks and artifact generation. They are Python-stdlib-first and must soft-fail when optional dependencies are absent.

- `scripts/preflight.py`: check Python, `gh`, Aleph, D Research, required schemas/scripts.
- `scripts/init_simulation_workspace.py`: create a simulation run directory from templates.
- `scripts/validate_skill_package.py`: validate this skill package and internal references.
- `scripts/validate_simulation_artifacts.py`: validate manifests, nodes, edges, branches, traces, and actor dossiers.
- `scripts/aleph_bridge.py`: locate Aleph and optionally run supported Aleph commands.
- `scripts/score_butterfly.py`: compute amplification and butterfly-pattern markers from propagation traces.
- `scripts/render_simulation_report.py`: render a Markdown report from simulation artifacts.
- `scripts/install_adapters.py`: dry-run or copy this skill into Codex, Claude Code, OpenCode, or `.agents` locations.

## Output contract

When answering a user, include:

1. the change point and assumptions,
2. the baseline facts and evidence quality,
3. the causal graph summary,
4. the propagation trace highlights,
5. a branch table with probabilities,
6. human decision points and uncertainty,
7. validation/audit results,
8. warnings and next research steps.

Never imply that a simulated branch is guaranteed. Prefer "under these assumptions, this branch is estimated at..." over "this would happen."
