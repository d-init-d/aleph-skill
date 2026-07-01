# Simulation workflow

Use this protocol for every Aleph Skill timeline simulation.

## Execution profiles and checkpoints

Choose one profile before research and record it in the manifest:

| Profile | Sources | Repair loops | Use |
|---|---:|---:|---|
| `quick` | 4-8 | 1 | bounded exploration |
| `standard` | 6-12 | 2 | default evidence-grounded simulation |
| `deep` | 12-25 | 3 | audit-grade or high-stakes analysis |

Initialize the workspace before opening sources. Update the manifest after each checkpoint: `initialized`, `baseline_researched`, `human_tracks_completed`, `graph_built`, `propagated`, `branched`, and `validated`. Write partial work to artifacts immediately; do not hold the whole simulation in context.

Stop source expansion at the profile maximum unless a named critical gap remains. If the gap cannot be closed within budget, record it and lower confidence. Do not trade completion for unbounded browsing.

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

Prefer directly opened primary and authoritative sources. A search-result snippet is discovery, not strong evidence; cap its confidence at `0.45`. Complete a contradiction pass before setting `baseline_researched: true`.

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

Run validation in this order:

1. draft validation while the report may still be absent,
2. render the Markdown report,
3. final validation with `--require-report`,
4. re-render so the report contains final validation results,
5. quality scoring with threshold `85`.

Attempt no more than the profile's repair-loop budget. If errors remain, return a partial result with the exact validator codes instead of looping indefinitely.

## Stopping rules

Stop propagation when:

- the horizon is reached,
- all remaining effects fall below threshold,
- max hops is reached,
- cycle damping caps are reached,
- evidence gaps make further propagation misleading.

When stopping due to evidence gaps, report the missing evidence and the next research step.
