# D Research integration

D Research is the preferred companion skill for evidence collection. Aleph Timeline Simulator can still run without it, but must mark research quality as `basic` or `limited`.

## Discovery order

Check for D Research in:

- explicit `--d-research <path>`,
- `D_RESEARCH_SKILL` environment variable,
- `~/.codex/skills/d-research`,
- `~/.agents/skills/d-research`,
- `D:\Downloads\aleph-qweb 3.7\d-research-skill`,
- installed skill metadata available to the current agent.

If absent, suggest installing `d-init-d/d-research-skill.git`. Do not install automatically unless the user asks.

## What to delegate

Use D Research for:

- baseline world-state research,
- source discovery,
- public-role person aggregation,
- evidence ledgers,
- contradiction passes,
- source quality scoring,
- blocked-source reports,
- historical and policy research.

## Evidence mapping

Map D Research ledger rows into Aleph simulation artifacts:

| D Research field | Aleph simulation use |
|---|---|
| claim | node description, edge mechanism, or evidence note |
| source_url | source node URL |
| source_type | source reliability |
| extracted_evidence | quote or paraphrase |
| access_method | provenance method |
| confidence | node/claim confidence input |
| contradiction_status | warning or contested claim |

## Prompt pattern

Use a narrow research prompt:

```text
Use D Research to build an evidence ledger for [topic] at [timeframe].
Focus on facts needed for an Aleph causal simulation:
1. baseline state,
2. actors and institutions,
3. measurable factors,
4. causal mechanisms,
5. contradictions and source quality.
Return claims with source URLs, dates, quotes or extracted values, access method, and confidence.
Do not collect private personal data or bypass access controls.
```

## Person/public-role prompt

```text
Use D Research person aggregation for [person] in their public role as [role].
Collect only public-role information relevant to a causal simulation:
biography, role constraints, public decisions, stated beliefs, public relationships, crisis behavior, and uncertainty.
Exclude private contact details, family/private life, private accounts, health speculation, whereabouts, and doxxing material.
```

## Fallback mode

When D Research is unavailable:

- use primary/public sources available to the current agent,
- keep evidence ledgers manually in `templates/evidence-map.csv`,
- reduce confidence on edges and actor predictions,
- report `research_quality: basic`.
