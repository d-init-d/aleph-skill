# Aleph Timeline Simulator

**Production-ready Agent Skill for evidence-backed butterfly-effect timeline simulation: counterfactual history, causal graph construction, human decision nodes, Monte Carlo-style propagation, branch auditing, and Aleph KB integration.**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-timeline-simulator?sort=semver)](https://github.com/d-init-d/aleph-timeline-simulator/releases)

Vietnamese docs: [README.vi.md](README.vi.md)

> Aleph Timeline Simulator turns a single change point into a disciplined, auditable simulation workflow: research the baseline, build causal nodes, admit only mechanism-backed edges, propagate effects over time, branch into multiple futures, and report the uncertainty instead of pretending to know one true timeline.

---

## At a glance

| Area | What Aleph Timeline Simulator provides |
|---|---|
| Primary users | AI agents and operators running counterfactual timelines, scenario forecasts, butterfly-effect analysis, or causal KB simulations. |
| Core model | A portable Agent Skill: `SKILL.md` plus references, templates, adapters, and small helper scripts. |
| Evidence model | Every meaningful node, edge, and actor claim should map to a source, user assumption, or D Research evidence-ledger row. |
| Causal model | Aleph-style directed graph with entities, events, factors, contexts, indicators, claims, sources, weighted causal edges, lags, context modifiers, confidence, and propagation traces. |
| Human model | Public-role actor dossiers with institutional constraints. Roleplay is allowed only as hypothesis generation, never as evidence. |
| Outputs | Simulation workspaces, manifests, node/edge ledgers, actor dossiers, evidence maps, propagation traces, branch ledgers, validation reports, and Markdown reports. |
| Verification | Portable skill validation, artifact validation, preflight checks, end-to-end workspace smoke tests, and GitHub Actions CI. |
| Safety posture | No private personal data, doxxing, stalking, access-control bypass, paywall/captcha evasion, or deterministic claims about uncertain futures. |

## When to use it

Use this skill when an agent needs to:

- simulate a counterfactual timeline from a specific point of divergence;
- analyze butterfly effects across people, events, nature, institutions, markets, and technology;
- build or audit Aleph-style causal graph nodes and causal relations;
- generate multiple future or alternate-history branches with probabilities;
- model public decision makers with sourced public-role evidence and institutional constraints;
- turn D Research evidence ledgers into simulation-ready causal inputs;
- produce a report that clearly separates fact, inference, simulation, and counterfactual assumptions.

Do **not** use it to claim that one future is certain, profile private people, bypass access controls, deanonymize people, or collect sensitive personal information.

## Product scope

This is **a skill repository**, not a hosted simulator, SaaS product, Python package, or complete Aleph KB distribution.

The repository contains:

- `SKILL.md` - the portable Agent Skills entry point.
- `AGENTS.md` - short root-level instructions for frameworks that read agent rules.
- `references/` - deep-dive guides for simulation workflow, Aleph integration, D Research integration, node construction, causal edges, propagation, human nodes, branch management, safety, reporting, and forward tests.
- `templates/` - JSON, CSV, JSONL, and Markdown starters for simulation artifacts.
- `scripts/` - stdlib-first helper scripts for preflight checks, workspace initialization, validation, Aleph bridging, butterfly scoring, report rendering, and platform installs.
- `adapters/` - platform notes for Codex, Claude Code, OpenCode, and generic `.agents` skill directories.
- `examples/` - realistic prompts for forward-testing.

The skill deliberately does **not** vendor the private [`d-init-d/Aleph`](https://github.com/d-init-d/Aleph) repository or [`d-init-d/d-research-skill`](https://github.com/d-init-d/d-research-skill). It detects and integrates with them when available.

---

## Workflow lifecycle

The skill is organized around seven simulation phases. Each phase produces artifacts that the next phase can audit.

| # | Phase | What happens | Key files |
|---:|---|---|---|
| 1 | Define | Capture change point, magnitude, time, location, scope, horizon, depth, and active contexts. | `references/simulation-workflow.md`, `templates/simulation-manifest.json` |
| 2 | Research | Reconstruct the baseline world state with sources, contradictions, and confidence. | `references/d-research-integration.md`, `templates/evidence-map.csv` |
| 3 | Construct | Build entity, event, factor, context, indicator, claim, and source nodes. | `references/node-builder.md`, `templates/timeline-node.json` |
| 4 | Link | Admit only causal edges with mechanism, evidence, lag, context, strength, and confidence. | `references/causal-edge-protocol.md`, `templates/causal-edge.json` |
| 5 | Propagate | Push perturbations through causal paths, feedback loops, thresholds, and dynamic contexts. | `references/propagation-engine.md`, `templates/propagation-trace.jsonl` |
| 6 | Branch | Produce multiple branches, normalize probabilities, prune/merge branches, and record black-swan paths. | `references/branch-management.md`, `templates/branch-ledger.json` |
| 7 | Validate | Run provenance, mechanism, confidence, branch, safety, and reporting gates. | `references/reporting-contract.md`, `templates/validation-report.json` |

---

## Core capabilities

1. **Portable Agent Skills core** - only `name` and `description` in `SKILL.md` frontmatter, so the same package can run in Codex, OpenCode, Claude Code, and generic Agent Skills runtimes.
2. **Aleph core bridge** - detects local Aleph repos, checks required schemas/scripts, and can dry-run or invoke supported Aleph commands without vendoring the private KB.
3. **D Research companion workflow** - maps source discovery and evidence-ledger rows into simulation nodes, edges, and actor dossiers.
4. **Causal node builder** - structured node rules for entities, events, factors, contexts, indicators, claims, and sources.
5. **Causal edge admission gates** - mechanism test, strength scoring, confidence caps, lag distributions, context modifiers, and status discipline.
6. **Propagation model** - Aleph-style per-hop formula with context multipliers, confidence, time decay, noise, saturation, amplification scoring, thresholds, and feedback-loop handling.
7. **Human-node protocol** - public-role-only actor dossiers, institutional constraints, crisis behavior, decision patterns, and bounded roleplay.
8. **Branch management** - 3+ timeline branches, probability normalization, black-swan branch handling, pruning, merging, and sensitivity notes.
9. **Simulation workspace tooling** - create a complete run folder with manifest, nodes, edges, actors, evidence map, branch ledger, trace, validation report, and final report.
10. **Safety and privacy guardrails** - explicit refusal/narrowing rules for private people, minors, doxxing, stalking, sensitive data, and access-control bypass.
11. **Release-gate validation** - package validation, artifact validation, preflight checks, and end-to-end report generation through local self-tests.

---

## Installation

### Option A: Clone as a standalone skill repo

```bash
git clone https://github.com/d-init-d/aleph-timeline-simulator.git
cd aleph-timeline-simulator
npm run self-test
```

Point your agent runtime at:

```text
aleph-timeline-simulator/SKILL.md
```

### Option B: Install into a user-level skill directory

From the repo root:

```bash
# Codex
python scripts/install_adapters.py --target codex --scope user --copy

# Claude Code
python scripts/install_adapters.py --target claude-code --scope user --copy

# OpenCode
python scripts/install_adapters.py --target opencode --scope user --copy

# Generic Agent Skills
python scripts/install_adapters.py --target agents --scope user --copy
```

Dry-run first if you want to inspect paths:

```bash
python scripts/install_adapters.py --target codex --scope user --dry-run
```

### Option C: Vendor into a project

```bash
mkdir -p .agents/skills
git clone https://github.com/d-init-d/aleph-timeline-simulator.git .agents/skills/aleph-timeline-simulator
```

OpenCode also supports `.opencode/skills/<name>/`, `.claude/skills/<name>/`, and `.agents/skills/<name>/`.

---

## Quick start

Create a simulation workspace:

```bash
python scripts/init_simulation_workspace.py \
  --slug oil-shock-2026 \
  --change-point "Oil price rises 40 percent starting June 2026" \
  --time 2026-06-01 \
  --horizon P24M \
  --domain mixed \
  --out-dir simulations
```

Validate artifacts:

```bash
python scripts/validate_simulation_artifacts.py --workspace simulations/oil-shock-2026 --write-report
```

Score butterfly amplification:

```bash
python scripts/score_butterfly.py --trace simulations/oil-shock-2026/propagation-trace.jsonl
```

Render a report:

```bash
python scripts/render_simulation_report.py --workspace simulations/oil-shock-2026
```

Ask your agent:

```text
Use $aleph-timeline-simulator to simulate an oil price +40% shock starting June 2026. Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months. Use D Research for evidence and Aleph-style causal branches.
```

---

## Aleph and D Research integration

Run preflight:

```bash
python scripts/preflight.py --aleph /path/to/Aleph --d-research /path/to/d-research-skill
```

If `gh` is authenticated, the preflight checks private Aleph repo access:

```bash
gh repo view d-init-d/Aleph --json nameWithOwner,isPrivate,defaultBranchRef,pushedAt,url
```

Aleph bridge commands:

```bash
python scripts/aleph_bridge.py check --aleph /path/to/Aleph
python scripts/aleph_bridge.py validate --aleph /path/to/Aleph --paths kb
python scripts/aleph_bridge.py run-scenario --aleph /path/to/Aleph --scenario scenario.yaml --dry-run
```

D Research is optional but strongly recommended for real simulations. Without it, mark research quality as limited and lower confidence.

---

## npm scripts

```bash
npm run preflight
npm run validate:skill
npm run sim:init -- --slug example --change-point "Example change"
npm run sim:validate -- --examples
npm run butterfly:score -- --trace path/to/propagation-trace.jsonl
npm run aleph:check -- --aleph /path/to/Aleph
npm run report:render -- --workspace path/to/workspace
npm run install:dry-run
npm run self-test
```

`npm run self-test` is the canonical local gate. It runs:

1. package validation,
2. bundled-template artifact validation,
3. preflight checks in JSON mode.

The release smoke test also exercises the full workspace lifecycle: init, validate, score, render.

---

## Verification

Recommended release gate:

```bash
python scripts/validate_skill_package.py .
python scripts/validate_simulation_artifacts.py --examples
python scripts/preflight.py --json
npm run self-test
```

End-to-end smoke test:

```bash
tmp="$(mktemp -d)"
run="$(python scripts/init_simulation_workspace.py --slug oil-shock-test --change-point 'Oil price rises 40 percent starting June 2026' --out-dir "$tmp")"
python scripts/validate_simulation_artifacts.py --workspace "$run" --write-report
python scripts/score_butterfly.py --trace "$run/propagation-trace.jsonl"
python scripts/render_simulation_report.py --workspace "$run"
```

Windows PowerShell equivalent:

```powershell
$tmp = Join-Path $env:TEMP "aleph-sim-e2e"
if (Test-Path $tmp) { Remove-Item -Recurse -Force -Path $tmp }
New-Item -ItemType Directory -Path $tmp | Out-Null
$run = python scripts\init_simulation_workspace.py --slug oil-shock-test --change-point "Oil price rises 40 percent starting June 2026" --out-dir $tmp
python scripts\validate_simulation_artifacts.py --workspace $run --write-report
python scripts\score_butterfly.py --trace (Join-Path $run "propagation-trace.jsonl")
python scripts\render_simulation_report.py --workspace $run
```

---

## Safety model

Aleph Timeline Simulator is designed for lawful, evidence-backed scenario analysis.

It refuses or narrows:

- private-person profiling;
- minors;
- stalking, harassment, doxxing, or re-identification;
- private contacts, private accounts, family/private-life details, medical/financial/legal/protected-trait speculation, or real-time whereabouts;
- login, paywall, captcha, rate-limit, or anti-bot bypass;
- deterministic claims that a simulated future is guaranteed.

Use `references/safety-and-privacy.md` before any simulation involving living people.

---

## Design philosophy

Aleph, in Borges' sense, is the impossible point that sees all places and times at once. This skill uses that image as a discipline, not a fantasy: it asks agents to widen the field of view while preserving epistemic humility.

The practical rule is simple:

> Build the grammar of the world first: nodes, evidence, mechanisms, lags, contexts, uncertainty. Only then simulate.

---

## Compatibility

The skill is framework-neutral and tested around:

- Agent Skills directory convention;
- Codex personal skills with `agents/openai.yaml`;
- Claude Code skill folders;
- OpenCode `.opencode/skills`, `.claude/skills`, and `.agents/skills` discovery paths;
- local Python 3.10+ helper scripts;
- npm script wrappers without required third-party dependencies.

The helper scripts are intentionally small and stdlib-first. Optional external systems such as Aleph and D Research are detected, not bundled.

---

## Release

Current release: **v1.0.0**.

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## License

This project is source-available for non-commercial use under the **Creative Commons Attribution-NonCommercial 4.0 International** license (`CC-BY-NC-4.0`). See [LICENSE](LICENSE).

Commercial use requires written permission from the copyright holder.
