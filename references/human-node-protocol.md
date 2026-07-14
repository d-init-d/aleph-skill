# Sealed human-node protocol

Use this protocol only for a public-role person whose decision materially changes the graph. Apply `references/safety-and-privacy.md` before any network access.

## Three owners

1. **Research execution:** collect public-role evidence and uncertainty with compatible D Research or the limited host-native fallback. Never predict or roleplay.
2. **Roleplay execution:** consume one frozen temporal packet offline. Propose decision-graph actions only; never add evidence or likelihood.
3. **Main adjudicator:** compare hypotheses against evidence, preserve alternatives, and assign `relative_weight` by default. It may assign `calibrated_probability` only after every calibration and validation gate passes.

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

The packet's `scenario_hash` is the canonical hash of stable scenario identity fields only: schema/simulation IDs, change point, temporal frame, scope, assumptions, and optional active contexts. Its `dossier_hash` binds the public-role dossier, research claims, decision graph, institutional constraints, and uncertainties while excluding mutable execution metadata, roleplay, adjudication, and predicted responses. Validation recomputes both hashes so a signed packet cannot be replayed into another scenario or a changed dossier.

The artifact passed to roleplay is the raw packet object itself, not an envelope containing the packet, dossier, privacy intake, exclusion ledger, or other adjudicator metadata. `actor_packet.py --out` writes this packet-only artifact; its stdout may report content-free adjudicator metadata separately.

## Roleplay output

Use `templates/subagent-roleplay-prompt.md`. Require at least two hypotheses with unique IDs, exact decision-graph actions, public-role reasoning, constraints, unknowns, `status: simulation`, and empty evidence IDs.

Reject any roleplay output containing browsing/tool use, new facts or sources, private motives, probability, confidence, or relative weight. Likelihood belongs to adjudication, not roleplay.

## Receipts and ledger

Write exactly one completed research row and one completed roleplay row per material actor. Each row records actor/track, agent and execution IDs, timestamps, one input/output artifact with SHA-256, receipt ID/hash, previous receipt hash, the attestation class (`host|wrapper|self|none|unknown`), a workspace-relative `receipt_ref` for every `host` or `wrapper` attestation, and status. A ledger string is never proof by itself: the referenced receipt must exist, match the row, and bind the same execution and artifact hashes.

The receipt body additionally binds runtime/adapter IDs, capability snapshot, declared network/tool policies, observed calls, and hashed artifacts. Each ledger row binds exactly one receipt input and one receipt output; an otherwise valid receipt with an extra roleplay input fails closed. Roleplay declares network and tools denied with no observed calls. Empty, tampered, reordered, overlapping, or mismatched chains fail closed.

Quality Tier A, and therefore `verified` assurance, requires the complete referenced receipt chain to pass HMAC verification with the configured `ALEPH_RECEIPT_KEY`. Aleph also reloads the packet-only research output and roleplay output, validates both closed semantic contracts, and binds actor, decision, cutoff, allowed actions, execution, packet hash, and hypotheses to `actors.json` and the ledger. Valid hashes over arbitrary bytes never qualify for Tier A or B. A referenced, internally consistent but unsigned and semantically valid chain is at most Tier B. Self-attested strings, missing references, invalid packet/output content, or unverifiable receipt bodies are at most Tier C and can never support `verified` output.
