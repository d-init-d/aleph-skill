# Artifact contract

Use schema version `1.1.0`. The bundled templates are canonical. Read them before writing, clone their exact keys, and replace values. Do not invent alternate field names or statuses.

## Construction rule

1. Initialize with `scripts/init_simulation_workspace.py --profile <profile> --out-dir <user-workspace>/simulation-output`.
2. Keep output outside the installed skill directory.
3. Read every starter artifact in the new workspace.
4. Duplicate complete template objects when adding nodes, edges, actors, and branches.
5. Use only declared IDs. Never use prose in an ID-reference field.
6. Run draft validation after evidence/actors and again after graph/branches. If one artifact produces many field errors, rebuild it from its template instead of patching fields one by one.

## Exact enums

- Node type: `entity`, `event`, `factor`, `context`, `indicator`, `claim`, `source`.
- Content status: `fact`, `inference`, `simulation`, `counterfactual`, `proposed`.
- Source tier: `primary`, `authoritative-secondary`, `secondary`, `tertiary`, `user-provided`.
- Retrieval status: `opened`, `downloaded`, `api`, `local-file`, `user-provided`, `search-snippet`, `blocked`.
- Contradiction status: `corroborated`, `contested`, `contradicted`, `no-conflict-found`, `not-applicable`, `unchecked`.
- Human execution mode: `subagent`, `isolated-pass`.
- D Research capability: `available`, `unavailable`, `unknown` (final output cannot remain `unknown`).
- Subagent capability: `available`, `unavailable`, `unknown` (final output cannot remain `unknown`).

Do not substitute aliases such as `observed`, `modeled`, `verified`, or `web-search-summary`.

Respect the profile budget exactly; the manifest cannot be expanded to fit evidence already collected. At least 50% of basic/quick rows, 60% of standard rows, and 70% of deep rows must be directly accessed rather than search snippets.

## Reference fields

These fields contain IDs, never prose:

- node `sources` and `evidence_ids` -> IDs in `evidence-map.csv`;
- edge `from`/`to` -> node IDs;
- edge `evidence` -> evidence IDs;
- actor `person_node` -> a declared entity node ID;
- actor and track evidence arrays -> evidence IDs;
- branch `causal_trace` -> edge IDs;
- branch `key_decision_points` -> actor IDs;
- branch `evidence_ids` -> evidence IDs;
- trace `edge_id` -> edge ID and trace `from`/`to` -> node IDs.

An assumption ID belongs in `assumption_ref`, not in an evidence array.

## Node contract

Every node uses all keys from `templates/timeline-node.json`:

`id`, `type`, `name`, `status`, `confidence`, `sources`, `assumption_ref`, `description`, `time`, `state_before`, `trigger`, `mechanism`, `state_after`, `lag`, `evidence_ids`, `probability`, `alternative_explanations`, `sensitivity`.

Do not create shortened entity/event objects. If a field is uncertain, provide an explicit unknown/unchanged state and lower confidence.

## Edge and trace contract

Every edge uses all keys from `templates/causal-edge.json`, including non-empty `context_modifiers` and `lag_distribution`. Every propagation row uses all keys from `templates/propagation-trace.jsonl`, especially `step` and `edge_id`.

## Human contract

Every material actor uses all keys from `templates/actor-dossier.json`. Create a matching entity node first. Research and roleplay tracks require separate timestamps, agent references, artifacts, and outputs. Roleplay starts after research completes, contains at least two normalized hypotheses, uses `status: simulation`, and has empty `evidence_ids`.

Write exactly one research row and one roleplay row per material actor to `human-track-ledger.jsonl`. When the runtime exposes a task/subagent tool, both rows use `execution_mode: subagent` with different `agent_ref` values.

## Branch contract

Every branch uses all keys from `templates/branch-ledger.json`. Branch probabilities sum to `1.0`; no branch exceeds `0.60`. `end_state` is an object with a `summary`.

## Completion contract

Set manifest status to `completed`, resolve D Research/subagent capability states, respect the profile source maximum, record repair loops, and mark all checkpoints true only when their artifacts exist. Then run draft validation, render, final validation with report required, re-render, and quality scoring.
