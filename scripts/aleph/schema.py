"""Schema constants and strict type helpers for Aleph 2.0."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

from . import SCHEMA_VERSION
from .issues import Issue, issue

EPISTEMIC_STATUS = frozenset({"fact", "inference", "simulation", "counterfactual", "assumption"})
# Legacy 1.2 statuses still accepted during dual-read
LEGACY_STATUS = frozenset({"fact", "inference", "simulation", "counterfactual", "proposed", "assumption"})
EDGE_STATUS = frozenset({"proposed", "admitted", "incomplete", "rejected", "deprecated", "inference", "fact", "simulation"})
SIMULATION_MODE = frozenset({"qualitative", "deterministic", "monte_carlo"})
LIKELIHOOD_MODE = frozenset({"deterministic", "relative_weight", "calibrated_probability"})
ASSURANCE_TIER = frozenset({"experimental", "limited", "verified", "calibrated"})
MATERIALITY = frozenset({"material", "non_material"})
SUBJECT_CLASS = frozenset({"public_role_person", "private_person", "minor", "fictional_person", "unknown"})
NODE_TYPES = frozenset({"entity", "event", "factor", "context", "indicator", "claim", "source"})
TIMELINE_MODES = frozenset({"retrospective_counterfactual", "prospective_intervention", "hybrid_projection"})
TIMELINE_LABELS = frozenset({"shared_baseline", "observed_baseline", "simulated_branch"})
RELATION_ALLOWED = frozenset(
    {
        "increases",
        "decreases",
        "enables",
        "inhibits",
        "causes",
        "prevents",
        "mediates",
        "moderates",
        "triggers",
        "amplifies",
        "dampens",
        "feedback",
        "autoregressive",
    }
)
LAG_TYPES = frozenset({"fixed", "uniform", "triangular", "truncated_exponential"})
TRANSFORMS = frozenset({"linear", "elasticity", "identity", "logistic", "threshold"})
SOURCE_TIERS = frozenset({"primary", "authoritative-secondary", "secondary", "tertiary", "user-provided"})
RETRIEVAL_STATUSES = frozenset(
    {"opened", "downloaded", "api", "local-file", "user-provided", "search-snippet", "blocked"}
)
COMPLEXITY_DIMENSIONS = frozenset(
    {
        "temporal_span",
        "domain_breadth",
        "geographic_breadth",
        "actor_density",
        "causal_depth",
        "evidence_uncertainty",
        "stakes",
    }
)
ID_PREFIXES = {
    "node": ("entity:", "event:", "factor:", "context:", "indicator:", "claim:", "source:"),
    "edge": ("causal:", "edge:"),
    "actor": ("actor:",),
    "branch": ("branch:",),
    "evidence": ("evidence:",),
    "assumption": ("assumption:",),
    "packet": ("packet:",),
    "hypothesis": ("hypothesis:",),
    "receipt": ("receipt:",),
}

REPORT_SECTIONS = [
    "executive summary",
    "methodology and scope",
    "baseline and change point",
    "evidence and source quality",
    "causal architecture and propagation",
    "scenario branches",
    "human decision tracks",
    "sensitivity, contradictions, and limitations",
    "validation and audit",
    "source appendix",
    "warnings and next steps",
]

KNOWN_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "simulation_id",
        "created_at",
        "status",
        "change_point",
        "temporal_frame",
        "scope",
        "execution",
        "artifact_paths",
        "assurance_tier",
        "likelihood_mode",
        "simulation_mode",
        "formula_version",
        "artifact_index",
        "validation_receipt",
        "quality_receipt",
        "migration",
        "active_contexts",
        "assumptions",
        "finalization",
    }
)

ARTIFACT_PATH_FIELDS = frozenset(
    {
        "nodes",
        "edges",
        "interventions",
        "actors",
        "human_track_ledger",
        "evidence_map",
        "branch_ledger",
        "propagation_trace",
        "validation_report",
        "quality_report",
        "final_report",
        "computational_model",
        "simulation_config",
        "run_manifest",
        "run_ledger",
        "replay_report",
        "sensitivity_report",
        "calibration_report",
        "roleplay_receipts",
        "research_import_receipt",
    }
)
ARTIFACT_INDEX_FIELDS = frozenset({"path", "media_type", "size", "sha256", "hash_scope"})
RECEIPT_REFERENCE_FIELDS = frozenset({"path", "sha256", "bundle_digest", "assurance_tier"})
FINALIZATION_FIELDS = frozenset({"status", "committed_at", "transaction_id"})
ASSUMPTION_FIELDS = frozenset({"id", "statement"})
MIGRATION_FIELDS = frozenset(
    {"source_schema_version", "target_schema_version", "source_digest", "transforms", "unresolved_fields"}
)

# Strict allowlists — unknown keys fail closed (AC2)
NODE_FIELDS = frozenset(
    {
        "id",
        "type",
        "name",
        "description",
        "time",
        "state_before",
        "trigger",
        "mechanism",
        "state_after",
        "lag",
        "evidence_ids",
        "sources",
        "status",
        "timeline",
        "probability",
        "confidence",
        "assumption_ref",
        "alternative_explanations",
        "sensitivity",
        "role",
        "datatype",
        "unit",
        "scale",
        "baseline",
        "bounds",
        "retention",
        "decay_rate",
    }
)

EDGE_FIELDS = frozenset(
    {
        "id",
        "from",
        "to",
        "relation",
        "sign",
        "base_strength",
        "confidence",
        "evidence_confidence",
        "mechanism",
        "lag_distribution",
        "context_modifiers",
        "evidence",
        "assumption_ref",
        "status",
        "transform",
        "transform_parameters",
        "effect_distribution",
        "integration",
        "effect_parameter",
        "existence_prob",
        "feedback_policy",
        "saturation",
        "effect_size",
    }
)

LAG_FIELDS = frozenset({"type", "min", "max", "mode", "fixed", "rate", "mean"})
CONTEXT_MODIFIER_FIELDS = frozenset({"context", "multiplier", "rationale", "active"})
TRANSFORM_PARAMETER_FIELDS = frozenset(
    {"midpoint", "steepness", "mode", "threshold", "deadband", "theta_on", "theta_off"}
)

ACTOR_FIELDS = frozenset(
    {
        "id",
        "person_node",
        "public_role",
        "scope_note",
        "materiality",
        "subject_class",
        "actor_basis",
        "assumptions",
        "living_status",
        "evidence_ids",
        "research_track",
        "roleplay_track",
        "adjudication",
        "decision_patterns",
        "predicted_responses",
        "biographical_foundation",
        "stated_beliefs",
        "institutional_constraints",
        "relationships",
        "crisis_behavior",
        "uncertainty_factors",
        "decision_graph",
    }
)

BRANCH_LEDGER_FIELDS = frozenset(
    {
        "schema_version",
        "likelihood_mode",
        "calibrated",
        "branches",
        "calibration",
        "unresolved_mass",
    }
)

BRANCH_FIELDS = frozenset(
    {
        "id",
        "name",
        "probability",
        "relative_weight",
        "likelihood_mode",
        "summary",
        "causal_trace",
        "key_decision_points",
        "end_state",
        "leading_indicators",
        "disconfirming_conditions",
        "evidence_ids",
        "confidence",
        "warnings",
        "stress",
        "method",
        "sample_count",
        "interval",
        "calibration_policy_ref",
        "representative_run",
        "derivation",
        "engine_cluster_id",
        "member_count",
        "trace_hash",
        "common_edges",
        "distinguishing_parameters",
        "unresolved_mass",
    }
)

TRACE_ROW_FIELDS = frozenset(
    {
        "step",
        "time",
        "edge_id",
        "from",
        "to",
        "input_effect",
        "input_change",
        "output_effect",
        "noise",
        "amplification",
        "amplification_ratio",
        "mechanism",
        "evidence_ids",
        "formula_version",
        "butterfly_pattern",
        "sample_refs",
        "run_id",
        "tick",
        "source_tick",
        "source_state",
        "target_state",
        "sampled_strength",
        "resolved_transform_parameters",
        "threshold_active_before",
        "threshold_active_after",
        "integrated_effect",
        "target_retention_factor",
        "hash_chain",
    }
)

NODE_STATE_FIELDS = frozenset({"summary", "value", "unit", "category", "lower", "upper"})
NODE_TRIGGER_FIELDS = frozenset({"kind", "description", "source", "magnitude"})
NODE_SENSITIVITY_FIELDS = frozenset({"level", "drivers"})
EFFECT_PARAMETER_FIELDS = frozenset(
    {
        "kind",
        "reference_value",
        "distribution",
        "parameters",
        "unit",
        "source_ref",
        "lower",
        "upper",
    }
)
BRANCH_END_STATE_FIELDS = frozenset({"time", "summary", "outcomes", "values"})
LEADING_INDICATOR_FIELDS = frozenset({"node", "direction", "window", "predicate"})
CALIBRATION_FIELDS = frozenset(
    {
        "method",
        "sample_count",
        "interval",
        "calibration_policy_ref",
        "model_version",
        "formula_version",
        "model_hash",
        "hindcast_report_ref",
    }
)
HUMAN_TRACK_LEDGER_FIELDS = frozenset(
    {
        "actor_id",
        "track",
        "execution_mode",
        "agent_ref",
        "execution_id",
        "started_at",
        "completed_at",
        "input_artifact",
        "input_hash",
        "output_artifact",
        "output_hash",
        "receipt_id",
        "receipt_hash",
        "previous_receipt_hash",
        "receipt_attestation",
        "receipt_ref",
        "status",
    }
)

EVIDENCE_COLUMNS = frozenset(
    {
        "evidence_id",
        "claim",
        "source",
        "source_type",
        "source_tier",
        "date",
        "retrieved_at",
        "access_method",
        "retrieval_status",
        "quote_or_value",
        "confidence",
        "contradiction_status",
        "notes",
    }
)

# Nested manifest objects
TEMPORAL_FRAME_FIELDS = frozenset(
    {
        "mode",
        "observation_cutoff",
        "simulation_start",
        "simulation_end",
        "future_projection",
        "calibration_strategy",
        "monitoring_indicators",
    }
)
CHANGE_POINT_FIELDS = frozenset(
    {
        "type",
        "target",
        "description",
        "magnitude",
        "value",
        "op",
        "start_tick",
        "end_tick",
        "release_policy",
        "time",
        "location",
        "assumption_ref",
    }
)
SCOPE_FIELDS = frozenset({"horizon", "domains", "geographies"})
EXECUTION_FIELDS = frozenset(
    {
        "adaptive_scope",
        "research_quality",
        "research_control",
        "d_research",
        "subagents",
        "repair_cycles_completed",
        "checkpoints",
    }
)
ADAPTIVE_SCOPE_FIELDS = frozenset(
    {
        "assessed",
        "overall_complexity",
        "dimensions",
        "rationale",
        "decomposition",
    }
)
DECOMPOSITION_FIELDS = frozenset(
    {
        "subquestions",
        "critical_paths",
        "research_waves_completed",
    }
)
RESEARCH_CONTROL_FIELDS = frozenset(
    {
        "policy",
        "sources_examined",
        "saturation_reached",
        "consecutive_no_new_material_claims",
        "stop_reason",
        "unresolved_critical_gaps",
        "next_wave_queue",
    }
)
D_RESEARCH_FIELDS = frozenset({"status", "invoked", "path", "package_major", "ledger_ref"})
SUBAGENTS_FIELDS = frozenset({"status", "tool", "detection_method", "fallback_reason"})
CHECKPOINT_FIELDS = frozenset(
    {
        "initialized",
        "scope_assessed",
        "baseline_researched",
        "human_tracks_completed",
        "graph_built",
        "propagated",
        "branched",
        "validated",
    }
)

# Nested actor objects
RESEARCH_TRACK_FIELDS = frozenset(
    {
        "status",
        "execution_mode",
        "agent_ref",
        "started_at",
        "completed_at",
        "artifact",
        "notes",
        "isolation_note",
        "preferred_tool",
        "claims",
        "execution_id",
    }
)
ROLEPLAY_TRACK_FIELDS = frozenset(
    {
        "status",
        "execution_mode",
        "agent_ref",
        "started_at",
        "completed_at",
        "artifact",
        "notes",
        "isolation_note",
        "knowledge_cutoff",
        "dossier_evidence_ids",
        "hypotheses",
        "execution_id",
        "packet_hash",
    }
)
ADJUDICATION_FIELDS = frozenset(
    {
        "method",
        "calibrated",
        "results",
        "owner",
        "accepted_hypotheses",
        "rejected_hypotheses",
        "confidence_cap",
        "rule",
        "evidence_refs",
        "base_rate_refs",
        "sample_count",
        "interval",
        "calibration_policy_ref",
        "disagreement_log",
    }
)
ADJUDICATION_RESULT_FIELDS = frozenset(
    {
        "action",
        "hypothesis_ref",
        "likelihood_mode",
        "relative_weight",
        "probability",
        "method",
        "evidence_refs",
        "base_rate_refs",
        "sample_count",
        "interval",
        "calibration_policy_ref",
    }
)
RESEARCH_CLAIM_FIELDS = frozenset(
    {
        "id",
        "claim",
        "evidence_ids",
        "confidence",
        "available_at",
        "access_basis",
    }
)
ROLEPLAY_HYPOTHESIS_FIELDS = frozenset(
    {
        "id",
        "action",
        "reasoning",
        "private_motive",
        "status",
        "evidence_ids",
        "probability",
        "relative_weight",
        "constraints_applied",
        "triggers",
        "known_unknowns",
    }
)
PREDICTED_RESPONSE_FIELDS = frozenset(
    {
        "action",
        "probability",
        "relative_weight",
        "reasoning",
        "status",
        "confidence",
    }
)


def is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except (OverflowError, TypeError, ValueError):
            return False
    return False


def refuse_string_number(value: Any, pointer: str, issues: list[Issue]) -> float | None:
    """Accept only real numbers; refuse string coercion."""
    if isinstance(value, str):
        issues.append(
            issue(
                "COERCION_REFUSED",
                pointer=pointer,
                message="string cannot be coerced to number",
                actual=value,
            )
        )
        return None
    if isinstance(value, bool):
        issues.append(
            issue("COERCION_REFUSED", pointer=pointer, message="bool is not a number", actual=value)
        )
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            number = math.inf
        if not math.isfinite(number):
            issues.append(issue("NON_FINITE", pointer=pointer, message="NaN/Infinity refused", actual=value))
            return None
        return number
    issues.append(issue("TYPE", pointer=pointer, message="must be a finite number", actual=type(value).__name__))
    return None


def refuse_string_bool(value: Any, pointer: str, issues: list[Issue]) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        issues.append(
            issue("COERCION_REFUSED", pointer=pointer, message="string cannot be coerced to bool", actual=value)
        )
        return None
    issues.append(issue("TYPE", pointer=pointer, message="must be boolean", actual=type(value).__name__))
    return None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        normalized = value.strip()
        if re.fullmatch(r"\d{4}", normalized):
            normalized = f"{normalized}-01-01"
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


_DURATION_RE = re.compile(
    r"^P(?=\d|T\d)(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<weeks>\d+)W)?"
    r"(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_duration_seconds(value: Any) -> float | None:
    """Parse the supported non-negative ISO-8601 duration subset.

    Calendar years/months are normalized to 365/30 days for ordering and lag
    lower-bound checks; artifacts retain the original duration string.
    """
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    parts = {name: float(raw or 0) for name, raw in match.groupdict().items()}
    if not any(parts.values()) and value.strip() not in {"P0D", "PT0S"}:
        return None
    days = parts["years"] * 365 + parts["months"] * 30 + parts["weeks"] * 7 + parts["days"]
    return days * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def has_id_prefix(value: Any, kind: str) -> bool:
    return nonempty_str(value) and any(str(value).startswith(prefix) for prefix in ID_PREFIXES[kind])


def reject_unknown_fields(obj: dict[str, Any], allowed: set[str] | frozenset[str], pointer: str, issues: list[Issue]) -> None:
    unknown = set(obj) - set(allowed)
    for key in sorted(unknown):
        issues.append(
            issue("UNKNOWN_FIELD", pointer=f"{pointer}.{key}", message="unknown field refused", actual=key)
        )


def unit_interval(value: Any, pointer: str, issues: list[Issue]) -> float | None:
    num = refuse_string_number(value, pointer, issues)
    if num is None:
        return None
    if not 0.0 <= num <= 1.0:
        issues.append(issue("RANGE", pointer=pointer, message="must be within [0, 1]", actual=num))
    return num


def nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def schema_is_current(version: Any) -> bool:
    return isinstance(version, str) and version == SCHEMA_VERSION


def schema_is_legacy(version: Any) -> bool:
    return isinstance(version, str) and version in {"1.2.0", "1.1.0"}
