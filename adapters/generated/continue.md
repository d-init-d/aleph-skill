---
name: Aleph 2.0 causal simulation
description: Evidence-grounded timeline simulation guardrails
alwaysApply: false
---

# Aleph for Continue

## Aleph 2.0 execution contract

- Target host: `continue`. Detect available tools and subagents at runtime; never claim capabilities from this file alone.
- Verified core: `<ABSOLUTE_PROJECT_ROOT>/.aleph/core/aleph-skill`. Resolve `<ABSOLUTE_PROJECT_ROOT>` from the absolute path of this loaded rule at `<ABSOLUTE_PROJECT_ROOT>/.continue/rules/aleph.md`, never from the process working directory. Export or assign the resulting absolute core path as `ALEPH_SKILL_ROOT` and refuse to run if its `SKILL.md` is missing.
- Initialize a workspace outside the installed skill directory and use only schema `2.0.0` artifacts.
- Separate fact, inference, assumption, simulation, and counterfactual statements.
- Use D Research 3.x when its identity and version pass the preflight helper under absolute `ALEPH_SKILL_ROOT` at `scripts/preflight.py`; otherwise use the core's limited host-native fallback and cap assurance at `limited`.
- For every material actor, complete research first, freeze a temporal knowledge packet, then use a distinct offline roleplay execution.
- Roleplay proposes decision-graph actions only. It never browses, adds evidence, invents private motives, or emits probability/confidence.
- Use `relative_weight` unless a declared calibration policy and hindcast gate authorize probability.
- Finalize only after strict validation, replay, integrity receipts, and assurance gates pass.

Replace `<ABSOLUTE_PROJECT_ROOT>` with the resolved absolute project path before running a helper. On POSIX, use `export ALEPH_SKILL_ROOT="<ABSOLUTE_PROJECT_ROOT>/.aleph/core/aleph-skill"`; on PowerShell, use `$env:ALEPH_SKILL_ROOT = "<ABSOLUTE_PROJECT_ROOT>/.aleph/core/aleph-skill"`. Then run `python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json` on POSIX or `python "$env:ALEPH_SKILL_ROOT/scripts/preflight.py" --json` on PowerShell. Never invoke the core through a process-relative path. Pass workspace paths explicitly. Do not copy secrets into artifacts or command prompts.
