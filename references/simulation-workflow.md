# Simulation workflow

Use this protocol for every Aleph Skill timeline simulation.

## Phase 1: Define

Capture a change point before research or propagation:

```json
{
  "change_point": {
    "type": "event_occurs | event_prevented | factor_change | entity_action",
    "target": "node id or plain-language target",
    "description": "specific intervention",
    "magnitude": "qualitative or normalized numeric magnitude",
    "time": "YYYY-MM-DD or YYYY",
    "location": "place or institutional scope"
  },
  "scope": {
    "horizon": "duration to simulate",
    "domain": "economics | geopolitics | technology | society | mixed",
    "depth": "shallow | medium | deep"
  },
  "active_contexts": []
}
```

Ask only if the missing field would materially change the result. Otherwise make a conservative assumption and record it.

## Phase 2: Research

Build the factual baseline at the change time:

- key entities and decision makers,
- ongoing events,
- measurable factors,
- active contexts,
- known indicators,
- existing causal relations,
- unresolved contradictions.

Use D Research for deep/public-source work when available. If D Research is missing, ask the user once whether they want to install or enable it; otherwise continue in limited mode. Preserve contradictions instead of smoothing them into a single narrative.

## Phase 3: Construct

Build nodes from evidence and assumptions. Each node must declare:

- `id`,
- `type`,
- `name`,
- `status`,
- `confidence`,
- `sources` or `assumption_ref`,
- `description`.

Use `references/node-builder.md` for type-specific fields.

## Phase 4: Link

Create causal edges only after the edge passes the mechanism test:

- What transmits the effect?
- How does it reach the target?
- Why is this causal rather than correlation?
- When does the effect arrive?
- Under which contexts is it stronger or weaker?

Use `references/causal-edge-protocol.md`.

## Phase 5: Propagate

Run hop-by-hop propagation. Keep a trace for every effect above threshold:

- source node,
- target node,
- input change,
- edge weights,
- lag,
- output effect,
- mechanism,
- evidence,
- uncertainty.

Use `references/propagation-engine.md`.

## Phase 6: Branch

Branch on:

- uncertain edges,
- human decision points,
- threshold crossings,
- feedback loops,
- dynamic context creation,
- low-probability high-impact events.

Use `references/branch-management.md`.

## Phase 7: Validate

If the simulated period overlaps known history, backtest against observed data. If it reaches the future, run audit gates only and label the result as scenario analysis.

Validation must cover:

- provenance coverage,
- mechanism completeness,
- confidence calibration,
- branch probability normalization,
- human-node research quality and research/roleplay separation,
- sensitivity points,
- safety/privacy compliance.

## Stopping rules

Stop propagation when:

- the horizon is reached,
- all remaining effects fall below threshold,
- max hops is reached,
- cycle damping caps are reached,
- evidence gaps make further propagation misleading.

When stopping due to evidence gaps, report the missing evidence and the next research step.
