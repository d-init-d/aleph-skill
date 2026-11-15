"""Repair and normalize LLM/migrated Aleph nodes.json and edges.json into canonical schema form."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.io import (
    canonical_hash,
    load_json_secure,
    load_jsonl_secure,
    write_bytes_atomic,
    write_json_atomic,
)
from aleph.paths import resolve_in_workspace
from aleph.schema import (
    GATE_COMPARE_OPS,
    ID_PREFIXES,
    INTERVENTION_FIELDS,
    NODE_TYPES,
    has_id_prefix,
    parse_duration_seconds,
)
from aleph.validator import _resolve_workspace_timestep_days

_SIGN_MAP = {
    "+": 1,
    "pos": 1,
    "positive": 1,
    "increase": 1,
    "increases": 1,
    "-": -1,
    "neg": -1,
    "negative": -1,
    "decrease": -1,
    "decreases": -1,
}

_NEGATIVE_RELATIONS = frozenset(
    {
        "decreases",
        "decrease",
        "reduces",
        "reduce",
        "inhibits",
        "inhibit",
        "prevents",
        "prevent",
        "dampens",
        "dampen",
        "lowers",
        "lower",
        "suppresses",
        "suppress",
        "cuts",
        "cut",
        "erodes",
        "erode",
        "curbs",
        "curb",
        "slows",
        "slow",
    }
)

_RELATION_ALIASES = {
    "reduces": "decreases",
    "reduce": "decreases",
    "lowers": "decreases",
    "lower": "decreases",
    "suppresses": "decreases",
    "suppress": "decreases",
    "cuts": "decreases",
    "cut": "decreases",
    "erodes": "decreases",
    "erode": "decreases",
    "curbs": "decreases",
    "curb": "decreases",
    "raises": "increases",
    "raise": "increases",
    "boosts": "increases",
    "boost": "increases",
    "drives": "increases",
    "drive": "increases",
}


def _representative_stock_retention(node: dict[str, Any]) -> float | None:
    raw_ret = node.get("retention")
    raw_dec = node.get("decay_rate")
    if isinstance(raw_ret, (int, float)) and not isinstance(raw_ret, bool):
        return float(raw_ret)
    if isinstance(raw_ret, dict):
        for key in ("value", "mean", "mode"):
            val = raw_ret.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
    if isinstance(raw_dec, (int, float)) and not isinstance(raw_dec, bool) and float(raw_dec) >= 0:
        return math.exp(-float(raw_dec))
    if isinstance(raw_dec, dict):
        for key in ("value", "mean", "mode"):
            val = raw_dec.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and float(val) >= 0:
                return math.exp(-float(val))
    dist_ret = node.get("retention_distribution")
    if isinstance(dist_ret, dict):
        for key in ("value", "mean", "mode"):
            val = dist_ret.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return float(val)
    dist_dec = node.get("decay_rate_distribution")
    if isinstance(dist_dec, dict):
        for key in ("value", "mean", "mode"):
            val = dist_dec.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool) and float(val) >= 0:
                return math.exp(-float(val))
    return None


def _collect_plausibility_warnings(
    nodes: list[dict[str, Any]], *, effective_timestep_days: float = 1.0
) -> list[dict[str, Any]]:
    """Emit non-fatal sanity warnings for likely mis-scaled macro/node baselines."""
    warnings: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or "")
        name = str(node.get("name") or "")
        label = f"{nid} {name}".lower()
        unit = str(node.get("unit") or "").strip().lower()
        baseline = _finite_num(node.get("baseline"))
        scale = str(node.get("scale") or "level").strip().lower()

        if (
            "cpi" in label
            and not any(tok in f"{label} {unit}" for tok in ("%", "percent", "pct", "yoy", "mom", "rate", "change", "inflation"))
            and baseline is not None
            and baseline < 20.0
        ):
            warnings.append(
                {
                    "code": "PLAUSIBILITY_CPI_LOW",
                    "node_id": nid,
                    "baseline": baseline,
                    "message": f"CPI index node '{nid}' has baseline {baseline} < 20 (verify index level vs YoY %).",
                }
            )
        if unit in {"%", "percent", "pct", "percentage", "percentage_points", "pp"} and baseline is not None and (baseline < 0.0 or baseline > 50.0):
            warnings.append(
                {
                    "code": "PLAUSIBILITY_PCT_OUTLIER",
                    "node_id": nid,
                    "baseline": baseline,
                    "message": f"Percentage/rate node '{nid}' has baseline {baseline} outside [0, 50].",
                }
            )
        if (
            any(tok in label for tok in ("brent", "wti", "crude_oil", "crude oil"))
            and not any(tok in f"{label} {unit}" for tok in ("%", "percent", "pct", "change", "delta", "shock"))
            and baseline is not None
            and (baseline <= 0.0 or baseline > 500.0)
        ):
            warnings.append(
                {
                    "code": "PLAUSIBILITY_OIL_OUTLIER",
                    "node_id": nid,
                    "baseline": baseline,
                    "message": f"Crude oil price node '{nid}' has baseline {baseline} outside (0, 500].",
                }
            )
        if scale == "stock":
            has_retention_param = any(
                node.get(k) is not None
                for k in ("retention", "retention_distribution", "decay_rate", "decay_rate_distribution")
            )
            if not has_retention_param:
                warnings.append(
                    {
                        "code": "PLAUSIBILITY_STOCK_RETENTION_MISSING",
                        "node_id": nid,
                        "message": f"Stock node '{nid}' lacks explicit retention or decay_rate.",
                    }
                )
            elif "retention_unit" not in node and effective_timestep_days >= 7.0:
                rep_ret = _representative_stock_retention(node)
                collapse_note = ""
                if rep_ret is not None and 0.0 < rep_ret < 0.98:
                    per_tick = rep_ret ** effective_timestep_days
                    collapse_note = f" (retention={rep_ret:.4g} collapses to {per_tick:.4g} per tick over {effective_timestep_days:.4g}d)"
                warnings.append(
                    {
                        "code": "PLAUSIBILITY_STOCK_RETENTION_UNIT_IMPLICIT",
                        "node_id": nid,
                        "timestep_days": effective_timestep_days,
                        "retention": rep_ret,
                        "message": (
                            f"Stock node '{nid}' sets retention/decay without explicit retention_unit under "
                            f"timestep={effective_timestep_days:.4g}d (>=7d){collapse_note}; default 'per_day' "
                            "applies retention**dt and scales inflows by dt. Explicitly set 'retention_unit': 'per_tick' or 'per_day'."
                        ),
                    }
                )
    return warnings


def _finite_num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fval = float(value)
    return fval if math.isfinite(fval) else None


def _normalize_node_id(raw_id: str, idx: int, raw_type: Any, seen: set[str]) -> str:
    candidate = raw_id.strip()
    prefix = ""
    for allowed in ID_PREFIXES["node"]:
        if candidate.lower().startswith(allowed):
            prefix = allowed
            candidate = candidate[len(allowed) :]
            break
    if not prefix:
        type_norm = str(raw_type or "").strip().lower()
        prefix = f"{type_norm}:" if type_norm in NODE_TYPES else "factor:"
    suffix = re.sub(r"[^0-9a-zA-Z._/-]+", "_", candidate).strip("_.-/") or f"node-{idx + 1}"
    out_id = f"{prefix}{suffix}"
    counter = 2
    while out_id in seen:
        out_id = f"{prefix}{suffix}_{counter}"
        counter += 1
    seen.add(out_id)
    return out_id


_NODE_TYPE_ALIASES = {
    "auxiliary": "factor",
    "variable": "factor",
    "stock": "factor",
    "flow": "factor",
    "parameter": "factor",
    "driver": "factor",
    "outcome": "factor",
    "rate": "factor",
    "actor": "entity",
    "person": "entity",
    "organization": "entity",
    "org": "entity",
    "country": "entity",
    "state": "entity",
    "shock": "event",
    "action": "event",
    "decision": "event",
}

_EXTRA_NODE_SCALE_ALIASES = {
    "dimensionless": "level",
    "normalized": "level",
    "unitless": "level",
    "ratio": "level",
    "proportion": "level",
    "index": "level",
    "percent": "level",
    "percentage": "level",
    "score": "level",
    "probability": "level",
}

_GATE_OP_ALIASES = {
    "and": "all",
    "all": "all",
    "or": "any",
    "any": "any",
    "not": "veto",
    "block": "veto",
    "unless": "veto",
    "veto": "veto",
}

_GATE_COMPARE_ALIASES = {
    ">": "gt",
    "gt": "gt",
    ">=": "gte",
    "gte": "gte",
    "<": "lt",
    "lt": "lt",
    "<=": "lte",
    "lte": "lte",
}


def _prenormalize_node_items(items: list[Any], repairs: list[str]) -> list[Any]:
    normalized: list[Any] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            normalized.append(entry)
            continue
        node = dict(entry)
        tag = str(node.get("id") or node.get("node_id") or f"nodes[{idx}]").strip() or f"nodes[{idx}]"
        if "node_id" in node:
            raw_nid = node.pop("node_id")
            if not node.get("id") and isinstance(raw_nid, str) and raw_nid.strip():
                node["id"] = raw_nid.strip()
                repairs.append(f"nodes[{idx}]:id_from_node_id")
            else:
                repairs.append(f"{tag}:removed_node_id_alias")
        if "label" in node:
            raw_label = node.pop("label")
            if not node.get("name") and isinstance(raw_label, str) and raw_label.strip():
                node["name"] = raw_label.strip()
                repairs.append(f"{tag}:name_from_label")
            else:
                repairs.append(f"{tag}:removed_label_alias")
        if "kind" in node:
            raw_kind = node.pop("kind")
            if not node.get("type") and isinstance(raw_kind, str) and raw_kind.strip():
                node["type"] = raw_kind.strip().lower()
                repairs.append(f"{tag}:type_from_kind")
            else:
                repairs.append(f"{tag}:removed_kind_alias")
        raw_type = node.get("type")
        if isinstance(raw_type, str):
            norm_type = raw_type.strip().lower()
            if norm_type in _NODE_TYPE_ALIASES and norm_type not in NODE_TYPES:
                node["type"] = _NODE_TYPE_ALIASES[norm_type]
                repairs.append(f"{tag}:type_{norm_type}_to_{node['type']}")
            elif norm_type in NODE_TYPES and norm_type != raw_type:
                node["type"] = norm_type
        if "initial_state" in node:
            raw_init_state = node.pop("initial_state")
            coerced = _finite_num(raw_init_state)
            if node.get("initial_value") is None and coerced is not None:
                node["initial_value"] = coerced
                repairs.append(f"{tag}:initial_value_from_initial_state")
            else:
                repairs.append(f"{tag}:removed_initial_state_alias")
        raw_scale = node.get("scale")
        if isinstance(raw_scale, str):
            norm_scale = raw_scale.strip().lower()
            mapped_scale = _EXTRA_NODE_SCALE_ALIASES.get(norm_scale)
            if mapped_scale:
                node["scale"] = mapped_scale
                repairs.append(f"{tag}:scale_{norm_scale}_to_{mapped_scale}")
        normalized.append(node)
    return normalized


def _repair_edge_gate(
    edge: dict[str, Any],
    eid: str,
    node_id_map: dict[str, str] | None,
    repairs: list[str],
) -> None:
    gate = edge.get("gate")
    if not isinstance(gate, dict):
        return
    fixed_gate = dict(gate)
    raw_op = str(fixed_gate.get("op") or "").strip().lower()
    if raw_op in _GATE_OP_ALIASES:
        canon_op = _GATE_OP_ALIASES[raw_op]
        if canon_op != fixed_gate.get("op"):
            fixed_gate["op"] = canon_op
            repairs.append(f"{eid}:normalized_gate_op")
    conds = fixed_gate.get("conditions")
    if isinstance(conds, list):
        fixed_conds: list[Any] = []
        for idx, cond in enumerate(conds):
            if not isinstance(cond, dict):
                fixed_conds.append(cond)
                continue
            fc = dict(cond)
            node_ref = fc.get("node")
            if isinstance(node_ref, str):
                node_ref_s = node_ref.strip()
                if node_id_map and node_ref_s in node_id_map and node_id_map[node_ref_s] != node_ref_s:
                    fc["node"] = node_id_map[node_ref_s]
                    repairs.append(f"{eid}:remapped_gate_node_{idx}")
                elif node_ref_s != node_ref:
                    fc["node"] = node_ref_s
            cop = str(fc.get("op") or "").strip().lower()
            if cop in _GATE_COMPARE_ALIASES:
                canon_cop = _GATE_COMPARE_ALIASES[cop]
                if canon_cop != fc.get("op") and canon_cop in GATE_COMPARE_OPS:
                    fc["op"] = canon_cop
                    repairs.append(f"{eid}:normalized_gate_condition_op_{idx}")
            val = fc.get("value")
            if isinstance(val, str):
                try:
                    fval = float(val.strip())
                except ValueError:
                    fval = math.nan
                if math.isfinite(fval):
                    fc["value"] = fval
                    repairs.append(f"{eid}:coerced_gate_value_{idx}")
            elif isinstance(val, int) and not isinstance(val, bool):
                fc["value"] = float(val)
            fixed_conds.append(fc)
        fixed_gate["conditions"] = fixed_conds
    edge["gate"] = fixed_gate


def _prenormalize_intervention_items(items: list[Any], repairs: list[str]) -> list[Any]:
    normalized: list[Any] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            normalized.append(entry)
            continue
        iv = dict(entry)
        tag = str(iv.get("id") or iv.get("intervention_id") or f"interventions[{idx}]").strip() or f"interventions[{idx}]"
        if "intervention_id" in iv:
            raw_iid = iv.pop("intervention_id")
            if not iv.get("id") and isinstance(raw_iid, str) and raw_iid.strip():
                iv["id"] = raw_iid.strip()
                repairs.append(f"interventions[{idx}]:id_from_intervention_id")
            else:
                repairs.append(f"{tag}:removed_intervention_id_alias")
        if "target_node_id" in iv:
            raw_target = iv.pop("target_node_id")
            if not iv.get("target") and isinstance(raw_target, str) and raw_target.strip():
                iv["target"] = raw_target.strip()
                repairs.append(f"{tag}:target_from_target_node_id")
            else:
                repairs.append(f"{tag}:removed_target_node_id_alias")
        for val_alias in ("magnitude", "delta"):
            if val_alias in iv:
                raw_mag = iv.pop(val_alias)
                if iv.get("value") is None and _finite_num(raw_mag) is not None:
                    iv["value"] = float(raw_mag)
                    repairs.append(f"{tag}:value_from_{val_alias}")
                else:
                    repairs.append(f"{tag}:removed_{val_alias}_alias")
        if "mode" in iv:
            raw_mode = iv.pop("mode")
            norm_mode = str(raw_mode or "").strip().lower()
            if not iv.get("op"):
                if norm_mode in {"step", "delta", "additive", "pulse", "add"}:
                    iv["op"] = "add"
                    repairs.append(f"{tag}:op_add_from_mode_{norm_mode}")
                elif norm_mode in {"set", "override", "level"}:
                    iv["op"] = "set"
                    repairs.append(f"{tag}:op_set_from_mode_{norm_mode}")
                elif norm_mode in {"mul", "multiply", "scale", "factor"}:
                    iv["op"] = "multiply"
                    repairs.append(f"{tag}:op_multiply_from_mode_{norm_mode}")
            else:
                repairs.append(f"{tag}:removed_mode_alias")
            if norm_mode == "pulse" and iv.get("op") == "set" and iv.get("release_policy") is None:
                iv["release_policy"] = "reset_baseline"
                repairs.append(f"{tag}:release_policy_from_mode_pulse")
        if iv.get("start_tick") is None:
            iv["start_tick"] = 0
            repairs.append(f"{tag}:default_start_tick_0")
        allowed_keys = INTERVENTION_FIELDS | {"release_policy"}
        for extra_k in [k for k in list(iv.keys()) if k not in allowed_keys]:
            iv.pop(extra_k, None)
            repairs.append(f"{tag}:removed_extra_field_{extra_k}")
        normalized.append(iv)
    return normalized


def repair_nodes(
    raw: Any,
    *,
    id_map_out: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    repairs: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("nodes"), list):
        items = raw["nodes"]
        repairs.append("nodes:unwrapped_object_to_list")
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("nodes.json must be an array or an object containing a 'nodes' array")
    items = _prenormalize_node_items(items, repairs)

    out: list[dict[str, Any]] = []
    seen_nids: set[str] = set()
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(f"nodes[{idx}] must be an object")
        node = dict(entry)
        raw_nid = str(node.get("id") or "")
        stripped_nid = raw_nid.strip()
        if not stripped_nid:
            nid = _normalize_node_id(f"node-{idx + 1}", idx, node.get("type"), seen_nids)
            node["id"] = nid
            repairs.append(f"nodes[{idx}]:assigned_id")
        elif (
            not has_id_prefix(stripped_nid, "node")
            or bool(re.search(r"\s", stripped_nid))
            or stripped_nid in seen_nids
        ):
            was_duplicate = stripped_nid in seen_nids
            nid = _normalize_node_id(stripped_nid, idx, node.get("type"), seen_nids)
            node["id"] = nid
            repairs.append(f"nodes[{idx}]:normalized_id:{stripped_nid}->{nid}")
            if id_map_out is not None and not was_duplicate and raw_nid != nid:
                id_map_out.setdefault(raw_nid, nid)
                id_map_out.setdefault(stripped_nid, nid)
        else:
            nid = stripped_nid
            if nid != raw_nid:
                node["id"] = nid
                repairs.append(f"nodes[{idx}]:trimmed_id")
                if id_map_out is not None:
                    id_map_out.setdefault(raw_nid, nid)
            seen_nids.add(nid)

        if node.get("baseline") is None:
            init_num = _finite_num(node.get("initial_value"))
            if init_num is not None:
                node["baseline"] = init_num
                repairs.append(f"{nid}:baseline_from_initial_value")
            elif isinstance(node.get("state_before"), dict):
                sb_num = _finite_num(node["state_before"].get("value"))
                if sb_num is not None:
                    node["baseline"] = sb_num
                    repairs.append(f"{nid}:baseline_from_state_before")
        if "initial_value" in node:
            init_val = _finite_num(node.get("initial_value"))
            if init_val is not None:
                if isinstance(node.get("initial_value"), int) and not isinstance(node.get("initial_value"), bool):
                    node["initial_value"] = init_val
            else:
                node.pop("initial_value", None)
                repairs.append(f"{nid}:removed_invalid_initial_value")

        if "unit" not in node and isinstance(node.get("units"), str):
            node["unit"] = node["units"]
            repairs.append(f"{nid}:unit_from_units")
        if "units" in node:
            node.pop("units", None)
            repairs.append(f"{nid}:removed_units_alias")
        if "type" not in node:
            node["type"] = "factor"
            repairs.append(f"{nid}:default_type_factor")
        if "role" not in node:
            node["role"] = "endogenous"
            repairs.append(f"{nid}:default_role_endogenous")
        scale_aliases = {
            "delta": "flow",
            "change": "flow",
            "rate": "flow",
            "flow_rate": "flow",
            "binary": "level",
            "boolean": "level",
            "bool": "level",
            "dummy": "level",
            "indicator": "level",
            "nominal": "level",
            "ordinal": "level",
            "categorical": "level",
            "index": "level",
            "price": "level",
            "continuous": "level",
            "percentage": "level",
            "percent": "level",
            "state": "level",
        }
        if "scale" not in node:
            node["scale"] = "level"
            repairs.append(f"{nid}:default_scale_level")
        elif isinstance(node.get("scale"), str):
            raw_scale = node["scale"].strip().lower()
            mapped_scale = scale_aliases.get(raw_scale)
            if mapped_scale is not None:
                node["scale"] = mapped_scale
                repairs.append(f"{nid}:scale_{raw_scale}_to_{mapped_scale}")
            elif raw_scale in {"level", "flow", "stock"} and raw_scale != node["scale"]:
                node["scale"] = raw_scale
                repairs.append(f"{nid}:normalized_scale_case")
        out.append(node)
    return out, repairs


_EDGE_ID_RE = re.compile(r"^(causal|edge):[0-9a-zA-Z_-]+$")


def _normalize_edge_id(raw_id: str, idx: int, seen: set[str]) -> str:
    candidate = raw_id.strip()
    prefix = "edge"
    while True:
        lower = candidate.lower()
        if lower.startswith("causal:"):
            if prefix == "edge" and not candidate[7:].lower().startswith(("causal:", "edge:")):
                prefix = "causal"
            candidate = candidate[7:]
        elif lower.startswith("edge:"):
            candidate = candidate[5:]
        else:
            break
    if raw_id.strip().lower().startswith("causal:"):
        prefix = "causal"
    suffix = re.sub(r"[^0-9a-zA-Z_-]+", "_", candidate).strip("_") or f"auto-{idx + 1}"
    out_id = f"{prefix}:{suffix}"
    counter = 2
    while out_id in seen:
        out_id = f"{prefix}:{suffix}_{counter}"
        counter += 1
    seen.add(out_id)
    return out_id


def repair_edges(
    raw: Any,
    *,
    node_id_map: dict[str, str] | None = None,
    id_map_out: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    repairs: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("edges"), list):
        items = raw["edges"]
        repairs.append("edges:unwrapped_object_to_list")
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("edges.json must be an array or an object containing an 'edges' array")

    out: list[dict[str, Any]] = []
    seen_eids: set[str] = set()
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(f"edges[{idx}] must be an object")
        edge = dict(entry)
        if "edge_id" in edge:
            raw_edge_id = edge.pop("edge_id")
            if not edge.get("id") and isinstance(raw_edge_id, str) and raw_edge_id.strip():
                edge["id"] = raw_edge_id.strip()
                repairs.append(f"edges[{idx}]:id_from_edge_id")
            else:
                repairs.append(f"edges[{idx}]:removed_edge_id_alias")
        raw_eid = str(edge.get("id") or "")
        stripped_eid = raw_eid.strip()
        if not stripped_eid:
            eid = _normalize_edge_id(f"auto-{idx + 1}", idx, seen_eids)
            edge["id"] = eid
            repairs.append(f"edges[{idx}]:assigned_id")
        elif not _EDGE_ID_RE.match(stripped_eid) or stripped_eid in seen_eids:
            was_duplicate = stripped_eid in seen_eids
            eid = _normalize_edge_id(stripped_eid, idx, seen_eids)
            edge["id"] = eid
            repairs.append(f"edges[{idx}]:normalized_id:{stripped_eid}->{eid}")
            if id_map_out is not None and not was_duplicate and raw_eid != eid:
                id_map_out.setdefault(raw_eid, eid)
                id_map_out.setdefault(stripped_eid, eid)
        else:
            eid = stripped_eid
            if eid != raw_eid:
                edge["id"] = eid
                repairs.append(f"edges[{idx}]:trimmed_id")
                if id_map_out is not None:
                    id_map_out.setdefault(raw_eid, eid)
            seen_eids.add(eid)

        if not edge.get("from") and isinstance(edge.get("source"), str) and edge["source"].strip():
            edge["from"] = edge["source"].strip()
            repairs.append(f"{eid}:from_from_source")
        if "source" in edge:
            edge.pop("source", None)
            repairs.append(f"{eid}:removed_source_alias")
        if not edge.get("to") and isinstance(edge.get("target"), str) and edge["target"].strip():
            edge["to"] = edge["target"].strip()
            repairs.append(f"{eid}:to_from_target")
        if "target" in edge:
            edge.pop("target", None)
            repairs.append(f"{eid}:removed_target_alias")
        if node_id_map:
            for endpoint in ("from", "to"):
                ep_val = edge.get(endpoint)
                if isinstance(ep_val, str) and ep_val in node_id_map:
                    mapped_ep = node_id_map[ep_val]
                    if mapped_ep != ep_val:
                        edge[endpoint] = mapped_ep
                        repairs.append(f"{eid}:remapped_{endpoint}_{ep_val}->{mapped_ep}")

        if "polarity" in edge:
            raw_pol = edge.pop("polarity")
            if edge.get("sign") is None and isinstance(raw_pol, str):
                mapped_pol = _SIGN_MAP.get(raw_pol.strip().lower())
                if mapped_pol is not None:
                    edge["sign"] = mapped_pol
                    repairs.append(f"{eid}:sign_from_polarity")
                else:
                    repairs.append(f"{eid}:removed_polarity_alias")
            else:
                repairs.append(f"{eid}:removed_polarity_alias")
        if "scale" in edge:
            edge.pop("scale", None)
            repairs.append(f"{eid}:removed_edge_scale_field")
        if "parameters" in edge:
            raw_params = edge.pop("parameters")
            if edge.get("transform_parameters") is None and isinstance(raw_params, dict):
                edge["transform_parameters"] = raw_params
                repairs.append(f"{eid}:transform_parameters_from_parameters")
            else:
                repairs.append(f"{eid}:removed_parameters_alias")
        if "weight_distribution" in edge:
            raw_wd = edge.pop("weight_distribution")
            if edge.get("effect_distribution") is None and isinstance(raw_wd, dict):
                edge["effect_distribution"] = raw_wd
                repairs.append(f"{eid}:effect_distribution_from_weight_distribution")
            else:
                repairs.append(f"{eid}:removed_weight_distribution_alias")
        if isinstance(edge.get("transform"), str) and edge["transform"].strip().lower() in {
            "saturating_tanh",
            "saturating-tanh",
        }:
            edge["transform"] = "tanh"
            repairs.append(f"{eid}:normalized_saturating_tanh_to_tanh")

        raw_rel = edge.get("relation")
        if isinstance(raw_rel, str):
            rel_lower = raw_rel.strip().lower()
            if rel_lower in _RELATION_ALIASES:
                edge["relation"] = _RELATION_ALIASES[rel_lower]
                repairs.append(f"{eid}:normalized_relation_{edge['relation']}")

        raw_sign = edge.get("sign")
        if isinstance(raw_sign, str) and raw_sign.strip().lower() in _SIGN_MAP:
            edge["sign"] = _SIGN_MAP[raw_sign.strip().lower()]
            repairs.append(f"{eid}:normalized_sign")
        elif isinstance(raw_sign, (int, float)) and not isinstance(raw_sign, bool) and raw_sign in (-1, 1):
            edge["sign"] = int(raw_sign)
        else:
            neg_alias = any(
                (val := _finite_num(edge.get(alias))) is not None and val < 0.0
                for alias in ("base_strength", "strength", "weight", "effect_size", "magnitude")
            )
            rel_key = str(edge.get("relation") or "").strip().lower()
            if neg_alias:
                edge["sign"] = -1
                repairs.append(f"{eid}:inferred_sign_-1_from_magnitude")
            elif rel_key in _NEGATIVE_RELATIONS:
                edge["sign"] = -1
                repairs.append(f"{eid}:inferred_sign_-1_from_relation")
            else:
                edge["sign"] = 1
                repairs.append(f"{eid}:default_sign_1")

        bs_existing = _finite_num(edge.get("base_strength"))
        if bs_existing is not None and bs_existing < 0.0:
            edge["base_strength"] = abs(bs_existing)
            repairs.append(f"{eid}:abs_base_strength")
        elif edge.get("base_strength") is None:
            for alias in ("strength", "weight", "effect_size", "magnitude"):
                val = _finite_num(edge.get(alias))
                if val is not None:
                    edge["base_strength"] = abs(val)
                    repairs.append(f"{eid}:base_strength_from_{alias}")
                    break
        for alias in ("strength", "weight", "effect_size", "magnitude"):
            if alias in edge and edge.get("base_strength") is not None:
                edge.pop(alias, None)
                repairs.append(f"{eid}:removed_{alias}_alias")

        raw_lt = edge.get("lag_ticks")
        if isinstance(raw_lt, int) and not isinstance(raw_lt, bool) and raw_lt >= 0:
            pass
        else:
            candidate = raw_lt if raw_lt is not None else edge.get("lag")
            parsed_lag = False
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                edge["lag_ticks"] = int(candidate)
                if raw_lt is None:
                    repairs.append(f"{eid}:lag_ticks_from_lag")
                parsed_lag = True
            elif isinstance(candidate, float) and math.isfinite(candidate) and candidate >= 0:
                edge["lag_ticks"] = int(round(candidate))
                repairs.append(f"{eid}:normalized_lag_ticks_int")
                parsed_lag = True
            elif isinstance(candidate, str) and candidate.strip():
                lag_text = candidate.strip()
                if re.fullmatch(r"\d+(?:\.\d+)?", lag_text):
                    edge["lag_ticks"] = int(round(float(lag_text)))
                    repairs.append(f"{eid}:lag_ticks_from_string")
                    parsed_lag = True
                else:
                    seconds = parse_duration_seconds(lag_text)
                    if seconds is not None and seconds >= 0:
                        edge["lag_ticks"] = max(0, int(math.ceil(seconds / 86400.0)))
                        edge["lag_unit"] = "days"
                        if edge.get("lag_distribution") is None:
                            edge["lag_distribution"] = {"type": "fixed", "fixed": lag_text}
                        repairs.append(f"{eid}:lag_ticks_from_iso")
                        parsed_lag = True
            if not parsed_lag:
                lag_dist = edge.get("lag_distribution")
                if isinstance(lag_dist, dict) and isinstance(lag_dist.get("fixed"), str):
                    seconds = parse_duration_seconds(lag_dist["fixed"].strip())
                    if seconds is not None and seconds >= 0:
                        edge["lag_ticks"] = max(0, int(math.ceil(seconds / 86400.0)))
                        edge["lag_unit"] = "days"
                        repairs.append(f"{eid}:lag_ticks_from_lag_distribution")
                        parsed_lag = True
            if not parsed_lag:
                edge["lag_ticks"] = 0
                repairs.append(f"{eid}:default_lag_ticks_0")
        if "lag" in edge and edge.get("lag_ticks") is not None:
            edge.pop("lag", None)
            repairs.append(f"{eid}:removed_lag_alias")

        raw_tf = edge.get("transform")
        if not raw_tf:
            edge["transform"] = "linear"
            repairs.append(f"{eid}:default_transform_linear")
        elif isinstance(raw_tf, str):
            norm_tf = raw_tf.strip().lower()
            if norm_tf in {"tanh", "saturation"}:
                edge["transform"] = "linear"
                if _finite_num(edge.get("saturation")) is None:
                    edge["saturation"] = 1.0
                repairs.append(f"{eid}:transform_{norm_tf}_to_saturated_linear")
            elif norm_tf in {"sigmoid", "s-curve", "logit"}:
                edge["transform"] = "logistic"
                repairs.append(f"{eid}:transform_{norm_tf}_to_logistic")
            elif norm_tf in {"step", "step_function", "piecewise", "heaviside"}:
                edge["transform"] = "threshold"
                repairs.append(f"{eid}:transform_{norm_tf}_to_threshold")
            elif norm_tf in {"linear", "elasticity", "identity", "logistic", "threshold"} and norm_tf != raw_tf:
                edge["transform"] = norm_tf
                repairs.append(f"{eid}:normalized_transform_case")
        _repair_edge_gate(edge, eid, node_id_map, repairs)
        if edge.get("relation") is None and edge.get("sign") in (-1, 1):
            edge["relation"] = "decreases" if edge["sign"] == -1 else "increases"
            repairs.append(f"{eid}:default_relation_{edge['relation']}_from_sign")
        out.append(edge)
    return out, repairs


def repair_interventions(
    raw: Any,
    node_scales: dict[str, str] | None = None,
    *,
    node_id_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    repairs: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("interventions"), list):
        items = raw["interventions"]
        repairs.append("interventions:unwrapped_object_to_list")
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("interventions.json must be an array or an object containing an 'interventions' array")
    items = _prenormalize_intervention_items(items, repairs)

    scales = node_scales or {}
    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(f"interventions[{idx}] must be an object")
        iv = dict(entry)
        iid = str(iv.get("id") or f"intervention:auto-{idx + 1}")
        if not iv.get("id"):
            iv["id"] = iid
            repairs.append(f"interventions[{idx}]:assigned_id")
        if node_id_map and isinstance(iv.get("target"), str) and iv["target"] in node_id_map:
            old_target = iv["target"]
            new_target = node_id_map[old_target]
            if new_target != old_target:
                iv["target"] = new_target
                repairs.append(f"{iid}:remapped_target_{old_target}->{new_target}")

        raw_op = iv.get("op")
        if isinstance(raw_op, str):
            norm_op = raw_op.strip().lower()
            if norm_op == "mul":
                iv["op"] = "multiply"
                repairs.append(f"{iid}:op_mul_to_multiply")
            elif norm_op in {"set", "add", "multiply"} and norm_op != raw_op:
                iv["op"] = norm_op
                repairs.append(f"{iid}:normalized_op_case")

        for tick_key in ("start_tick", "end_tick"):
            val = iv.get(tick_key)
            if isinstance(val, float) and not isinstance(val, bool) and math.isfinite(val) and val.is_integer():
                iv[tick_key] = int(val)
                repairs.append(f"{iid}:coerced_{tick_key}_int")
        if (
            isinstance(iv.get("start_tick"), int)
            and not isinstance(iv.get("start_tick"), bool)
            and isinstance(iv.get("end_tick"), int)
            and not isinstance(iv.get("end_tick"), bool)
            and iv["end_tick"] == iv["start_tick"]
        ):
            iv["end_tick"] = iv["start_tick"] + 1
            repairs.append(f"{iid}:end_tick_eq_start_tick_to_single_tick_pulse")

        rp = iv.get("release_policy")
        if isinstance(rp, str):
            norm_rp = rp.strip().lower()
            target_scale = scales.get(str(iv.get("target") or ""), "level")
            if norm_rp == "decay":
                iv["release_policy"] = "retain"
                repairs.append(f"{iid}:release_policy_decay_to_retain")
            elif norm_rp == "reset_baseline" and (iv.get("op") != "set" or target_scale != "stock"):
                iv["release_policy"] = "retain"
                repairs.append(f"{iid}:release_policy_invalid_reset_baseline_to_retain")
            elif norm_rp in {"retain", "reset_baseline"} and norm_rp != rp:
                iv["release_policy"] = norm_rp
                repairs.append(f"{iid}:normalized_release_policy_case")
        out.append(iv)
    return out, repairs


def _propagate_id_references(
    ws: Path,
    target_dir: Path,
    *,
    artifact_paths: dict[str, Any],
    node_id_map: dict[str, str],
    edge_id_map: dict[str, str],
    dry_run: bool,
    repairs: list[str],
    skip_propagation_trace: bool = False,
) -> None:
    """Rewrite normalized node/edge IDs across sibling workspace artifacts."""
    if not node_id_map and not edge_id_map:
        return

    def _resolve_rel(key: str, default: str) -> str:
        val = artifact_paths.get(key)
        return str(val).strip() if isinstance(val, str) and val.strip() else default

    def _resolve_rw(rel: str) -> tuple[Path | None, Path | None]:
        src, iss = resolve_in_workspace(ws, rel, must_exist=False, require_file=True)
        if iss or src is None or not src.is_file():
            return None, None
        dst, dst_iss = resolve_in_workspace(target_dir, rel, must_exist=False, require_file=False)
        if dst_iss or dst is None:
            return None, None
        return src, dst

    # 1) branch-ledger.json: causal_trace (edge/node IDs), leading_indicators[*].node, end_state keys
    bl_rel = _resolve_rel("branch_ledger", "branch-ledger.json")
    bl_src, bl_dst = _resolve_rw(bl_rel)
    if bl_src is not None and bl_dst is not None:
        bl_doc, bl_iss = load_json_secure(bl_src)
        if not bl_iss and isinstance(bl_doc, dict) and isinstance(bl_doc.get("branches"), list):
            bl_changed = False
            for b_idx, branch in enumerate(bl_doc["branches"]):
                if not isinstance(branch, dict):
                    continue
                trace = branch.get("causal_trace")
                if isinstance(trace, list):
                    for t_idx, item in enumerate(trace):
                        if isinstance(item, str):
                            mapped = edge_id_map.get(item) or node_id_map.get(item)
                            if mapped and mapped != item:
                                trace[t_idx] = mapped
                                bl_changed = True
                                repairs.append(f"crossref:{bl_rel}:branches[{b_idx}].causal_trace[{t_idx}]:{item}->{mapped}")
                indicators = branch.get("leading_indicators")
                if isinstance(indicators, list):
                    for i_idx, ind in enumerate(indicators):
                        if isinstance(ind, dict) and isinstance(ind.get("node"), str):
                            old_ind = ind["node"]
                            if old_ind in node_id_map and node_id_map[old_ind] != old_ind:
                                ind["node"] = node_id_map[old_ind]
                                bl_changed = True
                                repairs.append(
                                    f"crossref:{bl_rel}:branches[{b_idx}].leading_indicators[{i_idx}].node:{old_ind}->{node_id_map[old_ind]}"
                                )
                        elif isinstance(ind, str) and ind in node_id_map and node_id_map[ind] != ind:
                            indicators[i_idx] = node_id_map[ind]
                            bl_changed = True
                            repairs.append(f"crossref:{bl_rel}:branches[{b_idx}].leading_indicators[{i_idx}]:{ind}->{node_id_map[ind]}")
                end_state = branch.get("end_state")
                if isinstance(end_state, dict) and node_id_map:
                    for key in list(end_state.keys()):
                        if key in node_id_map and node_id_map[key] != key and node_id_map[key] not in end_state:
                            end_state[node_id_map[key]] = end_state.pop(key)
                            bl_changed = True
                            repairs.append(f"crossref:{bl_rel}:branches[{b_idx}].end_state:{key}->{node_id_map[key]}")
            if bl_changed and not dry_run:
                write_json_atomic(bl_dst, bl_doc)

    # 2) actors.json: person_node
    act_rel = _resolve_rel("actors", "actors.json")
    act_src, act_dst = _resolve_rw(act_rel)
    if act_src is not None and act_dst is not None and node_id_map:
        act_doc, act_iss = load_json_secure(act_src)
        if not act_iss and isinstance(act_doc, list):
            act_changed = False
            for a_idx, actor in enumerate(act_doc):
                if isinstance(actor, dict) and isinstance(actor.get("person_node"), str):
                    old_pn = actor["person_node"]
                    if old_pn in node_id_map and node_id_map[old_pn] != old_pn:
                        actor["person_node"] = node_id_map[old_pn]
                        act_changed = True
                        repairs.append(f"crossref:{act_rel}:actors[{a_idx}].person_node:{old_pn}->{node_id_map[old_pn]}")
            if act_changed and not dry_run:
                write_json_atomic(act_dst, act_doc)

    # 3) propagation-trace.jsonl: edge_id, from, to (+ recompute hash_chain)
    if not skip_propagation_trace:
        pt_rel = _resolve_rel("propagation_trace", "propagation-trace.jsonl")
        pt_src, pt_dst = _resolve_rw(pt_rel)
        if pt_src is not None and pt_dst is not None:
            rows, pt_iss = load_jsonl_secure(pt_src)
            if not pt_iss and rows:
                pt_changed = False
                for r_idx, row in enumerate(rows):
                    if not isinstance(row, dict):
                        continue
                    old_eid = row.get("edge_id")
                    if isinstance(old_eid, str) and old_eid in edge_id_map and edge_id_map[old_eid] != old_eid:
                        row["edge_id"] = edge_id_map[old_eid]
                        pt_changed = True
                        repairs.append(f"crossref:{pt_rel}:row[{r_idx}].edge_id:{old_eid}->{edge_id_map[old_eid]}")
                    for ep in ("from", "to"):
                        old_ep = row.get(ep)
                        if isinstance(old_ep, str) and old_ep in node_id_map and node_id_map[old_ep] != old_ep:
                            row[ep] = node_id_map[old_ep]
                            pt_changed = True
                            repairs.append(f"crossref:{pt_rel}:row[{r_idx}].{ep}:{old_ep}->{node_id_map[old_ep]}")
                if pt_changed and not dry_run:
                    prev_hash: str | None = None
                    out_lines: list[str] = []
                    for row in rows:
                        row_clean = dict(row)
                        row_clean.pop("hash_chain", None)
                        digest = canonical_hash({"previous_hash": prev_hash, "row": row_clean})
                        row_clean["hash_chain"] = digest
                        prev_hash = digest
                        out_lines.append(json.dumps(row_clean, ensure_ascii=False))
                    write_bytes_atomic(pt_dst, ("\n".join(out_lines) + "\n").encode("utf-8"))

    # 4) sensitivity-config.json (optional): target_nodes / nodes / edges
    sc_src, sc_dst = _resolve_rw("sensitivity-config.json")
    if sc_src is not None and sc_dst is not None:
        sc_doc, sc_iss = load_json_secure(sc_src)
        if not sc_iss and isinstance(sc_doc, dict):
            sc_changed = False
            for key, mapping in (("target_nodes", node_id_map), ("nodes", node_id_map), ("edges", edge_id_map)):
                seq = sc_doc.get(key)
                if isinstance(seq, list) and mapping:
                    for idx, val in enumerate(seq):
                        if isinstance(val, str) and val in mapping and mapping[val] != val:
                            seq[idx] = mapping[val]
                            sc_changed = True
                            repairs.append(f"crossref:sensitivity-config.json:{key}[{idx}]:{val}->{mapping[val]}")
            if sc_changed and not dry_run:
                write_json_atomic(sc_dst, sc_doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--in-place", action="store_true", help="Overwrite nodes.json and edges.json inside workspace")
    parser.add_argument("--out", type=Path, default=None, help="Output directory when not using --in-place")
    parser.add_argument("--dry-run", action="store_true", help="Report planned repairs and warnings without writing files (default when neither --in-place nor --out is passed)")
    parser.add_argument("--json", action="store_true", help="Print structured JSON summary")
    args = parser.parse_args(argv)

    ws = args.workspace.resolve()
    if not ws.is_dir():
        print(json.dumps({"ok": False, "error": f"workspace not found: {ws}"}), flush=True)
        return EXIT_USAGE
    dry_run = bool(args.dry_run or (not args.in_place and args.out is None))
    target_dir = ws if (dry_run or args.in_place) else args.out.resolve()
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    repairs: list[str] = []
    warnings: list[dict[str, Any]] = []
    nodes_count = 0
    edges_count = 0
    interventions_count = 0
    node_scales: dict[str, str] = {}
    node_id_map: dict[str, str] = {}
    edge_id_map: dict[str, str] = {}
    fixed_nodes: list[dict[str, Any]] = []
    fixed_ivs: list[dict[str, Any]] = []

    mf_data: dict[str, Any] | None = None
    mf_path, mf_issues = resolve_in_workspace(ws, "simulation-manifest.json", must_exist=False)
    if not mf_issues and mf_path is not None and mf_path.is_file():
        loaded_mf, mf_load_issues = load_json_secure(mf_path)
        if not mf_load_issues and isinstance(loaded_mf, dict):
            mf_data = loaded_mf

    effective_timestep_days = _resolve_workspace_timestep_days(ws, mf_data) if isinstance(mf_data, dict) else 1.0
    artifact_paths = dict(mf_data.get("artifact_paths") or {}) if isinstance(mf_data, dict) else {}

    def _artifact_rel(key: str, default: str) -> str:
        val = artifact_paths.get(key)
        return str(val).strip() if isinstance(val, str) and val.strip() else default

    nodes_rel = _artifact_rel("nodes", "nodes.json")
    nodes_path, nodes_issues = resolve_in_workspace(ws, nodes_rel, must_exist=False)
    if not nodes_issues and nodes_path is not None and nodes_path.is_file():
        raw_nodes, load_issues = load_json_secure(nodes_path)
        if load_issues:
            print(json.dumps({"ok": False, "error": load_issues[0].message}), flush=True)
            return EXIT_SEMANTIC
        fixed_nodes, node_repairs = repair_nodes(raw_nodes, id_map_out=node_id_map)
        repairs.extend(node_repairs)
        warnings.extend(_collect_plausibility_warnings(fixed_nodes, effective_timestep_days=effective_timestep_days))
        nodes_count = len(fixed_nodes)
        node_scales = {str(n.get("id")): str(n.get("scale", "level")) for n in fixed_nodes if isinstance(n, dict) and n.get("id")}
        if not dry_run:
            write_json_atomic(target_dir / nodes_rel, fixed_nodes)

    edges_rel = _artifact_rel("edges", "edges.json")
    edges_path, edges_issues = resolve_in_workspace(ws, edges_rel, must_exist=False)
    if not edges_issues and edges_path is not None and edges_path.is_file():
        raw_edges, load_issues = load_json_secure(edges_path)
        if load_issues:
            print(json.dumps({"ok": False, "error": load_issues[0].message}), flush=True)
            return EXIT_SEMANTIC
        fixed_edges, edge_repairs = repair_edges(raw_edges, node_id_map=node_id_map, id_map_out=edge_id_map)
        repairs.extend(edge_repairs)
        edges_count = len(fixed_edges)
        if not dry_run:
            write_json_atomic(target_dir / edges_rel, fixed_edges)

    iv_rel = _artifact_rel("interventions", "interventions.json")
    iv_path, iv_issues = resolve_in_workspace(ws, iv_rel, must_exist=False)
    if not iv_issues and iv_path is not None and iv_path.is_file():
        raw_ivs, load_issues = load_json_secure(iv_path)
        if load_issues:
            print(json.dumps({"ok": False, "error": load_issues[0].message}), flush=True)
            return EXIT_SEMANTIC
        fixed_ivs, iv_repairs = repair_interventions(raw_ivs, node_scales=node_scales, node_id_map=node_id_map)
        repairs.extend(iv_repairs)
        interventions_count = len(fixed_ivs)
        if not dry_run:
            write_json_atomic(target_dir / iv_rel, fixed_ivs)

    skip_stub_prop = False
    if isinstance(mf_data, dict):
        mf_changed = False
        cp = mf_data.get("change_point")
        if isinstance(cp, dict):
            old_cp_target = cp.get("target")
            if isinstance(old_cp_target, str) and old_cp_target in node_id_map and node_id_map[old_cp_target] != old_cp_target:
                cp["target"] = node_id_map[old_cp_target]
                mf_changed = True
                repairs.append(f"crossref:simulation-manifest.json:change_point.target:{old_cp_target}->{node_id_map[old_cp_target]}")
            elif not dry_run and args.in_place and fixed_nodes and "factor:example" not in node_scales and cp.get("target") == "factor:example":
                iv_targets = [
                    str(iv.get("target"))
                    for iv in fixed_ivs
                    if isinstance(iv, dict) and str(iv.get("target") or "") in node_scales
                ]
                factor_nodes = [nid for nid in node_scales if nid.startswith("factor:")]
                new_target = (
                    iv_targets[0]
                    if iv_targets
                    else (factor_nodes[0] if factor_nodes else next(iter(node_scales)))
                )
                cp["target"] = new_target
                if fixed_ivs:
                    cp["magnitude"] = 0.0
                mf_changed = True
                repairs.append(f"manifest:reanchored_change_point_target_to_{new_target}")
        tf = mf_data.get("temporal_frame")
        if isinstance(tf, dict) and isinstance(tf.get("monitoring_indicators"), list) and node_id_map:
            mi = tf["monitoring_indicators"]
            for idx, val in enumerate(mi):
                if isinstance(val, str) and val in node_id_map and node_id_map[val] != val:
                    mi[idx] = node_id_map[val]
                    mf_changed = True
                    repairs.append(f"crossref:simulation-manifest.json:temporal_frame.monitoring_indicators[{idx}]:{val}->{node_id_map[val]}")
        if not dry_run and args.in_place and mf_data.get("simulation_mode") in {"deterministic", "monte_carlo"}:
            ap = mf_data.get("artifact_paths")
            if isinstance(ap, dict) and isinstance(ap.get("propagation_trace"), str) and ap["propagation_trace"].strip():
                prop_rel = ap["propagation_trace"].strip()
                prop_path, prop_iss = resolve_in_workspace(ws, prop_rel, must_exist=False, require_file=True)

                def _is_stub_trace(candidate: Path | None) -> bool:
                    if candidate is None or not candidate.is_file():
                        return True
                    if candidate.stat().st_size == 0:
                        return True
                    lines = [
                        ln.strip()
                        for ln in candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip()
                    ]
                    if not lines:
                        return True
                    if len(lines) == 1:
                        try:
                            row = json.loads(lines[0])
                        except ValueError:
                            return False
                        if isinstance(row, dict) and row.get("edge_id") == "causal:example-increases-target":
                            return True
                    return False

                if not prop_iss and _is_stub_trace(prop_path):
                    ap.pop("propagation_trace", None)
                    if not str(ap.get("execution_trace") or "").strip():
                        ap["execution_trace"] = "execution-trace.json"
                    if prop_path is not None and prop_path.is_file():
                        prop_path.unlink(missing_ok=True)
                    mf_changed = True
                    skip_stub_prop = True
                    repairs.append("manifest:migrated_template_propagation_trace_to_execution_trace")
        if mf_changed and not dry_run:
            mf_dst, mf_dst_iss = resolve_in_workspace(target_dir, "simulation-manifest.json", must_exist=False, require_file=False)
            if not mf_dst_iss and mf_dst is not None:
                write_json_atomic(mf_dst, mf_data)

    _propagate_id_references(
        ws,
        target_dir,
        artifact_paths=artifact_paths,
        node_id_map=node_id_map,
        edge_id_map=edge_id_map,
        dry_run=dry_run,
        repairs=repairs,
        skip_propagation_trace=skip_stub_prop,
    )

    payload = {
        "ok": True,
        "dry_run": dry_run,
        "workspace": str(ws),
        "target_dir": str(target_dir),
        "nodes_count": nodes_count,
        "edges_count": edges_count,
        "interventions_count": interventions_count,
        "repair_count": len(repairs),
        "repairs": repairs,
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
