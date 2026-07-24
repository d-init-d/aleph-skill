# Sealed human-node protocol

Use this protocol when a person's decision materially changes the graph. It supports real or fictional, living or historical, public or private actors. Lack of public evidence changes unsupported details into explicit assumptions or simulation content; it never makes the actor or scenario unavailable.

## Three owners

1. **Research execution (evidence/mixed only):** collect public-role evidence and uncertainty through the bundled D Research gateway or, after a capability blocker, the limited host-native fallback. Never predict or roleplay. Assumption-only actors skip this execution.
2. **Roleplay execution:** consume one frozen temporal packet offline. Propose decision-graph actions only; never add evidence or likelihood.
3. **Main adjudicator:** compare hypotheses against evidence, preserve alternatives, and assign `relative_weight` by default. It may assign `calibrated_probability` only after every calibration and validation gate passes.

When research exists, research and roleplay require distinct `agent_ref` and `execution_id` values. Roleplay starts only after research completes and its input hash must equal the frozen research output hash. An assumption-only actor begins from a sealed assumption packet and must not fabricate a research receipt.

## Public-role dossier

Record role-relevant evidence when available:

- public identity, office, and institutional position;
- documented decision patterns;
- public statements and commitments;
- public counterparties and institutional constraints;
- documented crisis behavior;
- evidence gaps, contradictions, and uncertainty.

Every research claim has a stable ID, evidence IDs, evidence confidence, `available_at`, and an actor access basis. Creative diagnoses, motives, or sensitive/private traits may be modeled only as explicit assumptions or simulation content, never as sourced facts.

## Decision graph and temporal packet

Declare at least two exact allowed actions under `decision_graph`. Freeze the dossier, scenario hash, decision time, and knowledge cutoff. A claim crosses the seal only when:

- `available_at` is a valid timestamp no later than the cutoff;
- the actor's access is explicitly established as public-role, institutional, directly observed, or known;
- its content is necessary for the modeled public decision.

Keep content-free exclusion metadata with the adjudicator. Never put excluded text into the roleplay packet, prompt, logs, or error message.

An evidence/mixed packet carries only admitted temporal claims. An assumption-only packet carries no claims or research metadata; it seals the dossier's `explicit_assumptions` and `explicit_unknowns` as separate fields. At least one assumption or unknown is required when the packet has no claims.

The packet's `scenario_hash` is the canonical hash of stable scenario identity fields only: schema/simulation IDs, change point, temporal frame, scope, assumptions, and optional active contexts. Its `dossier_hash` binds the public-role dossier, research claims, decision graph, institutional constraints, and uncertainties while excluding mutable execution metadata, roleplay, adjudication, and predicted responses. Validation recomputes both hashes so a signed packet cannot be replayed into another scenario or a changed dossier.

The artifact passed to roleplay is the raw packet object itself, not an envelope containing the packet, dossier, privacy intake, exclusion ledger, or other adjudicator metadata. `actor_packet.py --out` writes this packet-only artifact; its stdout may report content-free adjudicator metadata separately.

## Roleplay output

Use `templates/subagent-roleplay-prompt.md`. Require at least two hypotheses with unique IDs, exact decision-graph actions, creative reasoning, constraints, unknowns, `status: simulation`, and empty evidence IDs. Optional private motives remain simulation content.

Reject any roleplay output containing browsing/tool use or new evidence, mislabeling invented content as sourced fact, or probability, confidence, or relative weight. Creative private motives and other invented details are allowed when their status is `simulation`. Likelihood belongs to adjudication, not roleplay.

## Receipts and ledger

Write exactly one completed research row and one completed roleplay row per evidence/mixed material actor. Write exactly one roleplay row for an assumption-only actor. Each row records actor/track, agent and execution IDs, timestamps, one input/output artifact with SHA-256, receipt ID/hash, previous receipt hash, the attestation class (`host|wrapper|self|none|unknown`), a workspace-relative `receipt_ref` for every `host` or `wrapper` attestation, and status. A ledger string is never proof by itself: the referenced receipt must exist, match the row, and bind the same execution and artifact hashes.

The receipt body additionally binds runtime/adapter IDs, capability snapshot, declared network/tool policies, observed calls, and hashed artifacts. Each ledger row binds exactly one receipt input and one receipt output; an otherwise valid receipt with an extra roleplay input fails closed. The sole assumption-only roleplay row must not chain to a fabricated research receipt. Roleplay declares network and tools denied with no observed calls. Empty, tampered, reordered, overlapping, or mismatched chains fail closed.

Quality Tier A, and therefore `verified` assurance, requires the complete referenced receipt chain to pass HMAC verification with the configured `ALEPH_RECEIPT_KEY`. Aleph reloads the sealed packet and roleplay output, validates both closed semantic contracts, and binds actor, scenario, decision, cutoff, allowed actions, assumptions/unknowns or admitted claims, execution, packet hash, and hypotheses to `actors.json` and the ledger. Valid hashes over arbitrary bytes never qualify for Tier A or B. A referenced, internally consistent but unsigned and semantically valid chain is at most Tier B. Self-attested strings, missing references, invalid packet/output content, or unverifiable receipt bodies are at most Tier C and can never support `verified` output.
