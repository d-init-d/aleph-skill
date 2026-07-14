"""Sealed actor packets, roleplay validation, and execution receipts.

The functions in this module are deliberately fail closed.  A packet is an
immutable-by-contract JSON value bound to a dossier and scenario hash; a
roleplay response may only propose actions already present in the decision
graph; and a receipt chain is accepted only when every hash, HMAC, policy,
identifier, and timestamp can be verified.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from pathlib import Path
from typing import Any, TypeGuard, cast

from .io import canonical_hash, load_json_secure, sha256_file
from .issues import Issue, issue
from .privacy import DOXXING_PATTERNS, SENSITIVE_KEYS, SENSITIVE_VALUE_PATTERNS
from .schema import parse_time

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
ACTOR_ACCESS_ALLOWED = frozenset({"known", "public_role", "institutional", "directly_observed"})
RECEIPT_FIELDS = frozenset(
    {
        "id",
        "runtime_id",
        "adapter_id",
        "execution_id",
        "parent_execution_id",
        "started_at",
        "completed_at",
        "inputs",
        "outputs",
        "declared_network_policy",
        "declared_tool_policy",
        "observed_tool_calls",
        "capability_snapshot_hash",
        "previous_receipt_hash",
        "receipt_hash",
        "hmac",
    }
)
ROLEPLAY_OUTPUT_FIELDS = frozenset(
    {
        "packet_hash",
        "actor_id",
        "decision_id",
        "execution_id",
        "status",
        "network_used",
        "tools_used",
        "browsed",
        "hypotheses",
    }
)
ROLEPLAY_HYPOTHESIS_FIELDS = frozenset(
    {
        "id",
        "action",
        "public_role_reasoning",
        "reasoning",
        "constraints_applied",
        "known_unknowns",
        "status",
        "evidence_ids",
        "triggers",
    }
)
KNOWLEDGE_PACKET_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "frozen",
        "actor_id",
        "decision_id",
        "decision_time",
        "knowledge_cutoff",
        "dossier_hash",
        "scenario_hash",
        "claims",
        "institutional_constraints",
        "allowed_actions",
        "explicit_unknowns",
        "packet_hash",
    }
)
KNOWLEDGE_CLAIM_FIELDS = frozenset({"id", "text", "available_at", "actor_access", "access_basis"})
ROLEPLAY_PROBABILITY_KEYS = frozenset(
    {"probability", "confidence", "relative_weight", "likelihood", "odds", "chance"}
)
ROLEPLAY_EVIDENCE_KEYS = frozenset(
    {"evidence", "evidence_ids", "facts", "sources", "source", "citations", "citation"}
)
PRIVATE_MOTIVE_RE = re.compile(
    r"\b(secretly|private motive|inner desire|diagnos(?:e|is)|mental disorder|"
    r"blackmail|family pressure|romantic|sexual|home address|personal phone)\b",
    re.IGNORECASE,
)
ROLEPLAY_LIKELIHOOD_TEXT_RE = re.compile(
    r"(?:\b\d{1,3}(?:\.\d+)?\s*(?:%|percent)\b|\b(?:odds|chance|likelihood|probability|"
    r"probable|more\s+likely|less\s+likely)\b)",
    re.IGNORECASE,
)
ROLEPLAY_SOURCE_TEXT_RE = re.compile(
    r"(?:https?://|\bdoi:\s*10\.\d{4,9}/|\baccording\s+to\s+(?!the\s+(?:packet|scenario)\b)|"
    r"\b(?:source|citation|study|report|dataset|evidence)\s+(?:shows|says|states|indicates)\b)",
    re.IGNORECASE,
)


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(HASH_RE.fullmatch(value.lower()))


def _valid_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def _normalise_field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _scan_sealed_value(value: Any, pointer: str, issues: list[Issue], *, roleplay: bool) -> None:
    """Recursively reject hidden PII, private motives, likelihood, or evidence."""
    if isinstance(value, dict):
        for key, nested in value.items():
            field = _normalise_field_name(key)
            child = f"{pointer}/{key}"
            if field in SENSITIVE_KEYS or field in {"private_motive", "private_motives"}:
                issues.append(issue("PRIVACY_REFUSAL", pointer=child, message="private or sensitive field is forbidden"))
            if roleplay and field in ROLEPLAY_PROBABILITY_KEYS and nested not in (None, "", [], {}):
                issues.append(issue("ROLEPLAY_PROBABILITY", pointer=child, message="roleplay cannot emit likelihood"))
            if roleplay and field in ROLEPLAY_EVIDENCE_KEYS and nested not in (None, "", [], {}):
                issues.append(issue("ROLEPLAY_EVIDENCE", pointer=child, message="roleplay cannot add evidence"))
            _scan_sealed_value(nested, child, issues, roleplay=roleplay)
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _scan_sealed_value(nested, f"{pointer}/{index}", issues, roleplay=roleplay)
    elif isinstance(value, str):
        if PRIVATE_MOTIVE_RE.search(value):
            issues.append(issue("PRIVACY_REFUSAL", pointer=pointer, message="private motive or sensitive inference refused"))
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
            issues.append(issue("PRIVACY_REFUSAL", pointer=pointer, message="sensitive personal data is forbidden"))
        if any(pattern.search(value) for pattern in DOXXING_PATTERNS):
            issues.append(issue("PRIVACY_REFUSAL", pointer=pointer, message="doxxing or manipulation content is forbidden"))
        if roleplay and ROLEPLAY_LIKELIHOOD_TEXT_RE.search(value):
            issues.append(issue("ROLEPLAY_PROBABILITY", pointer=pointer, message="roleplay prose cannot express likelihood"))
        if roleplay and ROLEPLAY_SOURCE_TEXT_RE.search(value):
            issues.append(issue("ROLEPLAY_EVIDENCE", pointer=pointer, message="roleplay prose cannot add sources or citations"))


def freeze_dossier(dossier: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied dossier whose digest binds the frozen content."""
    frozen = copy.deepcopy(dossier)
    frozen["frozen"] = True
    frozen["dossier_hash_algorithm"] = "sha256-canonical-json-v1"
    dossier_hash = canonical_hash(frozen)
    return {"dossier": frozen, "dossier_hash": dossier_hash}


