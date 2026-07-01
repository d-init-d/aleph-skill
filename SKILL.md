---
name: aleph-skill
description: Professional timeline-simulation skill for AI agents. Use when reconstructing counterfactual histories, modeling butterfly effects from a point of change, researching human/event/factor/context nodes with D Research, splitting human decisions into research and roleplay tracks, and producing evidence-grounded branching futures with explicit uncertainty.
---

# Aleph Skill

Use this skill to turn a single point of change into a disciplined causal simulation: reconstruct the baseline world, build evidence-backed nodes and edges, propagate effects through time, split into multiple plausible branches, and report uncertainty instead of pretending to possess prophecy.

Aleph is a frame for seeing many causal angles at once. It is not an oracle. Every answer must separate:

- `fact`: sourced evidence about the observed world.
- `inference`: analyst interpretation from evidence.
- `simulation`: model output from assumptions, weights, and branches.
- `counterfactual`: events that did not happen in the observed timeline.

## Invocation preflight

At the start of a simulation:

1. Define the change point, horizon, domain, and output depth.
2. Check whether D Research is available for evidence ledgers and public-source research.
3. If D Research is absent, ask the user once per task whether they want to install or enable `d-research-skill`. If they decline or cannot install it, continue in limited mode and mark `research_quality: basic`.
4. For any human decision node, split work into a Human Research track and a Human Roleplay track. Use actual subagents when the host runtime and user authorization allow it; otherwise keep the two passes separate in the main context and label artifacts clearly.

## Quick workflow

1. Define the change point: what changes, when, where, magnitude, scope, active contexts, and horizon.
2. Research the baseline world state. Read `references/d-research-integration.md` for D Research handoff and fallback rules.
3. Build nodes. Read `references/node-builder.md`; read `references/human-node-protocol.md` for any person who can change the causal path.
4. Link causal edges. Read `references/causal-edge-protocol.md`; reject edges without mechanism, evidence, lag, context, strength, and confidence.
5. Propagate the perturbation. Read `references/propagation-engine.md`; keep a trace for meaningful effects.
6. Branch and aggregate. Read `references/branch-management.md`; produce at least three branches unless the user explicitly asks for deterministic historical replay.
7. Validate and report. Read `references/reporting-contract.md`; run artifact validation when files are produced.

For the full phase protocol, intake schema, and stopping rules, read `references/simulation-workflow.md`.

## Human-node hard rule

When a simulation has a material human actor:

- Human Research track: use D Research or public sources to build a public-role dossier with evidence IDs, institutional constraints, decision patterns, relationships, and uncertainty. It must not roleplay.
- Human Roleplay track: use `templates/subagent-roleplay-prompt.md` with only the dossier and information available at the simulated time. It must not collect facts or invent private motives.
- Main simulator: adjudicate the roleplay output against evidence, create alternatives, cap confidence conservatively, and mark roleplay as `simulation`, never `fact`.

## Hard gates

Before presenting a simulation as complete, pass these gates:

- Provenance: every meaningful node, edge, and actor claim has at least one source or is marked as a user-provided assumption.
- Mechanism: every edge explains the transmission channel, target reach, causal rationale, lag, and context modifiers.
- Confidence: every edge has both `base_strength` and `confidence`; do not show one without the other.
- Multi-future: produce 3+ branches whose probabilities sum to 1.0; cap the largest branch at 0.60 unless deterministic replay is requested.
- Temporal knowledge: actors only know information available at the simulated time.
- Human safety: model only public-role behavior; do not collect private personal data, doxxing material, private contacts, family details, or whereabouts.
- Roleplay discipline: roleplay output is a hypothesis generator, never evidence.
- Audit: report assumptions, thin evidence, contested sources, sensitivity points, skipped paths, and research limitations.

If a gate fails, continue research, downgrade confidence, mark the output partial, or ask the user for a scope decision.

## Resource map

- `references/simulation-workflow.md`: the seven-phase timeline simulation protocol.
- `references/d-research-integration.md`: D Research companion workflow, evidence-ledger mapping, and install prompt rules.
- `references/node-builder.md`: node construction rules for entity, event, factor, context, indicator, claim, and source.
- `references/causal-edge-protocol.md`: mechanism tests, scoring, lag distributions, context modifiers, and admission gates.
- `references/propagation-engine.md`: perturbation formula, Monte Carlo-style reasoning, feedback loops, thresholds, and trace format.
- `references/human-node-protocol.md`: public-role actor dossiers plus separated research/roleplay tracks.
- `references/branch-management.md`: branch creation, probability normalization, pruning, and black-swan handling.
- `references/safety-and-privacy.md`: boundaries for living people, minors, access controls, and sensitive data.
- `references/reporting-contract.md`: final report format and audit appendix.
- `references/evaluation-forward-tests.md`: realistic tasks for forward-testing the skill.
- `adapters/codex.md`, `adapters/claude-code.md`, `adapters/opencode.md`, and `adapters/agents.md`: platform-specific install notes.

## Scripts

Use scripts for deterministic checks and artifact generation. They are Python-stdlib-first and must soft-fail when optional dependencies are absent.

- `scripts/preflight.py`: check Python and D Research availability.
- `scripts/init_simulation_workspace.py`: create a simulation run directory from templates.
- `scripts/validate_skill_package.py`: validate this skill package and internal references.
- `scripts/validate_simulation_artifacts.py`: validate manifests, nodes, edges, branches, traces, and actor dossiers.
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
6. human decision points and research/roleplay separation,
7. validation/audit results,
8. warnings and next research steps.

Never imply that a simulated branch is guaranteed. Prefer "under these assumptions, this branch is estimated at..." over "this would happen."
