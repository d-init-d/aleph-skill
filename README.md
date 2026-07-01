# Aleph Skill

**Evidence-grounded causal timeline simulation for agents: a way to stand at one point of change, unfold the possible past-present-future branches, and keep every imagined world accountable to sources, mechanisms, and uncertainty.**

[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-skill?sort=semver)](https://github.com/d-init-d/aleph-skill/releases)

Aleph Skill turns a single change point into a disciplined simulation workflow: research the baseline, build causal nodes, admit only mechanism-backed edges, propagate effects over time, branch into multiple futures, and report what is known, inferred, simulated, and still uncertain.

It is designed for Codex, OpenCode, Claude Code, and generic Agent Skills runtimes.

## What it is for

Use Aleph Skill for:

- counterfactual history and alternate-history analysis,
- butterfly-effect simulations from a specific intervention,
- policy, market, geopolitical, social, or technology scenario trees,
- human decision nodes where public-role behavior matters,
- structured causal reports with evidence ledgers, branch probabilities, and audit trails.

Do not use it to claim one future is certain, profile private people, bypass access controls, deanonymize people, or collect sensitive personal information.

## Recommended companion: D Research

Aleph Skill is strongest when used with [`d-research-skill`](https://github.com/d-init-d/d-research-skill).

D Research should handle source discovery, evidence ledgers, contradiction checks, and public-role actor research. If D Research is not installed when the skill is invoked, the agent should ask the user once whether they want to install or enable it. If the user declines, the simulation should continue in limited mode with lower confidence and an explicit `research_quality: basic` warning.

## Human decision protocol

For material human actors, the skill requires a split workflow:

1. **Human Research track** - uses D Research or public sources to build a public-role dossier. It must not roleplay.
2. **Human Roleplay track** - uses only the completed dossier and simulated-time situation to generate hypotheses. It must not browse or invent private motives.
3. **Main simulator** - adjudicates both tracks, creates alternatives, assigns conservative probabilities, and labels roleplay as `simulation`, never `fact`.

When a runtime supports subagents and the user allows them, run Research and Roleplay as separate subagents. Otherwise, keep them as separated passes in the main context.

## Package structure

```text
aleph-skill/
  SKILL.md
  AGENTS.md
  agents/openai.yaml
  adapters/
  examples/
  references/
  scripts/
  templates/
  package.json
  pyproject.toml
```

## Core workflow

| Phase | Purpose |
|---|---|
| Define | Capture the change point, magnitude, horizon, domain, and assumptions. |
| Research | Reconstruct the baseline world state with sources and contradictions. |
| Construct | Build entity, event, factor, context, indicator, claim, and source nodes. |
| Link | Admit causal edges only when mechanism, evidence, lag, strength, and confidence are explicit. |
| Propagate | Trace effects through the graph with thresholds, feedback loops, and uncertainty. |
| Branch | Produce at least three plausible timelines whose probabilities sum to 1.0. |
| Validate | Audit provenance, mechanisms, confidence, human-node separation, safety, and sensitivity. |

## Install

Clone:

```powershell
git clone https://github.com/d-init-d/aleph-skill.git
cd aleph-skill
```

Validate:

```powershell
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python scripts\preflight.py --json
npm run self-test
```

Dry-run adapter install:

```powershell
python scripts\install_adapters.py --target codex --scope user --dry-run
python scripts\install_adapters.py --target claude-code --scope user --dry-run
python scripts\install_adapters.py --target opencode --scope user --dry-run
python scripts\install_adapters.py --target agents --scope user --dry-run
```

## Platform paths

- Codex: `~/.codex/skills/aleph-skill`
- Claude Code: `~/.claude/skills/aleph-skill` or `.claude/skills/aleph-skill`
- OpenCode: `~/.config/opencode/skills/aleph-skill` or `.opencode/skills/aleph-skill`
- Generic Agent Skills: `~/.agents/skills/aleph-skill` or `.agents/skills/aleph-skill`

## Example prompt

```text
Use $aleph-skill to simulate an oil price +40% shock starting June 2026.
Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months.
Use D Research for evidence, split material human decisions into research and roleplay tracks,
and produce at least three branches with probabilities and uncertainty warnings.
```

## Safety posture

Aleph Skill is for lawful, evidence-backed scenario analysis. It refuses or narrows requests involving private-person profiling, doxxing, stalking, minors, private accounts, access-control bypass, captcha evasion, paywall bypass, or unsupported claims about sensitive personal traits.

## License

Source-available for non-commercial use under the Creative Commons Attribution-NonCommercial 4.0 International License. See `LICENSE`.
