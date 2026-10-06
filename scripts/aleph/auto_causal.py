"""Deterministic auto-causal induction bridge from research evidence to simulation models."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import FORMULA_VERSION
from .import_ledger import import_d_research_ledger
from .io import canonical_hash, canonical_json_bytes, load_json_secure, write_json_atomic
from .paths import path_contains_link_or_reparse

PROPOSAL_SCHEMA_VERSION = "1.0.0"
REVIEW_SCHEMA_VERSION = "1.0.0"

# --- Diacritic & Slug Utilities ---

def _strip_accents(text: str) -> str:
    """Normalize unicode and strip combining diacritical marks."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _slugify(text: str) -> str:
    """Create deterministic alphanumeric ASCII slug with hyphens."""
    ascii_text = _strip_accents(text).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return cleaned or "unnamed"


# --- Pattern Matching for Bilingual Causal Extraction ---

# English causal verbs / phrases
EN_POSITIVE_RE = re.compile(
    r"\b(?:increases?|boosts?|raises?|stimulates?|drives?|amplifies?|accelerates?|leads? to higher|causes? higher|results? in higher)\b",
    re.IGNORECASE,
)
EN_NEGATIVE_RE = re.compile(
    r"\b(?:decreases?|reduces?|lowers?|inhibits?|dampens?|cuts?|suppresses?|leads? to lower|causes? lower|results? in lower)\b",
    re.IGNORECASE,
)
EN_CORRELATION_RE = re.compile(
    r"\b(?:is correlated with|are correlated with|correlated with|associated with|co-occurs with|co-occur with|linked with|correlates? with)\b",
    re.IGNORECASE,
)
EN_NEUTRAL_CAUSAL_RE = re.compile(
    r"\b(?:causes?|triggers?|affects?|impacts?|influences?)\b",
    re.IGNORECASE,
)

# Vietnamese causal verbs / phrases
VI_POSITIVE_RE = re.compile(
    r"(?:làm tăng|thúc đẩy|nâng cao|kích thích|dẫn đến tăng|tăng cường|khuếch đại|đẩy mạnh)",
    re.IGNORECASE,
)
VI_NEGATIVE_RE = re.compile(
    r"(?:làm giảm|kìm hãm|kiềm chế|hạ thấp|cắt giảm|hạn chế|kéo tụt|làm suy yếu)",
    re.IGNORECASE,
)
VI_CORRELATION_RE = re.compile(
    r"(?:tương quan với|đi kèm với|đồng biến với|nghịch biến với|liên hệ với)",
    re.IGNORECASE,
)
VI_NEUTRAL_CAUSAL_RE = re.compile(
    r"(?:tác động đến|ảnh hưởng đến|gây ra|dẫn tới|chi phối)",
    re.IGNORECASE,
)

# Negation detection
NEGATION_EN_RE = re.compile(
    r"\b(?:it is false that|false that|is false that|there is no evidence that|no evidence (?:that|of)|does not|do not|did not|not|fails? to|failed to|never|cannot|has no effect on|have no effect on|disproves?|refutes?)\b",
    re.IGNORECASE,
)
NEGATION_VI_RE = re.compile(
    r"(?:sai lầm khi cho rằng|không đúng khi cho rằng|chưa có bằng chứng|không có bằng chứng|không làm|không|chẳng|chưa|không hề|không thể|thất bại trong việc|không có tác động|không ảnh hưởng)",
    re.IGNORECASE,
)

# Mechanism extraction
MECHANISM_EN_RE = re.compile(
    r"\b(?:via|through|by means of|by mechanism of)\s+([A-Za-z0-9_ -]{3,100})(?:[;,.]|$)",
    re.IGNORECASE,
)
MECHANISM_VI_RE = re.compile(
    r"(?:thông qua|bằng cơ chế|nhờ vào|thông qua kênh|bằng cách)\s+([^;,.\n]{3,100})(?:[;,.]|$)",
    re.IGNORECASE,
)

# Lag extraction
LAG_EN_RE = re.compile(
    r"\b(?:with (?:a )?lag of|delay of|lagged by|after)\s+(\d+)\s*(tick|ticks|day|days|month|months|quarter|quarters|year|years)\b",
    re.IGNORECASE,
)
LAG_VI_RE = re.compile(
    r"(?:với độ trễ|trễ|sau)\s+(\d+)\s*(tick|ngày|tháng|quý|năm)\b",
    re.IGNORECASE,
)

# Effect size / magnitude extraction
EFFECT_SIZE_EN_RE = re.compile(
    r"\b(?:by|effect size (?:of)?|magnitude (?:of)?|coefficient (?:of)?|elasticity (?:of)?)\s*([+-]?\d+(?:\.\d+)?%?)\b",
    re.IGNORECASE,
)
EFFECT_SIZE_VI_RE = re.compile(
    r"(?:mức|khoảng|hệ số|tỷ lệ|quy mô)\s*([+-]?\d+(?:\.\d+)?%?)",
    re.IGNORECASE,
)

# Stock keywords
STOCK_EN_WORDS = {"stock", "debt", "inventory", "reserve", "accumulated", "wealth", "capital", "holdings"}
STOCK_VI_WORDS = {"tồn kho", "nợ", "dự trữ", "tích lũy", "vốn", "tài sản"}

