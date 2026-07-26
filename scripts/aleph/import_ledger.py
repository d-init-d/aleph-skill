"""Exact D Research 3.x evidence-ledger interoperability."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .issues import Issue, issue

SUPPORTED_MAJORS = frozenset({3})
D_RESEARCH_SIGNATURE_VERSION = "d-research-skill/hmac-sha256/v1"
FIELDS_LEGACY = [
    "claim_id", "claim", "sub_question", "source_title", "source_url", "source_type",
    "date_published", "date_accessed", "access_method", "evidence", "quote_or_anchor",
    "contradiction", "confidence", "notes",
]
FIELDS_V2_1 = FIELDS_LEGACY + [
    "archive_url", "content_hash", "snapshot_status", "verifiability", "verifiability_note",
]
FIELDS_V3_0 = FIELDS_V2_1 + ["license_spdx", "robots_status", "prov_activity_id"]
FIELDS_V3_1 = FIELDS_V3_0 + ["record_type"]
FIELDS_POLICY = [
    "source_access_class", "subject_class", "purpose_category", "policy_tier",
    "speaker_identity", "speaker_relationship", "content_origin", "lineage_id",
    "data_sensitivity", "discovery_disposition", "reporting_disposition",
    "redaction_class", "retention_until", "authorization_scope_hash",
]
FIELDS_V3_3 = FIELDS_V3_1 + FIELDS_POLICY
FIELDS_ALEPH_PROTOTYPE = [
    "id", "record_type", "claim", "evidence", "source", "source_type", "source_tier",
    "date", "retrieved_at", "access_method", "retrieval_status", "confidence",
    "contradiction_status", "notes",
]
ACCEPTED_FIELD_SETS = (
    FIELDS_V3_3,
    FIELDS_V3_1,
    FIELDS_V3_0,
    FIELDS_V2_1,
    FIELDS_LEGACY,
    FIELDS_ALEPH_PROTOTYPE,
)
VALID_RECORD_TYPES = frozenset({"claim", "lead", "process", "blocker", ""})
VALID_CONFIDENCE = frozenset({"high", "medium", "low"})
EVIDENCE_CONFIDENCE = {"high": "0.85", "medium": "0.60", "low": "0.30"}
EVIDENCE_FIELDNAMES = [
    "evidence_id", "claim", "source", "source_type", "source_tier", "date",
    "retrieved_at", "access_method", "retrieval_status", "quote_or_value",
    "confidence", "contradiction_status", "notes",
]
SOURCE_CONTRACTS = {
    tuple(FIELDS_V3_3): "d-research-policy-37",
    tuple(FIELDS_V3_1): "d-research-record-type-23",
    tuple(FIELDS_V3_0): "d-research-provenance-22",
    tuple(FIELDS_V2_1): "d-research-social-19",
    tuple(FIELDS_LEGACY): "d-research-legacy-14",
    tuple(FIELDS_ALEPH_PROTOTYPE): "aleph-prototype-14",
}
POLICY_ENUMS = {
    "source_access_class": frozenset(
        {
            "standard_public",
            "public_reporting",
            "authorized_provider",
            "user_provided_private",
            "raw_leak_lead_only",
            "prohibited_secret",
        }
    ),
    "subject_class": frozenset(
        {
            "organization",
            "public_role_person",
            "private_person",
            "self",
            "minor",
            "infrastructure",
            "event",
            "unknown",
        }
    ),
    "purpose_category": frozenset(
        {
            "general_research",
            "academic",
            "journalism",
            "public_interest",
            "due_diligence",
            "fraud_prevention",
            "low_risk_factual",
            "self_research",
            "self_audit",
            "defensive_security",
            "threat_intel",
            "incident_response",
            "authorized_pentest",
        }
    ),
    "policy_tier": frozenset({"R0", "R1", "R2", "R3", "R4", "RX"}),
    "speaker_identity": frozenset(
        {
            "",
            "official",
            "verified_public_role",
            "claimed_identity",
            "pseudonymous",
            "anonymous",
            "unknown",
        }
    ),
    "speaker_relationship": frozenset(
        {
            "",
            "subject",
            "authorized_representative",
            "firsthand",
            "journalist",
            "secondhand",
            "commentary",
            "repost",
            "unknown",
        }
    ),
    "content_origin": frozenset(
        {"", "original", "firsthand", "quote", "repost", "screenshot", "unknown"}
    ),
    "data_sensitivity": frozenset(
        {"public", "professional", "personal", "sensitive", "secret", "minor"}
    ),
    "discovery_disposition": frozenset(
        {
            "permitted",
            "evidence",
            "lead_only",
            "context_only",
            "contradiction",
            "discarded",
            "blocked",
            "prohibited",
        }
    ),
    "reporting_disposition": frozenset(
        {
            "main_findings",
            "non_official_unverified_leads",
            "context_only",
            "blocked_prohibited_sources",
            "redacted",
            "excluded",
            "prohibited",
        }
    ),
    "redaction_class": frozenset(
        {
            "none",
            "personal_contact",
            "residential",
            "government_id",
            "financial",
            "medical",
            "family_minor",
            "whereabouts",
            "secret",
            "other_pii",
        }
    ),
}
_POLICY_REQUIRED = frozenset(
    {
        "source_access_class",
        "subject_class",
        "purpose_category",
        "policy_tier",
        "data_sensitivity",
        "discovery_disposition",
        "reporting_disposition",
        "redaction_class",
    }
)
_LINEAGE_ID_RE = re.compile(r"^\S{1,128}$")
_SCOPE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_TOKEN_128_RE = re.compile(r"^\S{1,128}$")
_LICENSE_SPDX_RE = re.compile(r"^[A-Za-z0-9.\-+]{1,64}$")
_VALID_SOURCE_TYPES = frozenset(
    {
        "primary",
        "official",
        "dataset",
        "code",
        "pdf",
        "paper",
        "filing",
        "secondary",
        "community",
        "unknown",
    }
)
_VALID_CONTRADICTIONS = frozenset({"", "none", "possible", "direct", "unresolved"})
_VALID_VERIFIABILITY = frozenset(
    {"", "direct_api", "direct_api_deleted", "archive_snapshot", "screenshot_only", "unverified"}
)
_VALID_SNAPSHOT_STATUS = frozenset(
    {
        "",
        "intact",
        "edited",
        "deleted",
        "access_denied",
        "rate_limited",
        "unavailable",
        "malformed",
        "unknown",
    }
)
_VALID_ROBOTS_STATUS = frozenset(
    {"", "allowed", "disallowed", "unknown", "not_checked", "not_applicable"}
)


def render_evidence_csv(rows: list[dict[str, Any]]) -> bytes:
    """Render imported evidence rows using Aleph's canonical CSV contract."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EVIDENCE_FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def canonicalise_d_research_csv(raw: bytes) -> tuple[bytes | None, list[str], list[dict[str, str]], list[Issue]]:
    """Mirror D Research ``evidence_ledger.py canonicalise`` byte-for-byte."""
    issues: list[Issue] = []
    try:
        text = raw.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fieldnames = list(reader.fieldnames or [])
        active_fields = next((candidate for candidate in ACCEPTED_FIELD_SETS if fieldnames == candidate), None)
        if active_fields is None:
            issues.append(
                issue(
                    "LEDGER_MALFORMED",
                    message="ledger header/order mismatch; expected exact D Research 14/19/22/23/37 contract or Aleph prototype 14-column migration contract",
                    actual=fieldnames,
                )
            )
            return None, fieldnames, [], issues
        rows = list(reader)
        if any(None in row for row in rows):
            issues.append(issue("LEDGER_MALFORMED", message="CSV row has excess columns"))
            return None, fieldnames, rows, issues
    except (UnicodeDecodeError, csv.Error) as exc:
        issues.append(issue("LEDGER_MALFORMED", message=str(exc)))
        return None, [], [], issues

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=active_fields, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: (row.get(key) or "").strip() for key in active_fields})
    return buffer.getvalue().encode("utf-8"), fieldnames, rows, issues