def build_knowledge_packet(
    *,
    actor_id: str,
    decision_id: str,
    decision_time: str,
    knowledge_cutoff: str,
    dossier_hash: str,
    scenario_hash: str,
    claims: list[dict[str, Any]],
    institutional_constraints: list[str],
    allowed_actions: list[str],
    unknowns: list[str],
) -> dict[str, Any]:
    """Build a temporal packet without exposing excluded claim content.

    Every admitted claim requires a valid ``available_at`` timestamp and an
    explicit ``actor_access`` value.  Exclusions are returned to the
    adjudicator as content-free audit metadata and are never embedded in the
    roleplay packet.
    """
    issues: list[Issue] = []
    cutoff = parse_time(knowledge_cutoff)
    decision = parse_time(decision_time)
    if cutoff is None:
        issues.append(issue("TEMPORAL_KNOWLEDGE", pointer="knowledge_cutoff", message="invalid ISO-8601 timestamp"))
    if decision is None:
        issues.append(issue("TEMPORAL_KNOWLEDGE", pointer="decision_time", message="invalid ISO-8601 timestamp"))
    if cutoff is not None and decision is not None and cutoff > decision:
        issues.append(issue("PACKET_CUTOFF", pointer="knowledge_cutoff", message="knowledge cutoff is after decision time"))
    if not _valid_id(actor_id) or not actor_id.startswith("actor:"):
        issues.append(issue("EMPTY_ID", pointer="actor_id", message="actor_id must be a stable actor:* ID", actual=actor_id))
    if not _valid_id(decision_id):
        issues.append(issue("EMPTY_ID", pointer="decision_id", message="invalid decision ID", actual=decision_id))
    if not _valid_hash(dossier_hash):
        issues.append(issue("TYPE", pointer="dossier_hash", message="expected sha256 hex", actual=dossier_hash))
    if not _valid_hash(scenario_hash):
        issues.append(issue("TYPE", pointer="scenario_hash", message="expected sha256 hex", actual=scenario_hash))

    admitted: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_claim_ids: set[str] = set()
    for index, raw_claim in enumerate(claims):
        pointer = f"claims/{index}"
        if not isinstance(raw_claim, dict):
            issues.append(issue("TYPE", pointer=pointer, message="claim must be an object"))
            continue
        claim = copy.deepcopy(raw_claim)
        claim_id = claim.get("id")
        if not _valid_id(claim_id):
            issues.append(issue("EMPTY_ID", pointer=f"{pointer}/id", message="claim requires a stable ID", actual=claim_id))
            continue
        if claim_id in seen_claim_ids:
            issues.append(issue("DUPLICATE_ID", pointer=f"{pointer}/id", actual=claim_id))
            continue
        seen_claim_ids.add(claim_id)
        available_raw = claim.get("available_at")
        available_at = parse_time(available_raw)
        access = claim.get("actor_access")
        claim_text = str(claim.get("text") or claim.get("claim") or "").strip()
        exclusion_reason: str | None = None
        if not claim_text:
            exclusion_reason = "empty-claim"
            issues.append(issue("MISSING_FIELD", pointer=f"{pointer}/text", message="claim text required"))
        if available_at is None:
            exclusion_reason = "invalid-or-missing-available-at"
            issues.append(issue("TEMPORAL_KNOWLEDGE", pointer=f"{pointer}/available_at", message="valid ISO-8601 timestamp required"))
        elif cutoff is not None and available_at > cutoff:
            exclusion_reason = "post-cutoff"
            issues.append(issue("PACKET_CUTOFF", severity="warning", pointer=pointer, message="post-cutoff claim excluded", actual=claim_id))
        if access not in ACTOR_ACCESS_ALLOWED:
            exclusion_reason = exclusion_reason or "actor-access-not-established"
            issues.append(issue("TEMPORAL_KNOWLEDGE", pointer=f"{pointer}/actor_access", message="actor access must be explicitly established", actual=access))
        if exclusion_reason:
            excluded.append(
                {
                    "claim_id": claim_id,
                    "reason": exclusion_reason,
                    "claim_hash": canonical_hash(claim),
                }
            )
            continue
        # Only a narrow, roleplay-safe claim projection crosses the seal.
        admitted.append(
            {
                "id": claim_id,
                "text": claim_text,
                "available_at": available_raw,
                "actor_access": access,
                "access_basis": str(claim.get("access_basis") or "").strip(),
            }
        )

    actions = [str(value).strip() for value in allowed_actions if isinstance(value, str) and value.strip()]
    if not actions or len(actions) != len(set(actions)):
        issues.append(issue("MATERIALITY_GRAPH", pointer="allowed_actions", message="decision graph requires unique allowed actions"))
    constraints = [str(value).strip() for value in institutional_constraints if isinstance(value, str) and value.strip()]
    packet = {
        "schema_version": "2.0.0",
        "id": f"packet:{actor_id}:{decision_id}",
        "frozen": True,
        "actor_id": actor_id,
        "decision_id": decision_id,
        "decision_time": decision_time,
        "knowledge_cutoff": knowledge_cutoff,
        "dossier_hash": dossier_hash,
        "scenario_hash": scenario_hash,
        "claims": admitted,
        "institutional_constraints": constraints,
        "allowed_actions": actions,
        "explicit_unknowns": [str(value).strip() for value in unknowns if isinstance(value, str) and value.strip()],
    }
    packet["packet_hash"] = canonical_hash(packet)
    issues.extend(validate_knowledge_packet(packet))
    return {
        "ok": not any(item.severity == "error" for item in issues),
        "packet": packet,
        "exclusion_ledger": excluded,
        "issues": [item.to_dict() for item in issues],
    }


