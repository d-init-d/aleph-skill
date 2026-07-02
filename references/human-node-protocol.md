# Human node protocol

Use this protocol for public-role people whose decisions can change the simulation path.

## Safety boundary

Collect only public-role information relevant to the simulation. Do not collect private contact details, family/private-life details, private accounts, medical or financial data, sexuality/orientation, real-time whereabouts, photos for identification, or doxxing material. Refuse requests involving minors, private individuals, stalking, harassment, or re-identification.

## Mandatory split for material actors

For every material human decision node, split work into three roles:

1. Human Research track: builds the sourced actor dossier. It uses D Research when available and never roleplays.
2. Human Roleplay track: receives only the dossier, allowed actions, constraints, and simulated-time facts. It generates hypotheses and never gathers evidence.
3. Main simulator: adjudicates both tracks, compares hypotheses against evidence, creates alternative branches, and assigns conservative probabilities.

Inspect the exposed tools before deciding. If a task/subagent/agent tool exists, subagents are available: dispatch one research subagent and, only after its dossier is frozen, a different roleplay subagent for each material actor. Sequential dispatch is acceptable when concurrency is limited. Do not use the same subagent for both roles.

If no subagent tool exists, run two isolated main-context passes with distinct `agent_ref` values and an explicit fallback reason. A sentence saying the tracks were separated is not evidence of separation.

Record each execution in `human-track-ledger.jsonl` with actor ID, track, execution mode, distinct agent reference, start/completion timestamps, input artifacts, output artifact, and status. The validator cross-checks this ledger against `actors.json`.

## When research must expand

Expand public-role research until saturation when a person:

- is a key decision maker,
- appears in three or more causal edges,
- is on a critical amplification path,
- has uncertain response with major downstream effects,
- is the target of a user-requested human-behavior simulation.

## Five-layer public-role dossier

1. Biographical foundation: public career timeline, offices, education, role-relevant formative events.
2. Decision pattern analysis: major public decisions, context, action, outcome, pattern.
3. Beliefs and values: public statements, writings, speeches, voting records, institutional commitments.
4. Relationship map: public allies, rivals, advisors, institutions, constraints.
5. Behavioral uncertainty: documented crisis behavior, risk tolerance, decision speed, information channels, known limits of evidence.

Do not infer clinical diagnoses or unsupported psychological traits.

## Actor dossier fields

Use `templates/actor-dossier.json` with:

- identity,
- public_role,
- evidence_ids,
- research_track,
- roleplay_track,
- adjudication,
- decision_patterns,
- stated_beliefs,
- institutional_constraints,
- relationships,
- crisis_behavior,
- uncertainty_factors,
- predicted_responses.

The research track must contain sourced `claims`. The roleplay track must contain a simulated-time `knowledge_cutoff`, the dossier evidence IDs it received, and at least two hypotheses whose probabilities sum to `1.0`. Roleplay hypotheses must use `status: simulation` and an empty `evidence_ids` list. Actor predicted responses must also provide at least two normalized alternatives.

Cap any single predicted response at 0.80 unless the action is legally or institutionally mandatory.

## Research track prompt

Use `templates/subagent-research-prompt.md` for a dedicated research pass. It must return only sourced public-role claims and uncertainty notes.

## Roleplay track prompt

Use `templates/subagent-roleplay-prompt.md` only after the dossier is ready.

Before roleplay:

- provide the actor dossier,
- provide only information available at the simulated time,
- list available actions and constraints,
- ask for a decision and reasoning.

After roleplay:

- extract the proposed action,
- compare it with the dossier,
- assign probability conservatively,
- create alternative branches,
- mark the roleplay row as `simulation`, not `fact`.
- preserve the raw hypothesis in the roleplay track and the simulator's acceptance/rejection decision in `adjudication`.

## Common errors

Avoid:

- treating institutions as one person's unconstrained will,
- overfitting to one famous decision,
- ignoring advisors and legal constraints,
- using modern knowledge unavailable at the simulation date,
- turning personality speculation into causal evidence,
- letting roleplay outputs become evidence ledger rows.