def _verify_sidecar(canonical: bytes, sidecar: Path, key: bytes | None) -> list[Issue]:
    if key is None:
        return [issue("HMAC_TAMPER", message="D Research HMAC sidecar exists but no verification key was supplied")]
    try:
        content = sidecar.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        return [issue("HMAC_TAMPER", message=str(exc))]
    parts = content.split()
    if len(parts) != 2 or parts[0] != D_RESEARCH_SIGNATURE_VERSION:
        return [issue("HMAC_TAMPER", message="unrecognized D Research signature sidecar format", actual=content)]
    expected = parts[1].lower()
    actual = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, actual):
        return [
            issue("HMAC_TAMPER", message="D Research canonical ledger HMAC mismatch"),
            issue("LEDGER_TAMPER", message="refusing tampered ledger"),
        ]
    return []


def _source_tier(source_type: str) -> str:
    if source_type in {"primary", "official", "dataset", "code", "filing"}:
        return "primary"
    if source_type in {"paper", "pdf"}:
        return "authoritative-secondary"
    if source_type in {"secondary", "community"}:
        return "secondary"
    return "tertiary"


def _retrieval_status(access_method: str) -> str:
    method = access_method.lower()
    if method in {"public_api", "api"}:
        return "api"
    if method in {"public_file", "download"}:
        return "downloaded"
    if method in {"search", "snippet"}:
        return "search-snippet"
    if method in {"manual_needed"}:
        return "blocked"
    return "opened"