# Flow keywords
FLOW_EN_WORDS = {"flow", "rate", "speed", "growth", "deficit", "surplus", "influx", "outflow", "issuance"}
FLOW_VI_WORDS = {"dòng tiền", "tốc độ", "tỷ lệ tăng", "thâm hụt", "thặng dư", "dòng vốn", "phát hành"}

# Baseline extraction
BASELINE_EN_RE = re.compile(r"\b(?:baseline (?:of)?|initial (?:value)? (?:of)?)\s*([+-]?\d+(?:\.\d+)?)\b", re.IGNORECASE)
BASELINE_VI_RE = re.compile(r"(?:mức cơ sở|giá trị ban đầu)\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)

# Observation time extraction
OBS_TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?)\b")

# Unit keywords
UNIT_PATTERNS = [
    (re.compile(r"\b(?:percent|percentage|%|điểm phần trăm)\b", re.IGNORECASE), "%"),
    (re.compile(r"\b(?:basis points|bps)\b", re.IGNORECASE), "bps"),
    (re.compile(r"\b(?:usd|dollar|\$)\b", re.IGNORECASE), "USD"),
    (re.compile(r"\b(?:vnd|đồng)\b", re.IGNORECASE), "VND"),
    (re.compile(r"\b(?:triệu usd|million usd)\b", re.IGNORECASE), "M_USD"),
    (re.compile(r"\b(?:tỷ vnd|billion vnd)\b", re.IGNORECASE), "B_VND"),
]


def _detect_unit(text: str) -> str:
    for regex, unit_name in UNIT_PATTERNS:
        if regex.search(text):
            return unit_name
    return ""


def _classify_scale(name: str) -> str:
    lower = name.lower()
    for word in STOCK_EN_WORDS:
        if re.search(rf"\b{word}\b", lower):
            return "stock"
    for word in STOCK_VI_WORDS:
        if word in lower:
            return "stock"
    for word in FLOW_EN_WORDS:
        if re.search(rf"\b{word}\b", lower):
            return "flow"
    for word in FLOW_VI_WORDS:
        if word in lower:
            return "flow"
    return "level"


