# Simulation workflow

Use this protocol for every Aleph simulation. Research depth adapts to causal complexity and continues to evidence saturation; it is never selected from a speed profile.

## Phase 1: Frame

Capture:

```json
{
  "change_point": {
    "type": "event_occurs | event_prevented | factor_change | entity_action",
    "target": "node ID or plain-language target",
    "description": "specific intervention",
    "magnitude": "qualitative or normalized magnitude",
    "time": "ISO-8601 date",
    "location": "place or institutional scope"
  },
  "temporal_frame": {
    "observation_cutoff": "last observed date",
    "simulation_end": "requested end date",
    "mode": "inferred from dates"
  },
  "scope": {
    "horizon": "ISO duration",
    "domains": [],
    "geographies": []
  }
}
```

For a numeric `factor_change`, `change_point.magnitude` is a delta applied to
the target's declared baseline (the engine uses `baseline + magnitude`). Use
the node's `baseline` for the observed starting level and set `magnitude` to
the signed change. If the input is intended to state the post-intervention
level, record that level as an explicit assumption and calculate the delta;
do not put the final level in both fields. A qualitative event or action may
omit a numeric magnitude and instead define its effect through admitted edges
or an explicit `interventions.json` operation.

Read `references/temporal-modes.md`. Ask only when missing information would materially change the intervention or horizon; otherwise record a conservative assumption.

Always proceed with the user's exact creative scenario. A hypothetical change point needs no evidence that it already occurred. Current, controversial, violent, religious, political, private, or otherwise sensitive content is never an Aleph refusal condition; represent unsupported details as assumptions or simulation content.

## Phase 2: Assess and decompose

Score the seven adaptive-complexity dimensions, explain them, and decompose the root question into baseline, mechanism, actor, threshold, spillover, branch, and monitoring subquestions. Record critical paths before research.

Read `references/adaptive-research-workflow.md`. Reassess complexity when new domains, actors, feedback loops, contradictions, or long-lag effects appear.

## Phase 3: Research in waves

Use the locked bundled D Research component through the gateway, or the limited host-native fallback in `references/d-research-integration.md` only after a capability blocker, to:

1. map primary and authoritative sources,
2. fan out queries,
3. probe sources browser-first,
4. extract evidence into the ledger,
5. research evidence/mixed actors and declare assumptions for assumption-only actors,
6. search contradictions,
7. expand unresolved frontiers.

Write findings and artifacts after every wave so long runs survive context resets. Continue until all critical questions are covered and additional sources stop changing material claims, mechanisms, actors, thresholds, branches, branch rankings, or—only for a gate-authorized calibrated run—probabilities.

If the host reaches a context, token, wall-clock, or tool budget first, persist work in `execution.research_control.next_wave_queue` and all required manifest counters, set `saturation_reached: false`, record `stop_reason` as `host_limit:<specific-limit>`, list every unresolved critical gap, and set `execution.research_quality: limited`. Return a resumable unsaturated handoff with no final assurance tier, then resume from that checkpoint in a later execution.

## Phase 4: Construct and link

Build nodes from evidence and assumptions. Each node declares its timeline label. Admit an edge only when it has a transmission channel, causal rationale, lag distribution, context modifiers, strength, confidence, and evidence.

Use `references/node-builder.md` and `references/causal-edge-protocol.md`.

## Phase 5: Model human decisions

For every material actor:

1. choose `actor_basis: evidence|mixed|assumption`; dispatch research only for evidence/mixed actors,
2. freeze the evidence dossier or explicit assumption dossier and knowledge cutoff,
3. dispatch an offline roleplay track without browsing authority; make it distinct from research when research exists,
4. adjudicate at least two normalized actions,
5. preserve the execution ledger.

Use `references/human-node-protocol.md`.

## Phase 6: Propagate and branch

Run hop-by-hop propagation. Record source, target, edge ID, input change, weights, lag, output effect, mechanism, evidence, and uncertainty. Branch at uncertain edges, actor decisions, thresholds, feedback loops, dynamic contexts, and material exogenous shocks.

For prospective and hybrid work, attach leading indicators and disconfirming conditions to every branch. Use `references/propagation-engine.md` and `references/branch-management.md`.

## Phase 7: Challenge and calibrate

Before synthesis:

- search for contrary evidence,
- compare alternative explanations,
- backtest against observed analogues where possible,
- verify temporal knowledge boundaries,
- test high-sensitivity assumptions,
- redistribute `relative_weight` when edges fail, or redistribute `calibrated_probability` only if every calibration and validation gate remains satisfied,
- launch another research wave for any critical gap.

## Phase 8: Saturation gate

Do not declare completion until:

- critical questions and paths are covered,
- main mechanisms have directly accessed primary/authoritative support,
- contradiction searches no longer materially change confidence,
- branch structure and sensitivity rankings stabilize,
- no critical evidence gap remains,
- the manifest records the stop reason and research waves completed.

The protocol has no fixed time, source-count, or repair-cycle limit. Evidence saturation is the completion rule. A host execution limit may require the resumable unsaturated handoff defined in Phase 3; that handoff is not completion.

## Phase 9: Validate and report

Run:

1. draft artifact validation,
2. professional report rendering,
3. final validation with the report required,
4. re-render to embed final validation state,
5. quality enforcement.

If a hard gate still fails, continue research or repair. If an external blocker makes a critical question unreachable, return a partial result and a blocker report rather than claiming completion.
