# Node builder

Build nodes as evidence-bearing simulation objects. Prefer Aleph schema names when a local Aleph repo is available.

## Universal node fields

Every node should include:

```json
{
  "id": "type:slug",
  "type": "entity | event | factor | context | indicator | claim | source",
  "name": "human-readable name",
  "status": "fact | inference | simulation | counterfactual | assumption",
  "timeline": "shared_baseline | observed_baseline | simulated_branch",
  "confidence": 0.0,
  "sources": [],
  "description": ""
}
```

Use lowercase hyphenated slugs. Do not reuse IDs for materially different objects.

## Entity nodes

Entities are persistent actors: people, organizations, nations, groups, regions.

Add:

- `entity_type`,
- `attributes`,
- `decision_patterns`,
- `relationships`,
- `behavioral_drivers`.

For people, use `references/human-node-protocol.md`.

## Event nodes

Events are dated occurrences.

Add:

- `start_time`,
- `end_time`,
- `duration`,
- `actors`,
- `location`,
- `caused_by`,
- `causes`,
- `significance`.

For counterfactual events, set `status: counterfactual` and include the observed-history contrast.

## Factor nodes

Factors are variables that can change.

Add:

- `unit`,
- `frequency`,
- `range`,
- `value_at_change_point`,
- `trend`,
- `thresholds`,
- `indicators`.

Factors are the best carriers for propagation when the effect is measurable.

For numerical dynamics, set `scale` explicitly:

- `level`: recompute from baseline and active inputs on every tick;
- `flow`: a per-day rate recomputed from baseline and active inputs; an edge from flow to stock defaults to rate integration;
- `stock`: carry the prior state across ticks. Prefer non-negative `decay_rate` per day; alternatively use per-day `retention` in `[0,1]`. Either may be a scalar distribution, but do not declare both.

## Context nodes

Contexts modulate causal edges.

Add:

- `active_conditions`,
- `historical_instances`,
- `typical_effects.amplifies`,
- `typical_effects.dampens`,
- `activation_thresholds`.

Contexts may be active at baseline or created dynamically during propagation.

## Indicator nodes

Indicators measure factors.

Add:

- `measures`,
- `source_organization`,
- `unit`,
- `frequency`,
- `current_value`,
- `current_date`,
- `historical_range`.

Use indicators as validation anchors.

## Claim nodes

Claims are source-backed assertions.

Add:

- `statement`,
- `source`,
- `quote_or_value`,
- `quote_status`,
- `page_or_section`,
- `about`,
- `supports`,
- `contradicts`.

Keep contradictory claims; do not merge them away.

## Source nodes

Sources are raw evidence.

Add:

- `source_type`,
- `author`,
- `published_date`,
- `url`,
- `file_path`,
- `reliability_score`,
- `reliability_rationale`,
- `covers`.

Prefer primary data, official records, peer-reviewed research, institutional reports, and high-quality journalism in that order.

## Adaptive completeness

Node detail follows causal materiality and adaptive complexity rather than a named depth level. Critical-path nodes require type-specific fields, directly supported mechanisms, contradiction checks, rival explanations, sensitivity drivers, and calibration anchors. Peripheral nodes may remain compact but still require the complete artifact schema.

Human decision makers on critical paths require an explicit `actor_basis`, sealed packets, and roleplay labeled `simulation`. Evidence/mixed actors separate research from roleplay; assumption-only actors skip research and state assumptions/unknowns directly.
