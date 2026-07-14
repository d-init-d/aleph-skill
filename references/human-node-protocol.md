# Sealed human-node protocol

Use this protocol only for a public-role person whose decision materially changes the graph. Apply `references/safety-and-privacy.md` before any network access.

## Three owners

1. **Research execution:** collect public-role evidence and uncertainty with D Research. Never predict or roleplay.
2. **Roleplay execution:** consume one frozen temporal packet offline. Propose decision-graph actions only; never add evidence or likelihood.
3. **Main adjudicator:** compare hypotheses against evidence, preserve alternatives, and assign relative weights or calibrated probabilities.

Research and roleplay require distinct `agent_ref` and `execution_id` values. Roleplay starts only after research completes and its input hash must equal the frozen research output hash.

## Public-role dossier

Record only role-relevant public material:

- public identity, office, and institutional position;
- documented decision patterns;
- public statements and commitments;
- public counterparties and institutional constraints;
- documented crisis behavior;
- evidence gaps, contradictions, and uncertainty.

Every research claim has a stable ID, evidence IDs, evidence confidence, `available_at`, and an actor access basis. Never diagnose, invent motives, or infer sensitive/private traits.

## Decision graph and temporal packet

Declare at least two exact allowed actions under `decision_graph`. Freeze the dossier, scenario hash, decision time, and knowledge cutoff. A claim crosses the seal only when:

- `available_at` is a valid timestamp no later than the cutoff;
- the actor's access is explicitly established as public-role, institutional, directly observed, or known;
- its content is necessary for the modeled public decision.

Keep content-free exclusion metadata with the adjudicator. Never put excluded text into the roleplay packet, prompt, logs, or error message.

## Roleplay output

Use `templates/subagent-roleplay-prompt.md`. Require at least two hypotheses with unique IDs, exact decision-graph actions, public-role reasoning, constraints, unknowns, `status: simulation`, and empty evidence IDs.

Reject any roleplay output containing browsing/tool use, new facts or sources, private motives, probability, confidence, or relative weight. Likelihood belongs to adjudication, not roleplay.

## Receipts and ledger

Write exactly one completed research row and one completed roleplay row per material actor. Each row records actor/track, agent and execution IDs, timestamps, one input/output artifact with SHA-256, receipt ID/hash, previous receipt hash, the attestation class (`host|wrapper|self|none|unknown`), a workspace-relative `receipt_ref` for every `host` or `wrapper` attestation, and status. A ledger string is never proof by itself: the referenced receipt must exist, match the row, and bind the same execution and artifact hashes.

The receipt body additionally binds runtime/adapter IDs, capability snapshot, declared network/tool policies, observed calls, and hashed artifacts. Roleplay declares network and tools denied with no observed calls. Empty, tampered, reordered, overlapping, or mismatched chains fail closed.

Quality Tier A, and therefore `verified` assurance, requires the complete referenced receipt chain to pass HMAC verification with the configured `ALEPH_RECEIPT_KEY`. A referenced, internally consistent but unsigned chain is at most Tier B. Self-attested strings, missing references, or unverifiable receipt bodies are at most Tier C and can never support `verified` output.
