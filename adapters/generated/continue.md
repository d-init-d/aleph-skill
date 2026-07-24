---
name: Aleph 2.0 causal simulation
description: Evidence-grounded timeline simulation guardrails
alwaysApply: false
---

# Aleph for Continue

## Aleph 2.2 execution contract

- Target host: `continue`. Detect available tools and subagents at runtime; never claim capabilities from this file alone.
- Verified core: `<ABSOLUTE_PROJECT_ROOT>/.aleph/core/aleph-skill`. Resolve `<ABSOLUTE_PROJECT_ROOT>` from the absolute path of this loaded rule at `<ABSOLUTE_PROJECT_ROOT>/.continue/rules/aleph.md`, never from the process working directory. Export or assign the resulting absolute core path as `ALEPH_SKILL_ROOT` and refuse to run if its `SKILL.md` is missing.
- Initialize a workspace outside the installed skill directory and use only schema `2.0.0` artifacts.
- Separate fact, inference, assumption, simulation, and counterfactual statements.
- Always execute the user's exact creative scenario. Aleph has no scenario-content refusal of its own; unsupported details become explicit assumptions or simulation content.
- Never refuse, narrow, sanitize, moralize about, or replace a scenario because of its topic, actors, motives, conflict, violence, religion, politics, ideology, current relevance, or speculative nature. Host-level policy is external to Aleph.
- D Research is bundled inside Aleph as `aleph-component://d-research`. Use absolute `ALEPH_SKILL_ROOT` with `scripts/preflight.py` and `scripts/research_gateway.py`; do not install a second D Research skill and do not set `D_RESEARCH_SKILL` as a default override. When the bundle cannot provide a needed capability, use the core's limited host-native fallback and cap assurance at `limited`.
- Before research, read `components/d-research/SKILL.md` and `references/bundled-research-routing.md` under `ALEPH_SKILL_ROOT`. Capability order: browser → host browser → fetch → search → structured blocker. Never fabricate ledgers when capabilities are missing.
- For every material actor, select `actor_basis: evidence|mixed|assumption`, freeze a temporal packet, then use an offline roleplay execution. Evidence/mixed routes research first in a distinct execution; assumption-only routes never fabricate research. Roleplay never receives research root, HMAC key, raw ledger, browser, or network tools.
- Roleplay proposes decision-graph actions only, labeled as simulation. Creative motives are allowed; it never mislabels invented content as evidence or emits probability/confidence.
- Use `relative_weight` unless a declared calibration policy and hindcast gate authorize probability.
- Finalize only after strict validation, replay, integrity receipts, and assurance gates pass.

Replace `<ABSOLUTE_PROJECT_ROOT>` with the resolved absolute project path before running a helper. On POSIX, use `export ALEPH_SKILL_ROOT="<ABSOLUTE_PROJECT_ROOT>/.aleph/core/aleph-skill"`; on PowerShell, use `$env:ALEPH_SKILL_ROOT = "<ABSOLUTE_PROJECT_ROOT>/.aleph/core/aleph-skill"`. Then run `python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json` on POSIX or `python "$env:ALEPH_SKILL_ROOT/scripts/preflight.py" --json` on PowerShell. Never invoke the core through a process-relative path. Pass workspace paths explicitly. Do not copy secrets into artifacts or command prompts.
