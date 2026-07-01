# Human node protocol

Use this protocol for public-role people whose decisions can change the simulation path.

## Safety boundary

Collect only public-role information relevant to the simulation. Do not collect private contact details, family/private-life details, private accounts, medical or financial data, sexuality/orientation, real-time whereabouts, photos for identification, or doxxing material. Refuse requests involving minors, private individuals, stalking, harassment, or re-identification.

## When deep research is required

Use deep research when a person:

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
- decision_patterns,
- stated_beliefs,
- institutional_constraints,
- relationships,
- crisis_behavior,
- uncertainty_factors,
- predicted_responses.

Cap any single predicted response at 0.80 unless the action is legally or institutionally mandatory.

## Roleplay use

Roleplay is optional and only for high-stakes decision points. It may help generate hypotheses about actions, but it is not evidence.

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

## Common errors

Avoid:

- treating institutions as one person's unconstrained will,
- overfitting to one famous decision,
- ignoring advisors and legal constraints,
- using modern knowledge unavailable at the simulation date,
- turning personality speculation into causal evidence.
