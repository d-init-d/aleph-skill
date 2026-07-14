"""Strict semantic validation for Aleph workspaces (1.2 dual-read + 2.0 write path)."""

from __future__ import annotations

import math
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from . import FORMULA_VERSION, SCHEMA_VERSION, VALIDATOR_VERSION
from .engine import (
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    run_deterministic,
    run_monte_carlo,
    semantic_result_payload,
)
from .execution_binding import build_trace_execution_binding
from .formula import replay_trace_row
from .io import (
    ResourceLimitError,
    canonical_hash,
    load_json_secure,
    load_workspace_artifact,
    sha256_file,
    validate_workspace_budget,
)
from .issues import CheckResult, Issue, issue
from .paths import resolve_in_workspace, validate_relative_artifact_path
from .schema import (
    ACTOR_FIELDS,
    ADAPTIVE_SCOPE_FIELDS,
    ADJUDICATION_FIELDS,
    ARTIFACT_INDEX_FIELDS,
    ARTIFACT_PATH_FIELDS,
    ASSUMPTION_FIELDS,
    ASSURANCE_TIER,
    BRANCH_END_STATE_FIELDS,
    BRANCH_FIELDS,
    BRANCH_LEDGER_FIELDS,
    CALIBRATION_FIELDS,
    CHANGE_POINT_FIELDS,
    CHECKPOINT_FIELDS,
    COMPLEXITY_DIMENSIONS,
    CONTEXT_MODIFIER_FIELDS,
    D_RESEARCH_FIELDS,
    DECOMPOSITION_FIELDS,
    EDGE_FIELDS,
    EDGE_STATUS,
    EFFECT_PARAMETER_FIELDS,
    EPISTEMIC_STATUS,
    EVIDENCE_COLUMNS,
    EXECUTION_FIELDS,
    FINALIZATION_FIELDS,
    HUMAN_TRACK_LEDGER_FIELDS,
    KNOWN_MANIFEST_FIELDS,
    LAG_FIELDS,
    LAG_TYPES,
    LEADING_INDICATOR_FIELDS,
    LEGACY_STATUS,
    MATERIALITY,
    MIGRATION_FIELDS,
    NODE_FIELDS,
    NODE_SENSITIVITY_FIELDS,
    NODE_STATE_FIELDS,
    NODE_TRIGGER_FIELDS,
    NODE_TYPES,
    PREDICTED_RESPONSE_FIELDS,
    RECEIPT_REFERENCE_FIELDS,
    RELATION_ALLOWED,
    REPORT_SECTIONS,
    RESEARCH_CLAIM_FIELDS,
    RESEARCH_CONTROL_FIELDS,
    RESEARCH_TRACK_FIELDS,
    RETRIEVAL_STATUSES,
    ROLEPLAY_HYPOTHESIS_FIELDS,
    ROLEPLAY_TRACK_FIELDS,
    SCOPE_FIELDS,
    SOURCE_TIERS,
    SUBAGENTS_FIELDS,
    SUBJECT_CLASS,
    TEMPORAL_FRAME_FIELDS,
    TIMELINE_LABELS,
    TIMELINE_MODES,
    TRACE_ROW_FIELDS,
    TRANSFORMS,
    ensure_list,
    has_id_prefix,
    is_bool,
    is_number,
    nonempty_str,
    parse_duration_seconds,
    parse_time,
    refuse_string_number,
    reject_unknown_fields,
    unit_interval,
)


def _check(cid: str, issues: list[Issue], metrics: dict[str, Any] | None = None) -> CheckResult:
    errors = [i for i in issues if i.severity == "error"]
    return CheckResult(id=cid, status="fail" if errors else "pass", metrics=metrics or {}, issues=list(issues))


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def validate_paths(manifest: dict[str, Any], workspace: Path) -> CheckResult:
    issues: list[Issue] = []
    artifact_paths = manifest.get("artifact_paths") or {}
    if not isinstance(artifact_paths, dict):
        issues.append(issue("TYPE", pointer="artifact_paths", message="must be object"))
        return _check("paths", issues)
    reject_unknown_fields(artifact_paths, ARTIFACT_PATH_FIELDS, "manifest.artifact_paths", issues)
    for required in (
        "nodes",
        "edges",
        "actors",
        "human_track_ledger",
        "evidence_map",
        "branch_ledger",
        "propagation_trace",
        "final_report",
    ):
        if required not in artifact_paths:
            issues.append(issue("MISSING_FIELD", pointer=f"artifact_paths.{required}", message="required core artifact path"))
    for key, rel in artifact_paths.items():
        if not isinstance(rel, str):
            issues.append(issue("TYPE", pointer=f"artifact_paths.{key}", message="path must be string"))
            continue
        path_issues = validate_relative_artifact_path(rel, artifact=f"artifact_paths.{key}")
        issues.extend(path_issues)
        if not path_issues:
            _, res_issues = resolve_in_workspace(workspace, rel, must_exist=False, require_file=False)
            issues.extend(res_issues)
    execution = manifest.get("execution")
    d_research = execution.get("d_research") if isinstance(execution, dict) else None
    if isinstance(d_research, dict) and d_research.get("status") == "verified":
        receipt_relative = artifact_paths.get("research_import_receipt")
        if not nonempty_str(receipt_relative):
            issues.append(
                issue(
                    "MISSING_FIELD",
                    pointer="artifact_paths.research_import_receipt",
                    message="verified D Research requires an import receipt",
                )
            )
        else:
            _, receipt_issues = resolve_in_workspace(
                workspace,
                str(receipt_relative),
                must_exist=True,
                require_file=True,
            )
            issues.extend(receipt_issues)
    return _check("paths", issues)


def _reject_nested_manifest(manifest: dict[str, Any], issues: list[Issue]) -> None:
    """AC2: every nested object forbids unknown fields."""
    change = manifest.get("change_point")
    if isinstance(change, dict):
        reject_unknown_fields(change, CHANGE_POINT_FIELDS, "manifest.change_point", issues)
    frame = manifest.get("temporal_frame")
    if isinstance(frame, dict):
        reject_unknown_fields(frame, TEMPORAL_FRAME_FIELDS, "manifest.temporal_frame", issues)
    scope = manifest.get("scope")
    if isinstance(scope, dict):
        reject_unknown_fields(scope, SCOPE_FIELDS, "manifest.scope", issues)
    assumptions = manifest.get("assumptions")
    if isinstance(assumptions, list):
        for index, assumption in enumerate(assumptions):
            if isinstance(assumption, dict):
                reject_unknown_fields(
                    assumption, ASSUMPTION_FIELDS, f"manifest.assumptions[{index}]", issues
                )
    migration = manifest.get("migration")
    if isinstance(migration, dict):
        reject_unknown_fields(migration, MIGRATION_FIELDS, "manifest.migration", issues)
    execution = manifest.get("execution")
    if not isinstance(execution, dict):
        return
    reject_unknown_fields(execution, EXECUTION_FIELDS, "manifest.execution", issues)
    adaptive = execution.get("adaptive_scope")
    if isinstance(adaptive, dict):
        reject_unknown_fields(adaptive, ADAPTIVE_SCOPE_FIELDS, "manifest.execution.adaptive_scope", issues)
        dims = adaptive.get("dimensions")
        if isinstance(dims, dict):
            # dimensions keys must be exactly the seven complexity dimensions
            for key in sorted(set(dims) - COMPLEXITY_DIMENSIONS):
                issues.append(
                    issue(
                        "UNKNOWN_FIELD",
                        pointer=f"manifest.execution.adaptive_scope.dimensions.{key}",
                        message="unknown field refused",
                        actual=key,
                    )
                )
        decomp = adaptive.get("decomposition")
        if isinstance(decomp, dict):
            reject_unknown_fields(decomp, DECOMPOSITION_FIELDS, "manifest.execution.adaptive_scope.decomposition", issues)
    control = execution.get("research_control")
    if isinstance(control, dict):
        reject_unknown_fields(control, RESEARCH_CONTROL_FIELDS, "manifest.execution.research_control", issues)
    d_research = execution.get("d_research")
    if isinstance(d_research, dict):
        reject_unknown_fields(d_research, D_RESEARCH_FIELDS, "manifest.execution.d_research", issues)
    subagents = execution.get("subagents")
    if isinstance(subagents, dict):
        reject_unknown_fields(subagents, SUBAGENTS_FIELDS, "manifest.execution.subagents", issues)
    checkpoints = execution.get("checkpoints")
    if isinstance(checkpoints, dict):
        reject_unknown_fields(checkpoints, CHECKPOINT_FIELDS, "manifest.execution.checkpoints", issues)
    artifact_index = manifest.get("artifact_index")
    if isinstance(artifact_index, list):
        for idx, entry in enumerate(artifact_index):
            if isinstance(entry, dict):
                reject_unknown_fields(entry, ARTIFACT_INDEX_FIELDS, f"manifest.artifact_index[{idx}]", issues)
    for key in ("validation_receipt", "quality_receipt"):
        ref = manifest.get(key)
        if isinstance(ref, dict):
            reject_unknown_fields(ref, RECEIPT_REFERENCE_FIELDS, f"manifest.{key}", issues)
    finalization = manifest.get("finalization")
    if isinstance(finalization, dict):
        reject_unknown_fields(finalization, FINALIZATION_FIELDS, "manifest.finalization", issues)


def _require_fields(
    value: dict[str, Any], fields: set[str] | frozenset[str] | tuple[str, ...], pointer: str, issues: list[Issue]
) -> None:
    for field in fields:
        if field not in value:
            issues.append(
                issue(
                    "MISSING_FIELD",
                    pointer=f"{pointer}.{field}" if pointer else field,
                    message="required by schema 2.0.0",
                )
            )


