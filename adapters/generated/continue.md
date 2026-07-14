---
name: Aleph 2.0 causal simulation
description: Evidence-grounded timeline simulation guardrails
---

# Aleph for Continue

## Aleph 2.0 execution contract

- Target host: `continue`. Detect available tools and subagents at runtime; never claim capabilities from this file alone.
- Verified core: `.aleph/core/aleph-skill`. Resolve every bundled reference and script from this directory, never from the process working directory.
- Initialize a workspace outside the installed skill directory and use only schema `2.0.0` artifacts.
- Separate fact, inference, assumption, simulation, and counterfactual statements.
- Use D Research 3.x when its identity and version pass `.aleph/core/aleph-skill/scripts/preflight.py`; otherwise declare limited research.
- For every material actor, complete research first, freeze a temporal knowledge packet, then use a distinct offline roleplay execution.
- Roleplay proposes decision-graph actions only. It never browses, adds evidence, invents private motives, or emits probability/confidence.
- Use `relative_weight` unless a declared calibration policy and hindcast gate authorize probability.
- Finalize only after strict validation, replay, integrity receipts, and assurance gates pass.

Run scripts with the host's Python 3.10+ executable, for example `python .aleph/core/aleph-skill/scripts/preflight.py --json`. Pass workspace paths explicitly. Do not copy secrets into artifacts or command prompts.
