# Node builder

Build nodes as evidence-bearing simulation objects. Prefer Aleph schema names when a local Aleph repo is available.

## Universal node fields

The canonical minimal node — every field below is required by the validator (this exact object lives in `tests/fixtures/canonical/minimal-graph.json` and is CI-validated):

```json
{
  "id": "factor:policy-rate",
  "type": "factor",
  "name": "Policy rate",
  "description": "Central bank policy interest rate.",
  "time": "2026-01-01",
  "state_before": {
    "summary": "Policy rate at baseline.",
    "value": 4.5,
    "unit": "percent"
  },
  "trigger": {
    "kind": "decision",
    "description": "Scheduled rate decision."
  },
  "mechanism": "The monetary policy committee votes on and publishes the policy rate at scheduled meetings.",
  "state_after": {
    "summary": "Policy rate raised.",
    "value": 5.0,
    "unit": "percent"
  },
  "lag": "P0D",
  "evidence_ids": [
    "evidence:example"
  ],
  "status": "fact",
  "timeline": "shared_baseline",
  "confidence": 0.9
}
```

`type` is one of `entity | event | factor | context | indicator | claim | source`; `status` is one of `fact | inference | simulation | counterfactual | assumption`; `timeline` is one of `shared_baseline | observed_baseline | simulated_branch`. `mechanism` needs a concrete sentence (ten words or more). Optional universal fields include `sources`, `probability`, `assumption_ref`, `alternative_explanations`, `sensitivity`, `role`, `datatype`, `unit`, `scale`, `baseline`, `bounds`, `retention`, and `decay_rate`.

Use lowercase hyphenated slugs. Do not reuse IDs for materially different objects.

## Type-specific details (schema 2.1.0)

In schema `2.1.0` workspaces, type-specific fields go inside the optional `details` object, discriminated by the node `type`; the validator refuses detail keys that belong to a different type. In schema `2.0.0` workspaces `details` is not accepted — keep type-specific context in `description`, `mechanism`, or evidence instead. A 2.1 node may also carry an optional `extensions` object whose keys are namespaced (for example `org.example/rating`):

```json
{
  "details": {
    "unit": "percent",
    "trend": "rising",
    "thresholds": []
  },
  "extensions": {
    "org.example/rating": "AA"
  }
}
```

## Entity nodes

Entities are persistent actors: people, organizations, nations, groups, regions.

Add under `details` (schema 2.1.0):

- `entity_type`,
- `attributes`,
- `decision_patterns`,
- `relationships`,
- `behavioral_drivers`.

For people, use `references/human-node-protocol.md`.

## Event nodes

Events are dated occurrences.

Add under `details` (schema 2.1.0):

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

Add under `details` (schema 2.1.0):

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

Add under `details` (schema 2.1.0):

- `active_conditions`,
- `historical_instances`,
- `typical_effects` (with `amplifies` and `dampens` lists),
- `activation_thresholds`.

Contexts may be active at baseline or created dynamically during propagation.

## Indicator nodes

Indicators measure factors.

Add under `details` (schema 2.1.0):

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

Add under `details` (schema 2.1.0):

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

Add under `details` (schema 2.1.0):

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