def _validate_string_array(value: Any, pointer: str, issues: list[Issue], *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or (nonempty and not value) or not all(nonempty_str(item) for item in value):
        issues.append(issue("TYPE", pointer=pointer, message="must be a string array"))


def _validate_v2_manifest_contract(manifest: dict[str, Any], issues: list[Issue]) -> None:
    """Enforce the published simulation-manifest JSON Schema contract."""
    _require_fields(
        manifest,
        (
            "schema_version",
            "simulation_id",
            "created_at",
            "status",
            "likelihood_mode",
            "simulation_mode",
            "change_point",
            "temporal_frame",
            "scope",
            "execution",
            "assumptions",
            "artifact_paths",
        ),
        "",
        issues,
    )
    simulation_id = manifest.get("simulation_id")
    if not isinstance(simulation_id, str) or re.match(r"^sim[-:]", simulation_id) is None:
        issues.append(issue("SCHEMA", pointer="simulation_id", expected="^sim[-:]", actual=simulation_id))
    if manifest.get("status") not in {"draft", "complete", "completed"}:
        issues.append(issue("ENUM", pointer="status", actual=manifest.get("status")))
    assurance = manifest.get("assurance_tier")
    if assurance is not None and assurance not in ASSURANCE_TIER:
        issues.append(issue("ENUM", pointer="assurance_tier", actual=assurance))

    artifact_paths = manifest.get("artifact_paths")
    if isinstance(artifact_paths, dict):
        for field, value in artifact_paths.items():
            if not nonempty_str(value):
                issues.append(
                    issue(
                        "TYPE",
                        pointer=f"artifact_paths.{field}",
                        message="must be a non-empty string",
                    )
                )

    change = manifest.get("change_point")
    if isinstance(change, dict):
        _require_fields(
            change,
            ("type", "target", "description", "magnitude", "time", "location", "assumption_ref"),
            "change_point",
            issues,
        )
        for field in ("type", "target", "description", "location"):
            if not nonempty_str(change.get(field)):
                issues.append(issue("TYPE", pointer=f"change_point.{field}", message="must be non-empty string"))
        if "magnitude" in change:
            refuse_string_number(change.get("magnitude"), "change_point.magnitude", issues)
        if parse_time(change.get("time")) is None:
            issues.append(issue("TEMPORAL_FRAME", pointer="change_point.time", message="must be valid ISO date/time"))
        assumption_ref = change.get("assumption_ref")
        if not isinstance(assumption_ref, str) or not assumption_ref.startswith("assumption:"):
            issues.append(
                issue(
                    "SCHEMA",
                    pointer="change_point.assumption_ref",
                    expected="^assumption:",
                    actual=assumption_ref,
                )
            )

    frame = manifest.get("temporal_frame")
    if isinstance(frame, dict):
        _require_fields(
            frame,
            ("mode", "observation_cutoff", "simulation_start", "simulation_end", "future_projection"),
            "temporal_frame",
            issues,
        )
        if "calibration_strategy" in frame and not nonempty_str(frame.get("calibration_strategy")):
            issues.append(issue("TYPE", pointer="temporal_frame.calibration_strategy", message="must be string"))
        if "monitoring_indicators" in frame:
            _validate_string_array(frame.get("monitoring_indicators"), "temporal_frame.monitoring_indicators", issues)

    execution = manifest.get("execution")
    if isinstance(execution, dict):
        _require_fields(
            execution,
            ("adaptive_scope", "research_quality", "research_control", "d_research", "subagents", "checkpoints"),
            "execution",
            issues,
        )
        adaptive = execution.get("adaptive_scope")
        if isinstance(adaptive, dict):
            _require_fields(
                adaptive,
                ("assessed", "overall_complexity", "dimensions", "rationale", "decomposition"),
                "execution.adaptive_scope",
                issues,
            )
            if not is_bool(adaptive.get("assessed")):
                issues.append(issue("TYPE", pointer="execution.adaptive_scope.assessed", message="must be boolean"))
            if not nonempty_str(adaptive.get("rationale")):
                issues.append(issue("TYPE", pointer="execution.adaptive_scope.rationale", message="must be string"))
            decomposition = adaptive.get("decomposition")
            if isinstance(decomposition, dict):
                _require_fields(
                    decomposition,
                    ("subquestions", "critical_paths", "research_waves_completed"),
                    "execution.adaptive_scope.decomposition",
                    issues,
                )
                for field in ("subquestions", "critical_paths"):
                    _validate_string_array(
                        decomposition.get(field), f"execution.adaptive_scope.decomposition.{field}", issues
                    )
                waves = decomposition.get("research_waves_completed")
                if not isinstance(waves, int) or isinstance(waves, bool) or waves < 0:
                    issues.append(
                        issue(
                            "TYPE",
                            pointer="execution.adaptive_scope.decomposition.research_waves_completed",
                            message="must be a non-negative integer",
                        )
                    )
            elif "decomposition" in adaptive:
                issues.append(issue("TYPE", pointer="execution.adaptive_scope.decomposition", message="must be object"))

        control = execution.get("research_control")
        if isinstance(control, dict):
            _require_fields(
                control,
                ("policy", "sources_examined", "saturation_reached", "unresolved_critical_gaps"),
                "execution.research_control",
                issues,
            )
            sources = control.get("sources_examined")
            if not isinstance(sources, int) or isinstance(sources, bool) or sources < 0:
                issues.append(
                    issue(
                        "TYPE",
                        pointer="execution.research_control.sources_examined",
                        message="must be a non-negative integer",
                    )
                )
            if not is_bool(control.get("saturation_reached")):
                issues.append(issue("TYPE", pointer="execution.research_control.saturation_reached", message="must be boolean"))
            _validate_string_array(
                control.get("unresolved_critical_gaps"),
                "execution.research_control.unresolved_critical_gaps",
                issues,
            )
            if "consecutive_no_new_material_claims" in control:
                count = control.get("consecutive_no_new_material_claims")
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    issues.append(
                        issue(
                            "TYPE",
                            pointer="execution.research_control.consecutive_no_new_material_claims",
                            message="must be a non-negative integer",
                        )
                    )
            if "stop_reason" in control and not isinstance(control.get("stop_reason"), str):
                issues.append(issue("TYPE", pointer="execution.research_control.stop_reason", message="must be string"))

        d_research = execution.get("d_research")
        if isinstance(d_research, dict):
            _require_fields(d_research, ("status", "invoked"), "execution.d_research", issues)
            if not nonempty_str(d_research.get("status")):
                issues.append(issue("TYPE", pointer="execution.d_research.status", message="must be string"))
            if not is_bool(d_research.get("invoked")):
                issues.append(issue("TYPE", pointer="execution.d_research.invoked", message="must be boolean"))
            if "package_major" in d_research:
                major = d_research.get("package_major")
                if not isinstance(major, int) or isinstance(major, bool) or major < 1:
                    issues.append(issue("TYPE", pointer="execution.d_research.package_major", message="must be positive integer"))
            for field in ("path", "ledger_ref"):
                if field in d_research and not isinstance(d_research.get(field), str):
                    issues.append(issue("TYPE", pointer=f"execution.d_research.{field}", message="must be string"))
        elif "d_research" in execution:
            issues.append(issue("TYPE", pointer="execution.d_research", message="must be object"))

        subagents = execution.get("subagents")
        if isinstance(subagents, dict):
            _require_fields(subagents, ("status",), "execution.subagents", issues)
            if not nonempty_str(subagents.get("status")):
                issues.append(issue("TYPE", pointer="execution.subagents.status", message="must be string"))
            if "tool" in subagents and subagents.get("tool") is not None and not isinstance(subagents.get("tool"), str):
                issues.append(issue("TYPE", pointer="execution.subagents.tool", message="must be string or null"))
            for field in ("detection_method", "fallback_reason"):
                if field in subagents and not isinstance(subagents.get(field), str):
                    issues.append(issue("TYPE", pointer=f"execution.subagents.{field}", message="must be string"))
        elif "subagents" in execution:
            issues.append(issue("TYPE", pointer="execution.subagents", message="must be object"))

        checkpoints = execution.get("checkpoints")
        if isinstance(checkpoints, dict):
            _require_fields(checkpoints, CHECKPOINT_FIELDS, "execution.checkpoints", issues)
            for field in CHECKPOINT_FIELDS & set(checkpoints):
                if not is_bool(checkpoints.get(field)):
                    issues.append(issue("TYPE", pointer=f"execution.checkpoints.{field}", message="must be boolean"))

    migration = manifest.get("migration")
    if isinstance(migration, dict):
        _require_fields(migration, MIGRATION_FIELDS, "migration", issues)
        for field in ("source_schema_version", "target_schema_version"):
            if not nonempty_str(migration.get(field)):
                issues.append(issue("TYPE", pointer=f"migration.{field}", message="must be string"))
        if migration.get("target_schema_version") != SCHEMA_VERSION:
            issues.append(
                issue(
                    "SCHEMA",
                    pointer="migration.target_schema_version",
                    expected=SCHEMA_VERSION,
                    actual=migration.get("target_schema_version"),
                )
            )
        digest = migration.get("source_digest")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            issues.append(issue("SCHEMA", pointer="migration.source_digest", message="must be SHA-256"))
        _validate_string_array(migration.get("transforms"), "migration.transforms", issues)
        unresolved = migration.get("unresolved_fields")
        if not isinstance(unresolved, list) or not all(isinstance(item, dict) for item in unresolved):
            issues.append(issue("TYPE", pointer="migration.unresolved_fields", message="must be an object array"))
    elif migration is not None:
        issues.append(issue("TYPE", pointer="migration", message="must be object"))

    if "active_contexts" in manifest:
        _validate_string_array(manifest.get("active_contexts"), "active_contexts", issues)

    artifact_index = manifest.get("artifact_index")
    if artifact_index is not None:
        if not isinstance(artifact_index, list):
            issues.append(issue("TYPE", pointer="artifact_index", message="must be array"))
        else:
            for index, entry in enumerate(artifact_index):
                pointer = f"artifact_index/{index}"
                if not isinstance(entry, dict):
                    issues.append(issue("TYPE", pointer=pointer, message="must be object"))
                    continue
                _require_fields(entry, ARTIFACT_INDEX_FIELDS, pointer, issues)
                for field in ("path", "media_type"):
                    if not nonempty_str(entry.get(field)):
                        issues.append(issue("TYPE", pointer=f"{pointer}/{field}", message="must be string"))
                size = entry.get("size")
                if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                    issues.append(issue("TYPE", pointer=f"{pointer}/size", message="must be non-negative integer"))
                digest = entry.get("sha256")
                if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                    issues.append(issue("SCHEMA", pointer=f"{pointer}/sha256", message="must be SHA-256"))
                if entry.get("hash_scope") not in {"full_file", "manifest_input_contract"}:
                    issues.append(issue("ENUM", pointer=f"{pointer}/hash_scope", actual=entry.get("hash_scope")))

    for field in ("validation_receipt", "quality_receipt"):
        receipt = manifest.get(field)
        if receipt is None:
            continue
        if not isinstance(receipt, dict):
            issues.append(issue("TYPE", pointer=field, message="must be object"))
            continue
        _require_fields(receipt, ("path", "sha256"), field, issues)
        if not nonempty_str(receipt.get("path")):
            issues.append(issue("TYPE", pointer=f"{field}.path", message="must be string"))
        digest = receipt.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            issues.append(issue("SCHEMA", pointer=f"{field}.sha256", message="must be SHA-256"))

    finalization = manifest.get("finalization")
    if isinstance(finalization, dict):
        _require_fields(finalization, ("status", "committed_at", "transaction_id"), "finalization", issues)
        if finalization.get("status") != "committed":
            issues.append(issue("ENUM", pointer="finalization.status", actual=finalization.get("status")))
        if parse_time(finalization.get("committed_at")) is None:
            issues.append(issue("TEMPORAL_FRAME", pointer="finalization.committed_at", message="must be date-time"))
        if not nonempty_str(finalization.get("transaction_id")):
            issues.append(issue("TYPE", pointer="finalization.transaction_id", message="must be string"))
    elif finalization is not None:
        issues.append(issue("TYPE", pointer="finalization", message="must be object"))


def validate_manifest_core(manifest: dict[str, Any], mode: str) -> CheckResult:
    issues: list[Issue] = []
    reject_unknown_fields(manifest, KNOWN_MANIFEST_FIELDS, "manifest", issues)
    _reject_nested_manifest(manifest, issues)
    version = manifest.get("schema_version")
    if version not in {SCHEMA_VERSION, "1.2.0", "1.1.0"}:
        issues.append(issue("SCHEMA", pointer="schema_version", message=f"unsupported {version}", expected=SCHEMA_VERSION))
    if version == SCHEMA_VERSION:
        _validate_v2_manifest_contract(manifest, issues)
    for field in ["simulation_id", "created_at", "status", "change_point", "temporal_frame", "scope", "execution", "artifact_paths"]:
        if field not in manifest:
            issues.append(issue("MISSING_FIELD", pointer=field, message="required"))
    for field in ("simulation_id", "created_at", "status"):
        if field in manifest and not nonempty_str(manifest.get(field)):
            issues.append(issue("TYPE", pointer=field, message="must be non-empty string"))
    if parse_time(manifest.get("created_at")) is None:
        issues.append(issue("TEMPORAL_FRAME", pointer="created_at", message="must be a valid ISO date/time"))
    for field in ("change_point", "temporal_frame", "scope", "execution", "artifact_paths"):
        if field in manifest and not isinstance(manifest.get(field), dict):
            issues.append(issue("TYPE", pointer=field, message="must be object"))
    if mode == "final" and manifest.get("status") not in {"complete", "completed"}:
        issues.append(issue("INCOMPLETE", pointer="status", message="final requires complete/completed"))

    if version == SCHEMA_VERSION:
        assumptions = manifest.get("assumptions")
        assumption_ids: set[str] = set()
        if not isinstance(assumptions, list) or not assumptions:
            issues.append(issue("MISSING_FIELD", pointer="assumptions", message="requires assumption records"))
        else:
            for index, raw in enumerate(assumptions):
                pointer = f"assumptions/{index}"
                if not isinstance(raw, dict):
                    issues.append(issue("TYPE", pointer=pointer, message="assumption must be object"))
                    continue
                assumption_id = raw.get("id")
                if not has_id_prefix(assumption_id, "assumption"):
                    issues.append(issue("SCHEMA", pointer=f"{pointer}/id", actual=assumption_id))
                elif str(assumption_id) in assumption_ids:
                    issues.append(issue("DUPLICATE_ID", pointer=f"{pointer}/id", actual=assumption_id))
                else:
                    assumption_ids.add(str(assumption_id))
                if not nonempty_str(raw.get("statement")):
                    issues.append(issue("MISSING_FIELD", pointer=f"{pointer}/statement", message="required"))
        change_point = manifest.get("change_point")
        assumption_ref = change_point.get("assumption_ref") if isinstance(change_point, dict) else None
        if not nonempty_str(assumption_ref) or str(assumption_ref) not in assumption_ids:
            issues.append(
                issue(
                    "UNKNOWN_REF",
                    pointer="change_point.assumption_ref",
                    actual=assumption_ref,
                    message="change point must reference a declared assumption",
                )
            )

    execution = manifest.get("execution") or {}
    if isinstance(execution, dict):
        for legacy in ["profile", "research_budget", "max_sources", "max_repair_loops", "depth"]:
            if legacy in execution:
                issues.append(issue("LEGACY_EXECUTION_CONTROL", pointer=f"execution.{legacy}", message="forbidden"))
        quality = execution.get("research_quality")
        if quality not in {"best-available", "limited"} and not (
            mode == "draft" and quality == "unknown"
        ):
            issues.append(issue("RESEARCH_QUALITY", pointer="execution.research_quality", message="invalid", actual=quality))
        control = execution.get("research_control") or {}
        if isinstance(control, dict):
            for legacy in ["max_sources", "max_repair_loops", "time_limit", "source_limit"]:
                if legacy in control:
                    issues.append(issue("LEGACY_EXECUTION_CONTROL", pointer=f"execution.research_control.{legacy}", message="forbidden"))
            if mode == "final" and control.get("saturation_reached") is not True:
                issues.append(issue("EVIDENCE_SATURATION", pointer="execution.research_control.saturation_reached", message="must be true"))
            if control.get("policy") != "evidence-saturation":
                issues.append(issue("EVIDENCE_SATURATION", pointer="execution.research_control.policy", message="must be evidence-saturation"))
            gaps = control.get("unresolved_critical_gaps")
            if mode == "final" and gaps != []:
                issues.append(issue("EVIDENCE_SATURATION", pointer="execution.research_control.unresolved_critical_gaps", message="final requires no unresolved critical gaps", actual=gaps))
        elif "research_control" in execution:
            issues.append(issue("TYPE", pointer="execution.research_control", message="must be object"))
        adaptive = execution.get("adaptive_scope") or {}
        if isinstance(adaptive, dict):
            if mode == "final" and adaptive.get("assessed") is not True:
                issues.append(issue("ADAPTIVE_SCOPE", pointer="execution.adaptive_scope.assessed", message="must be true"))
            unit_interval(adaptive.get("overall_complexity"), "execution.adaptive_scope.overall_complexity", issues)
            dims = adaptive.get("dimensions") or {}
            if isinstance(dims, dict):
                missing = COMPLEXITY_DIMENSIONS - set(dims)
                if missing:
                    issues.append(issue("ADAPTIVE_SCOPE", pointer="execution.adaptive_scope.dimensions", message=f"missing {sorted(missing)}"))
                for name in COMPLEXITY_DIMENSIONS & set(dims):
                    unit_interval(dims.get(name), f"execution.adaptive_scope.dimensions.{name}", issues)
            else:
                issues.append(issue("TYPE", pointer="execution.adaptive_scope.dimensions", message="must be object"))
        elif "adaptive_scope" in execution:
            issues.append(issue("TYPE", pointer="execution.adaptive_scope", message="must be object"))
        checkpoints = execution.get("checkpoints")
        if mode == "final":
            if not isinstance(checkpoints, dict):
                issues.append(issue("INCOMPLETE", pointer="execution.checkpoints", message="final requires checkpoints"))
            else:
                for name in CHECKPOINT_FIELDS:
                    if checkpoints.get(name) is not True:
                        issues.append(issue("INCOMPLETE", pointer=f"execution.checkpoints.{name}", message="final checkpoint must be true"))

    frame = manifest.get("temporal_frame") or {}
    if isinstance(frame, dict):
        if frame.get("mode") not in TIMELINE_MODES:
            issues.append(issue("TIMELINE_MODE", pointer="temporal_frame.mode", message="invalid mode", actual=frame.get("mode")))
        for name in ("observation_cutoff", "simulation_start", "simulation_end"):
            if parse_time(frame.get(name)) is None:
                issues.append(issue("TEMPORAL_FRAME", pointer=f"temporal_frame.{name}", message="must be valid ISO date/time"))
        cutoff = parse_time(frame.get("observation_cutoff"))
        start = parse_time(frame.get("simulation_start"))
        end = parse_time(frame.get("simulation_end"))
        if start and end and start > end:
            issues.append(issue("TEMPORAL_FRAME", pointer="temporal_frame", message="simulation_start must not exceed simulation_end"))
        if cutoff and start and frame.get("mode") == "prospective_intervention" and start < cutoff:
            issues.append(issue("TEMPORAL_FRAME", pointer="temporal_frame.simulation_start", message="prospective start must be at/after cutoff"))
        if not isinstance(frame.get("future_projection"), bool):
            issues.append(issue("TYPE", pointer="temporal_frame.future_projection", message="must be boolean"))
    scope = manifest.get("scope")
    if isinstance(scope, dict):
        if parse_duration_seconds(scope.get("horizon")) is None:
            issues.append(issue("TEMPORAL_FRAME", pointer="scope.horizon", message="must be supported ISO duration"))
        for field in ("domains", "geographies"):
            value = scope.get(field)
            if not isinstance(value, list) or not value or not all(nonempty_str(item) for item in value):
                issues.append(issue("TYPE", pointer=f"scope.{field}", message="must be a non-empty string array"))
    likelihood = manifest.get("likelihood_mode")
    if likelihood is None and version in {"1.2.0", "1.1.0"}:
        likelihood = "calibrated_probability"
    if likelihood not in {"deterministic", "relative_weight", "calibrated_probability"}:
        issues.append(issue("ENUM", pointer="likelihood_mode", message="invalid likelihood mode", actual=likelihood))
    simulation_mode = manifest.get("simulation_mode")
    if simulation_mode is None and version in {"1.2.0", "1.1.0"}:
        simulation_mode = "qualitative"
    if simulation_mode not in {"qualitative", "deterministic", "monte_carlo"}:
        issues.append(issue("ENUM", pointer="simulation_mode", message="invalid simulation mode", actual=simulation_mode))
    return _check("manifest", issues)


def validate_evidence(rows: list[dict[str, str]], manifest: dict[str, Any], mode: str) -> tuple[CheckResult, set[str]]:
    issues: list[Issue] = []
    ids: set[str] = set()
    if not rows:
        issues.append(issue("MISSING_FIELD", artifact="evidence_map", message="must contain at least one row"))
        return _check("evidence", issues, {"evidence_rows": 0}), ids
    required = {
        "evidence_id", "claim", "source", "source_type", "source_tier", "date", "retrieved_at",
        "access_method", "retrieval_status", "quote_or_value", "confidence", "contradiction_status",
    }
    missing_cols = required - set(rows[0])
    if missing_cols:
        issues.append(issue("MISSING_FIELD", artifact="evidence_map", message=f"missing columns {sorted(missing_cols)}"))
    unknown_cols = set(rows[0]) - EVIDENCE_COLUMNS
    for col in sorted(unknown_cols):
        issues.append(
            issue("UNKNOWN_FIELD", artifact="evidence_map", pointer=f"/evidence_map/{col}", message="unknown column refused", actual=col)
        )
    direct = 0
    high_q = 0
    execution = manifest.get("execution")
    quality = execution.get("research_quality", "unknown") if isinstance(execution, dict) else "unknown"
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(issue("TYPE", pointer=f"/evidence/{idx}", message="must be object"))
            continue
        for col in sorted(set(row) - EVIDENCE_COLUMNS):
            issues.append(issue("UNKNOWN_FIELD", pointer=f"/evidence/{idx}/{col}", message="unknown column refused", actual=col))
        eid = (row.get("evidence_id") or "").strip()
        p = f"/evidence/{idx}"
        if not eid:
            issues.append(issue("EMPTY_ID", pointer=f"{p}/evidence_id", message="empty"))
        elif eid in ids:
            issues.append(issue("DUPLICATE_ID", pointer=f"{p}/evidence_id", actual=eid))
        else:
            ids.add(eid)
            if not has_id_prefix(eid, "evidence"):
                issues.append(issue("SCHEMA", pointer=f"{p}/evidence_id", message="must use evidence: prefix", actual=eid))
        for required_field in required - {"evidence_id"}:
            if not nonempty_str(row.get(required_field)):
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{required_field}", message="required non-empty CSV value"))
        tier = row.get("source_tier", "")
        retrieval = row.get("retrieval_status", "")
        if tier and tier not in SOURCE_TIERS:
            issues.append(issue("ENUM", pointer=f"{p}/source_tier", actual=tier))
        if retrieval and retrieval not in RETRIEVAL_STATUSES:
            issues.append(issue("ENUM", pointer=f"{p}/retrieval_status", actual=retrieval))
        conf_raw = row.get("confidence", "")
        # CSV is stringly — parse carefully without silent inventing
        try:
            conf = float(conf_raw)
            if not math.isfinite(conf):
                issues.append(issue("NON_FINITE", pointer=f"{p}/confidence", actual=conf_raw))
            elif not 0.0 <= conf <= 1.0:
                issues.append(issue("RANGE", pointer=f"{p}/confidence", message="must be within [0, 1]", actual=conf))
            elif retrieval == "search-snippet" and conf > 0.45:
                issues.append(issue("SNIPPET_CONFIDENCE", pointer=f"{p}/confidence", message="cap 0.45", actual=conf))
        except (TypeError, ValueError):
            issues.append(issue("TYPE", pointer=f"{p}/confidence", message="must be numeric string", actual=conf_raw))
        if retrieval in {"opened", "downloaded", "api", "local-file", "user-provided"}:
            direct += 1
            if tier in {"primary", "authoritative-secondary", "user-provided"}:
                high_q += 1
    metrics = {
        "evidence_rows": len(rows),
        "direct_sources": direct,
        "high_quality_direct_sources": high_q,
        "direct_source_ratio": round(direct / len(rows), 4) if rows else 0.0,
    }
    if quality == "best-available" and high_q < 1:
        issues.append(issue("SOURCE_QUALITY", artifact="evidence_map", message="needs high-quality direct sources"))
    return _check("evidence", issues, metrics), ids


def validate_nodes(nodes: list[Any], evidence_ids: set[str], manifest: dict[str, Any]) -> tuple[CheckResult, set[str]]:
    issues: list[Issue] = []
    ids: set[str] = set()
    cutoff = parse_time(_mapping(manifest.get("temporal_frame")).get("observation_cutoff"))
    for idx, raw in enumerate(nodes):
        p = f"/nodes/{idx}"
        if not isinstance(raw, dict):
            issues.append(issue("TYPE", pointer=p, message="must be object"))
            continue
        reject_unknown_fields(raw, NODE_FIELDS, p, issues)
        for required in (
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
            "status",
            "timeline",
            "confidence",
        ):
            if required not in raw:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{required}", message="required"))
        nid = raw.get("id")
        if not nonempty_str(nid):
            issues.append(issue("EMPTY_ID", pointer=f"{p}/id", message="empty"))
        elif nid in ids:
            issues.append(issue("DUPLICATE_ID", pointer=f"{p}/id", actual=nid))
        else:
            ids.add(str(nid))
            if not has_id_prefix(nid, "node"):
                issues.append(issue("SCHEMA", pointer=f"{p}/id", message="invalid node ID prefix", actual=nid))
        if raw.get("type") not in NODE_TYPES:
            issues.append(issue("ENUM", pointer=f"{p}/type", actual=raw.get("type")))
        status = raw.get("status")
        if status not in LEGACY_STATUS and status not in EPISTEMIC_STATUS:
            issues.append(issue("ENUM", pointer=f"{p}/status", actual=status))
        if raw.get("timeline") not in TIMELINE_LABELS and raw.get("timeline") is not None:
            issues.append(issue("ENUM", pointer=f"{p}/timeline", actual=raw.get("timeline")))
        refs = [str(x) for x in ensure_list(raw.get("evidence_ids")) if nonempty_str(x)]
        for r in refs:
            if r not in evidence_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/evidence_ids", actual=r))
        if status == "fact" and not refs:
            issues.append(issue("FACT_PROVENANCE", pointer=f"{p}/evidence_ids", message="fact requires evidence"))
        if status == "assumption" and not nonempty_str(raw.get("assumption_ref")):
            issues.append(issue("ASSUMPTION", pointer=p, message="assumption requires assumption_ref"))
        if status == "fact" and not refs and nonempty_str(raw.get("assumption_ref")):
            issues.append(issue("ASSUMPTION", pointer=p, message="assumption must not pretend to be evidence-backed fact"))
        nt = parse_time(raw.get("time"))
        if nt is None:
            issues.append(issue("TEMPORAL_FRAME", pointer=f"{p}/time", message="must be valid ISO date/time"))
        if nt and cutoff and nt > cutoff and status == "fact":
            issues.append(issue("FUTURE_FACT", pointer=f"{p}/status", message="post-cutoff cannot be fact"))
        if not nonempty_str(raw.get("mechanism")) or len(str(raw.get("mechanism", "")).split()) < 10:
            issues.append(issue("MECHANISM", pointer=f"{p}/mechanism", message="need concrete mechanism"))
        unit_interval(raw.get("confidence"), f"{p}/confidence", issues)
        if "probability" in raw and raw.get("probability") is not None:
            unit_interval(raw.get("probability"), f"{p}/probability", issues)
        if parse_duration_seconds(raw.get("lag")) is None:
            issues.append(issue("LAG", pointer=f"{p}/lag", message="must be supported ISO duration", actual=raw.get("lag")))
        for field, allowed in (
            ("state_before", NODE_STATE_FIELDS),
            ("state_after", NODE_STATE_FIELDS),
            ("trigger", NODE_TRIGGER_FIELDS),
            ("sensitivity", NODE_SENSITIVITY_FIELDS),
        ):
            value = raw.get(field)
            if field == "sensitivity" and value is None:
                continue
            if not isinstance(value, dict):
                issues.append(issue("TYPE", pointer=f"{p}/{field}", message="must be object"))
            else:
                reject_unknown_fields(value, allowed, f"{p}/{field}", issues)
        for state_name in ("state_before", "state_after"):
            state = raw.get(state_name)
            if isinstance(state, dict):
                if not nonempty_str(state.get("summary")):
                    issues.append(issue("MISSING_FIELD", pointer=f"{p}/{state_name}/summary", message="required"))
                if "value" in state:
                    refuse_string_number(state.get("value"), f"{p}/{state_name}/value", issues)
        trigger = raw.get("trigger")
        if isinstance(trigger, dict):
            for field in ("kind", "description"):
                if not nonempty_str(trigger.get(field)):
                    issues.append(issue("MISSING_FIELD", pointer=f"{p}/trigger/{field}", message="required"))
            if "magnitude" in trigger:
                refuse_string_number(trigger.get("magnitude"), f"{p}/trigger/magnitude", issues)
        if "baseline" in raw:
            refuse_string_number(raw.get("baseline"), f"{p}/baseline", issues)
    return _check("nodes", issues, {"nodes": len(ids)}), ids


def validate_edges(
    edges: list[Any],
    node_ids: set[str],
    evidence_ids: set[str],
    node_types: dict[str, Any] | None = None,
) -> tuple[CheckResult, dict[str, dict[str, Any]]]:
    issues: list[Issue] = []
    by_id: dict[str, dict[str, Any]] = {}
    for idx, raw in enumerate(edges):
        p = f"/edges/{idx}"
        if not isinstance(raw, dict):
            issues.append(issue("TYPE", pointer=p, message="must be object"))
            continue
        reject_unknown_fields(raw, EDGE_FIELDS, p, issues)
        for required in (
            "id",
            "from",
            "to",
            "relation",
            "sign",
            "base_strength",
            "evidence_confidence",
            "mechanism",
            "lag_distribution",
            "context_modifiers",
            "status",
            "transform",
            "effect_parameter",
        ):
            if required not in raw:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{required}", message="required"))
        eid = raw.get("id")
        if not nonempty_str(eid):
            issues.append(issue("EMPTY_ID", pointer=f"{p}/id", message="empty"))
        elif eid in by_id:
            issues.append(issue("DUPLICATE_ID", pointer=f"{p}/id", actual=eid))
        else:
            by_id[str(eid)] = raw
            if not has_id_prefix(eid, "edge"):
                issues.append(issue("SCHEMA", pointer=f"{p}/id", message="invalid edge ID prefix", actual=eid))
        for ep in ("from", "to"):
            if raw.get(ep) not in node_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/{ep}", actual=raw.get(ep)))
        rel = raw.get("relation")
        if rel not in RELATION_ALLOWED:
            issues.append(issue("RELATION", pointer=f"{p}/relation", message="invalid relation", actual=rel))
        sign = raw.get("sign")
        if sign not in (-1, 1):
            issues.append(issue("SIGN", pointer=f"{p}/sign", message="must be -1 or 1", actual=sign))
        # relation/sign consistency
        if rel in {"decreases", "inhibits", "prevents", "dampens"} and sign == 1:
            issues.append(issue("SIGN", pointer=f"{p}/sign", message="relation implies negative sign"))
        if rel in {"increases", "enables", "causes", "amplifies", "triggers"} and sign == -1:
            issues.append(issue("SIGN", pointer=f"{p}/sign", message="relation implies positive sign"))
        refuse_string_number(raw.get("base_strength"), f"{p}/base_strength", issues)
        # confidence is evidence_confidence only — still validate if present
        if "confidence" in raw:
            unit_interval(raw.get("confidence"), f"{p}/confidence", issues)
        if "evidence_confidence" in raw:
            unit_interval(raw.get("evidence_confidence"), f"{p}/evidence_confidence", issues)
        if "existence_prob" in raw:
            unit_interval(raw.get("existence_prob"), f"{p}/existence_prob", issues)
        if raw.get("status") not in EDGE_STATUS:
            issues.append(issue("ENUM", pointer=f"{p}/status", message="invalid edge status", actual=raw.get("status")))
        if raw.get("transform") not in TRANSFORMS:
            issues.append(issue("SCHEMA", pointer=f"{p}/transform", message="unsupported transform", actual=raw.get("transform")))
        if not nonempty_str(raw.get("mechanism")) or len(str(raw.get("mechanism", "")).split()) < 10:
            issues.append(issue("MECHANISM", pointer=f"{p}/mechanism", message="mechanism required"))
        lag = raw.get("lag_distribution")
        if not isinstance(lag, dict):
            issues.append(issue("LAG", pointer=f"{p}/lag_distribution", message="requires object"))
        else:
            reject_unknown_fields(lag, LAG_FIELDS, f"{p}/lag_distribution", issues)
            ltype = lag.get("type")
            if ltype not in LAG_TYPES:
                issues.append(issue("LAG", pointer=f"{p}/lag_distribution/type", message="invalid lag type", actual=ltype))
            required_by_type = {
                "fixed": ("fixed",),
                "uniform": ("min", "max"),
                "triangular": ("min", "mode", "max"),
                "truncated_exponential": ("min", "max", "rate"),
            }
            for field in required_by_type.get(str(ltype), ()):
                if field not in lag:
                    issues.append(issue("LAG", pointer=f"{p}/lag_distribution/{field}", message=f"{ltype} requires {field}"))
            duration_fields = [field for field in ("fixed", "min", "mode", "max", "mean") if field in lag]
            parsed: dict[str, float] = {}
            for field in duration_fields:
                seconds = parse_duration_seconds(lag.get(field))
                if seconds is None:
                    issues.append(issue("LAG", pointer=f"{p}/lag_distribution/{field}", message="must be supported ISO duration", actual=lag.get(field)))
                else:
                    parsed[field] = seconds
            if "rate" in lag:
                rate = refuse_string_number(lag.get("rate"), f"{p}/lag_distribution/rate", issues)
                if rate is not None and rate <= 0:
                    issues.append(issue("RANGE", pointer=f"{p}/lag_distribution/rate", message="must be positive", actual=rate))
            if "min" in parsed and "max" in parsed and parsed["min"] > parsed["max"]:
                issues.append(issue("LAG_ORDER", pointer=f"{p}/lag_distribution", message="min > max"))
            if "mode" in parsed and "min" in parsed and parsed["mode"] < parsed["min"]:
                issues.append(issue("LAG_ORDER", pointer=f"{p}/lag_distribution/mode", message="mode < min"))
            if "mode" in parsed and "max" in parsed and parsed["mode"] > parsed["max"]:
                issues.append(issue("LAG_ORDER", pointer=f"{p}/lag_distribution/mode", message="mode > max"))
        mods = raw.get("context_modifiers")
        if not isinstance(mods, list) or not mods:
            issues.append(issue("CONTEXT", pointer=f"{p}/context_modifiers", message="requires modifiers"))
        else:
            for mi, mod in enumerate(mods):
                if not isinstance(mod, dict):
                    continue
                reject_unknown_fields(mod, CONTEXT_MODIFIER_FIELDS, f"{p}/context_modifiers/{mi}", issues)
                ctx = mod.get("context")
                if ctx not in node_ids:
                    issues.append(issue("CONTEXT_MISSING", pointer=f"{p}/context_modifiers/{mi}/context", actual=ctx))
                elif node_types is not None and node_types.get(str(ctx)) != "context":
                    issues.append(issue("CONTEXT_MISSING", pointer=f"{p}/context_modifiers/{mi}/context", message="must reference context node", actual=ctx))
                m = refuse_string_number(mod.get("multiplier"), f"{p}/context_modifiers/{mi}/multiplier", issues)
                if m is not None and (m <= 0 or not math.isfinite(m)):
                    issues.append(issue("MULTIPLIER", pointer=f"{p}/context_modifiers/{mi}/multiplier", actual=m))
                if not nonempty_str(mod.get("rationale")):
                    issues.append(issue("MULTIPLIER", pointer=f"{p}/context_modifiers/{mi}/rationale", message="required"))
        if raw.get("from") == raw.get("to"):
            if rel not in {"feedback", "autoregressive"} and raw.get("feedback_policy") is None:
                issues.append(issue("SELF_EDGE", pointer=p, message="self-edge requires feedback/autoregressive policy"))
        ev = ensure_list(raw.get("evidence"))
        if not ev and not nonempty_str(raw.get("assumption_ref")):
            issues.append(issue("MISSING_FIELD", pointer=f"{p}/evidence", message="evidence or assumption required"))
        for r in ev:
            if r not in evidence_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/evidence", actual=r))
        effect = raw.get("effect_parameter")
        if not isinstance(effect, dict):
            issues.append(issue("TYPE", pointer=f"{p}/effect_parameter", message="must be object"))
        else:
            reject_unknown_fields(effect, EFFECT_PARAMETER_FIELDS, f"{p}/effect_parameter", issues)
            if not nonempty_str(effect.get("kind")):
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/effect_parameter/kind", message="required"))
            numeric_present = False
            for field in ("reference_value", "lower", "upper"):
                if field in effect:
                    numeric_present = True
                    refuse_string_number(effect.get(field), f"{p}/effect_parameter/{field}", issues)
            if not numeric_present and "distribution" not in effect:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/effect_parameter", message="requires numerical reference or distribution"))
    return _check("edges", issues, {"edges": len(by_id)}), by_id


def validate_trace(
    rows: list[dict[str, Any]],
    node_ids: set[str],
    edge_by_id: dict[str, dict[str, Any]],
    evidence_ids: set[str],
    manifest: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]] | None = None,
) -> CheckResult:
    issues: list[Issue] = []
    if not rows:
        issues.append(issue("TRACE_EMPTY", artifact="propagation_trace", message="requires at least one row"))
        return _check("trace", issues)
    steps: list[int] = []
    prev_time = None
    frame = _mapping(manifest.get("temporal_frame"))
    start = parse_time(frame.get("simulation_start"))
    end = parse_time(frame.get("simulation_end"))
    previous_hash: str | None = None
    for idx, row in enumerate(rows):
        p = f"/propagation_trace/{idx}"
        if not isinstance(row, dict):
            issues.append(issue("TYPE", pointer=p, message="trace row must be object"))
            continue
        reject_unknown_fields(row, TRACE_ROW_FIELDS, p, issues)
        step = row.get("step")
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            issues.append(issue("TRACE_STEP", pointer=f"{p}/step", message="step must be positive int", actual=step))
        else:
            steps.append(step)
        if row.get("formula_version") != FORMULA_VERSION:
            issues.append(
                issue(
                    "SCHEMA",
                    pointer=f"{p}/formula_version",
                    message="trace formula version must match validator",
                    expected=FORMULA_VERSION,
                    actual=row.get("formula_version"),
                )
            )
        if "input_effect" not in row and "input_change" not in row:
            issues.append(issue("MISSING_FIELD", pointer=p, message="trace row requires input_effect or input_change"))
        sample_refs = row.get("sample_refs")
        if not isinstance(sample_refs, list) or not sample_refs or not all(nonempty_str(ref) for ref in sample_refs):
            issues.append(issue("MISSING_FIELD", pointer=f"{p}/sample_refs", message="requires non-empty sample reference array"))
        eid = row.get("edge_id")
        edge = edge_by_id.get(str(eid)) if eid else None
        if eid not in edge_by_id:
            issues.append(issue("UNKNOWN_REF", pointer=f"{p}/edge_id", actual=eid))
        for ep in ("from", "to"):
            if row.get(ep) not in node_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/{ep}", actual=row.get(ep)))
        t = parse_time(row.get("time"))
        if t is None:
            issues.append(issue("TRACE_TIME", pointer=f"{p}/time", message="invalid time"))
        else:
            if prev_time and t < prev_time:
                issues.append(issue("TRACE_TIME", pointer=f"{p}/time", message="time must not reverse"))
            prev_time = t
            if start and end and not (start <= t <= end):
                issues.append(issue("TRACE_TIME", pointer=f"{p}/time", message="outside simulation window"))
            if edge is not None and nodes_by_id is not None:
                source_node = nodes_by_id.get(str(edge.get("from")))
                source_time = parse_time(source_node.get("time")) if isinstance(source_node, dict) else None
                lag = edge.get("lag_distribution")
                if source_time and isinstance(lag, dict):
                    lower = lag.get("min", lag.get("fixed"))
                    lower_seconds = parse_duration_seconds(lower)
                    if lower_seconds is not None and t < source_time + timedelta(seconds=lower_seconds):
                        issues.append(
                            issue(
                                "TRACE_TIME",
                                pointer=f"{p}/time",
                                message="trace effect occurs before edge lag lower bound",
                                expected=(source_time + timedelta(seconds=lower_seconds)).isoformat(),
                                actual=row.get("time"),
                            )
                        )
        issues.extend(replay_trace_row(row, edge, node_ids, set(edge_by_id), pointer=p))
        for r in ensure_list(row.get("evidence_ids")):
            if r not in evidence_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/evidence_ids", actual=r))
        chain = row.get("hash_chain")
        if not isinstance(chain, str) or re.fullmatch(r"[0-9a-f]{64}", chain) is None:
            issues.append(issue("SCHEMA", pointer=f"{p}/hash_chain", message="requires lowercase SHA-256 hash"))
        else:
            payload = dict(row)
            payload.pop("hash_chain", None)
            expected_chain = canonical_hash({"previous_hash": previous_hash, "row": payload})
            if chain != expected_chain:
                issues.append(
                    issue(
                        "REPLAY_MISMATCH",
                        pointer=f"{p}/hash_chain",
                        message="trace hash chain mismatch",
                        expected=expected_chain,
                        actual=chain,
                    )
                )
            previous_hash = chain
    if steps:
        if len(steps) != len(set(steps)):
            issues.append(issue("TRACE_STEP", message="steps must be unique", actual=steps))
        expected = list(range(1, len(rows) + 1))
        if steps != expected:
            issues.append(issue("TRACE_STEP", message="steps must be ordered and continuous from 1", expected=expected, actual=steps))
    return _check("trace", issues, {"trace_rows": len(rows), "formula_version": FORMULA_VERSION})


