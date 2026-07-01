# Aleph core integration

Aleph is the external causal KB and scenario engine. Do not copy the Aleph repo into this skill.

## Discovery order

Locate Aleph in this order:

1. `--aleph <path>` CLI argument.
2. `ALEPH_REPO` environment variable.
3. Common sibling paths such as `..\Aleph` or `D:\Downloads\aleph-qweb 3.7\Aleph`.
4. GitHub access via `gh repo view d-init-d/Aleph`.

If no local repo exists but `gh` can access the private repository, report that the repo is reachable and ask the user to clone or provide a path before running local Aleph scripts.

## Required Aleph files

Preflight should check for:

- `schemas/scenario.schema.json`
- `schemas/forecast.schema.json`
- `schemas/causal-relation.schema.json`
- `schemas/entity.schema.json`
- `schemas/event.schema.json`
- `schemas/factor.schema.json`
- `scripts/run_scenario_v2.py`
- `scripts/validate.py`
- `scripts/kb_audit.py`
- `scripts/build_graph.py`

## Mapping

Use Aleph types as the canonical vocabulary:

- Entity: person, organization, country, group, region, other.
- Event: dated occurrence or counterfactual/hypothetical event.
- Factor: measurable or observable variable.
- Context: background condition that amplifies or dampens edges.
- Indicator: measurement source for a factor.
- Claim: source-backed assertion.
- Source: document, dataset, report, paper, or public record.
- Causal relation: directed, weighted, lagged, contextual edge.

## Recommended local commands

Run from the Aleph repo:

```powershell
python scripts\validate.py --paths kb --strict
python scripts\kb_audit.py --root kb --profile butterfly
python scripts\run_scenario_v2.py --help
```

Only run scenario execution after the scenario input is valid and the user has provided or approved the local Aleph path.

## Status discipline

Simulation-generated nodes and edges should stay `proposed` unless a human review or Aleph approval process promotes them. Never claim that AI-created causal relations are approved KB facts.
