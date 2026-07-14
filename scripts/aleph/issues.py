"""Typed check results and public error codes (stable contract)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Public semantic issue codes — regression contract
PUBLIC_CODES = frozenset(
    {
        "PATH_ESCAPE",
        "PATH_ABSOLUTE",
        "PATH_UNC",
        "PATH_DRIVE",
        "PATH_SYMLINK",
        "PATH_NOT_FILE",
        "STALE_ARTIFACT",
        "INSTALL_SOURCE_DEST",
        "INSTALL_NESTED",
        "INSTALL_NOT_ALLOWLISTED",
        "SCHEMA",
        "UNKNOWN_FIELD",
        "TYPE",
        "COERCION_REFUSED",
        "NON_FINITE",
        "MISSING_FIELD",
        "MISSING_ARTIFACT",
        "INVALID_ARTIFACT",
        "INVALID_JSONL",
        "DUPLICATE_ID",
        "EMPTY_ID",
        "UNKNOWN_REF",
        "EMPTY_REF",
        "ENUM",
        "RANGE",
        "FACT_PROVENANCE",
        "FUTURE_FACT",
        "ASSUMPTION",
        "MECHANISM",
        "SIGN",
        "RELATION",
        "LAG",
        "LAG_ORDER",
        "CONTEXT",
        "CONTEXT_MISSING",
        "MULTIPLIER",
        "SELF_EDGE",
        "TRACE_STEP",
        "TRACE_ENDPOINT",
        "TRACE_TIME",
        "TRACE_FORMULA_MISMATCH",
        "TRACE_AMPLIFICATION",
        "TRACE_EMPTY",
        "BRANCH_DUPLICATE",
        "BRANCH_NEAR_DUPLICATE",
        "BRANCH_COUNT",
        "BRANCH_CAP",
        "PROBABILITY_SUM",
        "RELATIVE_WEIGHT_ONLY",
        "PROBABILITY_UNCALIBRATED",
        "REPORT_SECTION",
        "REPORT_EMPTY",
        "MATERIALITY",
        "MATERIALITY_GRAPH",
        "PERSON_NODE",
        "SUBJECT_CLASS",
        "PRIVACY_REFUSAL",
        "ROLEPLAY_EVIDENCE",
        "ROLEPLAY_PROBABILITY",
        "ROLEPLAY_NETWORK",
        "RECEIPT_CHAIN",
        "RECEIPT_EXECUTION_ID",
        "PACKET_CUTOFF",
        "LEDGER_TAMPER",
        "LEDGER_DUPLICATE",
        "LEDGER_MALFORMED",
        "LEDGER_MAJOR",
        "LEDGER_MAPPING",
        "HMAC_TAMPER",
        "RESOURCE_LIMIT",
        "NONCONVERGENCE",
        "EVENT_STORM",
        "UNRESOLVED_MASS",
        "REPLAY_MISMATCH",
        "HARD_GATE",
        "INCOMPLETE",
        "LEGACY_EXECUTION_CONTROL",
        "RESEARCH_QUALITY",
        "EVIDENCE_SATURATION",
        "SOURCE_QUALITY",
        "DIRECT_ACCESS",
        "SNIPPET_CONFIDENCE",
        "SUBAGENT_REQUIRED",
        "SUBAGENT_SEPARATION",
        "TRACK_MISMATCH",
        "TRACK_ORDER",
        "TRACK_LEDGER",
        "HUMAN_TRACK",
        "TEMPORAL_KNOWLEDGE",
        "TEMPORAL_FRAME",
        "TIMELINE_MODE",
        "ADAPTIVE_SCOPE",
        "ADAPTIVE_DEPTH",
        "D_RESEARCH",
        "PACK_MATURITY",
        "PACK_PROBABILITY",
        "MIGRATION_UNRESOLVED",
        "VALIDATION_FAILED",
    }
)


@dataclass
class Issue:
    code: str
    severity: str  # error | warning | info
    artifact: str = ""
    pointer: str = ""
    message: str = ""
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["expected"] is None:
            del data["expected"]
        if data["actual"] is None:
            del data["actual"]
        return data

    def legacy_string(self) -> str:
        loc = self.pointer or self.artifact or ""
        msg = self.message or self.code
        return f"[{self.code}] {loc}: {msg}"


@dataclass
class CheckResult:
    id: str
    status: str  # pass | fail | skip
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "metrics": self.metrics,
            "issues": [i.to_dict() for i in self.issues],
        }

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def issue(
    code: str,
    *,
    severity: str = "error",
    artifact: str = "",
    pointer: str = "",
    message: str = "",
    expected: Any = None,
    actual: Any = None,
) -> Issue:
    if code not in PUBLIC_CODES:
        # Still emit; registry may grow — unknown codes are allowed but should be rare
        pass
    return Issue(
        code=code,
        severity=severity,
        artifact=artifact,
        pointer=pointer,
        message=message,
        expected=expected,
        actual=actual,
    )


def has_code(issues: list[Issue], code: str) -> bool:
    return any(i.code == code for i in issues)


def codes_of(issues: list[Issue]) -> set[str]:
    return {i.code for i in issues}