def validate_actors(actors: list[Any], node_ids: set[str], evidence_ids: set[str], nodes: list[Any]) -> tuple[CheckResult, set[str]]:
    issues: list[Issue] = []
    ids: set[str] = set()
    node_types = {str(n.get("id")): n.get("type") for n in nodes if isinstance(n, dict)}
    for idx, raw in enumerate(actors):
        p = f"/actors/{idx}"
        if not isinstance(raw, dict):
            issues.append(issue("TYPE", pointer=p, message="must be object"))
            continue
        reject_unknown_fields(raw, ACTOR_FIELDS, p, issues)
        aid = raw.get("id")
        if not nonempty_str(aid):
            issues.append(issue("EMPTY_ID", pointer=f"{p}/id", message="empty"))
        elif str(aid) in ids:
            issues.append(issue("DUPLICATE_ID", pointer=f"{p}/id", actual=aid))
        else:
            ids.add(str(aid))
            if not has_id_prefix(aid, "actor"):
                issues.append(issue("SCHEMA", pointer=f"{p}/id", message="must use actor: prefix", actual=aid))
        for required in ("id", "person_node", "public_role", "scope_note", "materiality", "subject_class", "evidence_ids"):
            if required not in raw:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{required}", message="required"))
        person = raw.get("person_node")
        if person not in node_ids:
            issues.append(issue("UNKNOWN_REF", pointer=f"{p}/person_node", actual=person))
        elif node_types.get(str(person)) != "entity":
            issues.append(issue("PERSON_NODE", pointer=f"{p}/person_node", message="must reference entity node", actual=person))
        mat = raw.get("materiality")
        if mat not in MATERIALITY:
            # typo or invalid — hard fail
            issues.append(issue("MATERIALITY", pointer=f"{p}/materiality", message="must be material|non_material", actual=mat))
        sc = raw.get("subject_class")
        if sc is not None and sc not in SUBJECT_CLASS:
            issues.append(issue("SUBJECT_CLASS", pointer=f"{p}/subject_class", actual=sc))
        if sc in {"private_person", "minor", "unknown"}:
            issues.append(issue("PRIVACY_REFUSAL", pointer=f"{p}/subject_class", message="cannot roleplay private/minor/unknown", actual=sc))
        # Forbidden dossier fields
        for banned in ("address", "phone", "email", "family", "diagnosis", "whereabouts", "ssn"):
            if banned in raw:
                issues.append(issue("PRIVACY_REFUSAL", pointer=f"{p}/{banned}", message="forbidden personal field"))
        actor_evidence = raw.get("evidence_ids")
        if not isinstance(actor_evidence, list) or not actor_evidence:
            issues.append(issue("MISSING_FIELD", pointer=f"{p}/evidence_ids", message="actor dossier requires evidence"))
        else:
            for ref in actor_evidence:
                if ref not in evidence_ids:
                    issues.append(issue("UNKNOWN_REF", pointer=f"{p}/evidence_ids", actual=ref))

        # Nested tracks — every object forbids unknown fields (AC2)
        research = raw.get("research_track")
        if isinstance(research, dict):
            reject_unknown_fields(research, RESEARCH_TRACK_FIELDS, f"{p}/research_track", issues)
            for ci, claim in enumerate(ensure_list(research.get("claims"))):
                if isinstance(claim, dict):
                    reject_unknown_fields(claim, RESEARCH_CLAIM_FIELDS, f"{p}/research_track/claims/{ci}", issues)
                    for required in ("id", "claim", "evidence_ids", "confidence", "available_at", "access_basis"):
                        if required not in claim:
                            issues.append(issue("MISSING_FIELD", pointer=f"{p}/research_track/claims/{ci}/{required}", message="required"))
                    unit_interval(claim.get("confidence"), f"{p}/research_track/claims/{ci}/confidence", issues)
                    if parse_time(claim.get("available_at")) is None:
                        issues.append(issue("TEMPORAL_KNOWLEDGE", pointer=f"{p}/research_track/claims/{ci}/available_at", message="must be valid ISO date/time"))
                    for ref in ensure_list(claim.get("evidence_ids")):
                        if ref not in evidence_ids:
                            issues.append(issue("UNKNOWN_REF", pointer=f"{p}/research_track/claims/{ci}/evidence_ids", actual=ref))
        elif mat == "material":
            issues.append(issue("HUMAN_TRACK", pointer=f"{p}/research_track", message="material actor requires research track"))

        roleplay = raw.get("roleplay_track")
        if isinstance(roleplay, dict):
            reject_unknown_fields(roleplay, ROLEPLAY_TRACK_FIELDS, f"{p}/roleplay_track", issues)
            for hi, hyp in enumerate(ensure_list(roleplay.get("hypotheses"))):
                if not isinstance(hyp, dict):
                    continue
                reject_unknown_fields(hyp, ROLEPLAY_HYPOTHESIS_FIELDS, f"{p}/roleplay_track/hypotheses/{hi}", issues)
                for required in ("id", "action", "reasoning", "status", "evidence_ids"):
                    if required not in hyp:
                        issues.append(issue("MISSING_FIELD", pointer=f"{p}/roleplay_track/hypotheses/{hi}/{required}", message="required"))
                if ensure_list(hyp.get("evidence_ids")):
                    issues.append(issue("ROLEPLAY_EVIDENCE", pointer=f"{p}/roleplay_track", message="roleplay is not evidence"))
                if "probability" in hyp or "confidence" in hyp:
                    issues.append(issue("ROLEPLAY_PROBABILITY", pointer=f"{p}/roleplay_track/hypotheses/{hi}", message="roleplay cannot emit probability or confidence"))
                if hyp.get("status") != "simulation":
                    issues.append(issue("ENUM", pointer=f"{p}/roleplay_track/hypotheses/{hi}/status", message="roleplay hypothesis must be simulation"))
            cutoff = parse_time(roleplay.get("knowledge_cutoff"))
            if cutoff is None:
                issues.append(issue("TEMPORAL_KNOWLEDGE", pointer=f"{p}/roleplay_track/knowledge_cutoff", message="must be valid ISO date/time"))
            if not nonempty_str(roleplay.get("packet_hash")):
                issues.append(issue("TEMPORAL_KNOWLEDGE", pointer=f"{p}/roleplay_track/packet_hash", message="sealed packet hash required"))
        elif mat == "material":
            issues.append(issue("HUMAN_TRACK", pointer=f"{p}/roleplay_track", message="material actor requires sealed roleplay track"))

        adj = raw.get("adjudication")
        if isinstance(adj, dict):
            reject_unknown_fields(adj, ADJUDICATION_FIELDS, f"{p}/adjudication", issues)
            if adj.get("calibrated") is False:
                for result_idx, result in enumerate(ensure_list(adj.get("results"))):
                    if isinstance(result, dict) and result.get("probability") is not None:
                        issues.append(issue("PROBABILITY_UNCALIBRATED", pointer=f"{p}/adjudication/results/{result_idx}/probability", message="uncalibrated adjudication cannot emit probability"))
        elif mat == "material":
            issues.append(issue("HUMAN_TRACK", pointer=f"{p}/adjudication", message="material actor requires adjudication"))

        for ri, resp in enumerate(ensure_list(raw.get("predicted_responses"))):
            if isinstance(resp, dict):
                reject_unknown_fields(resp, PREDICTED_RESPONSE_FIELDS, f"{p}/predicted_responses/{ri}", issues)
                if "confidence" in resp:
                    unit_interval(resp.get("confidence"), f"{p}/predicted_responses/{ri}/confidence", issues)
                if "probability" in resp and resp.get("probability") is not None:
                    calibrated = isinstance(adj, dict) and adj.get("calibrated") is True
                    if not calibrated:
                        issues.append(issue("PROBABILITY_UNCALIBRATED", pointer=f"{p}/predicted_responses/{ri}/probability", message="requires calibrated adjudication"))
    return _check("actors", issues, {"actors": len(ids)}), ids