def _parse_confidence(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        val = raw.strip().lower()
        if val == "high":
            return 0.85
        if val == "medium":
            return 0.60
        if val == "low":
            return 0.30
        try:
            parsed = float(val)
            return max(0.0, min(1.0, parsed))
        except ValueError:
            pass
    return 0.50


def _parse_lag_ticks(match: re.Match[str]) -> int:
    val = int(match.group(1))
    unit = match.group(2).lower()
    if unit in ("tick", "ticks"):
        return max(0, val)
    if unit in ("day", "days", "ngày"):
        return max(0, val)
    if unit in ("month", "months", "tháng"):
        return max(0, val * 30)
    if unit in ("quarter", "quarters", "quý"):
        return max(0, val * 90)
    if unit in ("year", "years", "năm"):
        return max(0, val * 365)
    return max(0, val)


def _parse_effect_size(val_str: str) -> float | None:
    val_str = val_str.strip()
    is_percent = val_str.endswith("%")
    if is_percent:
        val_str = val_str[:-1]
    try:
        num = float(val_str)
        return num / 100.0 if is_percent else num
    except ValueError:
        return None


def _extract_entities_and_relation(claim_text: str) -> tuple[str, str, int | None, str, bool]:
    """Extract source entity, target entity, sign (-1, 1 or None), relation label, and is_correlation flag."""
    is_correlation = bool(EN_CORRELATION_RE.search(claim_text) or VI_CORRELATION_RE.search(claim_text))
    sign: int | None = None
    relation = "causes"

    # Search for positive
    pos_match = EN_POSITIVE_RE.search(claim_text) or VI_POSITIVE_RE.search(claim_text)
    neg_match = EN_NEGATIVE_RE.search(claim_text) or VI_NEGATIVE_RE.search(claim_text)
    neut_match = EN_NEUTRAL_CAUSAL_RE.search(claim_text) or VI_NEUTRAL_CAUSAL_RE.search(claim_text)

    split_span: tuple[int, int] | None = None
    if pos_match:
        sign = 1
        relation = "increases"
        split_span = pos_match.span()
    elif neg_match:
        sign = -1
        relation = "decreases"
        split_span = neg_match.span()
    elif is_correlation:
        corr_match = EN_CORRELATION_RE.search(claim_text) or VI_CORRELATION_RE.search(claim_text)
        if corr_match:
            split_span = corr_match.span()
        sign = 1
        relation = "correlation"
    elif neut_match:
        sign = 1
        relation = "causes"
        split_span = neut_match.span()

    if split_span:
        source_part = claim_text[: split_span[0]].strip()
        target_part = claim_text[split_span[1] :].strip()
        # Cut off mechanism clauses from target
        target_part = re.split(
            r"\b(?:via|through|by means of|thông qua|bằng cơ chế|nhờ vào|bằng cách)\b",
            target_part,
            flags=re.IGNORECASE,
        )[0].strip()
        # Cut off secondary clauses at comma, semicolon, period
        target = re.split(r"[,;.]", target_part)[0].strip()
        # Clean up leading/trailing prepositions, articles, or negation prefixes
        clean_source = re.sub(
            r"^(?:it is false that|false that|is false that|there is no evidence that|no evidence (?:that|of)|sai lầm khi cho rằng|không đúng khi cho rằng)\s+",
            "",
            source_part,
            flags=re.IGNORECASE,
        ).strip()
        source = re.sub(r"^(an?|the|việc|sự|higher|lower)\s+", "", clean_source, flags=re.IGNORECASE).strip()
        target = re.sub(r"^(an?|the|việc|sự|đến|to|higher|lower)\s+", "", target, flags=re.IGNORECASE).strip()
        if source and target:
            return source, target, sign, relation, is_correlation

    # Fallback heuristic
    return "source-factor", "target-factor", sign, relation, is_correlation



# --- Core Pipeline Functions ---

def propose_model(
    evidence_input: list[dict[str, Any]] | Path | str,
    *,
    proposal_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Generate a deterministic causal proposal and gap report from imported evidence."""
    # 1. Resolve evidence rows
    raw_rows: list[dict[str, Any]] = []
    if isinstance(evidence_input, (str, Path)):
        path = Path(evidence_input)
        if path.suffix.lower() == ".csv":
            import_res = import_d_research_ledger(path)
            raw_rows = import_res.get("evidence_rows", [])
        elif path.suffix.lower() == ".json":
            data, _ = load_json_secure(path)
            if isinstance(data, list):
                raw_rows = data
            elif isinstance(data, dict):
                candidate_rows = data.get("evidence_rows", data.get("records", []))
                if not isinstance(candidate_rows, list):
                    raise ValueError("Evidence rows must be a JSON array")
                raw_rows = [item for item in candidate_rows if isinstance(item, dict)]
    else:
        raw_rows = evidence_input

    # Filter strictly to record_type == "claim" (P3.A02)
    claim_rows = [
        row for row in raw_rows
        if str(row.get("record_type", "claim")).strip().lower() == "claim"
    ]

    # Calculate deterministic evidence_hash
    evidence_canonical_bytes = canonical_json_bytes(
        sorted(claim_rows, key=lambda r: str(r.get("evidence_id") or r.get("claim_id") or ""))
    )
    evidence_hash = f"sha256:{hashlib.sha256(evidence_canonical_bytes).hexdigest()}"

    if not proposal_id:
        proposal_id = f"proposal:{hashlib.sha256(evidence_canonical_bytes).hexdigest()[:16]}"
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    candidate_nodes: list[dict[str, Any]] = []
    candidate_edges: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    entity_mappings: dict[str, dict[str, Any]] = {}

    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_id: dict[str, dict[str, Any]] = {}
    gap_count = 0

    # Pass 1: Parse claims into nodes, edges, and gaps
    for row in claim_rows:
        claim_id = str(row.get("claim_id") or row.get("evidence_id") or f"claim:{len(entity_mappings)+1}")
        evidence_id = str(row.get("evidence_id") or claim_id)
        claim_text = str(row.get("claim") or "")
        quote_text = str(row.get("quote_or_value") or row.get("quote_or_anchor") or row.get("evidence") or "")
        combined_text = f"{claim_text}. {quote_text}".strip()
        confidence = _parse_confidence(row.get("confidence"))

        source_name, target_name, sign, relation, is_corr = _extract_entities_and_relation(claim_text)
        source_slug = _slugify(source_name)
        target_slug = _slugify(target_name)

        source_id = f"factor:{source_slug}"
        target_id = f"factor:{target_slug}"
        edge_id = f"causal:{source_slug}-to-{target_slug}"

        # Register or update candidate nodes
        for node_id, node_name in [(source_id, source_name), (target_id, target_name)]:
            if node_id not in nodes_by_id:
                scale = _classify_scale(node_name)
                unit = _detect_unit(combined_text)
                measurements = row.get("node_measurements", {})
                candidate_measurement = measurements.get(node_id, measurements.get(node_name, {})) if isinstance(measurements, dict) else {}
                measurement: dict[str, Any] = candidate_measurement if isinstance(candidate_measurement, dict) else {}
                baseline_val = measurement.get("baseline")
                node_status = "inference"
                if not _finite_number(baseline_val):
                    baseline_val = None
                    node_status = "assumption"
                    gap_count += 1
                    gap_id = f"gap:{gap_count}"
                    gaps.append({
                        "gap_id": gap_id,
                        "target_id": node_id,
                        "gap_type": "missing_baseline",
                        "description": f"Baseline value missing for node '{node_name}'. Requires explicit assumption or empirical measurement.",
                    })

                obs_time = (
                    measurement.get("time") or row.get("observation_time")
                    or row.get("observed_at")
                )
                if not obs_time:
                    time_match = OBS_TIME_RE.search(combined_text)
                    if time_match:
                        obs_time = time_match.group(1)

                if obs_time:
                    node_time = str(obs_time)
                else:
                    node_time = None
                    node_status = "assumption"
                    gap_count += 1
                    gap_id = f"gap:{gap_count}"
                    gaps.append({
                        "gap_id": gap_id,
                        "target_id": node_id,
                        "gap_type": "missing_observation_time",
                        "description": f"Observation time missing for node '{node_name}'. Requires explicit timestamp or empirical anchor.",
                    })

                unit = measurement.get("unit", unit)
                if not unit:
                    node_status = "assumption"
                    gap_count += 1
                    gaps.append({"gap_id": f"gap:{gap_count}", "target_id": node_id,
                                 "gap_type": "missing_unit", "description": "An explicit measurement unit is required."})
                node_obj: dict[str, Any] = {
                    "id": node_id,
                    "name": node_name.strip(),
                    "scale": scale,
                    "unit": unit,
                    "baseline": baseline_val,
                    "time": node_time,
                    "status": node_status,
                    "confidence": confidence,
                    "evidence_ids": [evidence_id],
                    "source_anchor": quote_text[:120] if quote_text else claim_text[:120],
                }
                if scale == "stock":
                    retention = measurement.get("retention")
                    node_obj["retention"] = retention if isinstance(retention, (int, float)) and _finite_number(retention) and 0 <= retention <= 1 else None
                    if node_obj["retention"] is None:
                        node_obj["status"] = "assumption"
                        gap_count += 1
                        gaps.append({"gap_id": f"gap:{gap_count}", "target_id": node_id,
                                     "gap_type": "missing_retention", "description": "Stock retention must be measured or explicitly reviewed; no default is inferred."})
                nodes_by_id[node_id] = node_obj
            else:
                if evidence_id not in nodes_by_id[node_id]["evidence_ids"]:
                    nodes_by_id[node_id]["evidence_ids"].append(evidence_id)

        # Mechanism extraction
        mechanism: str | None = None
        mech_match = MECHANISM_EN_RE.search(combined_text) or MECHANISM_VI_RE.search(combined_text)
        if mech_match:
            mechanism = mech_match.group(1).strip()

        # Lag extraction
        lag_ticks: int | None = None
        lag_match = LAG_EN_RE.search(combined_text) or LAG_VI_RE.search(combined_text)
        if lag_match:
            lag_ticks = _parse_lag_ticks(lag_match)

        # Effect size extraction
        effect_size: float | None = None
        eff_match = EFFECT_SIZE_EN_RE.search(combined_text) or EFFECT_SIZE_VI_RE.search(combined_text)
        if eff_match:
            effect_size = _parse_effect_size(eff_match.group(1))

        # Check gaps for this edge
        edge_gap_refs: list[str] = []

        if is_corr:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "correlation_not_causation",
                "description": f"Empirical claim reports correlation without establishing causal direction: '{claim_text}'",
            })
            edge_gap_refs.append(gap_id)

        if not mechanism:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "missing_mechanism",
                "description": f"No transmission mechanism identified in claim text for '{source_name} -> {target_name}'",
            })
            edge_gap_refs.append(gap_id)

        if lag_ticks is None:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "unknown_lag",
                "description": f"Temporal lag unknown between '{source_name}' and '{target_name}'. Cannot infer lag from publishing date.",
            })
            edge_gap_refs.append(gap_id)

        if effect_size is None:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "missing_effect_size",
                "description": f"Numerical effect size / elasticity missing for '{source_name} -> {target_name}'",
            })
            edge_gap_refs.append(gap_id)

        # Contradiction detection
        contra = str(row.get("contradiction") or row.get("contradiction_status") or "").strip()
        if contra and contra.lower() not in ("none", "false", "no", "uncontradicted"):
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "contradiction",
                "description": f"Evidence ledger records contradiction for claim '{claim_id}': {contra}",
            })
            edge_gap_refs.append(gap_id)

        # Negation detection
        is_negated = bool(NEGATION_EN_RE.search(claim_text) or NEGATION_VI_RE.search(claim_text))
        if is_negated:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "negated_causal_statement",
                "description": f"Empirical claim reports negation or non-causal statement: '{claim_text}'",
            })
            edge_gap_refs.append(gap_id)

        # Blocked / prohibited source detection
        is_blocked = (
            str(row.get("retrieval_status", "")).strip().lower() in ("blocked", "denied", "prohibited", "error")
            or str(row.get("discovery_disposition", "")).strip().lower() in ("blocked", "denied", "prohibited")
            or "blocked" in str(row.get("reporting_disposition", "")).strip().lower()
        )
        if is_blocked:
            gap_count += 1
            gap_id = f"gap:{gap_count}"
            gaps.append({
                "gap_id": gap_id,
                "target_id": edge_id,
                "gap_type": "blocked_source",
                "description": f"Evidence source for claim '{claim_id}' is marked blocked or prohibited",
            })
            edge_gap_refs.append(gap_id)

        # Determine edge status
        edge_status = "incomplete" if edge_gap_refs else "proposed"

        edge_obj: dict[str, Any] = {
            "id": edge_id,
            "from": source_id,
            "to": target_id,
            "relation": relation,
            "sign": sign if sign in (-1, 1) else 1,
            "status": edge_status,
            "evidence_confidence": confidence,
            "evidence_ids": [evidence_id],
        }
        if mechanism:
            edge_obj["mechanism"] = mechanism
        if lag_ticks is not None:
            edge_obj["lag_ticks"] = lag_ticks
        if effect_size is not None:
            edge_obj["effect_size"] = effect_size
            edge_obj["base_strength"] = abs(effect_size)
        if edge_gap_refs:
            edge_obj["gap_refs"] = edge_gap_refs

        if edge_id in edges_by_id:
            existing = edges_by_id[edge_id]
            if existing["sign"] != sign:
                gap_count += 1
                gap_id = f"gap:{gap_count}"
                gaps.append({
                    "gap_id": gap_id,
                    "target_id": edge_id,
                    "gap_type": "contradiction",
                    "description": f"Conflicting signs proposed for edge {edge_id}: existing {existing['sign']} vs new {sign}",
                })
                existing.setdefault("gap_refs", []).append(gap_id)
                existing["status"] = "incomplete"
                edge_gap_refs.append(gap_id)
            if evidence_id not in existing["evidence_ids"]:
                existing["evidence_ids"].append(evidence_id)
            if edge_gap_refs:
                for gid in edge_gap_refs:
                    if gid not in existing.setdefault("gap_refs", []):
                        existing["gap_refs"].append(gid)
                existing["status"] = "incomplete"
        else:
            edges_by_id[edge_id] = edge_obj

        # Record entity mapping
        entity_mappings[claim_id] = {
            "claim_id": claim_id,
            "evidence_ids": [evidence_id],
            "node_ids": [source_id, target_id],
            "edge_ids": [edge_id],
            "source_anchor": quote_text[:120] if quote_text else claim_text[:120],
        }


    candidate_nodes = list(nodes_by_id.values())
    candidate_edges = list(edges_by_id.values())

    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposal_id": proposal_id,
        "created_at": created_at,
        "evidence_hash": evidence_hash,
        "candidate_nodes": candidate_nodes,
        "candidate_edges": candidate_edges,
        "gaps": gaps,
        "entity_mappings": entity_mappings,
    }


def review_model(
    proposal: dict[str, Any],
    reviewer: str = "deterministic:causal-screening",
    *,
    decisions: dict[str, Any] | None = None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """Examine causal candidate proposal and output cryptographically-bound admission decisions."""
    _validate_sidecar(proposal, "causal-proposal")
    proposal_hash = f"sha256:{canonical_hash(proposal)}"
    evidence_hash = proposal.get("evidence_hash", "")
    review_id = f"review:{proposal.get('proposal_id', '').split(':')[-1]}"

    if not reviewed_at:
        reviewed_at = datetime.now(timezone.utc).isoformat()

    edge_decisions: dict[str, dict[str, Any]] = {}
    node_decisions: dict[str, dict[str, Any]] = {}

    if decisions is not None and (not isinstance(decisions, dict) or not isinstance(reviewer, str) or not reviewer.startswith(("human:", "agent:")) or not reviewer.split(":", 1)[1].strip()):
        raise ValueError("MANUAL_REVIEW_IDENTITY: explicit decisions require a named human or agent reviewer")
    manual_edge_decisions = (decisions or {}).get("edge_decisions", {})
    manual_node_decisions = (decisions or {}).get("node_decisions", {})
    for supplied in (manual_edge_decisions, manual_node_decisions):
        if not isinstance(supplied, dict) or any(not isinstance(item, dict) or not isinstance(item.get("rationale"), str) or not item["rationale"].strip() for item in supplied.values()):
            raise ValueError("INVALID_REVIEW_DECISIONS: decisions require objects and nonempty rationales")
    admitted_count = 0
    incomplete_count = 0
    rejected_count = 0

    gap_by_id = {g["gap_id"]: g for g in proposal.get("gaps", [])}

    # Review edges
    for edge in proposal.get("candidate_edges", []):
        edge_id = edge["id"]
        evidence_refs = edge.get("evidence_ids", [])

        if edge_id in manual_edge_decisions:
            dec = manual_edge_decisions[edge_id]
            edge_decisions[edge_id] = {
                "decision": dec.get("decision", "incomplete"),
                "rationale": dec.get("rationale", "Manual agent decision."),
                "evidence_refs": evidence_refs,
            }
            if "modifications" in dec:
                edge_decisions[edge_id]["modifications"] = dec["modifications"]
        else:
            # Deterministic evaluation based on gaps and candidate completeness
            gaps = edge.get("gap_refs", [])
            gap_objs = [gap_by_id[gid] for gid in gaps if gid in gap_by_id]
            status = edge.get("status", "incomplete")
            if gaps or status == "incomplete":
                incomplete_count += 1
                decision_type = "incomplete"
                # If gap is correlation, contradiction, negation, or blocked source, reject from causal graph
                has_corr_gap = any(g.get("gap_type") == "correlation_not_causation" for g in gap_objs)
                has_contra_gap = any(g.get("gap_type") == "contradiction" for g in gap_objs)
                has_neg_gap = any(g.get("gap_type") == "negated_causal_statement" for g in gap_objs)
                has_blocked_gap = any(g.get("gap_type") == "blocked_source" for g in gap_objs)
                if has_corr_gap or has_contra_gap or has_neg_gap or has_blocked_gap:
                    decision_type = "reject"
                    rejected_count += 1
                    incomplete_count -= 1
                    rationale = f"Rejected: Causal edge blocked by critical gap (correlation, contradiction, negation, or blocked source): {gaps}"
                else:
                    rationale = f"Incomplete: Missing required parameters or mechanisms: {gaps}"

                edge_decisions[edge_id] = {
                    "decision": decision_type,
                    "rationale": rationale,
                    "evidence_refs": evidence_refs,
                }

            else:
                admitted_count += 1
                edge_decisions[edge_id] = {
                    "decision": "admit",
                    "rationale": "Admitted: Grounded causal mechanism, lag, sign, and effect size present in empirical evidence.",
                    "evidence_refs": evidence_refs,
                }

    # Review nodes
    for node in proposal.get("candidate_nodes", []):
        node_id = node["id"]
        if node_id in manual_node_decisions:
            node_decisions[node_id] = manual_node_decisions[node_id]
        else:
            # If connected to any admitted edge, admit node
            connected = any(
                (edge["from"] == node_id or edge["to"] == node_id)
                and edge_decisions.get(edge["id"], {}).get("decision") in ("admit", "revise")
                for edge in proposal.get("candidate_edges", [])
            )
            if connected:
                node_decisions[node_id] = {
                    "decision": "admit",
                    "rationale": f"Node connected to admitted causal transmission path ({node.get('scale', 'level')}).",
                }
            else:
                node_decisions[node_id] = {
                    "decision": "reject" if rejected_count > 0 and admitted_count == 0 else "revise",
                    "rationale": "Node not connected to any admitted transmission edge.",
                }

    counts = [d.get("decision") for d in edge_decisions.values()]
    admitted_count = sum(d in ("admit", "revise") for d in counts)
    incomplete_count = counts.count("incomplete")
    rejected_count = counts.count("reject")
    complete_manual_review = decisions is not None and all(
        item["id"] in supplied and isinstance(supplied[item["id"]], dict)
        and supplied[item["id"]].get("rationale", "").strip()
        for items, supplied in ((proposal.get("candidate_nodes", []), manual_node_decisions),
                                (proposal.get("candidate_edges", []), manual_edge_decisions)) for item in items
    )
    for decision in [*edge_decisions.values(), *node_decisions.values()]:
        if decision.get("decision") not in {"admit", "revise", "reject", "incomplete"}:
            raise ValueError("INVALID_REVIEW_DECISION")
    has_assumptions = any(n.get("status") == "assumption" for n in proposal.get("candidate_nodes", []))
    has_gaps = bool(proposal.get("gaps"))
    has_unadmitted_nodes = any(d.get("decision") not in {"admit", "revise"} for d in node_decisions.values())
    if admitted_count > 0 and (incomplete_count > 0 or rejected_count > 0 or has_assumptions or has_gaps):
        overall_status = "partial"
    elif admitted_count > 0 and incomplete_count == 0 and rejected_count == 0 and not has_assumptions and not has_gaps and not has_unadmitted_nodes:
        overall_status = "approved" if complete_manual_review else "partial"
    else:
        overall_status = "rejected"

    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "review_id": review_id,
        "proposal_id": proposal.get("proposal_id", ""),
        "proposal_hash": proposal_hash,
        "evidence_hash": evidence_hash,
        "reviewed_at": reviewed_at,
        "reviewer": reviewer,
        "edge_decisions": edge_decisions,
        "node_decisions": node_decisions,
        "overall_status": overall_status,
        "review_kind": "explicit_decisions" if complete_manual_review else "deterministic_screening",
        "interventions": (decisions or {}).get("interventions", []),
    }


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _validate_sidecar(data: Any, name: str) -> None:
    """Validate the exact vocabulary used by the two local causal schemas.

    This bounded stdlib validator is deliberately limited to these contracts;
    unsupported schema keywords fail closed rather than being silently ignored.
    """
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / f"{name}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    supported = {"$id", "$schema", "title", "description", "type", "const", "enum", "format",
                 "pattern", "minimum", "maximum", "required", "properties", "additionalProperties", "items"}

    def refuse(pointer: str, message: str) -> None:
        raise ValueError(f"SIDECAR_SCHEMA: {name}{pointer}: {message}")

    def walk(value: Any, rule: dict[str, Any], pointer: str = "", depth: int = 0) -> None:
        if depth > 64 or set(rule) - supported:
            refuse(pointer, "unsupported contract or excessive nesting")
        types = rule.get("type")
        if types:
            types = types if isinstance(types, list) else [types]
            matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
                       "string": isinstance(value, str), "number": _finite_number(value),
                       "integer": type(value) is int, "boolean": type(value) is bool, "null": value is None}
            if not any(matches.get(kind, False) for kind in types):
                refuse(pointer, f"expected {types}")
        if "const" in rule and value != rule["const"]:
            refuse(pointer, "constant differs")
        if "enum" in rule and not any(value == item and (type(value) is type(item) or _finite_number(value) and _finite_number(item)) for item in rule["enum"]):
            refuse(pointer, "invalid enum")
        if isinstance(value, str):
            if "pattern" in rule and re.search(rule["pattern"], value) is None:
                refuse(pointer, "pattern differs")
            if rule.get("format") == "date-time":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
                    refuse(pointer, "date-time requires an ISO timestamp and timezone")
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    refuse(pointer, "invalid calendar timestamp")
        if _finite_number(value):
            if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
                refuse(pointer, "number out of bounds")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                refuse(pointer, "object keys must be strings")
            if set(rule.get("required", [])) - set(value):
                refuse(pointer, "required fields missing")
            properties = rule.get("properties", {})
            additional = rule.get("additionalProperties", True)
            for key, child in value.items():
                child_pointer = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                child_rule = properties.get(key)
                if child_rule is None:
                    if additional is False:
                        refuse(child_pointer, "unknown field")
                    child_rule = additional if isinstance(additional, dict) else None
                if child_rule is not None:
                    walk(child, child_rule, child_pointer, depth + 1)
        elif isinstance(value, list) and "items" in rule:
            for index, child in enumerate(value):
                walk(child, rule["items"], f"{pointer}/{index}", depth + 1)

    walk(data, schema)


def _verify_staged_execution(stage: Path) -> str:
    """Run the public disk-based pipeline before publishing any workspace."""
    from compile_model import compile_workspace

    from .validator import validate_edges, validate_nodes

    nodes, node_issues = load_json_secure(stage / "nodes.json")
    edges, edge_issues = load_json_secure(stage / "edges.json")
    manifest, manifest_issues = load_json_secure(stage / "simulation-manifest.json")
    if (node_issues or edge_issues or manifest_issues
            or not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes)
            or not isinstance(edges, list) or not isinstance(manifest, dict)):
        raise ValueError("INVALID_STAGED_ARTIFACT")
    evidence_ids = {eid for node in nodes for eid in node.get("evidence_ids", [])}
    node_check, node_ids = validate_nodes(nodes, evidence_ids, manifest)
    edge_check, _ = validate_edges(edges, node_ids, evidence_ids,
        {n["id"]: n["type"] for n in nodes}, {n["id"]: n["scale"] for n in nodes})
    if node_check.errors or edge_check.errors:
        raise ValueError(f"STAGED_SCHEMA_FAILED: {[i.to_dict() for i in node_check.errors + edge_check.errors]}")

    expected = compile_workspace(stage)
    write_json_atomic(stage / "simulation-model.json", expected)
    scripts = Path(__file__).resolve().parents[1]
    for script in ("run_simulation.py", "replay_simulation.py"):
        completed = subprocess.run(
            [sys.executable, str(scripts / script), "--workspace", str(stage), "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if completed.returncode:
            raise ValueError(f"STAGED_EXECUTION_FAILED: {script}: {completed.stdout[-2000:]} {completed.stderr[-1000:]}")
    saved, problems = load_json_secure(stage / "simulation-model.json")
    replay, replay_problems = load_json_secure(stage / "replay-report.json")
    if problems or replay_problems or saved != expected or not isinstance(replay, dict) or replay.get("match") is not True:
        raise ValueError("STAGED_MODEL_MISMATCH: compile, disk execution and replay must agree")
    result_hash = expected.get("model_hash")
    if not isinstance(result_hash, str):
        raise ValueError("STAGED_MODEL_HASH_MISSING")
    return result_hash


def _publish_workspace(stage: Path, target: Path) -> None:
    """Publish once; restore an empty Windows target if rename fails."""
    removed_empty = False
    if sys.platform == "win32" and target.exists():
        target.rmdir()  # Fails safely if another writer populated the directory.
        removed_empty = True
    try:
        os.replace(stage, target)
    except OSError:
        if removed_empty and not os.path.lexists(target):
            target.mkdir()
        raise


def apply_model(
    proposal: dict[str, Any],
    review: dict[str, Any],
    target_dir: Path | str,
    *,
    workspace_id: str = "workspace:auto-causal-model",
) -> dict[str, Any]:
    """Export a draft or publish a reviewed, replayed model to a fresh workspace."""
    _validate_sidecar(proposal, "causal-proposal")
    _validate_sidecar(review, "causal-review")
    expected_hash = f"sha256:{canonical_hash(proposal)}"
    if review.get("proposal_hash") != expected_hash:
        raise ValueError("Review proposal_hash mismatch")
    if review.get("evidence_hash") != proposal.get("evidence_hash") or review.get("proposal_id") != proposal.get("proposal_id"):
        raise ValueError("Review evidence_hash or proposal_id mismatch")
    target = Path(os.path.abspath(target_dir))
    if path_contains_link_or_reparse(target):
        raise ValueError("REFUSED_LINKED_WORKSPACE")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        return {"ok": False, "error": "REFUSED_EXISTING_WORKSPACE: target must be new or empty",
                "workspace_dir": str(target), "compilation_ok": False, "simulation_run_ok": False}
    if review.get("overall_status") == "rejected":
        return {"ok": False, "error": "REJECTED_MODEL", "workspace_dir": str(target),
                "compilation_ok": False, "simulation_run_ok": False, "overall_status": "rejected"}
    if review.get("overall_status") not in {"approved", "partial"}:
        raise ValueError("INVALID_REVIEW_STATUS")

    node_decisions = review.get("node_decisions", {})
    edge_decisions = review.get("edge_decisions", {})
    admitted_edges = []
    for candidate in proposal.get("candidate_edges", []):
        decision = edge_decisions.get(candidate["id"], {})
        if decision.get("decision") not in {"admit", "revise"}:
            continue
        edge = {**candidate, **decision.get("modifications", {})}
        if any(node_decisions.get(edge[endpoint], {}).get("decision") not in {"admit", "revise"} for endpoint in ("from", "to")):
            continue
        if (isinstance(edge.get("sign"), bool) or edge.get("sign") not in (-1, 1)
                or not _finite_number(edge.get("effect_size"))
                or type(edge.get("lag_ticks")) is not int or edge["lag_ticks"] < 0
                or not isinstance(edge.get("mechanism"), str) or not edge["mechanism"].strip()):
            continue
        ticks = edge["lag_ticks"]
        compiled = {
            "id": edge["id"], "from": edge["from"], "to": edge["to"],
            "relation": edge.get("relation", "causes"), "sign": edge["sign"],
            "base_strength": abs(edge["effect_size"]),
            "evidence_confidence": edge.get("evidence_confidence"),
            "confidence": edge.get("evidence_confidence"), "mechanism": edge["mechanism"],
            "lag_ticks": ticks, "lag_unit": edge.get("lag_unit", "ticks"),
            "lag_distribution": edge.get("lag_distribution", {"type": "fixed", "fixed": f"P{ticks}D"}),
            "context_modifiers": edge.get("context_modifiers", []), "status": "admitted",
            "transform": edge.get("transform", "linear"),
            "effect_parameter": edge.get("effect_parameter", {"kind": "reference_value", "reference_value": abs(edge["effect_size"])}),
            "evidence": edge.get("evidence_ids", []),
        }
        for key in ("transform_parameters", "weight_distribution", "integration", "saturation", "existence_prob"):
            if key in edge:
                compiled[key] = edge[key]
        admitted_edges.append(compiled)
    ids = {e[k] for e in admitted_edges for k in ("from", "to")}
    admitted_nodes = []
    for candidate in proposal.get("candidate_nodes", []):
        if candidate["id"] not in ids:
            continue
        decision = node_decisions.get(candidate["id"], {})
        node = {**candidate, **decision.get("modifications", {})}
        compiled = {
            "id": node["id"], "type": "factor", "name": node["name"],
            "description": node.get("description", node.get("source_anchor", "")),
            "time": node.get("time"), "status": node.get("status"),
            "timeline": node.get("timeline", "observed_baseline"),
            "state_before": {"summary": "Reviewed baseline", "value": node.get("baseline")},
            "trigger": {"kind": "endogenous_propagation", "description": "Reviewed transmission path"},
            "mechanism": node.get("mechanism", node.get("source_anchor", "")),
            "state_after": {"summary": "Baseline before simulation", "value": node.get("baseline")},
            "lag": "P0D", "scale": node.get("scale", "level"), "unit": node.get("unit", ""),
            "baseline": node.get("baseline"), "confidence": node.get("confidence"),
            "evidence_ids": node.get("evidence_ids", []), "role": node.get("role", "endogenous"),
        }
        if node.get("scale") == "stock":
            compiled["retention"] = node.get("retention")
        for key in ("bounds", "decay_rate", "retention_distribution", "decay_rate_distribution"):
            if key in node:
                compiled[key] = node[key]
        admitted_nodes.append(compiled)
    if not admitted_edges or len(admitted_nodes) != len(ids):
        return {"ok": False, "error": "NO_COMPLETE_CAUSAL_GRAPH", "compilation_ok": False, "simulation_run_ok": False}
    approved = review["overall_status"] == "approved"
    runnable = approved and not proposal.get("gaps") and all(
        _finite_number(n.get("baseline")) and n.get("time") and n.get("unit")
        and n.get("status") != "assumption" and len(n["mechanism"].split()) >= 10
        and (n["scale"] != "stock" or _finite_number(n.get("retention"))) for n in admitted_nodes
    ) and all(len(e["mechanism"].split()) >= 10 for e in admitted_edges)
    if approved and (not runnable or not review.get("reviewer", "").startswith(("human:", "agent:")) or review.get("review_kind") != "explicit_decisions"):
        raise ValueError("INCOMPLETE_APPROVAL: explicit review and grounded node/edge parameters are required")
    manifest = {
        "schema_version": "2.1.0", "formula_version": FORMULA_VERSION, "simulation_id": workspace_id,
        "status": "initialized" if runnable else "draft", "created_at": review["reviewed_at"],
        "simulation_mode": "deterministic", "assurance_tier": "limited",
        "artifact_paths": {"nodes": "nodes.json", "edges": "edges.json",
            "causal_proposal": "causal-proposal.json", "causal_review": "causal-review.json",
            "assumptions": "assumptions.json", "computational_model": "simulation-model.json",
            "run_ledger": "simulation-run.json", "execution_trace": "execution-trace.json",
            "replay_report": "replay-report.json"},
    }
    node_filename = "nodes.json" if runnable else "candidate-nodes.json"
    edge_filename = "edges.json" if runnable else "candidate-edges.json"
    if not runnable:
        manifest["artifact_paths"] = {
            "candidate_nodes": node_filename, "candidate_edges": edge_filename,
            "causal_proposal": "causal-proposal.json", "causal_review": "causal-review.json",
            "assumptions": "assumptions.json",
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".aleph-apply-", dir=target.parent))
    verified_hash = None
    try:
        for name, value in {
            node_filename: admitted_nodes, edge_filename: admitted_edges,
            "causal-proposal.json": proposal, "causal-review.json": review,
            "simulation-manifest.json": manifest,
            "assumptions.json": {"schema_version": "1.0.0", "gaps": proposal.get("gaps", []),
                "assumptions": [n for n in proposal.get("candidate_nodes", []) if n.get("status") == "assumption"]},
        }.items():
            write_json_atomic(staged / name, value)
        if runnable:
            interventions = review.get("interventions", [])
            if not isinstance(interventions, list):
                raise ValueError("INVALID_REVIEW_INTERVENTIONS")
            write_json_atomic(staged / "interventions.json", interventions)
            write_json_atomic(staged / "simulation-config.json", {"mode": "deterministic", "seed": "42", "ticks": 5, "timestep": 1.0})
            verified_hash = _verify_staged_execution(staged)
        # One rename publishes the entire directory on the same filesystem.
        # Refuse a target populated by another writer during staging.
        if path_contains_link_or_reparse(target) or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
            raise ValueError("WORKSPACE_CHANGED_DURING_STAGING")
        _publish_workspace(staged, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return {"ok": True, "workspace_dir": str(target), "runnable": bool(runnable),
        "status": "ready" if runnable else "draft", "admitted_edge_count": len(admitted_edges),
        "admitted_node_count": len(admitted_nodes), "compilation_ok": bool(runnable),
        "simulation_run_ok": bool(runnable), "replay_ok": bool(runnable), "model_hash": verified_hash,
        "gaps_count": len(proposal.get("gaps", [])), "overall_status": review["overall_status"]}