def _valid_policy_timestamp(value: str) -> bool:
    if not value:
        return True
    if not _RFC3339_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _policy_timestamp(value: str) -> datetime | None:
    if not value or not _valid_policy_timestamp(value):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _retention_anchor(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_license(value: str) -> bool:
    if not value or value == "NOASSERTION":
        return True
    if value.startswith("LicenseRef-"):
        suffix = value[len("LicenseRef-") :]
        return bool(suffix and _LICENSE_SPDX_RE.fullmatch(suffix))
    return bool(_LICENSE_SPDX_RE.fullmatch(value))


def _policy_row_issues(
    clean: dict[str, str],
    *,
    record_type: str,
    index: int,
) -> list[Issue]:
    """Mirror the security-relevant D Research 3.3 row policy before import."""

    problems: list[Issue] = []

    def add(pointer: str, message: str, *, actual: Any = None) -> None:
        problems.append(
            issue(
                "LEDGER_MALFORMED",
                pointer=f"line/{index}/{pointer}" if pointer else f"line/{index}",
                message=message,
                actual=actual,
            )
        )

    values = {field: clean.get(field, "").lower() for field in POLICY_ENUMS}
    values["policy_tier"] = clean.get("policy_tier", "").upper()
    missing = sorted(field for field in _POLICY_REQUIRED if not values[field])
    if missing:
        add("", "37-column row is missing required policy fields", actual=missing)
    for field, allowed in POLICY_ENUMS.items():
        value = values[field]
        if value not in allowed:
            add(field, "invalid policy enum value", actual=value)

    claim = clean.get("claim", "")
    source_url = clean.get("source_url", "")
    source_title = clean.get("source_title", "")
    notes = clean.get("notes", "")
    source_access_class = values["source_access_class"]
    subject_class = values["subject_class"]
    policy_tier = values["policy_tier"]
    data_sensitivity = values["data_sensitivity"]
    discovery_disposition = values["discovery_disposition"]
    reporting_disposition = values["reporting_disposition"]
    redaction_class = values["redaction_class"]
    speaker_identity = values["speaker_identity"]
    speaker_relationship = values["speaker_relationship"]
    content_origin = values["content_origin"]
    lineage_id = clean.get("lineage_id", "")
    retention_until = clean.get("retention_until", "")
    authorization_scope_hash = clean.get("authorization_scope_hash", "")

    if not claim:
        add("claim", "policy row requires a claim or audit description")
    if record_type == "claim" and not source_url:
        add("source_url", "claim row requires source_url")
    if record_type == "lead" and not source_url and source_access_class != "raw_leak_lead_only":
        add("source_url", "lead row requires source_url")
    if record_type in {"process", "blocker"}:
        if not (source_url or source_title):
            add("source_title", "audit row requires source_url or source_title")
        if not (notes or clean.get("evidence")):
            add("notes", "audit row requires a reason in evidence or notes")
        status_fields = (
            clean.get("snapshot_status", ""),
            clean.get("robots_status", ""),
            clean.get("verifiability", ""),
        )
        if not any(status_fields) and re.search(
            r"\b(?:status|result|reason|fallback_result)\s*=\s*\S+",
            notes,
            flags=re.IGNORECASE,
        ) is None:
            add("notes", "audit row requires a structured status or result")

    source_type = clean.get("source_type", "").lower()
    if source_type and source_type not in _VALID_SOURCE_TYPES:
        add("source_type", "invalid source type", actual=source_type)
    confidence = clean.get("confidence", "").lower()
    if confidence and confidence not in VALID_CONFIDENCE:
        add("confidence", "invalid policy-ledger confidence", actual=confidence)
    contradiction = clean.get("contradiction", "").lower()
    if contradiction not in _VALID_CONTRADICTIONS:
        add("contradiction", "invalid contradiction value", actual=contradiction)
    verifiability = clean.get("verifiability", "").lower()
    if verifiability not in _VALID_VERIFIABILITY:
        add("verifiability", "invalid verifiability value", actual=verifiability)
    snapshot_status = clean.get("snapshot_status", "").lower()
    if snapshot_status not in _VALID_SNAPSHOT_STATUS:
        add("snapshot_status", "invalid snapshot status", actual=snapshot_status)
    robots_status = clean.get("robots_status", "").lower()
    if robots_status not in _VALID_ROBOTS_STATUS:
        add("robots_status", "invalid robots status", actual=robots_status)
    license_spdx = clean.get("license_spdx", "")
    if not _valid_license(license_spdx):
        add("license_spdx", "invalid SPDX-style license value", actual=license_spdx)
    prov_activity_id = clean.get("prov_activity_id", "")
    if prov_activity_id and not _TOKEN_128_RE.fullmatch(prov_activity_id):
        add("prov_activity_id", "invalid provenance activity identifier")

    if policy_tier in {"R0", "R1"} and record_type in {"claim", "lead"}:
        if subject_class in {"public_role_person", "private_person", "self", "minor"}:
            add("subject_class", f"{policy_tier} person row must use R2 or R3")
        allowed_sensitivity = data_sensitivity in {"public", "professional"}
        redacted_raw_lead = (
            record_type == "lead"
            and source_access_class == "raw_leak_lead_only"
            and data_sensitivity in {"personal", "sensitive"}
        )
        if not (allowed_sensitivity or redacted_raw_lead):
            add("data_sensitivity", f"{policy_tier} permits public/professional data only")
    if policy_tier == "R2":
        if subject_class not in {"public_role_person", "private_person", "self"}:
            add("subject_class", "R2 requires a person or self subject")
        if record_type in {"claim", "lead"} and data_sensitivity not in {
            "public",
            "professional",
        }:
            add("data_sensitivity", "R2 permits public/professional data only")
    if policy_tier == "R3" and subject_class not in {"self", "organization"}:
        add("subject_class", "R3 requires a self or organization subject")

    if reporting_disposition == "main_findings" and record_type != "claim":
        add("reporting_disposition", "main_findings requires record_type=claim")
    if reporting_disposition == "non_official_unverified_leads" and record_type != "lead":
        add(
            "reporting_disposition",
            "non_official_unverified_leads requires record_type=lead",
        )
    if discovery_disposition == "lead_only" and record_type != "lead":
        add("discovery_disposition", "lead_only requires record_type=lead")
    if data_sensitivity in {"personal", "sensitive"} and redaction_class in {"", "none"}:
        add("redaction_class", "personal or sensitive data requires redaction")

    if lineage_id and not _LINEAGE_ID_RE.fullmatch(lineage_id):
        add("lineage_id", "invalid lineage identifier")
    if not _valid_policy_timestamp(retention_until):
        add("retention_until", "retention timestamp must be RFC 3339 with timezone")
    if authorization_scope_hash and not _SCOPE_HASH_RE.fullmatch(authorization_scope_hash):
        add("authorization_scope_hash", "invalid authorization scope hash")

    social_values = (speaker_identity, speaker_relationship, content_origin)
    if any(social_values) and not all(social_values):
        add("speaker_identity", "social classification fields must be populated together")
    if reporting_disposition == "main_findings" and any(social_values):
        direct_integrity = bool(
            verifiability == "direct_api"
            and snapshot_status == "intact"
            and clean.get("content_hash")
        )
        archive_integrity = bool(
            verifiability == "archive_snapshot"
            and clean.get("archive_url")
            and clean.get("content_hash")
        )
        if not (
            speaker_identity in {"official", "verified_public_role"}
            and speaker_relationship in {"subject", "authorized_representative"}
            and content_origin == "original"
            and (direct_integrity or archive_integrity)
        ):
            add("reporting_disposition", "social main finding lacks verified original evidence")
        if re.search(
            r"(?:^|;\s*)claim_kind=statement_made(?:;|$)",
            notes.lower(),
        ) is None:
            add("notes", "social main finding requires claim_kind=statement_made")
    if (
        content_origin in {"quote", "repost", "screenshot"}
        or speaker_relationship == "repost"
    ) and not lineage_id:
        add("lineage_id", "derivative social evidence requires lineage_id")

    prohibited_for_evidence = (
        data_sensitivity in {"secret", "minor"}
        or subject_class == "minor"
        or source_access_class == "prohibited_secret"
        or policy_tier == "RX"
        or discovery_disposition == "prohibited"
        or reporting_disposition == "prohibited"
    )
    if record_type in {"claim", "lead"} and prohibited_for_evidence:
        add("", "prohibited policy row cannot be imported as claim or lead")
    if prohibited_for_evidence and reporting_disposition == "main_findings":
        add("reporting_disposition", "prohibited material cannot enter main findings")

    protected_fields = (
        "source_url",
        "evidence",
        "quote_or_anchor",
        "archive_url",
        "content_hash",
    )
    if source_access_class == "prohibited_secret" or data_sensitivity == "secret":
        populated = [field for field in protected_fields if clean.get(field)]
        if populated:
            add("", "secret or prohibited metadata retained protected fields", actual=populated)
    if record_type == "lead" and reporting_disposition == "main_findings":
        add("reporting_disposition", "lead row cannot enter main findings")
    if source_access_class == "raw_leak_lead_only":
        if record_type not in {"lead", "process", "blocker"}:
            add("record_type", "raw-leak metadata requires lead, process, or blocker")
        populated = [field for field in protected_fields if clean.get(field)]
        if populated:
            add("", "raw-leak metadata retained protected fields", actual=populated)
        if not source_title:
            add("source_title", "raw-leak metadata requires a redacted source title")
        if record_type == "lead" and reporting_disposition != "non_official_unverified_leads":
            add("reporting_disposition", "raw-leak lead must remain in the lead partition")

    if policy_tier in {"R3", "R4"}:
        if not authorization_scope_hash:
            add("authorization_scope_hash", f"{policy_tier} requires authorization binding")
        if not retention_until:
            add("retention_until", f"{policy_tier} requires a retention deadline")
    if policy_tier == "R3" and retention_until:
        retention = _policy_timestamp(retention_until)
        anchor = _retention_anchor(clean.get("date_accessed", ""))
        if anchor is None:
            add("date_accessed", "R3 requires a valid retention anchor")
        elif retention is not None and retention > anchor + timedelta(days=30):
            add("retention_until", "R3 retention exceeds the 30-day maximum")

    return problems


def import_d_research_ledger(
    ledger_path: Path,
    *,
    hmac_sidecar: Path | None = None,
    hmac_key: bytes | None = None,
    package_major: int | None = 3,
) -> dict[str, Any]:
    issues: list[Issue] = []
    if package_major not in SUPPORTED_MAJORS:
        issues.append(issue("LEDGER_MAJOR", message="only D Research major 3 is supported", actual=package_major, expected=[3]))
        return {
            "ok": False,
            "issues": [item.to_dict() for item in issues],
            "compatibility_report": {"supported_majors": [3], "got": package_major},
            "evidence_rows": [],
        }
    try:
        raw = ledger_path.read_bytes()
    except OSError as exc:
        return {"ok": False, "issues": [issue("LEDGER_MALFORMED", message=str(exc)).to_dict()], "evidence_rows": []}

    canonical, fieldnames, rows, parse_issues = canonicalise_d_research_csv(raw)
    issues.extend(parse_issues)
    if canonical is None:
        return {
            "ok": False,
            "issues": [item.to_dict() for item in issues],
            "evidence_rows": [],
            "raw_preserved": True,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
    signature = hmac_sidecar
    if signature is None:
        candidate = ledger_path.with_suffix(ledger_path.suffix + ".hmac")
        if candidate.is_file():
            signature = candidate
    hmac_verified = False
    if signature is not None:
        signature_issues = _verify_sidecar(canonical, signature, hmac_key)
        issues.extend(signature_issues)
        if signature_issues:
            return {
                "ok": False,
                "issues": [item.to_dict() for item in issues],
                "evidence_rows": [],
                "raw_preserved": True,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
            }
        hmac_verified = True

    seen: set[str] = set()
    evidence_rows: list[dict[str, str | None]] = []
    audit_rows: list[dict[str, str]] = []
    lead_rows: list[dict[str, str]] = []
    provenance_rows: list[dict[str, Any]] = []
    policy_schema = fieldnames == FIELDS_V3_3
    for index, row in enumerate(rows, start=2):
        clean = {key: (row.get(key) or "").strip() for key in fieldnames}
        record_type = (clean.get("record_type") or "claim").lower()
        claim_id = clean.get("claim_id") or clean.get("id", "")
        if record_type not in VALID_RECORD_TYPES:
            issues.append(issue("LEDGER_MALFORMED", pointer=f"line/{index}/record_type", actual=record_type))
            continue
        if record_type == "lead" and not policy_schema:
            issues.append(
                issue(
                    "LEDGER_MALFORMED",
                    pointer=f"line/{index}/record_type",
                    message="record_type=lead requires the exact 37-column policy contract",
                )
            )
            continue
        if not claim_id:
            issues.append(issue("EMPTY_ID", pointer=f"line/{index}/claim_id"))
            continue
        if claim_id in seen:
            issues.append(issue("LEDGER_DUPLICATE", pointer=f"line/{index}/claim_id", actual=claim_id))
            continue
        seen.add(claim_id)
        provenance_rows.append(
            {
                "claim_id": claim_id,
                "record_type": record_type,
                "raw_row": clean,
                "raw_row_sha256": hashlib.sha256(
                    ("\0".join(clean.get(key, "") for key in fieldnames)).encode("utf-8")
                ).hexdigest(),
            }
        )
        if policy_schema:
            policy_issues = _policy_row_issues(
                clean,
                record_type=record_type,
                index=index,
            )
            if policy_issues:
                issues.extend(policy_issues)
                continue

        if record_type == "lead":
            lead_rows.append(clean)
            continue
        if record_type in {"process", "blocker"}:
            audit_rows.append(clean)
            continue
        claim = clean.get("claim", "")
        confidence_label = clean.get("confidence", "").lower()
        if not claim:
            issues.append(issue("LEDGER_MALFORMED", pointer=f"line/{index}/claim", message="claim row is empty"))
            continue
        numeric_confidence: str | None = None
        if confidence_label not in VALID_CONFIDENCE:
            try:
                parsed_confidence = float(confidence_label)
                if 0.0 <= parsed_confidence <= 1.0:
                    numeric_confidence = str(parsed_confidence)
                else:
                    raise ValueError
            except ValueError:
                issues.append(issue("LEDGER_MALFORMED", pointer=f"line/{index}/confidence", actual=confidence_label))
                continue
        source_url = clean.get("source_url") or clean.get("source", "")
        if not source_url:
            issues.append(issue("LEDGER_MALFORMED", pointer=f"line/{index}/source_url", message="claim row requires source_url"))
            continue
        source_type = clean.get("source_type", "unknown")
        contradiction = clean.get("contradiction") or clean.get("contradiction_status") or "none"
        quote = clean.get("evidence")
        if policy_schema and not quote:
            issues.append(
                issue(
                    "LEDGER_MALFORMED",
                    pointer=f"line/{index}/evidence",
                    message="37-column claim row requires the evidence field",
                )
            )
            continue
        if not quote:
            quote = clean.get("quote_or_anchor")
        notes = [
            "imported from D Research canonical ledger",
            f"d_research_confidence={confidence_label}",
            f"source_title={clean.get('source_title', '')}",
            f"sub_question={clean.get('sub_question', '')}",
            f"raw_row_sha256={provenance_rows[-1]['raw_row_sha256']}",
        ]
        if clean.get("notes"):
            notes.append(clean["notes"])
        if policy_schema:
            notes.extend(
                [
                    f"policy_tier={clean.get('policy_tier', '')}",
                    f"source_access_class={clean.get('source_access_class', '')}",
                    f"discovery_disposition={clean.get('discovery_disposition', '')}",
                    f"reporting_disposition={clean.get('reporting_disposition', '')}",
                ]
            )
        evidence_rows.append(
            {
                "evidence_id": claim_id if claim_id.startswith("evidence:") else f"evidence:{claim_id}",
                "claim": claim,
                "source": source_url,
                "source_type": source_type,
                "source_tier": clean.get("source_tier") or _source_tier(source_type),
                "date": clean.get("date_published") or clean.get("date", ""),
                "retrieved_at": clean.get("date_accessed") or clean.get("retrieved_at", ""),
                "access_method": clean.get("access_method", ""),
                "retrieval_status": clean.get("retrieval_status") or _retrieval_status(clean.get("access_method", "")),
                "quote_or_value": quote,
                # This is evidence confidence, never event/branch probability.
                "confidence": numeric_confidence or EVIDENCE_CONFIDENCE[confidence_label],
                "contradiction_status": contradiction,
                "notes": "; ".join(notes),
            }
        )

    ok = not any(item.severity == "error" for item in issues)
    return {
        "ok": ok,
        "issues": [item.to_dict() for item in issues],
        "evidence_rows": evidence_rows,
        "audit_rows": audit_rows,
        "lead_rows": lead_rows,
        "source_provenance": provenance_rows,
        "raw_preserved": True,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "raw_size": len(raw),
        "column_count": len(fieldnames),
        "fieldnames": fieldnames,
        "mapping": "evidence",
        "mapping_contract": "d-research-3.x-canonical",
        "source_contract": SOURCE_CONTRACTS.get(tuple(fieldnames)),
        "hmac_verified": hmac_verified,
        "hmac_sidecar": str(signature) if signature else None,
    }