def _branch_fingerprint(branch: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "summary": str(branch.get("summary", "")).strip().lower(),
            "causal_trace": sorted(str(item) for item in ensure_list(branch.get("causal_trace"))),
            "end_state": branch.get("end_state"),
            "leading_indicators": branch.get("leading_indicators"),
            "disconfirming_conditions": branch.get("disconfirming_conditions"),
        }
    )


def _branch_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    def tokens(branch: dict[str, Any]) -> set[str]:
        text = " ".join(
            [
                str(branch.get("summary", "")),
                str((branch.get("end_state") or {}).get("summary", "")),
                " ".join(str(item) for item in ensure_list(branch.get("causal_trace"))),
            ]
        ).lower()
        return set(re.findall(r"[a-z0-9:_-]+", text))

    a, b = tokens(left), tokens(right)
    return len(a & b) / len(a | b) if a | b else 1.0


def validate_branches(
    data: Any,
    edge_ids: set[str],
    actor_ids: set[str],
    evidence_ids: set[str],
    manifest: dict[str, Any],
    node_ids: set[str] | None = None,
) -> CheckResult:
    issues: list[Issue] = []
    if not isinstance(data, dict):
        issues.append(issue("TYPE", artifact="branch_ledger", message="must be object"))
        return _check("branches", issues)
    reject_unknown_fields(data, BRANCH_LEDGER_FIELDS, "branch_ledger", issues)
    for field in ("schema_version", "likelihood_mode", "calibrated", "branches"):
        if field not in data:
            issues.append(
                issue(
                    "MISSING_FIELD",
                    pointer=f"branch_ledger.{field}",
                    message="required by branch-ledger schema 2.0.0",
                )
            )
    if data.get("schema_version") != SCHEMA_VERSION:
        issues.append(
            issue(
                "SCHEMA",
                pointer="branch_ledger.schema_version",
                expected=SCHEMA_VERSION,
                actual=data.get("schema_version"),
            )
        )
    if data.get("likelihood_mode") not in {"deterministic", "relative_weight", "calibrated_probability"}:
        issues.append(issue("ENUM", pointer="branch_ledger.likelihood_mode", actual=data.get("likelihood_mode")))
    if not is_bool(data.get("calibrated")):
        issues.append(issue("TYPE", pointer="branch_ledger.calibrated", message="must be boolean"))
    manifest_likelihood = manifest.get("likelihood_mode")
    ledger_likelihood = data.get("likelihood_mode")
    if manifest_likelihood != ledger_likelihood:
        issues.append(
            issue(
                "TRACK_MISMATCH",
                pointer="branch_ledger.likelihood_mode",
                expected=manifest_likelihood,
                actual=ledger_likelihood,
                message="manifest and branch ledger likelihood modes must match",
            )
        )
    expected_calibrated = ledger_likelihood == "calibrated_probability"
    if isinstance(data.get("calibrated"), bool) and data.get("calibrated") is not expected_calibrated:
        issues.append(
            issue(
                "TRACK_MISMATCH",
                pointer="branch_ledger.calibrated",
                expected=expected_calibrated,
                actual=data.get("calibrated"),
                message="calibrated flag must match likelihood mode",
            )
        )
    calibration = data.get("calibration")
    if calibration is not None:
        if not isinstance(calibration, dict):
            issues.append(issue("TYPE", pointer="branch_ledger.calibration", message="must be object"))
        else:
            reject_unknown_fields(calibration, CALIBRATION_FIELDS, "branch_ledger.calibration", issues)
    if expected_calibrated:
        if not isinstance(calibration, dict):
            issues.append(issue("MISSING_FIELD", pointer="branch_ledger.calibration", message="calibration metadata required"))
        raw_paths = manifest.get("artifact_paths")
        artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
        if not nonempty_str(artifact_paths.get("calibration_report")):
            issues.append(
                issue(
                    "MISSING_ARTIFACT",
                    pointer="manifest.artifact_paths.calibration_report",
                    message="calibrated probability requires a calibration report",
                )
            )
    raw_branches = data.get("branches")
    if not isinstance(raw_branches, list):
        issues.append(issue("TYPE", pointer="branch_ledger.branches", message="must be array"))
        branches: list[Any] = []
    else:
        branches = raw_branches
    if not branches:
        issues.append(issue("BRANCH_COUNT", pointer="branch_ledger.branches", message="requires at least one branch"))
    fps: dict[str, tuple[str, dict[str, Any]]] = {}
    branch_ids: set[str] = set()
    likelihood = manifest.get("likelihood_mode") or data.get("likelihood_mode") or "relative_weight"
    calibrated = likelihood == "calibrated_probability" or data.get("calibrated") is True
    total_prob = 0.0
    total_weight = 0.0
    for idx, branch in enumerate(branches):
        p = f"/branches/{idx}"
        if not isinstance(branch, dict):
            issues.append(issue("TYPE", pointer=p, message="must be object"))
            continue
        reject_unknown_fields(branch, BRANCH_FIELDS, p, issues)
        for required in (
            "id",
            "name",
            "summary",
            "causal_trace",
            "key_decision_points",
            "end_state",
            "leading_indicators",
            "disconfirming_conditions",
            "evidence_ids",
            "confidence",
        ):
            if required not in branch:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{required}", message="required"))
        fp = _branch_fingerprint(branch)
        bid = str(branch.get("id", idx))
        if not has_id_prefix(branch.get("id"), "branch"):
            issues.append(issue("SCHEMA", pointer=f"{p}/id", message="must use branch: prefix", actual=branch.get("id")))
        if bid in branch_ids:
            issues.append(issue("DUPLICATE_ID", pointer=f"{p}/id", actual=bid))
        branch_ids.add(bid)
        for other_id, (other_fp, other_branch) in fps.items():
            if other_fp == fp:
                issues.append(issue("BRANCH_DUPLICATE", pointer=p, message=f"exact duplicate of {other_id}"))
            elif _branch_similarity(branch, other_branch) >= 0.90:
                issues.append(
                    issue("BRANCH_NEAR_DUPLICATE", severity="warning", pointer=p, message=f"near-duplicate of {other_id}")
                )
        fps[bid] = (fp, branch)
        for r in ensure_list(branch.get("causal_trace")):
            if r not in edge_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/causal_trace", actual=r))
        for r in ensure_list(branch.get("key_decision_points")):
            if r not in actor_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/key_decision_points", actual=r))
        end_state = branch.get("end_state")
        if not isinstance(end_state, dict):
            issues.append(issue("TYPE", pointer=f"{p}/end_state", message="must be object"))
        else:
            reject_unknown_fields(end_state, BRANCH_END_STATE_FIELDS, f"{p}/end_state", issues)
            if parse_time(end_state.get("time")) is None:
                issues.append(issue("TEMPORAL_FRAME", pointer=f"{p}/end_state/time", message="must be valid ISO date/time"))
            if not nonempty_str(end_state.get("summary")):
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/end_state/summary", message="required"))
        indicators = branch.get("leading_indicators")
        if not isinstance(indicators, list):
            issues.append(issue("TYPE", pointer=f"{p}/leading_indicators", message="must be array"))
        else:
            if _mapping(manifest.get("temporal_frame")).get("future_projection") is True and not indicators:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/leading_indicators", message="future branch requires monitoring indicators"))
            for indicator_idx, indicator in enumerate(indicators):
                ip = f"{p}/leading_indicators/{indicator_idx}"
                if not isinstance(indicator, dict):
                    issues.append(issue("TYPE", pointer=ip, message="must be object"))
                    continue
                reject_unknown_fields(indicator, LEADING_INDICATOR_FIELDS, ip, issues)
                for field in LEADING_INDICATOR_FIELDS:
                    if not nonempty_str(indicator.get(field)):
                        issues.append(issue("MISSING_FIELD", pointer=f"{ip}/{field}", message="required"))
                if node_ids is not None and indicator.get("node") not in node_ids:
                    issues.append(issue("UNKNOWN_REF", pointer=f"{ip}/node", actual=indicator.get("node")))
                if parse_duration_seconds(indicator.get("window")) is None:
                    issues.append(issue("TEMPORAL_FRAME", pointer=f"{ip}/window", message="must be supported ISO duration"))
        disconfirming = branch.get("disconfirming_conditions")
        if not isinstance(disconfirming, list) or (
            _mapping(manifest.get("temporal_frame")).get("future_projection") is True
            and (not disconfirming or not all(nonempty_str(item) for item in disconfirming))
        ):
            issues.append(issue("MISSING_FIELD", pointer=f"{p}/disconfirming_conditions", message="future branch requires non-empty conditions"))
        for ref in ensure_list(branch.get("evidence_ids")):
            if ref not in evidence_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/evidence_ids", actual=ref))
        unit_interval(branch.get("confidence"), f"{p}/confidence", issues)
        if manifest.get("schema_version") == SCHEMA_VERSION and manifest.get("simulation_mode") in {"deterministic", "monte_carlo"}:
            derivation = branch.get("derivation")
            if derivation not in {"analyst_authored", "engine_derived"}:
                issues.append(
                    issue(
                        "MISSING_FIELD",
                        pointer=f"{p}/derivation",
                        message="numerical branches must declare analyst_authored or engine_derived",
                    )
                )
            elif derivation == "analyst_authored" and branch.get("representative_run") is not None:
                issues.append(
                    issue(
                        "TRACK_MISMATCH",
                        pointer=f"{p}/representative_run",
                        message="analyst-authored branches cannot claim an engine representative run",
                    )
                )
            elif derivation == "engine_derived" and not nonempty_str(branch.get("representative_run")):
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/representative_run", message="engine-derived branch requires representative run"))
            trace_hash = branch.get("trace_hash")
            if not isinstance(trace_hash, str) or re.fullmatch(r"[0-9a-f]{64}", trace_hash) is None:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/trace_hash", message="numerical branch requires SHA-256 trace hash"))
        # stress scenarios
        if branch.get("stress") is True or branch.get("probability") is None and "relative_weight" in branch:
            if branch.get("probability") is not None and branch.get("stress") is True:
                issues.append(issue("TYPE", pointer=f"{p}/probability", message="stress probability must be null"))
        if "probability" in branch and branch.get("probability") is not None:
            if not calibrated and likelihood != "deterministic":
                # v2: uncalibrated should use relative_weight
                issues.append(
                    issue(
                        "PROBABILITY_UNCALIBRATED",
                        pointer=f"{p}/probability",
                        message="uncalibrated branches must use relative_weight, not probability",
                        actual=branch.get("probability"),
                    )
                )
            else:
                val = refuse_string_number(branch.get("probability"), f"{p}/probability", issues)
                if val is not None:
                    if not 0.0 <= val <= 1.0:
                        issues.append(issue("RANGE", pointer=f"{p}/probability", message="must be within [0, 1]", actual=val))
                    total_prob += val
                    if calibrated:
                        for req in ("method", "sample_count", "interval", "calibration_policy_ref"):
                            if req not in branch and req not in (data.get("calibration") or {}):
                                issues.append(
                                    issue("MISSING_FIELD", pointer=f"{p}/{req}", message="calibrated probability requires metadata")
                                )
        if "relative_weight" in branch:
            w = refuse_string_number(branch.get("relative_weight"), f"{p}/relative_weight", issues)
            if w is not None:
                if w <= 0:
                    issues.append(issue("RANGE", pointer=f"{p}/relative_weight", message="must be positive", actual=w))
                total_weight += w
        # legacy 1.2 branch cap check only if using probability sum mode without calibration metadata
        if calibrated and "probability" in branch:
            val = branch.get("probability")
            if is_number(val) and float(cast(int | float, val)) > 0.60:
                # no longer hard cap — removed in 2.0; only warn
                pass
    non_stress_prob = [branch for branch in branches if isinstance(branch, dict) and branch.get("stress") is not True and branch.get("probability") is not None]
    if calibrated and non_stress_prob and not math.isclose(total_prob, 1.0, abs_tol=0.01):
        issues.append(issue("PROBABILITY_SUM", pointer="branch_ledger.branches", expected=1.0, actual=total_prob))
    if likelihood == "relative_weight" and any(isinstance(branch, dict) and branch.get("probability") is not None for branch in branches):
        issues.append(issue("PROBABILITY_UNCALIBRATED", pointer="branch_ledger.branches", message="relative-weight ledger cannot contain probabilities"))
    metrics = {"branches": len(branches), "near_duplicates": sum(1 for i in issues if i.code == "BRANCH_NEAR_DUPLICATE")}
    return _check("branches", issues, metrics)


