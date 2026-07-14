"""Exact D Research 3.x evidence-ledger interoperability."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
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
FIELDS_ALEPH_PROTOTYPE = [
    "id", "record_type", "claim", "evidence", "source", "source_type", "source_tier",
    "date", "retrieved_at", "access_method", "retrieval_status", "confidence",
    "contradiction_status", "notes",
]
ACCEPTED_FIELD_SETS = (FIELDS_V3_1, FIELDS_V3_0, FIELDS_V2_1, FIELDS_LEGACY, FIELDS_ALEPH_PROTOTYPE)
VALID_RECORD_TYPES = frozenset({"claim", "process", "blocker", ""})
VALID_CONFIDENCE = frozenset({"high", "medium", "low"})
EVIDENCE_CONFIDENCE = {"high": "0.85", "medium": "0.60", "low": "0.30"}
EVIDENCE_FIELDNAMES = [
    "evidence_id", "claim", "source", "source_type", "source_tier", "date",
    "retrieved_at", "access_method", "retrieval_status", "quote_or_value",
    "confidence", "contradiction_status", "notes",
]
SOURCE_CONTRACTS = {
    tuple(FIELDS_V3_1): "d-research-record-type-23",
    tuple(FIELDS_V3_0): "d-research-provenance-22",
    tuple(FIELDS_V2_1): "d-research-social-19",
    tuple(FIELDS_LEGACY): "d-research-legacy-14",
    tuple(FIELDS_ALEPH_PROTOTYPE): "aleph-prototype-14",
}


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
                    message="ledger header/order mismatch; expected exact D Research 14/19/22/23 contract or Aleph prototype 14-column migration contract",
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
    provenance_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        clean = {key: (row.get(key) or "").strip() for key in fieldnames}
        record_type = (clean.get("record_type") or "claim").lower()
        claim_id = clean.get("claim_id") or clean.get("id", "")
        if record_type not in VALID_RECORD_TYPES:
            issues.append(issue("LEDGER_MALFORMED", pointer=f"line/{index}/record_type", actual=record_type))
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
        quote = clean.get("evidence") or clean.get("quote_or_anchor")
        notes = [
            "imported from D Research canonical ledger",
            f"d_research_confidence={confidence_label}",
            f"source_title={clean.get('source_title', '')}",
            f"sub_question={clean.get('sub_question', '')}",
            f"raw_row_sha256={provenance_rows[-1]['raw_row_sha256']}",
        ]
        if clean.get("notes"):
            notes.append(clean["notes"])
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
