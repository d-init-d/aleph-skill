# Aleph Skill

**Evidence-grounded timeline simulation for agents that need to reason from one change point across counterfactual pasts, alternate presents, and branching futures.**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-skill?sort=semver)](https://github.com/d-init-d/aleph-skill/releases)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-portable-6f42c1)](https://agentskills.io/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Self-test](https://img.shields.io/badge/self--test-npm%20run%20self--test-brightgreen)](#verification)

Vietnamese overview: [README.vi.md](README.vi.md)

> Aleph Skill turns a “what if?” into an auditable scenario model: sources become evidence, evidence becomes nodes, nodes become causal edges, and causal edges unfold into probabilistic timelines with explicit uncertainty.

It is built for Codex, OpenCode, Claude Code, and generic Agent Skills runtimes.

## At a glance

| Area | What Aleph Skill provides |
|---|---|
| Primary use | Counterfactual history, present-day intervention analysis, hybrid past-to-future timelines, and butterfly-effect scenario trees. |
| Simulation model | Evidence-backed nodes, mechanism-tested causal edges, propagation traces, branch probabilities, and uncertainty labels. |
| Human decisions | Public-role actor dossiers separated from simulated decision hypotheses, so roleplay never becomes evidence. |
| Research depth | Adaptive expansion based on temporal span, domain breadth, geography, actor density, causal depth, evidence uncertainty, and stakes. |
| Outputs | Professional scenario reports, evidence maps, causal graphs, branch ledgers, propagation traces, validation reports, and audit metadata. |
| Runtime posture | Portable markdown skill with stdlib-first helper scripts and optional adapters for major agent environments. |
| Safety posture | No deterministic prophecy, private-person profiling, doxxing, access-control bypass, or unsupported sensitive claims. |

## When to use it

Use Aleph Skill when an agent needs to:

- reconstruct an observed baseline before a point of change;
- simulate how a past divergence could alter a later present;
- project a present-day intervention into multiple future branches;
- model policy, market, geopolitical, social, climate, technology, or institutional scenarios;
- map butterfly effects through causal chains, thresholds, feedback loops, and lagged consequences;
- reason about public-role human decisions without turning private speculation into fact;
- produce a decision-grade report that separates `fact`, `inference`, `simulation`, and `counterfactual`.

Do not use it to claim one future is certain, profile private people, deanonymize people, bypass access controls, or collect sensitive personal information.

## Product scope

This repository is a portable Agent Skill package, not a hosted forecasting service, Python package, API server, crawler, or benchmark leaderboard.

An agent reads `SKILL.md` as the entry point, then loads only the reference files and templates needed for the scenario. The helper scripts are deliberately small and local: they initialize workspaces, validate artifacts, score butterfly amplification, render reports, and check package integrity. They support the workflow; they do not replace the agent’s reasoning.

For the strongest evidence layer, pair Aleph Skill with [D Research](https://github.com/d-init-d/d-research-skill), a companion skill for browser-first research and auditable evidence workflows. Aleph remains the causal simulation layer; D Research is recommended rather than bundled, so the skill stays portable.

## Workflow lifecycle

| Phase | What happens | Main artifacts |
|---|---|---|
| 0. Frame | Define the change point, observation cutoff, horizon, geography, domains, and inferred temporal mode. | `simulation-manifest.json` |
| 1. Research | Build the baseline, source map, evidence map, contradiction notes, and uncertainty register. | `evidence-map.csv` |
| 2. Construct | Create entity, event, factor, context, indicator, claim, source, and actor nodes. | `timeline-node.json`, `actor-dossier.json` |
| 3. Link | Admit only causal edges with a concrete mechanism, lag, context modifier, evidence, strength, and confidence. | `causal-edge.json` |
| 4. Propagate | Trace effects through thresholds, feedback loops, amplification paths, and decay. | `propagation-trace.jsonl` |
| 5. Branch | Produce multiple plausible timelines with probabilities that sum to 1.0. | `branch-ledger.json` |
| 6. Human decisions | Keep sourced public-role research separate from simulated decision hypotheses. | `human-track-ledger.jsonl` |
| 7. Report and audit | Render a professional scenario report and validate readiness before delivery. | `validation-report.json`, final Markdown report |

## Core capabilities

1. **Retrospective counterfactuals** — simulate how a historical divergence could change a later historical state.
2. **Prospective interventions** — treat the current baseline as fixed and project future outcomes from a new intervention.
3. **Hybrid projections** — carry a past divergence into an alternate present, then branch into future scenarios.
4. **Adaptive depth** — expand research and validation according to scenario complexity rather than fixed speed profiles.
5. **Mechanism-first causality** — reject edges that lack a plausible transmission channel, lag, context, and evidence.
6. **Human-node discipline** — use public-role information for actor dossiers and label all roleplay as simulation.
7. **Future monitoring** — attach leading indicators and disconfirming conditions to prospective branches.
8. **Professional reporting** — produce executive summaries, methodology notes, evidence quality, causal architecture, branch probabilities, sensitivity analysis, limitations, and audit appendices.
9. **Portable validation** — enforce referential integrity across evidence, nodes, edges, actors, branches, traces, and reports.

## Repository layout

```text
aleph-skill/
  SKILL.md                  # agent entry point
  AGENTS.md                 # concise agent-framework instructions
  README.md                 # public overview
  README.vi.md              # Vietnamese overview
  LICENSE                   # CC BY-NC 4.0
  agents/openai.yaml        # Codex UI metadata
  adapters/                 # runtime-specific notes
  examples/                 # forward-test prompts and example artifacts
  references/               # workflow, safety, causal, reporting, and research guides
  scripts/                  # stdlib-first validation and rendering helpers
  templates/                # JSON/CSV/JSONL artifact starters
  package.json              # local verification scripts
  pyproject.toml            # Python project metadata
```

## Install

Clone the repository:

```powershell
git clone https://github.com/d-init-d/aleph-skill.git
cd aleph-skill
```

Dry-run adapter installation:

```powershell
python scripts\install_adapters.py --target codex --scope user --dry-run
python scripts\install_adapters.py --target claude-code --scope user --dry-run
python scripts\install_adapters.py --target opencode --scope user --dry-run
python scripts\install_adapters.py --target agents --scope user --dry-run
```

Supported skill locations:

| Runtime | User / global path | Project path |
|---|---|---|
| Codex | `~/.codex/skills/aleph-skill` | runtime-dependent |
| Claude Code | `~/.claude/skills/aleph-skill` | `.claude/skills/aleph-skill` |
| OpenCode | `~/.config/opencode/skills/aleph-skill` | `.opencode/skills/aleph-skill` |
| Generic Agent Skills | `~/.agents/skills/aleph-skill` | `.agents/skills/aleph-skill` |

## Verification

Run the local release gate:

```powershell
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python scripts\preflight.py --json
npm run self-test
```

For a completed simulation workspace:

```powershell
python scripts\validate_simulation_artifacts.py --workspace <run-dir> --mode draft --write-report
python scripts\render_simulation_report.py --workspace <run-dir>
python scripts\validate_simulation_artifacts.py --workspace <run-dir> --mode final --require-report --write-report
python scripts\evaluate_simulation_quality.py --workspace <run-dir> --threshold 90 --enforce
```

## Example prompt

```text
Use $aleph-skill to simulate an oil price +40% shock starting June 2026.
Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months.
Use D Research where available for the evidence layer, keep sourced actor dossiers separate from simulated decisions,
and produce at least three branches with probabilities, indicators, contradictions, and uncertainty warnings.
```

## Safety boundary

Aleph Skill is for lawful, evidence-backed scenario analysis. It refuses or narrows requests involving private-person profiling, doxxing, stalking, minors, private accounts, access-control bypass, captcha evasion, paywall bypass, or unsupported claims about sensitive personal traits.

It does not predict the future. It builds transparent, source-aware simulations so users can inspect assumptions, mechanisms, uncertainties, and alternatives.

## License

Source-available for non-commercial use under the [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/).

SPDX-License-Identifier: `CC-BY-NC-4.0`
