from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from _lib import load_csv_rows, load_json, load_optional_yaml, skill_root, utc_now, write_json


SCHEMA_VERSION = "1.1.0"
NODE_REQUIRED = {
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
    "probability",
    "confidence",
    "alternative_explanations",
    "sensitivity",
}
EDGE_REQUIRED = {
    "id",
    "from",
    "to",
    "relation",
    "sign",
    "base_strength",
    "confidence",
    "mechanism",
    "lag_distribution",
    "context_modifiers",
    "evidence",
    "status",
}
ACTOR_REQUIRED = {
    "id",
    "person_node",
    "public_role",
    "scope_note",
    "materiality",
    "evidence_ids",
    "research_track",
    "roleplay_track",
    "adjudication",
    "decision_patterns",
    "predicted_responses",
}
BRANCH_REQUIRED = {
    "id",
    "name",
    "probability",
    "summary",
    "causal_trace",
    "key_decision_points",
    "end_state",
    "evidence_ids",
    "confidence",
    "warnings",
}
EVIDENCE_REQUIRED = {
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
CHECKPOINTS = {
    "initialized",
    "baseline_researched",
    "human_tracks_completed",
    "graph_built",
    "propagated",
    "branched",
    "validated",
}
NODE_TYPES = {"entity", "event", "factor", "context", "indicator", "claim", "source"}
STATUSES = {"fact", "inference", "simulation", "counterfactual", "proposed"}
SOURCE_TIERS = {"primary", "authoritative-secondary", "secondary", "tertiary", "user-provided"}
RETRIEVAL_STATUSES = {"opened", "downloaded", "api", "local-file", "user-provided", "search-snippet", "blocked"}
CONTRADICTION_STATUSES = {"corroborated", "contested", "contradicted", "no-conflict-found", "not-applicable", "unchecked"}
DIRECT_RETRIEVAL = {"opened", "downloaded", "api", "local-file", "user-provided"}
HIGH_QUALITY_TIERS = {"primary", "authoritative-secondary", "user-provided"}
PROFILE_BUDGETS = {
    "basic": {"min_sources": 1, "max_sources": 6, "max_repair_loops": 2},
    "quick": {"min_sources": 4, "max_sources": 8, "max_repair_loops": 1},
    "standard": {"min_sources": 6, "max_sources": 12, "max_repair_loops": 2},
    "deep": {"min_sources": 12, "max_sources": 25, "max_repair_loops": 3},
}


def issue(code: str, path: str, message: str) -> str:
    return f"[{code}] {path}: {message}"


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def probability(value: Any, path: str, errors: list[str]) -> float:
    if not is_number(value):
        errors.append(issue("TYPE", path, "must be a finite number"))
        return 0.0
    result = float(value)
    if not 0.0 <= result <= 1.0:
        errors.append(issue("RANGE", path, "must be within [0, 1]"))
    return result


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_structured(path: Path) -> Any:
    if path.suffix.lower() == ".json":
        return load_json(path)
    if path.suffix.lower() in {".yaml", ".yml"}:
        return load_optional_yaml(path)
    raise ValueError(f"Unsupported structured file type: {path}")


def load_artifact(
    workspace: Path,
    artifact_paths: dict[str, Any],
    key: str,
    default: str,
    errors: list[str],
) -> tuple[Path, Any | None]:
    path = workspace / str(artifact_paths.get(key, default))
    if not path.exists():
        errors.append(issue("MISSING_ARTIFACT", key, f"file does not exist: {path}"))
        return path, None
    try:
        return path, load_structured(path)
    except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
        errors.append(issue("INVALID_ARTIFACT", key, str(exc)))
        return path, None


def validate_references(refs: Any, known: set[str], path: str, errors: list[str]) -> list[str]:
    values = ensure_list(refs)
    for index, value in enumerate(values):
        if not nonempty(value):
            errors.append(issue("EMPTY_REF", f"{path}[{index}]", "reference must be a non-empty string"))
        elif value not in known:
            errors.append(issue("UNKNOWN_REF", f"{path}[{index}]", f"unknown reference {value!r}"))
    return [str(value) for value in values if nonempty(value)]


def validate_manifest(manifest: dict[str, Any], mode: str, errors: list[str], warnings: list[str]) -> None:
    for field in ["schema_version", "simulation_id", "created_at", "status", "change_point", "scope", "execution", "artifact_paths"]:
        if field not in manifest:
            errors.append(issue("MISSING_FIELD", f"manifest.{field}", "required field is missing"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(issue("SCHEMA", "manifest.schema_version", f"expected {SCHEMA_VERSION}"))
    if mode == "final" and manifest.get("status") not in {"complete", "completed"}:
        errors.append(issue("INCOMPLETE", "manifest.status", "final validation requires complete or completed"))

    change = manifest.get("change_point", {})
    for field in ["type", "target", "description", "time", "location", "assumption_ref"]:
        if not nonempty(change.get(field)):
            errors.append(issue("MISSING_FIELD", f"manifest.change_point.{field}", "required value is missing"))

    execution = manifest.get("execution", {})
    if execution.get("profile") not in {"basic", "quick", "standard", "deep"}:
        errors.append(issue("ENUM", "manifest.execution.profile", "use basic, quick, standard, or deep"))
    quality = execution.get("research_quality")
    if quality not in {"basic", "standard", "deep"}:
        errors.append(issue("ENUM", "manifest.execution.research_quality", "use basic, standard, or deep"))
    d_research = execution.get("d_research", {})
    if d_research.get("status") not in {"available", "unavailable", "unknown"}:
        errors.append(issue("ENUM", "manifest.execution.d_research.status", "use available, unavailable, or unknown"))
    if mode == "final" and d_research.get("status") == "unknown":
        errors.append(issue("D_RESEARCH", "manifest.execution.d_research.status", "must be detected before final validation"))
    if mode == "final" and d_research.get("status") == "available" and d_research.get("invoked") is not True:
        errors.append(issue("D_RESEARCH", "manifest.execution.d_research.invoked", "must be true when D Research is available"))

    subagents = execution.get("subagents", {})
    if subagents.get("status") not in {"available", "unavailable", "unknown"}:
        errors.append(issue("ENUM", "manifest.execution.subagents.status", "use available, unavailable, or unknown"))
    if subagents.get("status") == "unavailable" and not nonempty(subagents.get("fallback_reason")):
        errors.append(issue("SUBAGENT_FALLBACK", "manifest.execution.subagents.fallback_reason", "explain why isolated passes were used"))
    if subagents.get("status") == "unknown":
        target = errors if mode == "final" else warnings
        target.append(issue("SUBAGENT_UNKNOWN", "manifest.execution.subagents.status", "capability was not established"))

    budget = execution.get("research_budget", {})
    for field in ["min_sources", "max_sources", "max_repair_loops"]:
        if not isinstance(budget.get(field), int) or budget.get(field, 0) < 1:
            errors.append(issue("BUDGET", f"manifest.execution.research_budget.{field}", "must be a positive integer"))
    if isinstance(budget.get("min_sources"), int) and isinstance(budget.get("max_sources"), int):
        if budget["min_sources"] > budget["max_sources"]:
            errors.append(issue("BUDGET", "manifest.execution.research_budget", "min_sources cannot exceed max_sources"))
    expected_budget = PROFILE_BUDGETS.get(execution.get("profile"))
    if expected_budget and budget != expected_budget:
        errors.append(issue("PROFILE_BUDGET", "manifest.execution.research_budget", f"profile {execution.get('profile')!r} requires {expected_budget}; do not expand it to fit collected evidence"))
    repair_loops = execution.get("repair_loops_used")
    if not isinstance(repair_loops, int) or repair_loops < 0:
        errors.append(issue("BUDGET", "manifest.execution.repair_loops_used", "must be a non-negative integer"))
    elif isinstance(budget.get("max_repair_loops"), int) and repair_loops > budget["max_repair_loops"]:
        warnings.append(issue("REPAIR_BUDGET", "manifest.execution.repair_loops_used", "exceeds the planned repair-loop budget"))

    checkpoints = execution.get("checkpoints", {})
    missing = CHECKPOINTS - set(checkpoints)
    if missing:
        errors.append(issue("CHECKPOINTS", "manifest.execution.checkpoints", f"missing {sorted(missing)}"))
    if mode == "final":
        for checkpoint in CHECKPOINTS:
            if checkpoints.get(checkpoint) is not True:
                errors.append(issue("CHECKPOINT", f"manifest.execution.checkpoints.{checkpoint}", "must be true for final validation"))


def validate_evidence(
    rows: list[dict[str, str]],
    manifest: dict[str, Any],
    mode: str,
    errors: list[str],
    warnings: list[str],
) -> tuple[set[str], dict[str, Any]]:
    if not rows:
        errors.append(issue("EVIDENCE_EMPTY", "evidence_map", "must contain at least one row"))
        return set(), {"evidence_rows": 0, "direct_sources": 0, "high_quality_direct_sources": 0}
    missing_columns = EVIDENCE_REQUIRED - set(rows[0])
    if missing_columns:
        errors.append(issue("EVIDENCE_COLUMNS", "evidence_map", f"missing columns {sorted(missing_columns)}"))

    ids: set[str] = set()
    direct_count = 0
    high_quality_direct = 0
    quality = manifest.get("execution", {}).get("research_quality", "basic")
    for index, row in enumerate(rows):
        path = f"evidence[{index}]"
        evidence_id = row.get("evidence_id", "").strip()
        if not evidence_id:
            errors.append(issue("EMPTY_ID", f"{path}.evidence_id", "must not be empty"))
        elif evidence_id in ids:
            errors.append(issue("DUPLICATE_ID", f"{path}.evidence_id", evidence_id))
        ids.add(evidence_id)
        for field in EVIDENCE_REQUIRED - {"notes"}:
            if not row.get(field, "").strip():
                errors.append(issue("MISSING_FIELD", f"{path}.{field}", "must not be empty"))

        tier = row.get("source_tier", "")
        retrieval = row.get("retrieval_status", "")
        contradiction = row.get("contradiction_status", "")
        if tier not in SOURCE_TIERS:
            errors.append(issue("ENUM", f"{path}.source_tier", f"use one of {sorted(SOURCE_TIERS)}"))
        if retrieval not in RETRIEVAL_STATUSES:
            errors.append(issue("ENUM", f"{path}.retrieval_status", f"use one of {sorted(RETRIEVAL_STATUSES)}"))
        if contradiction not in CONTRADICTION_STATUSES:
            errors.append(issue("ENUM", f"{path}.contradiction_status", f"use one of {sorted(CONTRADICTION_STATUSES)}"))
        if quality in {"standard", "deep"} and contradiction == "unchecked":
            errors.append(issue("CONTRADICTION_PASS", f"{path}.contradiction_status", "cannot remain unchecked"))

        confidence = probability(row.get("confidence"), f"{path}.confidence", errors)
        if retrieval == "search-snippet" and confidence > 0.45:
            errors.append(issue("SNIPPET_CONFIDENCE", f"{path}.confidence", "search snippets are capped at 0.45"))
        if tier == "tertiary" and confidence > 0.60:
            errors.append(issue("TERTIARY_CONFIDENCE", f"{path}.confidence", "tertiary sources are capped at 0.60"))
        if retrieval == "blocked":
            errors.append(issue("BLOCKED_EVIDENCE", f"{path}.retrieval_status", "blocked sources cannot support a ledger claim"))
        if retrieval in DIRECT_RETRIEVAL:
            direct_count += 1
            if tier in HIGH_QUALITY_TIERS:
                high_quality_direct += 1

        source = row.get("source", "")
        if retrieval not in {"user-provided", "local-file"}:
            parsed = urlparse(source)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(issue("SOURCE_URL", f"{path}.source", "must be a valid public http(s) URL"))
        if len(row.get("quote_or_value", "").split()) < 5:
            warnings.append(issue("THIN_EVIDENCE", f"{path}.quote_or_value", "use a more specific excerpt or value"))

    budget = manifest.get("execution", {}).get("research_budget", {})
    if len(rows) < budget.get("min_sources", 1):
        errors.append(issue("SOURCE_BUDGET", "evidence_map", f"has {len(rows)} rows, below minimum {budget.get('min_sources')}"))
    if len(rows) > budget.get("max_sources", len(rows)):
        target = errors if mode == "final" else warnings
        target.append(issue("SOURCE_BUDGET", "evidence_map", f"has {len(rows)} rows, above planned maximum {budget.get('max_sources')}"))
    minimum_high_quality = {"basic": 1, "standard": 2, "deep": 4}.get(quality, 1)
    if high_quality_direct < minimum_high_quality:
        errors.append(issue("SOURCE_QUALITY", "evidence_map", f"needs at least {minimum_high_quality} directly accessed primary/authoritative sources"))
    direct_ratio = direct_count / len(rows) if rows else 0.0
    minimum_direct_ratio = {"basic": 0.50, "standard": 0.60, "deep": 0.70}.get(quality, 0.50)
    if direct_ratio < minimum_direct_ratio:
        errors.append(issue("DIRECT_ACCESS", "evidence_map", f"directly accessed ratio is {direct_ratio:.2f}; {quality} quality requires at least {minimum_direct_ratio:.2f}"))

    return ids, {
        "evidence_rows": len(rows),
        "direct_sources": direct_count,
        "high_quality_direct_sources": high_quality_direct,
        "direct_source_ratio": round(direct_ratio, 4),
    }


def validate_nodes(nodes: list[Any], evidence_ids: set[str], errors: list[str], warnings: list[str]) -> set[str]:
    ids: set[str] = set()
    for index, raw in enumerate(nodes):
        path = f"nodes[{index}]"
        if not isinstance(raw, dict):
            errors.append(issue("TYPE", path, "must be an object"))
            continue
        node = raw
        missing = NODE_REQUIRED - set(node)
        if missing:
            errors.append(issue("MISSING_FIELD", path, f"missing {sorted(missing)}"))
        node_id = node.get("id")
        if not nonempty(node_id):
            errors.append(issue("EMPTY_ID", f"{path}.id", "must not be empty"))
        elif node_id in ids:
            errors.append(issue("DUPLICATE_ID", f"{path}.id", str(node_id)))
        else:
            ids.add(str(node_id))
        if node.get("type") not in NODE_TYPES:
            errors.append(issue("ENUM", f"{path}.type", f"use one of {sorted(NODE_TYPES)}"))
        if node.get("status") not in STATUSES:
            errors.append(issue("ENUM", f"{path}.status", f"use one of {sorted(STATUSES)}"))
        probability(node.get("confidence"), f"{path}.confidence", errors)
        probability(node.get("probability"), f"{path}.probability", errors)
        refs = validate_references(node.get("evidence_ids"), evidence_ids, f"{path}.evidence_ids", errors)
        validate_references(node.get("sources", refs), evidence_ids, f"{path}.sources", errors)
        if node.get("status") == "fact" and not refs:
            errors.append(issue("FACT_PROVENANCE", f"{path}.evidence_ids", "fact nodes require evidence"))
        if node.get("status") in {"simulation", "counterfactual"} and not refs and not nonempty(node.get("assumption_ref")):
            errors.append(issue("ASSUMPTION", path, "modeled nodes require evidence or assumption_ref"))
        if len(str(node.get("mechanism", "")).split()) < 10:
            errors.append(issue("MECHANISM", f"{path}.mechanism", "must explain a concrete causal channel"))
        if not ensure_list(node.get("alternative_explanations")):
            warnings.append(issue("ALTERNATIVES", f"{path}.alternative_explanations", "record at least one rival explanation"))
        sensitivity = node.get("sensitivity")
        if not isinstance(sensitivity, dict) or sensitivity.get("level") not in {"low", "medium", "high"}:
            errors.append(issue("SENSITIVITY", f"{path}.sensitivity", "requires level low, medium, or high"))
    return ids


def validate_edges(
    edges: list[Any],
    node_ids: set[str],
    evidence_ids: set[str],
    errors: list[str],
    warnings: list[str],
) -> set[str]:
    ids: set[str] = set()
    for index, raw in enumerate(edges):
        path = f"edges[{index}]"
        if not isinstance(raw, dict):
            errors.append(issue("TYPE", path, "must be an object"))
            continue
        edge = raw
        missing = EDGE_REQUIRED - set(edge)
        if missing:
            errors.append(issue("MISSING_FIELD", path, f"missing {sorted(missing)}"))
        edge_id = edge.get("id")
        if not nonempty(edge_id):
            errors.append(issue("EMPTY_ID", f"{path}.id", "must not be empty"))
        elif edge_id in ids:
            errors.append(issue("DUPLICATE_ID", f"{path}.id", str(edge_id)))
        else:
            ids.add(str(edge_id))
        for endpoint in ["from", "to"]:
            if edge.get(endpoint) not in node_ids:
                errors.append(issue("UNKNOWN_REF", f"{path}.{endpoint}", f"unknown node {edge.get(endpoint)!r}"))
        if edge.get("status") not in STATUSES:
            errors.append(issue("ENUM", f"{path}.status", f"use one of {sorted(STATUSES)}"))
        if edge.get("sign") not in {-1, 1}:
            errors.append(issue("SIGN", f"{path}.sign", "must be -1 or 1"))
        probability(edge.get("base_strength"), f"{path}.base_strength", errors)
        probability(edge.get("confidence"), f"{path}.confidence", errors)
        mechanism_words = len(str(edge.get("mechanism", "")).split())
        if mechanism_words < 10:
            errors.append(issue("MECHANISM", f"{path}.mechanism", "must explain transmission, target, timing, and causal rationale"))
        elif mechanism_words < 20:
            warnings.append(issue("SHORT_MECHANISM", f"{path}.mechanism", "target at least 20 words"))
        lag = edge.get("lag_distribution")
        if not isinstance(lag, dict) or not all(nonempty(lag.get(key)) for key in ["type", "min", "max"]):
            errors.append(issue("LAG", f"{path}.lag_distribution", "requires type, min, and max"))
        if not ensure_list(edge.get("context_modifiers")):
            errors.append(issue("CONTEXT", f"{path}.context_modifiers", "requires at least one context modifier"))
        validate_references(edge.get("evidence"), evidence_ids, f"{path}.evidence", errors)
    return ids


def parse_time(value: Any) -> datetime | None:
    if not nonempty(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_actor_track(
    actor: dict[str, Any],
    path: str,
    evidence_ids: set[str],
    subagent_status: str,
    errors: list[str],
) -> None:
    research = actor.get("research_track", {})
    roleplay = actor.get("roleplay_track", {})
    for name, track in [("research_track", research), ("roleplay_track", roleplay)]:
        track_path = f"{path}.{name}"
        for field in ["status", "execution_mode", "agent_ref", "started_at", "completed_at", "artifact", "notes"]:
            if not nonempty(track.get(field)):
                errors.append(issue("HUMAN_TRACK", f"{track_path}.{field}", "required value is missing"))
        if track.get("status") != "completed":
            errors.append(issue("HUMAN_TRACK", f"{track_path}.status", "must be completed"))
        if track.get("execution_mode") not in {"subagent", "isolated-pass"}:
            errors.append(issue("HUMAN_TRACK", f"{track_path}.execution_mode", "use subagent or isolated-pass"))
    if subagent_status == "available":
        if research.get("execution_mode") != "subagent" or roleplay.get("execution_mode") != "subagent":
            errors.append(issue("SUBAGENT_REQUIRED", path, "runtime exposes subagents, so both human tracks must use them"))
        if research.get("agent_ref") == roleplay.get("agent_ref"):
            errors.append(issue("SUBAGENT_SEPARATION", path, "research and roleplay require distinct agent_ref values"))
    elif research.get("execution_mode") == "isolated-pass" or roleplay.get("execution_mode") == "isolated-pass":
        if not nonempty(roleplay.get("isolation_note")):
            errors.append(issue("ISOLATION_NOTE", f"{path}.roleplay_track.isolation_note", "document the no-subagent fallback"))

    research_end = parse_time(research.get("completed_at"))
    roleplay_start = parse_time(roleplay.get("started_at"))
    if research_end is None or roleplay_start is None:
        errors.append(issue("TRACK_TIME", path, "track timestamps must be ISO-8601"))
    elif roleplay_start < research_end:
        errors.append(issue("TRACK_ORDER", path, "roleplay must start after research completes"))

    claims = ensure_list(research.get("claims"))
    if not claims:
        errors.append(issue("RESEARCH_OUTPUT", f"{path}.research_track.claims", "requires sourced public-role claims"))
    for claim_index, claim in enumerate(claims):
        claim_path = f"{path}.research_track.claims[{claim_index}]"
        if not isinstance(claim, dict) or not nonempty(claim.get("claim")):
            errors.append(issue("RESEARCH_OUTPUT", claim_path, "requires a claim string"))
            continue
        validate_references(claim.get("evidence_ids"), evidence_ids, f"{claim_path}.evidence_ids", errors)
        probability(claim.get("confidence"), f"{claim_path}.confidence", errors)

    if not nonempty(roleplay.get("knowledge_cutoff")):
        errors.append(issue("TEMPORAL_KNOWLEDGE", f"{path}.roleplay_track.knowledge_cutoff", "required"))
    validate_references(roleplay.get("dossier_evidence_ids"), evidence_ids, f"{path}.roleplay_track.dossier_evidence_ids", errors)
    hypotheses = ensure_list(roleplay.get("hypotheses"))
    if len(hypotheses) < 2:
        errors.append(issue("ROLEPLAY_ALTERNATIVES", f"{path}.roleplay_track.hypotheses", "requires at least two actions"))
    total = 0.0
    for hyp_index, hypothesis in enumerate(hypotheses):
        hyp_path = f"{path}.roleplay_track.hypotheses[{hyp_index}]"
        if not isinstance(hypothesis, dict):
            errors.append(issue("TYPE", hyp_path, "must be an object"))
            continue
        for field in ["action", "reasoning"]:
            if not nonempty(hypothesis.get(field)):
                errors.append(issue("ROLEPLAY_OUTPUT", f"{hyp_path}.{field}", "required"))
        total += probability(hypothesis.get("probability"), f"{hyp_path}.probability", errors)
        if hypothesis.get("status") != "simulation":
            errors.append(issue("ROLEPLAY_STATUS", f"{hyp_path}.status", "must be simulation"))
        if ensure_list(hypothesis.get("evidence_ids")):
            errors.append(issue("ROLEPLAY_EVIDENCE", f"{hyp_path}.evidence_ids", "roleplay is not evidence; keep this empty"))
    if hypotheses and abs(total - 1.0) > 0.0001:
        errors.append(issue("PROBABILITY_SUM", f"{path}.roleplay_track.hypotheses", f"sum is {total:.6f}, expected 1.0"))


def validate_actors(
    actors: list[Any],
    node_ids: set[str],
    evidence_ids: set[str],
    subagent_status: str,
    errors: list[str],
) -> set[str]:
    ids: set[str] = set()
    for index, raw in enumerate(actors):
        path = f"actors[{index}]"
        if not isinstance(raw, dict):
            errors.append(issue("TYPE", path, "must be an object"))
            continue
        actor = raw
        missing = ACTOR_REQUIRED - set(actor)
        if missing:
            errors.append(issue("MISSING_FIELD", path, f"missing {sorted(missing)}"))
        actor_id = actor.get("id")
        if not nonempty(actor_id):
            errors.append(issue("EMPTY_ID", f"{path}.id", "must not be empty"))
        elif actor_id in ids:
            errors.append(issue("DUPLICATE_ID", f"{path}.id", str(actor_id)))
        else:
            ids.add(str(actor_id))
        if actor.get("person_node") not in node_ids:
            errors.append(issue("UNKNOWN_REF", f"{path}.person_node", f"unknown node {actor.get('person_node')!r}"))
        validate_references(actor.get("evidence_ids"), evidence_ids, f"{path}.evidence_ids", errors)
        if actor.get("materiality") == "material":
            validate_actor_track(actor, path, evidence_ids, subagent_status, errors)
        responses = ensure_list(actor.get("predicted_responses"))
        if len(responses) < 2:
            errors.append(issue("ACTOR_ALTERNATIVES", f"{path}.predicted_responses", "requires at least two responses"))
        total = 0.0
        for response_index, response in enumerate(responses):
            response_path = f"{path}.predicted_responses[{response_index}]"
            if not isinstance(response, dict):
                errors.append(issue("TYPE", response_path, "must be an object"))
                continue
            value = probability(response.get("probability"), f"{response_path}.probability", errors)
            total += value
            if value > 0.80:
                errors.append(issue("HUMAN_CONFIDENCE_CAP", f"{response_path}.probability", "exceeds 0.80"))
            if response.get("status") != "simulation":
                errors.append(issue("ACTOR_STATUS", f"{response_path}.status", "must be simulation"))
        if responses and abs(total - 1.0) > 0.0001:
            errors.append(issue("PROBABILITY_SUM", f"{path}.predicted_responses", f"sum is {total:.6f}, expected 1.0"))
    return ids


def read_jsonl(path: Path, errors: list[str], code: str) -> list[dict[str, Any]]:
    if not path.exists():
        errors.append(issue("MISSING_ARTIFACT", code, f"file does not exist: {path}"))
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(issue("INVALID_JSONL", f"{code}[line {line_no}]", str(exc)))
            continue
        if not isinstance(value, dict):
            errors.append(issue("TYPE", f"{code}[line {line_no}]", "must be an object"))
            continue
        rows.append(value)
    return rows


def validate_human_track_ledger(
    rows: list[dict[str, Any]],
    actors: list[Any],
    subagent_status: str,
    errors: list[str],
) -> None:
    material_actors = {
        str(actor.get("id")): actor
        for actor in actors
        if isinstance(actor, dict) and actor.get("materiality") == "material"
    }
    material_actor_ids = set(material_actors)
    by_actor: dict[str, dict[str, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        path = f"human_track_ledger[{index}]"
        for field in ["actor_id", "track", "execution_mode", "agent_ref", "started_at", "completed_at", "output_artifact", "status"]:
            if not nonempty(row.get(field)):
                errors.append(issue("TRACK_LEDGER", f"{path}.{field}", "required"))
        actor_id = str(row.get("actor_id", ""))
        track = str(row.get("track", ""))
        if actor_id not in material_actor_ids:
            errors.append(issue("UNKNOWN_REF", f"{path}.actor_id", actor_id))
        if track not in {"research", "roleplay"}:
            errors.append(issue("ENUM", f"{path}.track", "use research or roleplay"))
        actor_tracks = by_actor.setdefault(actor_id, {})
        if track in actor_tracks:
            errors.append(issue("DUPLICATE_TRACK", path, f"duplicate {track!r} row for {actor_id!r}"))
        actor_tracks[track] = row
    for actor_id in material_actor_ids:
        tracks = by_actor.get(actor_id, {})
        if set(tracks) != {"research", "roleplay"}:
            errors.append(issue("TRACK_LEDGER", actor_id, "requires exactly one research and one roleplay row"))
            continue
        if subagent_status == "available":
            if any(track.get("execution_mode") != "subagent" for track in tracks.values()):
                errors.append(issue("SUBAGENT_REQUIRED", actor_id, "track ledger must record subagent execution"))
            if tracks["research"].get("agent_ref") == tracks["roleplay"].get("agent_ref"):
                errors.append(issue("SUBAGENT_SEPARATION", actor_id, "track ledger agent_ref values must differ"))
        actor = material_actors[actor_id]
        for track_name in ["research", "roleplay"]:
            actor_track = actor.get(f"{track_name}_track", {})
            ledger_track = tracks[track_name]
            for field in ["execution_mode", "agent_ref", "started_at", "completed_at", "artifact", "status"]:
                actor_value = actor_track.get(field)
                ledger_field = "output_artifact" if field == "artifact" else field
                ledger_value = ledger_track.get(ledger_field)
                if actor_value != ledger_value:
                    errors.append(issue("TRACK_MISMATCH", f"{actor_id}.{track_name}.{field}", f"actor dossier has {actor_value!r}, ledger has {ledger_value!r}"))
        research_end = parse_time(tracks["research"].get("completed_at"))
        roleplay_start = parse_time(tracks["roleplay"].get("started_at"))
        if research_end is None or roleplay_start is None or roleplay_start < research_end:
            errors.append(issue("TRACK_ORDER", actor_id, "roleplay ledger row must start after research completes"))


def validate_branches(
    data: Any,
    edge_ids: set[str],
    actor_ids: set[str],
    evidence_ids: set[str],
    errors: list[str],
) -> int:
    if not isinstance(data, dict):
        errors.append(issue("TYPE", "branch_ledger", "must be an object"))
        return 0
    branches = ensure_list(data.get("branches"))
    if len(branches) < 3:
        errors.append(issue("BRANCH_COUNT", "branch_ledger.branches", "must contain at least 3 branches"))
    ids: set[str] = set()
    total = 0.0
    for index, branch in enumerate(branches):
        path = f"branches[{index}]"
        if not isinstance(branch, dict):
            errors.append(issue("TYPE", path, "must be an object"))
            continue
        missing = BRANCH_REQUIRED - set(branch)
        if missing:
            errors.append(issue("MISSING_FIELD", path, f"missing {sorted(missing)}"))
        branch_id = branch.get("id")
        if not nonempty(branch_id) or branch_id in ids:
            errors.append(issue("DUPLICATE_ID", f"{path}.id", str(branch_id)))
        else:
            ids.add(str(branch_id))
        value = probability(branch.get("probability"), f"{path}.probability", errors)
        total += value
        if value > 0.60:
            errors.append(issue("BRANCH_CAP", f"{path}.probability", "exceeds 0.60"))
        probability(branch.get("confidence"), f"{path}.confidence", errors)
        validate_references(branch.get("causal_trace"), edge_ids, f"{path}.causal_trace", errors)
        validate_references(branch.get("key_decision_points"), actor_ids, f"{path}.key_decision_points", errors)
        validate_references(branch.get("evidence_ids"), evidence_ids, f"{path}.evidence_ids", errors)
        if not isinstance(branch.get("end_state"), dict) or not nonempty(branch.get("end_state", {}).get("summary")):
            errors.append(issue("END_STATE", f"{path}.end_state.summary", "required"))
    if branches and abs(total - 1.0) > 0.0001:
        errors.append(issue("PROBABILITY_SUM", "branch_ledger.branches", f"sum is {total:.6f}, expected 1.0"))
    return len(branches)


def validate_trace(
    rows: list[dict[str, Any]],
    node_ids: set[str],
    edge_ids: set[str],
    evidence_ids: set[str],
    errors: list[str],
) -> None:
    if not rows:
        errors.append(issue("TRACE_EMPTY", "propagation_trace", "requires at least one propagation row"))
    for index, row in enumerate(rows):
        path = f"propagation_trace[{index}]"
        for field in ["step", "time", "edge_id", "from", "to", "output_effect", "mechanism", "evidence_ids"]:
            if field not in row:
                errors.append(issue("MISSING_FIELD", f"{path}.{field}", "required"))
        if row.get("edge_id") not in edge_ids:
            errors.append(issue("UNKNOWN_REF", f"{path}.edge_id", str(row.get("edge_id"))))
        for endpoint in ["from", "to"]:
            if row.get(endpoint) not in node_ids:
                errors.append(issue("UNKNOWN_REF", f"{path}.{endpoint}", str(row.get(endpoint))))
        if not is_number(row.get("output_effect")):
            errors.append(issue("TYPE", f"{path}.output_effect", "must be a finite number"))
        validate_references(row.get("evidence_ids"), evidence_ids, f"{path}.evidence_ids", errors)


def validate_report(path: Path, errors: list[str], warnings: list[str], required: bool) -> None:
    if not path.exists():
        target = errors if required else warnings
        target.append(issue("FINAL_REPORT", "artifact_paths.final_report", f"file does not exist: {path}"))
        return
    text = path.read_text(encoding="utf-8")
    expected = ["change point", "evidence", "propagation", "branch", "human", "validation", "warning"]
    for term in expected:
        if term not in text.lower():
            warnings.append(issue("REPORT_SECTION", path.name, f"missing section or discussion for {term!r}"))


def validate_workspace(workspace: Path, mode: str = "final", require_report: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {}
    metrics: dict[str, Any] = {}
    manifest_path = workspace / "simulation-manifest.json"
    if not manifest_path.exists():
        errors.append(issue("MISSING_MANIFEST", "simulation-manifest.json", str(manifest_path)))
        return {"schema_version": SCHEMA_VERSION, "validated_at": utc_now(), "mode": mode, "status": "fail", "checks": checks, "metrics": metrics, "errors": errors, "warnings": warnings}
    try:
        manifest = load_json(manifest_path)
    except json.JSONDecodeError as exc:
        errors.append(issue("INVALID_MANIFEST", "simulation-manifest.json", str(exc)))
        return {"schema_version": SCHEMA_VERSION, "validated_at": utc_now(), "mode": mode, "status": "fail", "checks": checks, "metrics": metrics, "errors": errors, "warnings": warnings}

    validate_manifest(manifest, mode, errors, warnings)
    checks["manifest"] = "pass" if not errors else "fail"
    artifact_paths = manifest.get("artifact_paths", {}) if isinstance(manifest.get("artifact_paths"), dict) else {}

    nodes_path, nodes_raw = load_artifact(workspace, artifact_paths, "nodes", "nodes.json", errors)
    edges_path, edges_raw = load_artifact(workspace, artifact_paths, "edges", "edges.json", errors)
    actors_path, actors_raw = load_artifact(workspace, artifact_paths, "actors", "actors.json", errors)
    branches_path, branches_raw = load_artifact(workspace, artifact_paths, "branch_ledger", "branch-ledger.json", errors)
    evidence_path = workspace / str(artifact_paths.get("evidence_map", "evidence-map.csv"))
    if evidence_path.exists():
        evidence_rows = load_csv_rows(evidence_path)
    else:
        errors.append(issue("MISSING_ARTIFACT", "evidence_map", str(evidence_path)))
        evidence_rows = []

    evidence_ids, evidence_metrics = validate_evidence(evidence_rows, manifest, mode, errors, warnings)
    metrics.update(evidence_metrics)
    nodes = ensure_list(nodes_raw) if nodes_raw is not None else []
    edges = ensure_list(edges_raw) if edges_raw is not None else []
    actors = ensure_list(actors_raw) if actors_raw is not None else []
    node_ids = validate_nodes(nodes, evidence_ids, errors, warnings)
    edge_ids = validate_edges(edges, node_ids, evidence_ids, errors, warnings)
    subagent_status = manifest.get("execution", {}).get("subagents", {}).get("status", "unknown")
    actor_ids = validate_actors(actors, node_ids, evidence_ids, subagent_status, errors)

    human_ledger_path = workspace / str(artifact_paths.get("human_track_ledger", "human-track-ledger.jsonl"))
    human_rows = read_jsonl(human_ledger_path, errors, "human_track_ledger")
    validate_human_track_ledger(human_rows, actors, subagent_status, errors)
    branch_count = validate_branches(branches_raw, edge_ids, actor_ids, evidence_ids, errors) if branches_raw is not None else 0
    trace_path = workspace / str(artifact_paths.get("propagation_trace", "propagation-trace.jsonl"))
    trace_rows = read_jsonl(trace_path, errors, "propagation_trace")
    validate_trace(trace_rows, node_ids, edge_ids, evidence_ids, errors)
    report_path = workspace / str(artifact_paths.get("final_report", "REPORT.md"))
    validate_report(report_path, errors, warnings, require_report)

    metrics.update({
        "nodes": len(nodes),
        "edges": len(edges),
        "actors": len(actors),
        "human_track_rows": len(human_rows),
        "branches": branch_count,
        "trace_rows": len(trace_rows),
    })
    checks.update({
        "evidence": "pass" if not any("evidence" in error.lower() for error in errors) else "fail",
        "graph": "pass" if not any("nodes[" in error or "edges[" in error for error in errors) else "fail",
        "human_tracks": "pass" if not any("actor" in error.lower() or "track" in error.lower() or "roleplay" in error.lower() for error in errors) else "fail",
        "branches": "pass" if not any("branch" in error.lower() for error in errors) else "fail",
        "trace": "pass" if not any("trace" in error.lower() for error in errors) else "fail",
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "validated_at": utc_now(),
        "mode": mode,
        "status": "pass" if not errors else "fail",
        "checks": checks,
        "metrics": metrics,
        "errors": errors,
        "warnings": warnings,
        "artifacts": {
            "nodes": str(nodes_path),
            "edges": str(edges_path),
            "actors": str(actors_path),
            "branches": str(branches_path),
        },
    }


def validate_examples() -> dict[str, Any]:
    root = skill_root()
    templates = root / "templates"
    with tempfile.TemporaryDirectory(prefix="aleph-skill-example-") as temp_dir:
        workspace = Path(temp_dir)
        manifest = load_json(templates / "simulation-manifest.json")
        write_json(workspace / "simulation-manifest.json", manifest)
        write_json(workspace / "nodes.json", [load_json(templates / "timeline-node.json")])
        write_json(workspace / "edges.json", [load_json(templates / "causal-edge.json")])
        write_json(workspace / "actors.json", [load_json(templates / "actor-dossier.json")])
        write_json(workspace / "branch-ledger.json", load_json(templates / "branch-ledger.json"))
        for name in ["evidence-map.csv", "propagation-trace.jsonl", "human-track-ledger.jsonl"]:
            shutil.copyfile(templates / name, workspace / name)
        (workspace / "REPORT.md").write_text(
            "# Example\n\n## Change point\n\n## Evidence\n\n## Propagation\n\n## Branches\n\n## Human tracks\n\n## Validation\n\n## Warnings\n",
            encoding="utf-8",
            newline="\n",
        )
        return validate_workspace(workspace, mode="final", require_report=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Aleph Skill simulation artifacts with schema and audit gates.")
    parser.add_argument("--workspace", help="Simulation workspace directory.")
    parser.add_argument("--examples", action="store_true", help="Validate bundled templates as a complete fixture.")
    parser.add_argument("--mode", choices=["draft", "final"], default="final", help="Validation strictness.")
    parser.add_argument("--require-report", action="store_true", help="Fail when the final Markdown report is missing.")
    parser.add_argument("--write-report", action="store_true", help="Write validation-report.json into workspace.")
    args = parser.parse_args()

    if args.examples:
        result = validate_examples()
    elif args.workspace:
        workspace = Path(args.workspace).resolve()
        result = validate_workspace(workspace, mode=args.mode, require_report=args.require_report)
        if args.write_report:
            write_json(workspace / "validation-report.json", result)
    else:
        parser.error("Provide --workspace or --examples")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