def validate_knowledge_packet(packet: dict[str, Any]) -> list[Issue]:
    """Revalidate a serialized packet and its canonical hash."""
    problems: list[Issue] = []
    if not isinstance(packet, dict):
        return [issue("TYPE", pointer="packet", message="packet must be an object")]
    for key in sorted(set(packet) - KNOWLEDGE_PACKET_FIELDS):
        problems.append(issue("UNKNOWN_FIELD", pointer=f"packet/{key}", message="packet field is not allowed"))
    for key in KNOWLEDGE_PACKET_FIELDS - {"packet_hash"}:
        if key not in packet:
            problems.append(issue("MISSING_FIELD", pointer=f"packet/{key}", message="required"))
    if packet.get("schema_version") != "2.0.0":
        problems.append(issue("SCHEMA", pointer="packet/schema_version", expected="2.0.0", actual=packet.get("schema_version")))
    if not _valid_id(packet.get("id")) or not str(packet.get("id", "")).startswith("packet:"):
        problems.append(issue("EMPTY_ID", pointer="packet/id", message="stable packet:* ID required"))
    if not _valid_id(packet.get("actor_id")) or not str(packet.get("actor_id", "")).startswith("actor:"):
        problems.append(issue("EMPTY_ID", pointer="packet/actor_id", message="stable actor:* ID required"))
    if not _valid_id(packet.get("decision_id")):
        problems.append(issue("EMPTY_ID", pointer="packet/decision_id", message="stable decision ID required"))
    for key in ("dossier_hash", "scenario_hash"):
        if not _valid_hash(packet.get(key)):
            problems.append(issue("TYPE", pointer=f"packet/{key}", message="sha256 required"))
    if packet.get("frozen") is not True:
        problems.append(issue("HARD_GATE", pointer="packet/frozen", message="knowledge packet is not frozen"))
    stored = packet.get("packet_hash")
    body = {key: value for key, value in packet.items() if key != "packet_hash"}
    if not _valid_hash(stored) or stored != canonical_hash(body):
        problems.append(issue("STALE_ARTIFACT", pointer="packet/packet_hash", message="knowledge packet hash mismatch"))
    cutoff = parse_time(packet.get("knowledge_cutoff"))
    decision = parse_time(packet.get("decision_time"))
    if cutoff is None or decision is None or cutoff > decision:
        problems.append(issue("TEMPORAL_KNOWLEDGE", pointer="packet", message="invalid packet temporal boundary"))
    if "exclusion_ledger" in packet or "excluded_claims" in packet:
        problems.append(issue("TEMPORAL_KNOWLEDGE", pointer="packet", message="excluded content must not cross the roleplay seal"))
    claims = packet.get("claims")
    if not isinstance(claims, list) or not claims:
        problems.append(issue("MISSING_FIELD", pointer="packet/claims", message="non-empty claims array required"))
        claims = []
    seen_claims: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            problems.append(issue("TYPE", pointer=f"packet/claims/{index}"))
            continue
        for key in sorted(set(claim) - KNOWLEDGE_CLAIM_FIELDS):
            problems.append(issue("UNKNOWN_FIELD", pointer=f"packet/claims/{index}/{key}", message="claim field is not allowed"))
        claim_id = claim.get("id")
        if not _valid_id(claim_id) or claim_id in seen_claims:
            problems.append(issue("DUPLICATE_ID" if claim_id in seen_claims else "EMPTY_ID", pointer=f"packet/claims/{index}/id", actual=claim_id))
        else:
            seen_claims.add(claim_id)
        available = parse_time(claim.get("available_at"))
        if available is None or cutoff is None or available > cutoff:
            problems.append(issue("PACKET_CUTOFF", pointer=f"packet/claims/{index}"))
        if claim.get("actor_access") not in ACTOR_ACCESS_ALLOWED:
            problems.append(issue("TEMPORAL_KNOWLEDGE", pointer=f"packet/claims/{index}/actor_access"))
        if not isinstance(claim.get("text"), str) or not claim.get("text", "").strip():
            problems.append(issue("MISSING_FIELD", pointer=f"packet/claims/{index}/text"))
        if not isinstance(claim.get("access_basis"), str) or not claim.get("access_basis", "").strip():
            problems.append(issue("MISSING_FIELD", pointer=f"packet/claims/{index}/access_basis"))
    for field in ("institutional_constraints", "allowed_actions", "explicit_unknowns"):
        values = packet.get(field)
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and item.strip() for item in values):
            problems.append(issue("TYPE", pointer=f"packet/{field}", message="non-empty string array required"))
        elif len(values) != len(set(values)):
            problems.append(issue("DUPLICATE_ID", pointer=f"packet/{field}", message="values must be unique"))
    _scan_sealed_value(body, "packet", problems, roleplay=False)
    return problems