def validate_report(text: str, manifest: dict[str, Any], required: bool) -> CheckResult:
    issues: list[Issue] = []
    if not text.strip():
        issues.append(issue("REPORT_EMPTY", artifact="REPORT.md", message="report empty"))
        return _check("report", issues)
    # Parse heading tree
    heading_matches = list(re.finditer(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", text, re.M))
    headings = [match.group(2).strip().lower() for match in heading_matches]
    expected = list(REPORT_SECTIONS)
    if _mapping(manifest.get("temporal_frame")).get("future_projection") is True:
        expected.append("future monitoring and probability updates")
    for term in expected:
        match_index = next((idx for idx, heading in enumerate(headings) if term == heading or term in heading), None)
        if match_index is None:
            if required:
                issues.append(issue("REPORT_SECTION", artifact="REPORT.md", message=f"missing section {term!r}"))
            continue
        current = heading_matches[match_index]
        level = len(current.group(1))
        next_start = len(text)
        for candidate in heading_matches[match_index + 1 :]:
            if len(candidate.group(1)) <= level:
                next_start = candidate.start()
                break
        body_stripped = text[current.end() : next_start].strip()
        normalized = re.sub(r"[`*_#>\-\s]", "", body_stripped).lower()
        if not normalized or normalized in {"todo", "tbd", "...", "placeholder", "na", "none"}:
            issues.append(issue("REPORT_EMPTY", artifact="REPORT.md", message=f"empty section {term!r}"))
        elif len(re.findall(r"\w+", body_stripped)) < 3:
            issues.append(issue("REPORT_EMPTY", artifact="REPORT.md", message=f"placeholder section {term!r}"))

    appendix_idx = next((idx for idx, heading in enumerate(headings) if "source appendix" in heading), None)
    if appendix_idx is not None:
        appendix_match = heading_matches[appendix_idx]
        appendix_level = len(appendix_match.group(1))
        appendix_end = len(text)
        for candidate in heading_matches[appendix_idx + 1 :]:
            if len(candidate.group(1)) <= appendix_level:
                appendix_end = candidate.start()
                break
        appendix = text[appendix_match.end() : appendix_end]
        elsewhere = text[: appendix_match.start()] + text[appendix_end:]
        used_refs = set(re.findall(r"\bevidence:[A-Za-z0-9._:-]+", elsewhere))
        appendix_refs = set(re.findall(r"\bevidence:[A-Za-z0-9._:-]+", appendix))
        for missing in sorted(used_refs - appendix_refs):
            issues.append(issue("UNKNOWN_REF", artifact="REPORT.md", pointer="source appendix", message="reported evidence reference missing from appendix", actual=missing))
    return _check("report", issues)


def manifest_integrity_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable manifest input without self-referential receipt/index fields."""
    payload = dict(manifest)
    payload.pop("artifact_index", None)
    payload.pop("validation_receipt", None)
    payload.pop("quality_receipt", None)
    payload.pop("finalization", None)
    payload.pop("assurance_tier", None)
    return payload


def artifact_integrity_hash(path: Path, relative: str, manifest: dict[str, Any]) -> str:
    if relative.replace("\\", "/") == "simulation-manifest.json":
        return canonical_hash(manifest_integrity_payload(manifest))
    return sha256_file(path)


def _current_schema_digest() -> str:
    schema_dir = Path(__file__).resolve().parents[2] / "schemas"
    hashes: dict[str, str] = {}
    if schema_dir.is_dir():
        for path in sorted(schema_dir.rglob("*.json")):
            hashes[path.relative_to(schema_dir).as_posix()] = sha256_file(path)
    return canonical_hash(hashes)


def validate_stale(
    workspace: Path,
    manifest: dict[str, Any],
    *,
    require_receipts: bool = False,
) -> CheckResult:
    """Dereference receipts and verify every indexed artifact fail-closed."""
    issues: list[Issue] = []
    receipts: dict[str, dict[str, Any]] = {}
    receipt_paths: dict[str, Path] = {}
    committed = isinstance(manifest.get("finalization"), dict) and manifest["finalization"].get("status") == "committed"
    for key in ("validation_receipt", "quality_receipt"):
        reference = manifest.get(key)
        conventional = f"{key.replace('_', '-')}.json"
        relative = reference.get("path") if isinstance(reference, dict) else conventional
        if not nonempty_str(relative):
            issues.append(issue("MISSING_FIELD", pointer=f"manifest.{key}.path", message="receipt path required"))
            continue
        candidate, path_issues = resolve_in_workspace(workspace, str(relative), must_exist=False, require_file=True)
        if path_issues:
            if require_receipts or committed or isinstance(reference, dict):
                issues.extend(path_issues)
            continue
        assert candidate is not None
        if not candidate.exists():
            if require_receipts or committed or isinstance(reference, dict):
                issues.append(issue("MISSING_ARTIFACT", artifact=str(relative), message="finalization receipt missing"))
            continue
        data, load_issues = load_json_secure(candidate)
        issues.extend(load_issues)
        if not isinstance(data, dict):
            if not load_issues:
                issues.append(issue("TYPE", artifact=str(relative), message="receipt must be object"))
            continue
        receipts[key] = data
        receipt_paths[key] = candidate
        if isinstance(reference, dict):
            expected_receipt_hash = reference.get("sha256")
            if not isinstance(expected_receipt_hash, str):
                issues.append(issue("MISSING_FIELD", pointer=f"manifest.{key}.sha256", message="receipt SHA-256 required"))
            else:
                actual_receipt_hash = sha256_file(candidate)
                if actual_receipt_hash != expected_receipt_hash:
                    issues.append(issue("STALE_ARTIFACT", artifact=str(relative), message="receipt hash mismatch", expected=expected_receipt_hash, actual=actual_receipt_hash))

    validation_receipt = receipts.get("validation_receipt")
    quality_receipt = receipts.get("quality_receipt")
    if validation_receipt is not None:
        reject_unknown_fields(
            validation_receipt,
            frozenset(
                {
                    "schema_version",
                    "validator_version",
                    "formula_version",
                    "schema_digest",
                    "bundle_digest",
                    "artifact_hashes",
                    "status",
                    "assurance_status",
                    "created_at",
                    "transaction_id",
                }
            ),
            "validation_receipt",
            issues,
        )
        for field in ("schema_digest", "validator_version", "bundle_digest", "artifact_hashes", "status"):
            if field not in validation_receipt:
                issues.append(issue("MISSING_FIELD", artifact="validation-receipt.json", pointer=field, message="required"))
        if validation_receipt.get("status") != "pass":
            issues.append(issue("VALIDATION_FAILED", artifact="validation-receipt.json", message="receipt status must be pass"))
        if validation_receipt.get("schema_version") != SCHEMA_VERSION:
            issues.append(issue("SCHEMA", artifact="validation-receipt.json", pointer="schema_version", expected=SCHEMA_VERSION, actual=validation_receipt.get("schema_version")))
        if validation_receipt.get("validator_version") != VALIDATOR_VERSION:
            issues.append(issue("STALE_ARTIFACT", artifact="validation-receipt.json", pointer="validator_version", message="receipt produced by a different validator", expected=VALIDATOR_VERSION, actual=validation_receipt.get("validator_version")))
        if validation_receipt.get("formula_version") != FORMULA_VERSION:
            issues.append(issue("STALE_ARTIFACT", artifact="validation-receipt.json", pointer="formula_version", message="formula version changed", expected=FORMULA_VERSION, actual=validation_receipt.get("formula_version")))
        expected_schema_digest = _current_schema_digest()
        if validation_receipt.get("schema_digest") != expected_schema_digest:
            issues.append(issue("STALE_ARTIFACT", artifact="validation-receipt.json", pointer="schema_digest", message="schema bundle changed", expected=expected_schema_digest, actual=validation_receipt.get("schema_digest")))
    if quality_receipt is not None and validation_receipt is not None:
        reject_unknown_fields(
            quality_receipt,
            frozenset(
                {
                    "schema_version",
                    "validation_receipt_hash",
                    "artifact_hashes",
                    "assurance_tier",
                    "diagnostic_score",
                    "status",
                    "created_at",
                    "transaction_id",
                }
            ),
            "quality_receipt",
            issues,
        )
        expected_validation_hash = quality_receipt.get("validation_receipt_hash")
        actual_validation_hash = sha256_file(receipt_paths["validation_receipt"])
        if expected_validation_hash != actual_validation_hash:
            issues.append(issue("STALE_ARTIFACT", artifact="quality-receipt.json", pointer="validation_receipt_hash", message="quality receipt does not bind validation receipt", expected=actual_validation_hash, actual=expected_validation_hash))
        if quality_receipt.get("transaction_id") != validation_receipt.get("transaction_id"):
            issues.append(issue("RECEIPT_CHAIN", artifact="quality-receipt.json", pointer="transaction_id", message="receipt transaction mismatch"))
        if manifest.get("assurance_tier") != quality_receipt.get("assurance_tier"):
            issues.append(issue("STALE_ARTIFACT", artifact="simulation-manifest.json", pointer="assurance_tier", message="manifest tier differs from quality receipt", expected=quality_receipt.get("assurance_tier"), actual=manifest.get("assurance_tier")))
        finalization = manifest.get("finalization")
        if isinstance(finalization, dict) and finalization.get("transaction_id") != validation_receipt.get("transaction_id"):
            issues.append(issue("RECEIPT_CHAIN", artifact="simulation-manifest.json", pointer="finalization.transaction_id", message="manifest finalization transaction mismatch"))

    artifact_hashes: dict[str, Any] = {}
    if validation_receipt is not None:
        raw_hashes = validation_receipt.get("artifact_hashes")
        if not isinstance(raw_hashes, dict) or not raw_hashes:
            issues.append(issue("MISSING_FIELD", artifact="validation-receipt.json", pointer="artifact_hashes", message="non-empty object required"))
        else:
            artifact_hashes = raw_hashes
            if validation_receipt.get("bundle_digest") != canonical_hash(raw_hashes):
                issues.append(issue("STALE_ARTIFACT", artifact="validation-receipt.json", pointer="bundle_digest", message="bundle digest mismatch"))
    if quality_receipt is not None:
        quality_hashes = quality_receipt.get("artifact_hashes")
        if quality_hashes != artifact_hashes:
            issues.append(issue("STALE_ARTIFACT", artifact="quality-receipt.json", pointer="artifact_hashes", message="quality receipt artifact set differs from validation receipt"))

    index = manifest.get("artifact_index")
    indexed: dict[str, str] = {}
    if index is not None and not isinstance(index, list):
        issues.append(issue("TYPE", pointer="manifest.artifact_index", message="must be array"))
    if isinstance(index, list):
        for idx, entry in enumerate(index):
            if not isinstance(entry, dict):
                issues.append(issue("TYPE", pointer=f"manifest.artifact_index[{idx}]", message="must be object"))
                continue
            rel = entry.get("path")
            expected = entry.get("sha256")
            if not nonempty_str(rel) or not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                issues.append(issue("SCHEMA", pointer=f"manifest.artifact_index[{idx}]", message="path and SHA-256 required"))
                continue
            if str(rel) in indexed:
                issues.append(issue("DUPLICATE_ID", pointer=f"manifest.artifact_index[{idx}].path", actual=rel))
            indexed[str(rel)] = expected
            resolved, path_issues = resolve_in_workspace(workspace, str(rel), must_exist=True, require_file=True)
            issues.extend(path_issues)
            if resolved is None:
                continue
            try:
                actual = artifact_integrity_hash(resolved, str(rel), manifest)
            except ResourceLimitError as exc:
                issues.append(exc.issue)
                continue
            if actual != expected:
                issues.append(issue("STALE_ARTIFACT", artifact=str(rel), message="artifact index hash mismatch", expected=expected, actual=actual))
            try:
                actual_size = resolved.stat().st_size
            except OSError as exc:
                issues.append(issue("INVALID_ARTIFACT", artifact=str(rel), message=str(exc)))
            else:
                if entry.get("size") != actual_size:
                    issues.append(issue("STALE_ARTIFACT", artifact=str(rel), pointer="size", message="artifact size mismatch", expected=entry.get("size"), actual=actual_size))

    if artifact_hashes and indexed != artifact_hashes:
        issues.append(issue("STALE_ARTIFACT", artifact="simulation-manifest.json", pointer="artifact_index", message="artifact index differs from signed receipt"))
    if committed or require_receipts:
        if not isinstance(index, list) or not index:
            issues.append(issue("MISSING_FIELD", pointer="manifest.artifact_index", message="committed finalization requires non-empty artifact index"))
        for key in ("validation_receipt", "quality_receipt"):
            if key not in receipts:
                issues.append(issue("MISSING_ARTIFACT", artifact=f"{key.replace('_', '-')}.json", message="committed finalization requires receipt"))
    return _check("stale", issues, {"receipts_verified": bool(receipts) and not [i for i in issues if i.severity == "error"]})


def validate_numerical_artifacts(workspace: Path, manifest: dict[str, Any]) -> CheckResult:
    """Validate declared immutable model/run/replay/sensitivity contracts."""
    issues: list[Issue] = []
    raw_paths = manifest.get("artifact_paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
    numerical_required = manifest.get("simulation_mode") in {"deterministic", "monte_carlo"}
    core_artifacts = ("computational_model", "run_ledger", "replay_report")
    declared_core = {key for key in core_artifacts if nonempty_str(paths.get(key))}
    if numerical_required or declared_core:
        for key in core_artifacts:
            if key not in declared_core:
                issues.append(
                    issue(
                        "MISSING_ARTIFACT",
                        pointer=f"manifest.artifact_paths.{key}",
                        message="numerical modes require model, run, and replay contracts",
                    )
                )

    def load_declared(key: str) -> dict[str, Any] | None:
        relative = paths.get(key)
        if relative is None:
            return None
        _, data, load_issues = load_workspace_artifact(workspace, str(relative), kind="json")
        issues.extend(load_issues)
        if not isinstance(data, dict):
            if not load_issues:
                issues.append(issue("TYPE", artifact=str(relative), message="top-level must be object"))
            return None
        return data

    model = load_declared("computational_model")
    run = load_declared("run_ledger")
    replay = load_declared("replay_report")
    sensitivity = load_declared("sensitivity_report")
    calibration_report = load_declared("calibration_report")
    model_digest: str | None = None
    independent_replay_passed = False
    independent_result_hash: str | None = None
    independent_result: dict[str, Any] | None = None
    independent_model: ComputationalModel | None = None
    independent_config: EngineConfig | None = None
    if model is not None:
        reject_unknown_fields(
            model,
            frozenset({"schema_version", "model_version", "model_hash", "variables", "edges", "interventions", "source_hashes", "source_set_hash"}),
            "computational_model",
            issues,
        )
        for field in ("schema_version", "model_version", "model_hash", "variables", "edges", "interventions", "source_hashes", "source_set_hash"):
            if field not in model:
                issues.append(issue("MISSING_FIELD", artifact="computational_model", pointer=field, message="required"))
        model_body = {key: model.get(key) for key in ("variables", "edges", "interventions")}
        model_digest = canonical_hash(model_body)
        if model.get("model_hash") != model_digest:
            issues.append(issue("REPLAY_MISMATCH", artifact="computational_model", pointer="model_hash", expected=model_digest, actual=model.get("model_hash")))
        source_hashes = model.get("source_hashes")
        if not isinstance(source_hashes, dict):
            issues.append(issue("TYPE", artifact="computational_model", pointer="source_hashes", message="must be object"))
        else:
            if model.get("source_set_hash") != canonical_hash(source_hashes):
                issues.append(issue("REPLAY_MISMATCH", artifact="computational_model", pointer="source_set_hash", message="source set digest mismatch"))
            for relative, expected in source_hashes.items():
                path, path_issues = resolve_in_workspace(workspace, str(relative), must_exist=True, require_file=True)
                issues.extend(path_issues)
                if path is None:
                    continue
                actual = artifact_integrity_hash(path, str(relative), manifest)
                if actual != expected:
                    issues.append(issue("STALE_ARTIFACT", artifact=str(relative), message="compiled model source changed", expected=expected, actual=actual))

    if run is not None:
        reject_unknown_fields(
            run,
            frozenset(
                {
                    "schema_version",
                    "run_contract_version",
                    "mode",
                    "ticks",
                    "model_hash",
                    "config",
                    "config_hash",
                    "result_hash",
                    "result",
                    "trace_contract",
                    "trace_execution_binding",
                    "contract_hash",
                }
            ),
            "run_ledger",
            issues,
        )
        for field in (
            "schema_version",
            "run_contract_version",
            "mode",
            "ticks",
            "model_hash",
            "config",
            "config_hash",
            "result_hash",
            "result",
            "trace_contract",
            "trace_execution_binding",
            "contract_hash",
        ):
            if field not in run:
                issues.append(issue("MISSING_FIELD", artifact="run_ledger", pointer=field, message="required"))
        if run.get("schema_version") != SCHEMA_VERSION:
            issues.append(
                issue(
                    "SCHEMA",
                    artifact="run_ledger",
                    pointer="schema_version",
                    expected=SCHEMA_VERSION,
                    actual=run.get("schema_version"),
                )
            )
        if run.get("run_contract_version") != "aleph-run-2.0":
            issues.append(issue("SCHEMA", artifact="run_ledger", pointer="run_contract_version", actual=run.get("run_contract_version")))
        if run.get("mode") not in {"deterministic", "monte_carlo"}:
            issues.append(issue("ENUM", artifact="run_ledger", pointer="mode", actual=run.get("mode")))
        if run.get("mode") != manifest.get("simulation_mode"):
            issues.append(
                issue(
                    "TRACK_MISMATCH",
                    artifact="run_ledger",
                    pointer="mode",
                    expected=manifest.get("simulation_mode"),
                    actual=run.get("mode"),
                    message="manifest and numerical run modes must match",
                )
            )
        ticks = run.get("ticks")
        if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
            issues.append(
                issue(
                    "TYPE",
                    artifact="run_ledger",
                    pointer="ticks",
                    message="must be a non-negative integer",
                )
            )
        for field in ("model_hash", "config_hash", "result_hash", "contract_hash"):
            digest = run.get(field)
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                issues.append(
                    issue(
                        "SCHEMA",
                        artifact="run_ledger",
                        pointer=field,
                        message="must be SHA-256",
                    )
                )
        contract_body = {key: value for key, value in run.items() if key != "contract_hash"}
        if run.get("contract_hash") != canonical_hash(contract_body):
            issues.append(issue("REPLAY_MISMATCH", artifact="run_ledger", pointer="contract_hash", message="run contract hash mismatch"))
        config = run.get("config")
        if not isinstance(config, dict) or run.get("config_hash") != canonical_hash(config if isinstance(config, dict) else {}):
            issues.append(issue("REPLAY_MISMATCH", artifact="run_ledger", pointer="config_hash", message="config hash mismatch"))
        if model_digest is not None and run.get("model_hash") != model_digest:
            issues.append(issue("REPLAY_MISMATCH", artifact="run_ledger", pointer="model_hash", message="run/model hash mismatch", expected=model_digest, actual=run.get("model_hash")))
        result = run.get("result")
        if not isinstance(result, dict):
            issues.append(issue("TYPE", artifact="run_ledger", pointer="result", message="must be object"))
        else:
            recorded_result_hash = result.get("run_hash")
            if run.get("mode") == "monte_carlo":
                summary = result.get("summary")
                recorded_result_hash = summary.get("canonical_hash") if isinstance(summary, dict) else None
            if run.get("result_hash") != recorded_result_hash:
                issues.append(issue("REPLAY_MISMATCH", artifact="run_ledger", pointer="result_hash", message="result hash does not bind result", expected=recorded_result_hash, actual=run.get("result_hash")))
        if model is not None and isinstance(config, dict) and isinstance(ticks, int) and not isinstance(ticks, bool):
            try:
                raw_variables = model.get("variables")
                raw_edges = model.get("edges")
                raw_interventions = model.get("interventions")
                if not isinstance(raw_variables, dict) or not isinstance(raw_edges, list) or not isinstance(raw_interventions, list):
                    raise ValueError("compiled model payload is incomplete")
                replay_model = ComputationalModel(
                    variables={
                        str(key): Variable(**value)
                        for key, value in raw_variables.items()
                        if isinstance(value, dict)
                    },
                    edges=[ModelEdge(**value) for value in raw_edges if isinstance(value, dict)],
                    interventions=[value for value in raw_interventions if isinstance(value, dict)],
                )
                replay_config = EngineConfig(**config)
                if replay_config.mode != run.get("mode"):
                    raise ValueError("run mode does not match hashed engine configuration")
                independent_model = replay_model
                independent_config = replay_config
                independent_result = (
                    run_monte_carlo(replay_model, replay_config, ticks=ticks)
                    if run.get("mode") == "monte_carlo"
                    else run_deterministic(replay_model, replay_config, ticks=ticks, run_id=0)
                )
                independent_result_hash = (
                    (independent_result.get("summary") or {}).get("canonical_hash")
                    if run.get("mode") == "monte_carlo"
                    else independent_result.get("run_hash")
                )
                independent_replay_passed = bool(
                    independent_result.get("ok")
                    and independent_result_hash == run.get("result_hash")
                    and semantic_result_payload(independent_result)
                    == semantic_result_payload(cast(dict[str, Any], result))
                )
                if not independent_replay_passed:
                    issues.append(
                        issue(
                            "REPLAY_MISMATCH",
                            artifact="run_ledger",
                            pointer="result",
                            expected=independent_result_hash,
                            actual=run.get("result_hash"),
                            message="validator-independent engine replay differs from the saved result",
                        )
                    )
            except (TypeError, ValueError) as exc:
                issues.append(
                    issue(
                        "REPLAY_MISMATCH",
                        artifact="run_ledger",
                        pointer="config",
                        message=f"independent engine replay failed: {exc}",
                    )
                )
        trace_rows_for_binding: list[dict[str, Any]] | None = None
        bound_trace_hash: str | None = None
        trace_contract = run.get("trace_contract")
        if not isinstance(trace_contract, dict):
            issues.append(
                issue(
                    "TYPE",
                    artifact="run_ledger",
                    pointer="trace_contract",
                    message="must be an object binding the propagation trace",
                )
            )
        else:
            reject_unknown_fields(
                trace_contract,
                frozenset({"path", "sha256", "row_count"}),
                "run_ledger.trace_contract",
                issues,
            )
            for field in ("path", "sha256", "row_count"):
                if field not in trace_contract:
                    issues.append(
                        issue(
                            "MISSING_FIELD",
                            artifact="run_ledger",
                            pointer=f"trace_contract.{field}",
                            message="required",
                        )
                    )
            trace_relative = trace_contract.get("path")
            declared_trace = paths.get("propagation_trace")
            if not nonempty_str(trace_relative):
                issues.append(
                    issue(
                        "TYPE",
                        artifact="run_ledger",
                        pointer="trace_contract.path",
                        message="must be a workspace-relative string",
                    )
                )
            elif declared_trace is not None and str(trace_relative).replace("\\", "/") != str(
                declared_trace
            ).replace("\\", "/"):
                issues.append(
                    issue(
                        "REPLAY_MISMATCH",
                        artifact="run_ledger",
                        pointer="trace_contract.path",
                        expected=declared_trace,
                        actual=trace_relative,
                        message="run contract must bind the declared propagation trace",
                    )
                )
            trace_hash = trace_contract.get("sha256")
            if not isinstance(trace_hash, str) or re.fullmatch(r"[0-9a-f]{64}", trace_hash) is None:
                issues.append(
                    issue(
                        "SCHEMA",
                        artifact="run_ledger",
                        pointer="trace_contract.sha256",
                        message="must be SHA-256",
                    )
                )
            else:
                bound_trace_hash = trace_hash
            row_count = trace_contract.get("row_count")
            if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count <= 0:
                issues.append(
                    issue(
                        "TYPE",
                        artifact="run_ledger",
                        pointer="trace_contract.row_count",
                        message="must be a positive integer",
                    )
                )
            if nonempty_str(trace_relative):
                trace_path, trace_path_issues = resolve_in_workspace(
                    workspace,
                    str(trace_relative),
                    must_exist=True,
                    require_file=True,
                )
                issues.extend(trace_path_issues)
                if trace_path is not None:
                    current_trace_hash = sha256_file(trace_path)
                    if trace_hash != current_trace_hash:
                        issues.append(
                            issue(
                                "REPLAY_MISMATCH",
                                artifact=str(trace_relative),
                                pointer="trace_contract.sha256",
                                expected=trace_hash,
                                actual=current_trace_hash,
                                message="propagation trace digest changed",
                            )
                        )
                    _, trace_rows, trace_load_issues = load_workspace_artifact(
                        workspace, str(trace_relative), kind="jsonl"
                    )
                    issues.extend(trace_load_issues)
                    if isinstance(trace_rows, list) and row_count != len(trace_rows):
                        issues.append(
                            issue(
                                "REPLAY_MISMATCH",
                                artifact=str(trace_relative),
                                pointer="trace_contract.row_count",
                                expected=row_count,
                                actual=len(trace_rows),
                                message="propagation trace row count changed",
                            )
                        )
                    if isinstance(trace_rows, list) and all(isinstance(row, dict) for row in trace_rows):
                        trace_rows_for_binding = cast(list[dict[str, Any]], trace_rows)

        execution_binding = run.get("trace_execution_binding")
        if not isinstance(execution_binding, dict):
            issues.append(
                issue(
                    "TYPE",
                    artifact="run_ledger",
                    pointer="trace_execution_binding",
                    message="must bind trace rows to independently replayed engine histories",
                )
            )
        else:
            reject_unknown_fields(
                execution_binding,
                frozenset({"version", "mode", "ticks", "rows", "binding_hash"}),
                "run_ledger.trace_execution_binding",
                issues,
            )
            for field in ("version", "mode", "ticks", "rows", "binding_hash"):
                if field not in execution_binding:
                    issues.append(
                        issue(
                            "MISSING_FIELD",
                            artifact="run_ledger",
                            pointer=f"trace_execution_binding.{field}",
                            message="required",
                        )
                    )
            binding_body = {
                key: value for key, value in execution_binding.items() if key != "binding_hash"
            }
            if execution_binding.get("binding_hash") != canonical_hash(binding_body):
                issues.append(
                    issue(
                        "REPLAY_MISMATCH",
                        artifact="run_ledger",
                        pointer="trace_execution_binding.binding_hash",
                        message="trace execution binding hash mismatch",
                    )
                )
            if (
                execution_binding.get("version") != "aleph-trace-execution-binding-v1"
                or execution_binding.get("mode") != run.get("mode")
                or execution_binding.get("ticks") != run.get("ticks")
            ):
                issues.append(
                    issue(
                        "REPLAY_MISMATCH",
                        artifact="run_ledger",
                        pointer="trace_execution_binding",
                        message="trace execution binding header differs from run contract",
                    )
                )
            if (
                independent_model is not None
                and independent_config is not None
                and independent_result is not None
                and trace_rows_for_binding is not None
                and isinstance(ticks, int)
                and not isinstance(ticks, bool)
            ):
                expected_binding, binding_issues = build_trace_execution_binding(
                    trace_rows_for_binding,
                    independent_model,
                    independent_config,
                    ticks=ticks,
                    result=independent_result,
                    manifest=manifest,
                )
                issues.extend(binding_issues)
                if expected_binding != execution_binding:
                    issues.append(
                        issue(
                            "REPLAY_MISMATCH",
                            artifact="run_ledger",
                            pointer="trace_execution_binding",
                            expected=expected_binding,
                            actual=execution_binding,
                            message="saved trace execution binding differs from independent engine replay",
                        )
                    )

        branch_relative = paths.get("branch_ledger", "branch-ledger.json")
        _, numerical_branch_ledger, branch_load_issues = load_workspace_artifact(
            workspace,
            str(branch_relative),
            kind="json",
        )
        issues.extend(branch_load_issues)
        if isinstance(numerical_branch_ledger, dict):
            raw_numerical_branches = numerical_branch_ledger.get("branches")
            if isinstance(raw_numerical_branches, list):
                numerical_branches = [
                    value for value in raw_numerical_branches if isinstance(value, dict)
                ]
                derivations = {value.get("derivation") for value in numerical_branches}
                if len(derivations) != 1:
                    issues.append(
                        issue(
                            "TRACK_MISMATCH",
                            artifact=str(branch_relative),
                            pointer="branches",
                            message="a numerical branch ledger cannot mix analyst-authored and engine-derived branches",
                            actual=sorted(str(value) for value in derivations),
                        )
                    )
                for index, branch in enumerate(numerical_branches):
                    if branch.get("trace_hash") != bound_trace_hash:
                        issues.append(
                            issue(
                                "REPLAY_MISMATCH",
                                artifact=str(branch_relative),
                                pointer=f"branches/{index}/trace_hash",
                                expected=bound_trace_hash,
                                actual=branch.get("trace_hash"),
                                message="branch must bind the run contract propagation trace",
                            )
                        )
                if derivations == {"analyst_authored"}:
                    for index, branch in enumerate(numerical_branches):
                        for field in ("representative_run", "engine_cluster_id", "member_count"):
                            if branch.get(field) is not None:
                                issues.append(
                                    issue(
                                        "TRACK_MISMATCH",
                                        artifact=str(branch_relative),
                                        pointer=f"branches/{index}/{field}",
                                        message="analyst-authored branches cannot claim engine-derived metadata",
                                    )
                                )
                elif derivations == {"engine_derived"}:
                    if run.get("mode") == "deterministic":
                        if len(numerical_branches) != 1:
                            issues.append(
                                issue(
                                    "BRANCH_COUNT",
                                    artifact=str(branch_relative),
                                    message="a deterministic engine run yields exactly one engine-derived branch",
                                    actual=len(numerical_branches),
                                )
                            )
                        for index, branch in enumerate(numerical_branches):
                            if branch.get("representative_run") != "run:0" or branch.get("relative_weight") != 1.0:
                                issues.append(
                                    issue(
                                        "REPLAY_MISMATCH",
                                        artifact=str(branch_relative),
                                        pointer=f"branches/{index}",
                                        message="deterministic engine branch must bind run:0 with weight 1.0",
                                    )
                                )
                    else:
                        summary = result.get("summary") if isinstance(result, dict) else None
                        raw_clusters = summary.get("branches") if isinstance(summary, dict) else None
                        clusters = {
                            str(value.get("id")): value
                            for value in raw_clusters
                            if isinstance(value, dict) and nonempty_str(value.get("id"))
                        } if isinstance(raw_clusters, list) else {}
                        declared_clusters = {
                            str(value.get("engine_cluster_id")) for value in numerical_branches
                        }
                        if declared_clusters != set(clusters):
                            issues.append(
                                issue(
                                    "REPLAY_MISMATCH",
                                    artifact=str(branch_relative),
                                    pointer="branches",
                                    expected=sorted(clusters),
                                    actual=sorted(declared_clusters),
                                    message="engine-derived branches must cover every Monte Carlo cluster exactly once",
                                )
                            )
                        for index, branch in enumerate(numerical_branches):
                            cluster = clusters.get(str(branch.get("engine_cluster_id")))
                            if cluster is None:
                                continue
                            expected_fields = {
                                "representative_run": cluster.get("representative_run"),
                                "member_count": cluster.get("member_count"),
                                "relative_weight": cluster.get("relative_weight"),
                            }
                            for field, expected in expected_fields.items():
                                branch_actual = branch.get(field)
                                numeric_match = (
                                    field == "relative_weight"
                                    and isinstance(branch_actual, (int, float))
                                    and not isinstance(branch_actual, bool)
                                    and isinstance(expected, (int, float))
                                    and math.isclose(float(branch_actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
                                )
                                if branch_actual != expected and not numeric_match:
                                    issues.append(
                                        issue(
                                            "REPLAY_MISMATCH",
                                            artifact=str(branch_relative),
                                            pointer=f"branches/{index}/{field}",
                                            expected=expected,
                                            actual=branch_actual,
                                            message="branch metadata differs from Monte Carlo cluster",
                                        )
                                    )

    if replay is not None:
        replay_fields = frozenset(
            {
                "schema_version",
                "formula_version",
                "recorded_contract_hash",
                "contract_hash_ok",
                "recorded_model_hash",
                "current_model_hash",
                "model_hash_ok",
                "config_hash_ok",
                "recorded_result_hash",
                "replay_result_hash",
                "result_hash_ok",
                "saved_result_ok",
                "trace_rows",
                "trace_contract_ok",
                "trace_execution_binding_ok",
                "recorded_trace_hash",
                "current_trace_hash",
                "trace_ok",
                "issues",
                "match",
                "report_hash",
            }
        )
        reject_unknown_fields(replay, replay_fields, "replay_report", issues)
        for field in replay_fields:
            if field not in replay:
                issues.append(issue("MISSING_FIELD", artifact="replay_report", pointer=field, message="required"))
        replay_body = {key: value for key, value in replay.items() if key != "report_hash"}
        if replay.get("report_hash") != canonical_hash(replay_body):
            issues.append(issue("REPLAY_MISMATCH", artifact="replay_report", pointer="report_hash", message="replay report hash mismatch"))
        if replay.get("schema_version") != SCHEMA_VERSION or replay.get("formula_version") != FORMULA_VERSION:
            issues.append(issue("SCHEMA", artifact="replay_report", message="replay schema/formula version mismatch"))
        required_flags = (
            "contract_hash_ok",
            "model_hash_ok",
            "config_hash_ok",
            "result_hash_ok",
            "saved_result_ok",
            "trace_contract_ok",
            "trace_execution_binding_ok",
            "trace_ok",
            "match",
        )
        for field in required_flags:
            if replay.get(field) is not True:
                issues.append(issue("REPLAY_MISMATCH", artifact="replay_report", pointer=field, message="saved replay must match", actual=replay.get(field)))
        if run is not None and replay.get("recorded_contract_hash") != run.get("contract_hash"):
            issues.append(issue("REPLAY_MISMATCH", artifact="replay_report", pointer="recorded_contract_hash", message="replay report references different run"))
        if run is not None:
            expected_trace: dict[str, Any] = (
                cast(dict[str, Any], run.get("trace_contract"))
                if isinstance(run.get("trace_contract"), dict)
                else {}
            )
            replay_bindings = {
                "recorded_model_hash": run.get("model_hash"),
                "current_model_hash": model_digest,
                "recorded_result_hash": run.get("result_hash"),
                "replay_result_hash": independent_result_hash,
                "trace_rows": expected_trace.get("row_count"),
                "recorded_trace_hash": expected_trace.get("sha256"),
                "current_trace_hash": expected_trace.get("sha256"),
            }
            for field, expected in replay_bindings.items():
                if replay.get(field) != expected:
                    issues.append(
                        issue(
                            "REPLAY_MISMATCH",
                            artifact="replay_report",
                            pointer=field,
                            expected=expected,
                            actual=replay.get(field),
                            message="replay report binding differs from independent validation",
                        )
                    )
        if not independent_replay_passed:
            issues.append(issue("REPLAY_MISMATCH", artifact="replay_report", message="independent replay did not pass"))
        if replay.get("issues") != []:
            issues.append(issue("REPLAY_MISMATCH", artifact="replay_report", pointer="issues", message="successful replay must have no issues"))

    if calibration_report is not None:
        required = {
            "schema_version",
            "status",
            "policy_locked",
            "model_hash",
            "config_hash",
            "policy_hash",
            "hindcast_digest",
            "outcome_digest",
            "case_count",
            "unique_case_count",
            "metrics",
            "beats_baseline",
            "report_hash",
        }
        for field in required:
            if field not in calibration_report:
                issues.append(issue("MISSING_FIELD", artifact="calibration_report", pointer=field, message="required"))
        calibration_body = {key: value for key, value in calibration_report.items() if key != "report_hash"}
        if calibration_report.get("report_hash") != canonical_hash(calibration_body):
            issues.append(issue("STALE_ARTIFACT", artifact="calibration_report", pointer="report_hash", message="calibration report hash mismatch"))
        for field in ("model_hash", "config_hash", "policy_hash", "hindcast_digest", "outcome_digest"):
            value = calibration_report.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                issues.append(issue("SCHEMA", artifact="calibration_report", pointer=field, message="SHA-256 required"))
        case_count = calibration_report.get("case_count")
        unique_count = calibration_report.get("unique_case_count")
        if not isinstance(case_count, int) or isinstance(case_count, bool) or case_count < 30:
            issues.append(issue("PACK_MATURITY", artifact="calibration_report", pointer="case_count", message="at least 30 cases required"))
        if unique_count != case_count:
            issues.append(issue("PACK_MATURITY", artifact="calibration_report", pointer="unique_case_count", message="all calibration cases must be unique"))
        if calibration_report.get("status") != "pass" or calibration_report.get("policy_locked") is not True or calibration_report.get("beats_baseline") is not True:
            issues.append(issue("PACK_MATURITY", artifact="calibration_report", message="calibration gates did not pass"))
        if model_digest is not None and calibration_report.get("model_hash") != model_digest:
            issues.append(issue("REPLAY_MISMATCH", artifact="calibration_report", pointer="model_hash", message="calibration/model hash mismatch"))

    if sensitivity is not None:
        declared_hash = sensitivity.get("report_hash")
        body = {key: value for key, value in sensitivity.items() if key != "report_hash"}
        if declared_hash != canonical_hash(body):
            issues.append(issue("REPLAY_MISMATCH", artifact="sensitivity_report", pointer="report_hash", message="sensitivity report hash mismatch"))
        if model_digest is not None and sensitivity.get("model_hash") != model_digest:
            issues.append(issue("REPLAY_MISMATCH", artifact="sensitivity_report", pointer="model_hash", message="sensitivity/model hash mismatch"))
    return _check(
        "numerical_artifacts",
        issues,
        {
            "model_present": model is not None,
            "run_present": run is not None,
            "replay_present": replay is not None,
            "sensitivity_present": sensitivity is not None,
            "numerical_required": numerical_required,
            "independent_replay_passed": independent_replay_passed,
            "calibration_present": calibration_report is not None,
        },
    )


def validate_workspace(
    workspace: Path,
    mode: str = "final",
    require_report: bool = False,
    *,
    verify_integrity: bool = True,
    require_receipts: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    all_issues: list[Issue] = []
    checks: dict[str, Any] = {}

    if not workspace.is_dir():
        return _fail_result(mode, [issue("MISSING_ARTIFACT", artifact=str(workspace), message="workspace directory missing")], checks, "workspace directory missing")
    workspace_bytes, budget_issues = validate_workspace_budget(workspace)
    checks["resources"] = _check("resources", budget_issues, {"workspace_bytes": workspace_bytes}).to_dict()
    all_issues.extend(budget_issues)
    if any(problem.severity == "error" for problem in budget_issues):
        return _fail_result(mode, all_issues, checks, "workspace resource limit failed")

    man_path, manifest, man_issues = load_workspace_artifact(workspace, "simulation-manifest.json", kind="json")
    all_issues.extend(man_issues)
    if not isinstance(manifest, dict):
        return _fail_result(mode, all_issues, checks, "missing or invalid manifest")

    c_paths = validate_paths(manifest, workspace)
    checks["paths"] = c_paths.to_dict()
    all_issues.extend(c_paths.issues)

    c_man = validate_manifest_core(manifest, mode)
    checks["manifest"] = c_man.to_dict()
    all_issues.extend(c_man.issues)

    raw_artifact_paths = manifest.get("artifact_paths")
    artifact_paths: dict[str, Any] = raw_artifact_paths if isinstance(raw_artifact_paths, dict) else {}

    def rel(key: str, default: str) -> str:
        return str(artifact_paths.get(key, default))

    # evidence
    ev_path, ev_rows, ev_iss = load_workspace_artifact(workspace, rel("evidence_map", "evidence-map.csv"), kind="csv")
    all_issues.extend(ev_iss)
    c_ev, evidence_ids = validate_evidence(ev_rows or [], manifest, mode)
    checks["evidence"] = c_ev.to_dict()
    all_issues.extend(c_ev.issues)

    _, nodes_raw, n_iss = load_workspace_artifact(workspace, rel("nodes", "nodes.json"), kind="json")
    all_issues.extend(n_iss)
    if not isinstance(nodes_raw, list):
        all_issues.append(issue("TYPE", artifact=rel("nodes", "nodes.json"), message="top-level must be array"))
        nodes: list[Any] = []
    else:
        nodes = nodes_raw
    c_nodes, node_ids = validate_nodes(nodes, evidence_ids, manifest)
    checks["nodes"] = c_nodes.to_dict()
    all_issues.extend(c_nodes.issues)

    _, edges_raw, e_iss = load_workspace_artifact(workspace, rel("edges", "edges.json"), kind="json")
    all_issues.extend(e_iss)
    if not isinstance(edges_raw, list):
        all_issues.append(issue("TYPE", artifact=rel("edges", "edges.json"), message="top-level must be array"))
        edges: list[Any] = []
    else:
        edges = edges_raw
    node_types = {str(node.get("id")): node.get("type") for node in nodes if isinstance(node, dict)}
    nodes_by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict) and nonempty_str(node.get("id"))}
    c_edges, edge_by_id = validate_edges(edges, node_ids, evidence_ids, node_types)
    checks["edges"] = c_edges.to_dict()
    checks["graph"] = c_edges.to_dict()  # alias
    all_issues.extend(c_edges.issues)

    _, actors_raw, a_iss = load_workspace_artifact(workspace, rel("actors", "actors.json"), kind="json")
    all_issues.extend(a_iss)
    if not isinstance(actors_raw, list):
        all_issues.append(issue("TYPE", artifact=rel("actors", "actors.json"), message="top-level must be array"))
        actors: list[Any] = []
    else:
        actors = actors_raw
    c_act, actor_ids = validate_actors(actors, node_ids, evidence_ids, nodes)
    checks["actors"] = c_act.to_dict()
    checks["human_tracks"] = c_act.to_dict()
    all_issues.extend(c_act.issues)

    _, branches_raw, b_iss = load_workspace_artifact(workspace, rel("branch_ledger", "branch-ledger.json"), kind="json")
    all_issues.extend(b_iss)
    c_br = validate_branches(branches_raw, set(edge_by_id), actor_ids, evidence_ids, manifest, node_ids)
    checks["branches"] = c_br.to_dict()
    all_issues.extend(c_br.issues)

    _, trace_rows, t_iss = load_workspace_artifact(workspace, rel("propagation_trace", "propagation-trace.jsonl"), kind="jsonl")
    all_issues.extend(t_iss)
    c_tr = validate_trace(trace_rows or [], node_ids, edge_by_id, evidence_ids, manifest, nodes_by_id)
    checks["trace"] = c_tr.to_dict()
    all_issues.extend(c_tr.issues)

    c_numerical = validate_numerical_artifacts(workspace, manifest)
    checks["numerical_artifacts"] = c_numerical.to_dict()
    all_issues.extend(c_numerical.issues)

    # Material actor audit is an artifact, not a prose claim. Keep the import
    # lazy so packet validation can evolve independently without a cycle.
    _, human_rows, h_iss = load_workspace_artifact(
        workspace,
        rel("human_track_ledger", "human-track-ledger.jsonl"),
        kind="jsonl",
    )
    all_issues.extend(h_iss)
    protocol_issues: list[Issue] = []
    if isinstance(human_rows, list):
        for row_idx, row in enumerate(human_rows):
            if isinstance(row, dict):
                reject_unknown_fields(row, HUMAN_TRACK_LEDGER_FIELDS, f"human-track-ledger/{row_idx}", protocol_issues)
    try:
        from .packets import validate_actor_protocol

        protocol_issues.extend(
            validate_actor_protocol(
                actors,
                human_rows if isinstance(human_rows, list) else [],
                branches=branches_raw.get("branches", []) if isinstance(branches_raw, dict) else [],
                manifest=manifest,
                workspace=workspace,
            )
        )
    except (ImportError, AttributeError) as exc:
        protocol_issues.append(issue("VALIDATION_FAILED", artifact="human-track-ledger.jsonl", message=f"actor protocol validator unavailable: {exc}"))
    except Exception as exc:  # malformed packets must become a structured blocker
        protocol_issues.append(issue("VALIDATION_FAILED", artifact="human-track-ledger.jsonl", message=f"actor protocol validation failed closed: {type(exc).__name__}: {exc}"))
    material_actors = [actor for actor in actors if isinstance(actor, dict) and actor.get("materiality") == "material"]
    if material_actors and not human_rows:
        protocol_issues.append(issue("TRACK_LEDGER", artifact="human-track-ledger.jsonl", message="material actors require non-empty track ledger"))
    c_protocol = _check("actor_protocol", protocol_issues, {"material_actors": len(material_actors), "ledger_rows": len(human_rows or []) if isinstance(human_rows, list) else 0})
    checks["actor_protocol"] = c_protocol.to_dict()
    all_issues.extend(protocol_issues)

    # temporal alias
    temporal_issues = [i for i in all_issues if i.code in {"FUTURE_FACT", "TRACE_TIME", "TIMELINE_MODE", "TEMPORAL_FRAME"}]
    checks["temporal"] = _check("temporal", temporal_issues).to_dict()

    report_rel = rel("final_report", "REPORT.md")
    report_path, report_text, r_iss = load_workspace_artifact(workspace, report_rel, kind="text")
    if require_report or mode == "final":
        all_issues.extend(r_iss)
        if isinstance(report_text, str):
            c_rep = validate_report(report_text, manifest, required=True)
        else:
            c_rep = _check("report", [issue("MISSING_ARTIFACT", artifact=report_rel, message="report missing")])
        checks["report"] = c_rep.to_dict()
        all_issues.extend(c_rep.issues)

    if verify_integrity:
        c_stale = validate_stale(workspace, manifest, require_receipts=require_receipts)
        checks["stale"] = c_stale.to_dict()
        all_issues.extend(c_stale.issues)

    errors = [i for i in all_issues if i.severity == "error"]
    warnings = [i for i in all_issues if i.severity == "warning"]
    status = "fail" if errors else "pass"
    # Assurance cannot be verified if fail
    assurance = "failed" if status == "fail" else "experimental"

    metrics: dict[str, Any] = {}
    for key in ("evidence", "nodes", "edges", "actors", "actor_protocol", "branches", "trace", "numerical_artifacts", "stale"):
        if key in checks and isinstance(checks[key], dict):
            metrics.update(checks[key].get("metrics") or {})

    # Artifact digests for receipt
    digests: dict[str, str] = {}
    artifact_defaults: tuple[tuple[str | None, str], ...] = (
        (None, "simulation-manifest.json"),
        ("nodes", "nodes.json"),
        ("edges", "edges.json"),
        ("actors", "actors.json"),
        ("human_track_ledger", "human-track-ledger.jsonl"),
        ("evidence_map", "evidence-map.csv"),
        ("branch_ledger", "branch-ledger.json"),
        ("propagation_trace", "propagation-trace.jsonl"),
        ("final_report", "REPORT.md"),
    )
    for artifact_key, default in artifact_defaults:
        name = default if artifact_key is None else rel(artifact_key, default)
        p, path_issues = resolve_in_workspace(workspace, name, must_exist=False, require_file=True)
        if path_issues or p is None or not p.is_file():
            continue
        try:
            digests[name.replace("\\", "/")] = artifact_integrity_hash(p, name, manifest)
        except ResourceLimitError as exc:
            all_issues.append(exc.issue)

    result = {
        "schema_version": SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "validated_at": _utc(),
        "mode": mode,
        "status": status,
        "assurance_status": assurance,
        "checks": {k: (v.get("status") if isinstance(v, dict) else v) for k, v in checks.items()},
        "check_results": checks,
        "metrics": metrics,
        "errors": [i.legacy_string() for i in errors],
        "warnings": [i.legacy_string() for i in warnings],
        "issues": [i.to_dict() for i in all_issues],
        "error_codes": sorted({i.code for i in errors}),
        "artifact_digests": digests,
        "bundle_digest": canonical_hash(digests),
        "formula_version": FORMULA_VERSION,
    }
    return result


def _utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fail_result(mode: str, issues: list[Issue], checks: dict, msg: str) -> dict[str, Any]:
    if not any(i.message == msg for i in issues):
        issues.append(issue("MISSING_ARTIFACT", message=msg))
    errors = [i for i in issues if i.severity == "error"]
    return {
        "schema_version": SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "validated_at": _utc(),
        "mode": mode,
        "status": "fail",
        "assurance_status": "failed",
        "checks": checks,
        "check_results": checks,
        "metrics": {},
        "errors": [i.legacy_string() for i in errors],
        "warnings": [],
        "issues": [i.to_dict() for i in issues],
        "error_codes": sorted({i.code for i in errors}),
    }
