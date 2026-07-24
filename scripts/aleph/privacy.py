"""Actor provenance intake that never blocks creative simulation or roleplay."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from .issues import Issue, issue
from .schema import SUBJECT_CLASS

DOXXING_PATTERNS = (
    re.compile(r"\b(doxx?|doxxing)\b", re.I),
    re.compile(r"\b(home|residential|precise)\s+(address|location)\b", re.I),
    re.compile(r"\b(stalk|stalking|surveil|track\s+in\s+real[ -]?time)\b", re.I),
    re.compile(r"\b(phone\s*number|personal\s*email|ssn|social\s*security)\b", re.I),
    re.compile(r"\bwhere\s+(?:does|is)\s+.{1,80}\s+(?:live|staying|located)\b", re.I),
    re.compile(r"\b(re-?identif(?:y|ication)|private\s+account|leaked\s+data)\b", re.I),
    re.compile(r"\b(manipulate|blackmail|coerce|intimidate)\b", re.I),
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"(?:\+\d[\d .()\-]{7,}\d|\b(?:phone|tel|mobile)\s*[:=]\s*\d)", re.I),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(?:latitude|longitude|gps)\s*[:=]\s*-?\d", re.I),
    re.compile(
        r"\b(?:diagnos(?:is|es|ed)|medical\s+(?:history|record|condition)|"
        r"mental\s+health\s+(?:history|record|condition))\b",
        re.I,
    ),
)
SENSITIVE_KEYS = frozenset(
    {
        "home_address",
        "residential_address",
        "personal_email",
        "phone_number",
        "private_phone",
        "ssn",
        "social_security_number",
        "precise_location",
        "current_location",
        "whereabouts",
        "private_account",
        "private_messages",
        "family_details",
        "medical_record",
        "medical_records",
        "medical_history",
        "medical_information",
        "medical_condition",
        "medical_diagnosis",
        "diagnosis",
        "diagnoses",
        "health_record",
        "health_records",
        "health_information",
        "health_condition",
        "mental_health",
        "financial_account",
        "protected_trait",
        "sexual_orientation",
    }
)
# Fictional subjects have no real-world vital status; preserve that explicit
# label instead of treating it as malformed or silently coercing it to living.
VALID_LIVING_STATUS = frozenset({"living", "deceased", "fictional", "unknown"})


def _walk(value: Any, path: str = "payload", depth: int = 0) -> Iterator[tuple[str, Any]]:
    if depth > 32:
        yield path, "<depth-limit>"
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            yield child, item
            yield from _walk(item, child, depth + 1)
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            child = f"{path}[{index}]"
            yield child, item
            yield from _walk(item, child, depth + 1)


def _normalise_key(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.rsplit(".", 1)[-1].lower()).strip("_")


POLICY_BOUNDARY_MARKERS = ("out of scope", "do not collect", "exclude ", "must not collect", "refuse ")
CONTRAST_RE = re.compile(r"(?:[;.!?]|\b(?:but|however|instead|except|then|also)\b)", re.I)
AFFIRMATIVE_ACTION_RE = re.compile(
    r"\b(?:collect|find|get|obtain|reveal|provide|share|locate|track|send|give|look\s+up|retrieve)\b",
    re.I,
)


def _harmful_match_is_negated(text: str, start: int) -> bool:
    """Recognize a local safety boundary without exempting the rest of a mixed request."""
    lowered = text.lower()
    positions = [lowered.rfind(marker, 0, start) for marker in POLICY_BOUNDARY_MARKERS]
    boundary = max(positions, default=-1)
    if boundary < 0:
        return False
    marker = next(marker for marker in POLICY_BOUNDARY_MARKERS if lowered.rfind(marker, 0, start) == boundary)
    between = text[boundary + len(marker):start]
    return CONTRAST_RE.search(between) is None and AFFIRMATIVE_ACTION_RE.search(between) is None


def privacy_intake(
    *,
    subject_class: str,
    living_status: str = "unknown",
    request_text: str = "",
    public_role_anchor: str | None = None,
    evidence_ids: list[str] | None = None,
    payload: Any = None,
) -> dict[str, Any]:
    """Classify the evidence basis without acting as a scenario-content gate.

    ``payload`` may contain the complete nested actor request/dossier.  Keys and
    scalar values are traversed recursively so prohibited data cannot be hidden
    in a nested object.
    """
    issues: list[Issue] = []
    if subject_class not in SUBJECT_CLASS:
        issues.append(issue("SUBJECT_CLASS", message="invalid subject_class", actual=subject_class))
        subject_class = "unknown"
    if living_status not in VALID_LIVING_STATUS:
        issues.append(issue("ENUM", pointer="living_status", actual=living_status))
        living_status = "unknown"
    if living_status == "unknown":
        living_status = "living"

    reasons: list[str] = []
    assumption_required = False

    if subject_class in {"private_person", "minor", "fictional_person", "unknown"}:
        assumption_required = True
        reasons.append(f"subject_class={subject_class} uses assumption-first simulation unless evidence is supplied")
        issues.append(
            issue(
                "ASSUMPTION_PROVENANCE",
                severity="warning",
                message="actor is simulated without a public-role evidence requirement",
                actual=subject_class,
            )
        )

    text_values: list[tuple[str, str]] = [("request_text", str(request_text or ""))]
    if payload is not None:
        for path, value in _walk(payload):
            key = _normalise_key(path)
            if key in SENSITIVE_KEYS and value not in (None, "", [], {}):
                assumption_required = True
                reasons.append(f"restricted nested field: {path}")
                issues.append(issue("ASSUMPTION_PROVENANCE", severity="warning", pointer=path, message="treat personal detail as simulation content, not sourced fact"))
            if isinstance(value, str):
                text_values.append((path, value))

    for path, text in text_values:
        if not text:
            continue
        harmful = next(
            (
                match
                for pattern in DOXXING_PATTERNS
                for match in pattern.finditer(text)
                if not _harmful_match_is_negated(text, match.start())
            ),
            None,
        )
        sensitive = next((pattern for pattern in SENSITIVE_VALUE_PATTERNS if pattern.search(text)), None)
        if harmful or sensitive:
            assumption_required = True
            reasons.append(f"privacy/harm content detected at {path}")
            issues.append(issue("ASSUMPTION_PROVENANCE", severity="warning", pointer=path, message="treat unsupported personal or motive content as an explicit simulation assumption"))

    if subject_class == "public_role_person":
        if not isinstance(public_role_anchor, str) or not public_role_anchor.strip() or not (evidence_ids or []):
            assumption_required = True
            reasons.append("public_role_person requires an evidence-backed public-role anchor")
            issues.append(issue("ASSUMPTION_PROVENANCE", severity="warning", message="missing public-role anchor; continue in assumption-first mode"))
        if evidence_ids is not None and any(not isinstance(value, str) or not value.startswith("evidence:") for value in evidence_ids):
            assumption_required = True
            reasons.append("public-role evidence IDs are malformed")
            issues.append(issue("ASSUMPTION_PROVENANCE", severity="warning", pointer="evidence_ids", message="malformed evidence IDs cannot support fact labels"))

    return {
        "ok": True,
        "allowed": True,
        "simulation_allowed": True,
        "subject_class": subject_class,
        "living_status": living_status,
        "network_allowed": True,
        "roleplay_allowed": True,
        "research_mode": "assumption_only" if assumption_required else "evidence_supported",
        "assumption_required": assumption_required,
        "reasons": reasons,
        "issues": [item.to_dict() for item in issues],
        "stop_before": [],
    }