def validate_roleplay_output(output: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Validate a roleplay response as a sealed, evidence-free hypothesis set."""
    issues = validate_knowledge_packet(packet)
    if not isinstance(output, dict):
        issues.append(issue("TYPE", pointer="roleplay_output", message="output must be an object"))
        return {"ok": False, "issues": [item.to_dict() for item in issues]}
    _scan_sealed_value(output, "roleplay_output", issues, roleplay=True)
    for key in sorted(set(output) - ROLEPLAY_OUTPUT_FIELDS):
        issues.append(issue("UNKNOWN_FIELD", pointer=f"roleplay_output/{key}", message="roleplay field is not allowed"))
    for key in (
        "packet_hash",
        "actor_id",
        "decision_id",
        "execution_id",
        "status",
        "network_used",
        "tools_used",
        "browsed",
        "hypotheses",
    ):
        if key not in output:
            issues.append(issue("MISSING_FIELD", pointer=f"roleplay_output/{key}"))
    for key in ("probability", "confidence", "relative_weight", "evidence", "facts", "sources", "private_motive"):
        if key in output:
            issues.append(issue("ROLEPLAY_PROBABILITY" if key in {"probability", "confidence", "relative_weight"} else "ROLEPLAY_EVIDENCE", pointer=f"roleplay_output/{key}", message="forbidden roleplay field"))
    if output.get("packet_hash") != packet.get("packet_hash"):
        issues.append(issue("STALE_ARTIFACT", pointer="roleplay_output/packet_hash", message="response is not bound to this packet"))
    if output.get("actor_id") != packet.get("actor_id") or output.get("decision_id") != packet.get("decision_id"):
        issues.append(issue("TRACK_MISMATCH", pointer="roleplay_output", message="actor/decision mismatch"))
    if output.get("status") != "completed":
        issues.append(issue("INCOMPLETE", pointer="roleplay_output/status", expected="completed", actual=output.get("status")))
    if not _valid_id(output.get("execution_id")):
        issues.append(issue("RECEIPT_EXECUTION_ID", pointer="roleplay_output/execution_id", message="stable execution ID required"))
    if output.get("network_used") is not False or output.get("browsed") is not False:
        issues.append(issue("ROLEPLAY_NETWORK", message="roleplay cannot browse or use network"))
    tools_used = output.get("tools_used")
    if tools_used != []:
        issues.append(issue("ROLEPLAY_NETWORK", message="roleplay cannot call tools"))

    allowed = set(packet.get("allowed_actions") or [])
    hypotheses = output.get("hypotheses")
    if not isinstance(hypotheses, list) or len(hypotheses) < 2:
        issues.append(issue("MISSING_FIELD", pointer="roleplay_output/hypotheses", message="at least two hypotheses required"))
        hypotheses = []
    seen_ids: set[str] = set()
    for index, hypothesis in enumerate(hypotheses):
        pointer = f"roleplay_output/hypotheses/{index}"
        if not isinstance(hypothesis, dict):
            issues.append(issue("TYPE", pointer=pointer))
            continue
        for key in sorted(set(hypothesis) - ROLEPLAY_HYPOTHESIS_FIELDS):
            code = "ROLEPLAY_PROBABILITY" if key in {"probability", "confidence", "relative_weight"} else "UNKNOWN_FIELD"
            issues.append(issue(code, pointer=f"{pointer}/{key}", message="field is not allowed in a roleplay hypothesis"))
        hypothesis_id = hypothesis.get("id")
        if not _valid_id(hypothesis_id) or hypothesis_id in seen_ids:
            issues.append(issue("DUPLICATE_ID" if hypothesis_id in seen_ids else "EMPTY_ID", pointer=f"{pointer}/id", actual=hypothesis_id))
        else:
            seen_ids.add(hypothesis_id)
        action = hypothesis.get("action")
        if not isinstance(action, str) or action not in allowed:
            issues.append(issue("ENUM", pointer=f"{pointer}/action", message="action outside sealed decision graph", actual=action))
        if hypothesis.get("status") != "simulation":
            issues.append(issue("ENUM", pointer=f"{pointer}/status", expected="simulation", actual=hypothesis.get("status")))
        if hypothesis.get("evidence_ids") not in (None, []):
            issues.append(issue("ROLEPLAY_EVIDENCE", pointer=f"{pointer}/evidence_ids", message="roleplay cannot introduce evidence"))
        if hypothesis.get("public_role_reasoning") is not None and hypothesis.get("reasoning") is not None:
            issues.append(issue("UNKNOWN_FIELD", pointer=pointer, message="use exactly one reasoning field"))
        reasoning = hypothesis.get("public_role_reasoning") or hypothesis.get("reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            issues.append(issue("MISSING_FIELD", pointer=f"{pointer}/public_role_reasoning"))
        elif PRIVATE_MOTIVE_RE.search(reasoning):
            issues.append(issue("PRIVACY_REFUSAL", pointer=f"{pointer}/public_role_reasoning", message="private motive or sensitive inference refused"))
        constraints_applied = hypothesis.get("constraints_applied")
        if not isinstance(constraints_applied, list):
            issues.append(issue("MISSING_FIELD", pointer=f"{pointer}/constraints_applied"))
        elif any(value not in set(packet.get("institutional_constraints") or []) for value in constraints_applied):
            issues.append(issue("ROLEPLAY_EVIDENCE", pointer=f"{pointer}/constraints_applied", message="constraint was not present in the sealed packet"))
        known_unknowns = hypothesis.get("known_unknowns")
        if not isinstance(known_unknowns, list):
            issues.append(issue("MISSING_FIELD", pointer=f"{pointer}/known_unknowns"))
        elif any(value not in set(packet.get("explicit_unknowns") or []) for value in known_unknowns):
            issues.append(issue("ROLEPLAY_EVIDENCE", pointer=f"{pointer}/known_unknowns", message="unknown was not present in the sealed packet"))
        triggers = hypothesis.get("triggers")
        if triggers is not None and (
            not isinstance(triggers, list)
            or not triggers
            or not all(isinstance(value, str) and value.strip() for value in triggers)
        ):
            issues.append(issue("TYPE", pointer=f"{pointer}/triggers", message="triggers must be a non-empty string array"))
    return {"ok": not any(item.severity == "error" for item in issues), "issues": [item.to_dict() for item in issues]}


def adjudicate(
    hypotheses: list[dict[str, Any]],
    *,
    method: str,
    calibrated: bool = False,
    evidence_refs: list[str] | None = None,
    base_rate_refs: list[str] | None = None,
    sample_count: int | None = None,
    interval: list[float] | None = None,
    calibration_policy_ref: str | None = None,
) -> dict[str, Any]:
    """Convert roleplay hypotheses into adjudicator-owned likelihood outputs."""
    results = []
    for hypothesis in hypotheses:
        entry = {
            "action": hypothesis.get("action"),
            "method": method,
            "evidence_refs": evidence_refs or [],
            "base_rate_refs": base_rate_refs or [],
            "hypothesis_ref": hypothesis.get("id"),
        }
        weight = hypothesis.get("adjudicated_weight", hypothesis.get("relative_weight", 0.0))
        if calibrated:
            entry.update(
                {
                    "probability": weight,
                    "sample_count": sample_count,
                    "interval": interval,
                    "calibration_policy_ref": calibration_policy_ref,
                    "likelihood_mode": "calibrated_probability",
                }
            )
        else:
            entry.update({"relative_weight": weight, "probability": None, "likelihood_mode": "relative_weight"})
        results.append(entry)
    return {"calibrated": calibrated, "results": results, "method": method}


def build_receipt(
    *,
    runtime_id: str,
    adapter_id: str,
    execution_id: str,
    parent_execution_id: str | None,
    start: str,
    end: str,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    network_policy: str,
    tool_policy: str,
    observed_tools: list[str],
    capability_snapshot_hash: str,
    previous_receipt_hash: str | None,
    hmac_key: bytes | None = None,
) -> dict[str, Any]:
    """Build a canonical receipt.  Verified assurance requires ``hmac_key``."""
    receipt: dict[str, Any] = {
        "id": f"receipt:{execution_id}",
        "runtime_id": runtime_id,
        "adapter_id": adapter_id,
        "execution_id": execution_id,
        "parent_execution_id": parent_execution_id,
        "started_at": start,
        "completed_at": end,
        "inputs": copy.deepcopy(inputs),
        "outputs": copy.deepcopy(outputs),
        "declared_network_policy": network_policy,
        "declared_tool_policy": tool_policy,
        "observed_tool_calls": list(observed_tools),
        "capability_snapshot_hash": capability_snapshot_hash,
        "previous_receipt_hash": previous_receipt_hash,
    }
    receipt_hash = canonical_hash(receipt)
    receipt["receipt_hash"] = receipt_hash
    if hmac_key:
        receipt["hmac"] = hmac.new(hmac_key, receipt_hash.encode("ascii"), hashlib.sha256).hexdigest()
    return receipt


def _validate_artifact_descriptors(values: Any, pointer: str, issues: list[Issue]) -> None:
    if not isinstance(values, list) or not values:
        issues.append(issue("MISSING_FIELD", pointer=pointer, message="at least one hashed artifact descriptor is required"))
        return
    for index, value in enumerate(values):
        p = f"{pointer}/{index}"
        if not isinstance(value, dict):
            issues.append(issue("TYPE", pointer=p, message="artifact descriptor must be object"))
            continue
        if not _valid_hash(value.get("sha256")):
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{p}/sha256", message="artifact sha256 is required"))
        if not isinstance(value.get("path") or value.get("id"), str):
            issues.append(issue("MISSING_FIELD", pointer=p, message="artifact path or id is required"))


def verify_receipt_chain(
    receipts: list[dict[str, Any]],
    *,
    research_id: str,
    roleplay_id: str,
    hmac_key: bytes | None = None,
    require_hmac: bool = True,
) -> dict[str, Any]:
    """Verify a research -> roleplay receipt chain without tolerating ambiguity."""
    issues: list[Issue] = []
    if not isinstance(receipts, list) or not receipts:
        issues.append(issue("RECEIPT_CHAIN", message="receipt chain is empty"))
        return {"ok": False, "issues": [item.to_dict() for item in issues]}
    if research_id == roleplay_id or not _valid_id(research_id) or not _valid_id(roleplay_id):
        issues.append(issue("RECEIPT_EXECUTION_ID", message="research and roleplay require distinct stable execution IDs"))

    previous_hash: str | None = None
    by_execution: dict[str, tuple[int, dict[str, Any]]] = {}
    previous_end = None
    for index, receipt in enumerate(receipts):
        pointer = f"receipts/{index}"
        if not isinstance(receipt, dict):
            issues.append(issue("TYPE", pointer=pointer))
            continue
        for key in sorted(set(receipt) - RECEIPT_FIELDS):
            issues.append(issue("UNKNOWN_FIELD", pointer=f"{pointer}/{key}"))
        execution_id = receipt.get("execution_id")
        if not _valid_id(execution_id) or execution_id in by_execution:
            issues.append(issue("RECEIPT_EXECUTION_ID", pointer=f"{pointer}/execution_id", actual=execution_id))
        else:
            by_execution[execution_id] = (index, receipt)
        if receipt.get("id") != f"receipt:{execution_id}":
            issues.append(issue("RECEIPT_EXECUTION_ID", pointer=f"{pointer}/id", message="receipt ID does not match execution ID"))
        if not _valid_id(receipt.get("runtime_id")) or not _valid_id(receipt.get("adapter_id")):
            issues.append(issue("RECEIPT_EXECUTION_ID", pointer=pointer, message="runtime_id and adapter_id are required"))
        if not isinstance(receipt.get("declared_network_policy"), str) or not receipt.get("declared_network_policy"):
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/declared_network_policy", message="network policy is required"))
        if not isinstance(receipt.get("declared_tool_policy"), str) or not receipt.get("declared_tool_policy"):
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/declared_tool_policy", message="tool policy is required"))
        if not isinstance(receipt.get("observed_tool_calls"), list):
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/observed_tool_calls", message="observed tool calls must be an array"))
        if index == 0 and receipt.get("parent_execution_id") is not None:
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/parent_execution_id", message="root receipt cannot have a parent"))
        started = parse_time(receipt.get("started_at"))
        completed = parse_time(receipt.get("completed_at"))
        if started is None or completed is None or started > completed:
            issues.append(issue("TRACK_ORDER", pointer=pointer, message="invalid receipt timestamps"))
        if previous_end is not None and started is not None and started < previous_end:
            issues.append(issue("TRACK_ORDER", pointer=pointer, message="receipt executions overlap or are out of order"))
        if completed is not None:
            previous_end = completed
        if receipt.get("previous_receipt_hash") != previous_hash:
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/previous_receipt_hash", message="broken receipt chain", expected=previous_hash, actual=receipt.get("previous_receipt_hash")))
        body = {key: value for key, value in receipt.items() if key not in {"receipt_hash", "hmac"}}
        expected_hash = canonical_hash(body)
        stored_hash = receipt.get("receipt_hash")
        if stored_hash != expected_hash:
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/receipt_hash", message="receipt hash mismatch", expected=expected_hash, actual=stored_hash))
        if require_hmac:
            if hmac_key is None:
                issues.append(issue("HMAC_TAMPER", pointer=f"{pointer}/hmac", message="HMAC key is required"))
            else:
                expected_hmac = hmac.new(hmac_key, expected_hash.encode("ascii"), hashlib.sha256).hexdigest()
                if not isinstance(receipt.get("hmac"), str) or not hmac.compare_digest(receipt["hmac"].lower(), expected_hmac):
                    issues.append(issue("HMAC_TAMPER", pointer=f"{pointer}/hmac", message="receipt HMAC mismatch"))
        _validate_artifact_descriptors(receipt.get("inputs"), f"{pointer}/inputs", issues)
        _validate_artifact_descriptors(receipt.get("outputs"), f"{pointer}/outputs", issues)
        if not _valid_hash(receipt.get("capability_snapshot_hash")):
            issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/capability_snapshot_hash", message="capability snapshot hash required"))
        previous_hash = stored_hash if _valid_hash(stored_hash) else None

    research = by_execution.get(research_id)
    roleplay = by_execution.get(roleplay_id)
    if research is None or roleplay is None:
        issues.append(issue("RECEIPT_EXECUTION_ID", message="research and roleplay receipts must both be present"))
    else:
        research_index, research_receipt = research
        roleplay_index, roleplay_receipt = roleplay
        if research_index >= roleplay_index:
            issues.append(issue("TRACK_ORDER", message="roleplay must occur after research"))
        if roleplay_receipt.get("parent_execution_id") != research_id:
            issues.append(issue("RECEIPT_CHAIN", pointer="roleplay/parent_execution_id", expected=research_id, actual=roleplay_receipt.get("parent_execution_id")))
        if roleplay_receipt.get("declared_network_policy") not in {"deny", "none", "offline"}:
            issues.append(issue("ROLEPLAY_NETWORK", message="roleplay receipt must declare network denied"))
        if roleplay_receipt.get("declared_tool_policy") not in {"deny", "none"}:
            issues.append(issue("ROLEPLAY_NETWORK", message="roleplay receipt must declare tools denied"))
        if roleplay_receipt.get("observed_tool_calls") not in ([], None):
            issues.append(issue("ROLEPLAY_NETWORK", message="roleplay receipt reports observed tool calls"))
        research_end = parse_time(research_receipt.get("completed_at"))
        roleplay_start = parse_time(roleplay_receipt.get("started_at"))
        if research_end is None or roleplay_start is None or roleplay_start < research_end:
            issues.append(issue("TRACK_ORDER", message="roleplay started before research completed"))
    return {"ok": not any(item.severity == "error" for item in issues), "issues": [item.to_dict() for item in issues]}


def _decision_actions(actor: dict[str, Any]) -> list[str]:
    graph = actor.get("decision_graph")
    if isinstance(graph, dict):
        values = graph.get("allowed_actions") or graph.get("actions") or []
    elif isinstance(graph, list):
        values = graph
    else:
        values = []
    actions: list[str] = []
    for value in values:
        if isinstance(value, str):
            actions.append(value)
        elif isinstance(value, dict) and isinstance(value.get("action"), str):
            actions.append(value["action"])
    return actions


def receipt_binds_ledger_artifacts(receipt: dict[str, Any], row: dict[str, Any]) -> bool:
    """Return whether a receipt binds the exact ledger input and output digests."""
    for receipt_field, artifact_field, hash_field in (
        ("inputs", "input_artifact", "input_hash"),
        ("outputs", "output_artifact", "output_hash"),
    ):
        descriptors = receipt.get(receipt_field)
        artifact = row.get(artifact_field)
        digest = row.get(hash_field)
        if not isinstance(descriptors, list) or not any(
            isinstance(descriptor, dict)
            and (descriptor.get("path") == artifact or descriptor.get("id") == artifact)
            and descriptor.get("sha256") == digest
            for descriptor in descriptors
        ):
            return False
    return True


def verify_receipt_artifact_bytes(receipt: dict[str, Any], workspace: Path) -> list[Issue]:
    """Verify every receipt input/output descriptor against retained workspace bytes."""
    from .paths import resolve_in_workspace

    problems: list[Issue] = []
    for field in ("inputs", "outputs"):
        descriptors = receipt.get(field)
        if not isinstance(descriptors, list) or not descriptors:
            problems.append(issue("RECEIPT_CHAIN", pointer=field, message="retained artifact descriptors required"))
            continue
        for index, descriptor in enumerate(descriptors):
            pointer = f"{field}/{index}"
            if not isinstance(descriptor, dict):
                problems.append(issue("TYPE", pointer=pointer, message="artifact descriptor must be object"))
                continue
            relative = descriptor.get("path")
            if not isinstance(relative, str) or not relative.strip() or "#" in relative:
                problems.append(
                    issue(
                        "RECEIPT_CHAIN",
                        pointer=f"{pointer}/path",
                        message="Tier A/B requires a retained whole-file workspace-relative path",
                    )
                )
                continue
            path, path_issues = resolve_in_workspace(
                workspace,
                relative,
                must_exist=True,
                require_file=True,
            )
            problems.extend(path_issues)
            if path is None or path_issues:
                continue
            try:
                actual = sha256_file(path)
            except OSError as exc:
                problems.append(issue("INVALID_ARTIFACT", artifact=relative, message=str(exc)))
                continue
            if descriptor.get("sha256") != actual:
                problems.append(
                    issue(
                        "STALE_ARTIFACT",
                        artifact=relative,
                        pointer=f"{pointer}/sha256",
                        expected=descriptor.get("sha256"),
                        actual=actual,
                        message="receipt artifact digest mismatch",
                    )
                )
    return problems


def validate_human_track_ledger(
    rows: list[dict[str, Any]],
    actors: list[dict[str, Any]],
    workspace: Path | None = None,
) -> list[Issue]:
    """Cross-check material actors against the auditable two-track ledger.

    ``workspace`` is optional.  When a row provides ``receipt_ref`` the file is
    required and must remain inside that workspace; receipt cryptographic
    verification is performed by the release receipt gate, which supplies the
    secret key separately.
    """
    issues: list[Issue] = []
    if not isinstance(rows, list):
        return [issue("TRACK_LEDGER", message="human-track ledger must be a list")]
    by_actor: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for index, row in enumerate(rows):
        pointer = f"human-track-ledger/{index}"
        if not isinstance(row, dict):
            issues.append(issue("TYPE", pointer=pointer))
            continue
        actor_id = row.get("actor_id")
        track = row.get("track")
        if not _valid_id(actor_id) or not isinstance(track, str) or track not in {"research", "roleplay"}:
            issues.append(issue("TRACK_LEDGER", pointer=pointer, message="invalid actor_id or track"))
            continue
        started = parse_time(row.get("started_at"))
        completed = parse_time(row.get("completed_at"))
        if started is None or completed is None or started > completed:
            issues.append(issue("TRACK_ORDER", pointer=pointer, message="invalid track timestamps"))
        if row.get("status") != "completed":
            issues.append(issue("INCOMPLETE", pointer=f"{pointer}/status"))
        if not isinstance(row.get("agent_ref"), str) or not row.get("agent_ref"):
            issues.append(issue("TRACK_LEDGER", pointer=f"{pointer}/agent_ref"))
        if not isinstance(row.get("execution_id"), str) or not row.get("execution_id"):
            issues.append(issue("TRACK_LEDGER", pointer=f"{pointer}/execution_id"))
        if not isinstance(row.get("input_artifact"), str) or not row.get("input_artifact"):
            issues.append(issue("TRACK_LEDGER", pointer=f"{pointer}/input_artifact"))
        if not _valid_hash(row.get("input_hash")):
            issues.append(issue("TRACK_LEDGER", pointer=f"{pointer}/input_hash", message="sha256 required"))
        if not isinstance(row.get("output_artifact"), str) or not row.get("output_artifact"):
            issues.append(issue("TRACK_LEDGER", pointer=f"{pointer}/output_artifact"))
        if not _valid_hash(row.get("output_hash")):
            issues.append(issue("TRACK_LEDGER", pointer=f"{pointer}/output_hash", message="sha256 required"))
        if not _valid_id(row.get("receipt_id")) or not _valid_hash(row.get("receipt_hash")):
            issues.append(issue("TRACK_LEDGER", pointer=pointer, message="receipt_id and receipt_hash required"))
        attestation = row.get("receipt_attestation")
        if attestation not in {"host", "wrapper", "self", "none", "unknown"}:
            issues.append(issue("ENUM", pointer=f"{pointer}/receipt_attestation", actual=attestation))
        receipt_ref = row.get("receipt_ref")
        if workspace is not None and attestation in {"host", "wrapper"} and not isinstance(receipt_ref, str):
            issues.append(
                issue(
                    "RECEIPT_CHAIN",
                    pointer=f"{pointer}/receipt_ref",
                    message="host/wrapper attestation requires a referenced receipt artifact",
                )
            )
        if isinstance(receipt_ref, str) and workspace is not None:
            from .paths import resolve_in_workspace

            receipt_path, path_issues = resolve_in_workspace(workspace, receipt_ref, must_exist=True)
            issues.extend(path_issues)
            if receipt_path is not None and not path_issues:
                receipt_data, receipt_issues = load_json_secure(receipt_path)
                issues.extend(receipt_issues)
                candidates: list[Any]
                if isinstance(receipt_data, list):
                    candidates = receipt_data
                elif isinstance(receipt_data, dict) and isinstance(receipt_data.get("receipts"), list):
                    candidates = receipt_data["receipts"]
                else:
                    candidates = [receipt_data]
                receipt = next(
                    (
                        value
                        for value in candidates
                        if isinstance(value, dict) and value.get("execution_id") == row.get("execution_id")
                    ),
                    None,
                )
                if not isinstance(receipt, dict):
                    issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/receipt_ref", message="execution receipt not found"))
                else:
                    body = {key: value for key, value in receipt.items() if key not in {"receipt_hash", "hmac"}}
                    expected_hash = canonical_hash(body)
                    if receipt.get("id") != row.get("receipt_id") or receipt.get("receipt_hash") != row.get("receipt_hash"):
                        issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/receipt_ref", message="ledger row does not match receipt artifact"))
                    if receipt.get("receipt_hash") != expected_hash:
                        issues.append(issue("RECEIPT_CHAIN", pointer=f"{pointer}/receipt_ref", message="receipt artifact hash mismatch"))
                    if not receipt_binds_ledger_artifacts(receipt, row):
                        issues.append(
                            issue(
                                "RECEIPT_CHAIN",
                                pointer=f"{pointer}/receipt_ref",
                                message="receipt does not bind the ledger input/output artifact hashes",
                            )
                        )
                    issues.extend(verify_receipt_artifact_bytes(receipt, workspace))
        by_actor.setdefault(actor_id, {}).setdefault(track, []).append(row)

    for actor in actors if isinstance(actors, list) else []:
        if not isinstance(actor, dict) or actor.get("materiality") != "material":
            continue
        actor_id = cast(str, actor.get("id"))
        tracks = by_actor.get(actor_id, {})
        research_rows = tracks.get("research", [])
        roleplay_rows = tracks.get("roleplay", [])
        if len(research_rows) != 1 or len(roleplay_rows) != 1:
            issues.append(issue("SUBAGENT_REQUIRED", pointer=str(actor_id), message="exactly one research and one roleplay ledger row required"))
            continue
        research_row = research_rows[0]
        roleplay_row = roleplay_rows[0]
        raw_research_track = actor.get("research_track")
        research_track: dict[str, Any] = raw_research_track if isinstance(raw_research_track, dict) else {}
        raw_roleplay_track = actor.get("roleplay_track")
        roleplay_track: dict[str, Any] = raw_roleplay_track if isinstance(raw_roleplay_track, dict) else {}
        research_agent = research_row.get("agent_ref")
        roleplay_agent = roleplay_row.get("agent_ref")
        research_execution = research_row.get("execution_id")
        roleplay_execution = roleplay_row.get("execution_id")
        if research_agent == roleplay_agent or research_execution == roleplay_execution:
            issues.append(issue("SUBAGENT_SEPARATION", pointer=str(actor_id), message="research and roleplay require distinct agents and executions"))
        research_end = parse_time(research_row.get("completed_at"))
        roleplay_start = parse_time(roleplay_row.get("started_at"))
        if research_end is None or roleplay_start is None or roleplay_start < research_end:
            issues.append(issue("TRACK_ORDER", pointer=str(actor_id), message="roleplay must start after research completes"))
        if roleplay_row.get("previous_receipt_hash") != research_row.get("receipt_hash"):
            issues.append(issue("RECEIPT_CHAIN", pointer=str(actor_id), message="roleplay ledger row must chain to research receipt"))
        if roleplay_row.get("input_hash") != research_row.get("output_hash"):
            issues.append(issue("TRACK_MISMATCH", pointer=str(actor_id), message="roleplay input hash must equal frozen research output hash"))
        for row, track_name, track_data in (
            (research_row, "research", research_track),
            (roleplay_row, "roleplay", roleplay_track),
        ):
            for field in ("agent_ref", "started_at", "completed_at", "status"):
                if track_data.get(field) != row.get(field):
                    issues.append(issue("TRACK_MISMATCH", pointer=f"{actor_id}/{track_name}/{field}", expected=track_data.get(field), actual=row.get(field)))
        packet_hash = roleplay_track.get("packet_hash")
        if not _valid_hash(packet_hash):
            issues.append(issue("HUMAN_TRACK", pointer=f"{actor_id}/roleplay_track/packet_hash", message="sealed packet hash required"))
        if not _decision_actions(actor):
            issues.append(issue("MATERIALITY_GRAPH", pointer=f"{actor_id}/decision_graph", message="material actor requires explicit allowed actions"))
    return issues


def validate_actor_protocol(
    actors: list[dict[str, Any]],
    human_ledger_rows: list[dict[str, Any]],
    branches: Any = None,
    manifest: dict[str, Any] | None = None,
    workspace: Path | None = None,
) -> list[Issue]:
    """Public integration hook for validator/finalizer actor hard gates."""
    del branches, manifest  # Reserved for branch/adjudication cross-checks.
    issues = validate_human_track_ledger(human_ledger_rows, actors, workspace)
    for actor in actors if isinstance(actors, list) else []:
        if not isinstance(actor, dict) or actor.get("materiality") != "material":
            continue
        actions = _decision_actions(actor)
        raw_roleplay_track = actor.get("roleplay_track")
        roleplay_track: dict[str, Any] = raw_roleplay_track if isinstance(raw_roleplay_track, dict) else {}
        packet = {
            "packet_hash": roleplay_track.get("packet_hash"),
            "actor_id": actor.get("id"),
            "decision_id": roleplay_track.get("decision_id") or "decision:main",
            "allowed_actions": actions,
        }
        for index, hypothesis in enumerate(roleplay_track.get("hypotheses") or []):
            pointer = f"{actor.get('id')}/roleplay_track/hypotheses/{index}"
            if not isinstance(hypothesis, dict):
                issues.append(issue("TYPE", pointer=pointer))
                continue
            _scan_sealed_value(hypothesis, pointer, issues, roleplay=True)
            if hypothesis.get("action") not in actions:
                issues.append(issue("ENUM", pointer=f"{pointer}/action", message="action outside decision graph"))
            for forbidden in ("probability", "confidence", "relative_weight", "source", "facts"):
                if forbidden in hypothesis:
                    issues.append(issue("ROLEPLAY_PROBABILITY" if forbidden in {"probability", "confidence", "relative_weight"} else "ROLEPLAY_EVIDENCE", pointer=f"{pointer}/{forbidden}"))
            if hypothesis.get("evidence_ids") not in (None, []):
                issues.append(issue("ROLEPLAY_EVIDENCE", pointer=f"{pointer}/evidence_ids"))
            if hypothesis.get("status") != "simulation":
                issues.append(issue("ENUM", pointer=f"{pointer}/status", expected="simulation", actual=hypothesis.get("status")))
            for reasoning_field in ("public_role_reasoning", "reasoning"):
                reasoning = hypothesis.get(reasoning_field)
                if isinstance(reasoning, str) and PRIVATE_MOTIVE_RE.search(reasoning):
                    issues.append(issue("PRIVACY_REFUSAL", pointer=f"{pointer}/{reasoning_field}"))
        if len(roleplay_track.get("hypotheses") or []) < 2:
            issues.append(issue("HUMAN_TRACK", pointer=f"{actor.get('id')}/roleplay_track/hypotheses", message="at least two hypotheses required"))
        # The synthetic packet above is only used for action binding; full packet
        # integrity is checked when the packet artifact is loaded.
        del packet
    return issues
