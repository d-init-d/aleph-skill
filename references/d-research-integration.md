# D Research integration

D Research is the recommended companion skill for evidence collection. Aleph Skill can run without it, but simulations produced without D Research must mark `research_quality: basic` or `limited` and use more conservative confidence.

## Discovery order

Check for D Research in:

- explicit `--d-research <path>`,
- `D_RESEARCH_SKILL` environment variable,
- `~/.codex/skills/d-research`,
- `~/.agents/skills/d-research`,
- `~/.claude/skills/d-research`,
- `~/.config/opencode/skills/d-research`,
- `D:\Downloads\aleph-qweb 3.7\d-research-skill`,
- installed skill metadata available to the current agent.

If absent, ask the user once per task whether they want to install or enable `d-research-skill`. Do not install automatically. If the user declines, continue in limited mode and do not repeat the prompt during the same task.

## What to delegate

Use D Research for:

- baseline world-state research,
- source discovery,
- public-role person aggregation,
- evidence ledgers,
- contradiction passes,
- source quality scoring,
- blocked-source reports,
- historical, technical, market, and policy research.

## Evidence mapping

Map D Research ledger rows into simulation artifacts:

| D Research field | Simulation use |
|---|---|
| claim | node description, edge mechanism, or evidence note |
| source_url | source node URL |
| source_type | source reliability |
| extracted_evidence | short quote, paraphrase, or numeric value |
| access_method | provenance method |
| confidence | node/claim confidence input |
| contradiction_status | warning or contested claim |

## Prompt pattern

Use a narrow research prompt:

```text
Use D Research to build an evidence ledger for [topic] at [timeframe].
Focus on facts needed for a causal timeline simulation:
1. baseline state,
2. actors and institutions,
3. measurable factors,
4. causal mechanisms,
5. contradictions and source quality.
Return claims with source URLs, dates, short quotes or extracted values, access method, and confidence.
Do not collect private personal data or bypass access controls.
```

## Person/public-role prompt

```text
Use D Research person aggregation for [person] in their public role as [role].
Collect only public-role information relevant to a causal simulation:
biography, role constraints, public decisions, stated beliefs, public relationships, crisis behavior, and uncertainty.
Exclude private contact details, family/private life, private accounts, health speculation, whereabouts, and doxxing material.
```

For material human decision nodes, D Research feeds only the Human Research track. The roleplay track receives the finished dossier and simulated-time situation, not raw browsing authority.

## Fallback mode

When D Research is unavailable:

- use primary/public sources available to the current agent,
- keep evidence ledgers manually in `templates/evidence-map.csv`,
- reduce confidence on edges and actor predictions,
- flag missing contradiction checks,
- report `research_quality: basic`.
