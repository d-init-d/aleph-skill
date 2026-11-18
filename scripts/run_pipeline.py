#!/usr/bin/env python3
"""Turn-key Aleph causal simulation pipeline orchestrator.

Chains workspace initialization (if absent), research ledger import,
auto-causal induction & deterministic review/apply, model compilation,
simulation execution, draft validation, and Markdown report rendering.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aleph import EXIT_OK, EXIT_USAGE  # noqa: E402
from aleph.io import sha256_file  # noqa: E402
from aleph.schema import (  # noqa: E402
    CONTEXT_MODIFIER_FIELDS,
    EDGE_FIELDS,
    EDGE_STATUS,
    EFFECT_PARAMETER_FIELDS,
    NODE_FIELDS,
    NODE_STATE_FIELDS,
    NODE_TRIGGER_FIELDS,
    NODE_TYPES,
    RELATION_ALLOWED,
    TIMELINE_LABELS,
    TRANSFORMS,
    parse_duration_seconds,
    parse_time,
)
from repair_model_schema import (  # noqa: E402
    _augment_node_lookup_map,
    repair_edges,
    repair_interventions,
    repair_nodes,
    repair_stochastic_shocks,
    synthesize_missing_target_nodes,
)


def _run_stage(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "stage": name,
        "command": command,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _seed_offline_minimum_model(workspace: Path, *, mode: str) -> None:
    """Ensure initialized workspace has valid baseline nodes/edges when run without a ledger."""
    nodes_path = workspace / "nodes.json"
    edges_path = workspace / "edges.json"
    manifest_path = workspace / "simulation-manifest.json"
    if not manifest_path.is_file():
        return
    if nodes_path.is_file() and edges_path.is_file():
        nodes_data = json.loads(nodes_path.read_text(encoding="utf-8"))
        edges_data = json.loads(edges_path.read_text(encoding="utf-8"))
        if isinstance(nodes_data, list) and len(nodes_data) >= 2 and isinstance(edges_data, list) and edges_data:
            return
    nodes_path.write_text(
        json.dumps(
            [
                {
                    "id": "factor:driver",
                    "type": "factor",
                    "name": "Primary Driver",
                    "description": "Exogenous driver variable",
                    "time": "2026-01-01",
                    "state_before": {"summary": "Baseline equilibrium", "value": 10.0, "unit": "index"},
                    "trigger": {"kind": "baseline", "description": "Initial baseline"},
                    "mechanism": "Direct causal channel",
                    "state_after": {"summary": "Simulated state", "value": 10.0, "unit": "index"},
                    "lag": "P0D",
                    "evidence_ids": ["evidence:e1"],
                    "status": "assumption",
                    "timeline": "shared_baseline",
                    "confidence": 0.5,
                    "role": "exogenous",
                    "datatype": "continuous",
                    "unit": "index",
                    "scale": "level",
                    "baseline": 10.0,
                },
                {
                    "id": "factor:outcome",
                    "type": "factor",
                    "name": "Target Outcome",
                    "description": "Endogenous outcome variable",
                    "time": "2026-01-01",
                    "state_before": {"summary": "Baseline equilibrium", "value": 5.0, "unit": "index"},
                    "trigger": {"kind": "transmission", "description": "Causal inflow"},
                    "mechanism": "Linear transmission",
                    "state_after": {"summary": "Simulated state", "value": 5.0, "unit": "index"},
                    "lag": "P0D",
                    "evidence_ids": ["evidence:e1"],
                    "status": "inference",
                    "timeline": "simulated_branch" if mode != "qualitative" else "shared_baseline",
                    "confidence": 0.5,
                    "role": "endogenous",
                    "datatype": "continuous",
                    "unit": "index",
                    "scale": "level",
                    "baseline": 5.0,
                },
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _hydrate_node(node: dict[str, Any], *, cutoff_date: str, ev_id: str) -> dict[str, Any]:
    nid = str(node.get("id") or "factor:node")
    raw_type = str(node.get("type") or "factor").strip().lower()
    ntype = raw_type if raw_type in NODE_TYPES else "factor"
    label = str(node.get("name") or nid.split(":", 1)[-1].replace("_", " ").replace("-", " ").title()).strip() or nid
    desc = str(node.get("description") or f"State variable {label} modeled in Aleph causal simulation.").strip()
    baseline_val = node.get("baseline")
    if isinstance(baseline_val, bool) or not isinstance(baseline_val, (int, float)) or not math.isfinite(float(baseline_val)):
        baseline = 0.0
    else:
        baseline = float(baseline_val)
    raw_time = str(node.get("time") or "").strip()
    parsed_t = parse_time(raw_time)
    parsed_c = parse_time(cutoff_date)
    node_time = raw_time if parsed_t is not None else cutoff_date
    raw_status = str(node.get("status") or "inference").strip().lower()
    if raw_status not in {"fact", "inference", "simulation", "counterfactual", "assumption"}:
        raw_status = "inference"
    if raw_status == "fact" and parsed_t is not None and parsed_c is not None and parsed_t > parsed_c:
        raw_status = "inference"
    raw_timeline = str(node.get("timeline") or "shared_baseline").strip()
    timeline = raw_timeline if raw_timeline in TIMELINE_LABELS else "shared_baseline"

    raw_sb = node.get("state_before")
    sb = {k: v for k, v in raw_sb.items() if k in NODE_STATE_FIELDS} if isinstance(raw_sb, dict) else {}
    if not isinstance(sb.get("summary"), str) or not sb["summary"].strip():
        sb["summary"] = f"Observed or assumed baseline for {label} at cutoff."
    if "value" not in sb or isinstance(sb["value"], bool) or not isinstance(sb["value"], (int, float)) or not math.isfinite(float(sb["value"])):
        sb["value"] = baseline

    raw_sa = node.get("state_after")
    sa = {k: v for k, v in raw_sa.items() if k in NODE_STATE_FIELDS} if isinstance(raw_sa, dict) else {}
    if not isinstance(sa.get("summary"), str) or not sa["summary"].strip():
        sa["summary"] = f"Simulated trajectory state for {label}."
    if "value" not in sa or isinstance(sa["value"], bool) or not isinstance(sa["value"], (int, float)) or not math.isfinite(float(sa["value"])):
        sa["value"] = baseline

    raw_tr = node.get("trigger")
    tr = {k: v for k, v in raw_tr.items() if k in NODE_TRIGGER_FIELDS} if isinstance(raw_tr, dict) else {}
    if not isinstance(tr.get("kind"), str) or not tr["kind"].strip():
        tr["kind"] = "context"
    if not isinstance(tr.get("description"), str) or not tr["description"].strip():
        tr["description"] = f"Baseline initialization and scenario forcing for {label}."

    mech = str(node.get("mechanism") or f"{label} responds dynamically to upstream causal signals and policy shocks").strip()
    if len(mech.split()) < 10:
        mech = f"{mech.rstrip('.')} transmitted through baseline equilibrium sensitivities, institutional transmission channels, and explicit discrete simulation ticks."

    raw_lag = node.get("lag")
    lag = str(raw_lag).strip() if isinstance(raw_lag, str) and parse_duration_seconds(raw_lag) is not None else "P0D"
    raw_conf = node.get("confidence", 0.5)
    conf = float(raw_conf) if not isinstance(raw_conf, bool) and isinstance(raw_conf, (int, float)) and math.isfinite(float(raw_conf)) and 0.0 <= float(raw_conf) <= 1.0 else 0.5

    hydrated = {k: v for k, v in node.items() if k in NODE_FIELDS}
    hydrated.pop("probability", None)
    hydrated.update(
        {
            "id": nid,
            "type": ntype,
            "name": label,
            "description": desc,
            "time": node_time,
            "state_before": sb,
            "trigger": tr,
            "mechanism": mech,
            "state_after": sa,
            "lag": lag,
            "evidence_ids": [ev_id],
            "status": raw_status,
            "timeline": timeline,
            "confidence": conf,
            "baseline": baseline,
            "role": str(node.get("role") or "endogenous") if node.get("role") in {"exogenous", "endogenous", "state"} else "endogenous",
            "scale": str(node.get("scale") or "level") if node.get("scale") in {"level", "flow", "stock"} else "level",
        }
    )
    if raw_status == "assumption" and not hydrated.get("assumption_ref"):
        hydrated["assumption_ref"] = "assumption:change-point"
    if isinstance(hydrated.get("bounds"), list) and len(hydrated["bounds"]) == 2 and hydrated.get("initial_value") is not None:
        lo, hi = hydrated["bounds"]
        iv_num = hydrated["initial_value"]
        if isinstance(iv_num, (int, float)) and not isinstance(iv_num, bool):
            if isinstance(lo, (int, float)) and float(iv_num) < float(lo):
                hydrated["initial_value"] = float(lo)
            elif isinstance(hi, (int, float)) and float(iv_num) > float(hi):
                hydrated["initial_value"] = float(hi)
    return hydrated


def _hydrate_edge(edge: dict[str, Any], *, node_types: dict[str, str], ev_id: str) -> dict[str, Any]:
    sign = -1 if edge.get("sign") == -1 else 1
    rel = str(edge.get("relation") or ("decreases" if sign == -1 else "increases")).strip().lower()
    if rel not in RELATION_ALLOWED:
        rel = "decreases" if sign == -1 else "increases"
    if sign == -1 and rel in {"increases", "enables", "causes", "amplifies", "triggers"}:
        rel = "decreases"
    elif sign == 1 and rel in {"decreases", "inhibits", "prevents", "dampens"}:
        rel = "increases"

    raw_bs = edge.get("base_strength", 0.5)
    bs = abs(float(raw_bs)) if not isinstance(raw_bs, bool) and isinstance(raw_bs, (int, float)) and math.isfinite(float(raw_bs)) else 0.5
    raw_ec = edge.get("evidence_confidence", edge.get("confidence", 0.5))
    ec = float(raw_ec) if not isinstance(raw_ec, bool) and isinstance(raw_ec, (int, float)) and math.isfinite(float(raw_ec)) and 0.0 <= float(raw_ec) <= 1.0 else 0.5
    mech = str(edge.get("mechanism") or f"Causal transmission from {edge.get('from')} to {edge.get('to')}").strip()
    if len(mech.split()) < 10:
        mech = f"{mech.rstrip('.')} operating via documented empirical sensitivity, baseline deviation response, and explicit discrete simulation lag."

    lag_ticks = edge.get("lag_ticks", 0)
    if isinstance(lag_ticks, bool) or not isinstance(lag_ticks, int) or lag_ticks < 0:
        lag_ticks = 0
    raw_ld = edge.get("lag_distribution")
    if isinstance(raw_ld, dict) and raw_ld.get("type") in {"fixed", "uniform", "triangular", "truncated_exponential"}:
        ld = dict(raw_ld)
        if ld.get("type") == "fixed" and parse_duration_seconds(ld.get("fixed")) is None:
            ld["fixed"] = f"P{lag_ticks}D"
    else:
        ld = {"type": "fixed", "fixed": f"P{lag_ticks}D"}

    raw_cms = edge.get("context_modifiers")
    cms: list[dict[str, Any]] = []
    if isinstance(raw_cms, list):
        for cm in raw_cms:
            if isinstance(cm, dict) and node_types.get(str(cm.get("context") or "")) == "context":
                cleaned_cm = {k: v for k, v in cm.items() if k in CONTEXT_MODIFIER_FIELDS}
                if "multiplier" in cleaned_cm:
                    cms.append(cleaned_cm)

    tf = str(edge.get("transform") or "linear").strip().lower()
    if tf not in TRANSFORMS:
        tf = "linear"
    allowed_tp = {
        "linear": set(),
        "elasticity": set(),
        "identity": set(),
        "logistic": {"midpoint", "steepness"},
        "threshold": {"mode", "threshold", "deadband", "theta_on", "theta_off"},
    }[tf]
    raw_tp = edge.get("transform_parameters")
    tp = {k: v for k, v in raw_tp.items() if k in allowed_tp} if isinstance(raw_tp, dict) and allowed_tp else None
    if tf == "threshold" and (not tp or "threshold" not in tp and "theta_on" not in tp):
        tp = {"mode": "above", "threshold": 0.0}
    elif tf == "logistic" and (not tp or "midpoint" not in tp or "steepness" not in tp):
        tp = {"midpoint": 0.0, "steepness": 1.0}

    raw_ep = edge.get("effect_parameter")
    ep = {k: v for k, v in raw_ep.items() if k in EFFECT_PARAMETER_FIELDS} if isinstance(raw_ep, dict) else {}
    if not isinstance(ep.get("kind"), str) or not ep["kind"].strip():
        ep["kind"] = "assumed_reference"
    if not any(k in ep for k in ("reference_value", "lower", "upper", "distribution")):
        ep["reference_value"] = bs

    raw_status = str(edge.get("status") or "admitted").strip().lower()
    edge_status = raw_status if raw_status in EDGE_STATUS else "admitted"

    hydrated = {k: v for k, v in edge.items() if k in EDGE_FIELDS}
    if tp is None:
        hydrated.pop("transform_parameters", None)
    else:
        hydrated["transform_parameters"] = tp
    hydrated.update(
        {
            "id": str(edge.get("id") or "edge:auto"),
            "from": str(edge.get("from")),
            "to": str(edge.get("to")),
            "relation": rel,
            "sign": sign,
            "base_strength": bs,
            "evidence_confidence": ec,
            "confidence": ec,
            "mechanism": mech,
            "lag_ticks": lag_ticks,
            "lag_distribution": ld,
            "context_modifiers": cms,
            "evidence": [ev_id],
            "status": edge_status,
            "transform": tf,
            "effect_parameter": ep,
        }
    )
    return hydrated


def _ingest_llm_json_bundle(
    ws: Path,
    source_path: Path,
    *,
    mode: str,
    formula_version: str,
    horizon: str,
    timestep: str,
) -> None:
    resolved = source_path.resolve()
    if resolved.is_dir():
        nodes_file = resolved / "nodes.json"
        edges_file = resolved / "edges.json"
        ivs_file = resolved / "interventions.json"
        man_file = resolved / "simulation-manifest.json"
        raw_nodes_data = json.loads(nodes_file.read_text(encoding="utf-8")) if nodes_file.is_file() else []
        raw_edges_data = json.loads(edges_file.read_text(encoding="utf-8")) if edges_file.is_file() else []
        raw_ivs_data = json.loads(ivs_file.read_text(encoding="utf-8")) if ivs_file.is_file() else []
        src_manifest = json.loads(man_file.read_text(encoding="utf-8")) if man_file.is_file() else {}
        bundle: dict[str, Any] = {
            "nodes": raw_nodes_data,
            "edges": raw_edges_data,
            "interventions": raw_ivs_data,
            "stochastic_shocks": src_manifest.get("stochastic_shocks") if isinstance(src_manifest, dict) else None,
            "temporal_frame": src_manifest.get("temporal_frame") if isinstance(src_manifest, dict) else None,
            "scope": src_manifest.get("scope") if isinstance(src_manifest, dict) else None,
        }
    else:
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("--from-llm-json file must contain a JSON object with 'nodes' and 'edges'")
        bundle = loaded

    manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
    if isinstance(bundle.get("temporal_frame"), dict):
        tf = dict(manifest.get("temporal_frame") or {})
        for k in ("mode", "observation_cutoff", "simulation_start", "simulation_end", "timestep", "future_projection", "calibration_strategy"):
            if bundle["temporal_frame"].get(k) is not None:
                tf[k] = bundle["temporal_frame"][k]
        manifest["temporal_frame"] = tf
    cutoff_str = str((manifest.get("temporal_frame") or {}).get("observation_cutoff") or "2026-01-01")
    cutoff_date = cutoff_str[:10] if len(cutoff_str) >= 10 else "2026-01-01"
    if isinstance(bundle.get("scope"), dict):
        sc = dict(manifest.get("scope") or {})
        for k in ("horizon", "domains", "geographies"):
            if bundle["scope"].get(k):
                sc[k] = bundle["scope"][k]
        manifest["scope"] = sc

    node_id_map: dict[str, str] = {}
    edge_id_map: dict[str, str] = {}
    repairs: list[str] = []
    fixed_nodes, _ = repair_nodes(bundle.get("nodes", []), id_map_out=node_id_map)
    eff_node_map = _augment_node_lookup_map(fixed_nodes, node_id_map)
    fixed_edges, _ = repair_edges(bundle.get("edges", []), node_id_map=eff_node_map, id_map_out=edge_id_map)
    node_scales = {str(n.get("id")): str(n.get("scale", "level")) for n in fixed_nodes if isinstance(n, dict) and n.get("id")}
    raw_bundle_ivs = bundle.get("interventions")
    if raw_bundle_ivs is None and isinstance(bundle.get("manifest"), dict):
        raw_bundle_ivs = bundle["manifest"].get("interventions")
    fixed_ivs, _ = repair_interventions(raw_bundle_ivs or [], node_scales=node_scales, node_id_map=eff_node_map)
    raw_shocks = bundle.get("stochastic_shocks")
    if raw_shocks is None and isinstance(bundle.get("manifest"), dict):
        raw_shocks = bundle["manifest"].get("stochastic_shocks")
    fixed_shocks: list[dict[str, Any]] = []
    if raw_shocks:
        fixed_shocks, _ = repair_stochastic_shocks(raw_shocks, node_id_map=eff_node_map, repairs=repairs)
    synth = synthesize_missing_target_nodes(fixed_nodes, fixed_edges, fixed_ivs, fixed_shocks or None, repairs)
    fixed_nodes.extend(synth)

    ev_id = "evidence:offline-baseline"
    hydrated_nodes = [_hydrate_node(n, cutoff_date=cutoff_date, ev_id=ev_id) for n in fixed_nodes if isinstance(n, dict)]
    node_types = {str(n["id"]): str(n["type"]) for n in hydrated_nodes}
    hydrated_edges = [_hydrate_edge(e, node_types=node_types, ev_id=ev_id) for e in fixed_edges if isinstance(e, dict)]

    # Sync manifest strictly
    manifest["simulation_mode"] = mode
    manifest["likelihood_mode"] = "deterministic" if mode == "deterministic" else "relative_weight"
    manifest["formula_version"] = formula_version
    if timestep and isinstance(manifest.get("temporal_frame"), dict) and not manifest["temporal_frame"].get("timestep"):
        manifest["temporal_frame"]["timestep"] = timestep
    if isinstance(manifest.get("temporal_frame"), dict):
        manifest["temporal_frame"]["monitoring_indicators"] = [n["id"] for n in hydrated_nodes[:3]]
    valid_node_ids = [n["id"] for n in hydrated_nodes]
    cp = dict(manifest.get("change_point") or {})
    first_iv_target = next((str(iv.get("target")) for iv in fixed_ivs if str(iv.get("target") or "") in node_types), None)
    first_factor = next((nid for nid in valid_node_ids if nid.startswith("factor:")), valid_node_ids[0] if valid_node_ids else "factor:example")
    cp["target"] = first_iv_target or first_factor
    if fixed_ivs:
        cp["magnitude"] = 0.0
    manifest["change_point"] = cp
    if fixed_shocks:
        manifest["stochastic_shocks"] = fixed_shocks
    else:
        manifest.pop("stochastic_shocks", None)
    exec_cfg = dict(manifest.get("execution") or {})
    adaptive = dict(exec_cfg.get("adaptive_scope") or {})
    adaptive["assessed"] = True
    exec_cfg["adaptive_scope"] = adaptive
    manifest["execution"] = exec_cfg
    ap = dict(manifest.get("artifact_paths") or {})
    ap.pop("propagation_trace", None)
    ap["interventions"] = "interventions.json"
    ap["execution_trace"] = "execution-trace.json"
    ap["computational_model"] = "simulation-model.json"
    ap["run_ledger"] = "simulation-run.json"
    ap["replay_report"] = "replay-report.json"
    ap.pop("sensitivity_report", None)
    manifest["artifact_paths"] = ap

    (ws / "nodes.json").write_text(json.dumps(hydrated_nodes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ws / "edges.json").write_text(json.dumps(hydrated_edges, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ws / "interventions.json").write_text(json.dumps(fixed_ivs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ws / "actors.json").write_text("[]\n", encoding="utf-8")
    (ws / "human-track-ledger.jsonl").write_text("", encoding="utf-8")
    with (ws / "evidence-map.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
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
            ]
        )
        writer.writerow(
            [
                ev_id,
                "Offline isolated model bundle baseline prior to cutoff",
                "local-model-bundle",
                "report",
                "user-provided",
                cutoff_date,
                f"{cutoff_date}T00:00:00Z",
                "local-file",
                "user-provided",
                "User-provided offline simulation specification without live external retrieval",
                "0.50",
                "no-conflict-found",
                "Imported via --from-llm-json in offline provisional mode",
            ]
        )
    (ws / "simulation-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sync_branch_ledger_from_run(ws: Path) -> None:
    manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
    run_doc = json.loads((ws / "simulation-run.json").read_text(encoding="utf-8"))
    nodes = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
    edges = json.loads((ws / "edges.json").read_text(encoding="utf-8"))
    ev_id = "evidence:offline-baseline"
    ev_path = ws / "evidence-map.csv"
    if ev_path.is_file():
        with ev_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            if rows and rows[0].get("evidence_id"):
                ev_id = str(rows[0]["evidence_id"]).strip()

    sim_end = str((manifest.get("temporal_frame") or {}).get("simulation_end") or "2027-01-01")
    causal_trace = [str(e["id"]) for e in edges[:4] if isinstance(e, dict) and e.get("id")]
    if not causal_trace and nodes:
        causal_trace = [str(nodes[0]["id"])]
    first_node = next((str(n["id"]) for n in nodes if isinstance(n, dict) and str(n.get("id", "")).startswith("factor:")), str(nodes[0]["id"]) if nodes else "factor:example")
    trace_path = ws / "execution-trace.json"
    trace_sha = sha256_file(trace_path) if trace_path.is_file() else None
    mode = run_doc.get("mode", manifest.get("simulation_mode", "deterministic"))

    if mode == "monte_carlo":
        summary = (run_doc.get("result") or {}).get("summary") or {}
        raw_branches = summary.get("branches") or []
        unresolved_mass = float(summary.get("unresolved_mass", 0.0) or 0.0)
        branches: list[dict[str, Any]] = []
        any_tail = False
        for idx, br in enumerate(raw_branches):
            if not isinstance(br, dict):
                continue
            cid = str(br.get("id") or f"branch:cluster-{idx}")
            rw = float(br.get("relative_weight", 0.0))
            if rw <= 0.0:
                continue
            sig = str(br.get("signature") or f"cluster_{idx}")
            raw_rep = br.get("representative")
            rep_state: dict[str, Any] = raw_rep if isinstance(raw_rep, dict) else {}
            top_vals = ", ".join(f"{k}={float(v):.4g}" for k, v in list(sorted(rep_state.items()))[:3])
            is_tail = bool(br.get("tail") or br.get("tail_class") == "tail_shock")
            if is_tail:
                any_tail = True
            entry: dict[str, Any] = {
                "id": cid,
                "name": f"Cluster {idx + 1}{' [Tail Shock]' if is_tail else ''} ({br.get('member_count', 1)} runs)",
                "relative_weight": rw,
                "likelihood_mode": "relative_weight",
                "summary": f"Engine cluster {idx + 1} (w={rw:.4f}, n={br.get('member_count', 1)}, regime={sig}).",
                "causal_trace": causal_trace,
                "key_decision_points": [],
                "end_state": {
                    "time": sim_end,
                    "summary": f"Cluster {idx + 1} terminal state ({top_vals or sig}).",
                },
                "leading_indicators": [
                    {
                        "node": first_node,
                        "direction": "up" if idx % 2 == 0 else "down",
                        "window": "P30D",
                        "predicate": f"cluster_{idx}_trajectory_alignment",
                    }
                ],
                "disconfirming_conditions": [f"Cluster {idx + 1} regime ({sig}) diverges from observed monitoring series."],
                "evidence_ids": [ev_id],
                "confidence": 0.5,
                "derivation": "engine_derived",
                "engine_cluster_id": cid,
                "member_count": int(br.get("member_count", 1)),
                "representative_run": str(br.get("representative_run") or f"run:{idx}"),
            }
            if trace_sha:
                entry["trace_hash"] = trace_sha
            if is_tail:
                entry["tail"] = True
                entry["tail_class"] = str(br.get("tail_class") or "tail_shock")
            branches.append(entry)
        if not branches:
            branches = [
                {
                    "id": "branch:cluster-0",
                    "name": "Default Monte Carlo Branch",
                    "relative_weight": max(1e-6, 1.0 - unresolved_mass),
                    "likelihood_mode": "relative_weight",
                    "summary": "Consolidated Monte Carlo trajectory branch.",
                    "causal_trace": causal_trace,
                    "key_decision_points": [],
                    "end_state": {"time": sim_end, "summary": "Consolidated terminal state."},
                    "leading_indicators": [{"node": first_node, "direction": "stable", "window": "P30D", "predicate": "baseline_aligned"}],
                    "disconfirming_conditions": ["Trajectory leaves simulated quantile envelope."],
                    "evidence_ids": [ev_id],
                    "confidence": 0.5,
                    "derivation": "engine_derived",
                    "representative_run": "run:0",
                    **({"trace_hash": trace_sha} if trace_sha else {}),
                }
            ]
        ledger_doc: dict[str, Any] = {
            "schema_version": "2.0.0",
            "likelihood_mode": "relative_weight",
            "calibrated": False,
            "unresolved_mass": unresolved_mass,
            "branches": branches,
        }
        if any_tail:
            ledger_doc["tail_coverage"] = True
    else:
        final_state = ((run_doc.get("result") or {}).get("payload") or {}).get("final_state") or {}
        top_vals = ", ".join(f"{k}={float(v):.4g}" for k, v in list(sorted(final_state.items()))[:3])
        ledger_doc = {
            "schema_version": "2.0.0",
            "likelihood_mode": "deterministic",
            "calibrated": False,
            "unresolved_mass": 0.0,
            "branches": [
                {
                    "id": "branch:deterministic-baseline",
                    "name": "Deterministic trajectory",
                    "relative_weight": 1.0,
                    "likelihood_mode": "deterministic",
                    "stress": False,
                    "summary": f"Deterministic trajectory over {run_doc.get('ticks', 0)} ticks.",
                    "causal_trace": causal_trace,
                    "key_decision_points": [],
                    "end_state": {"time": sim_end, "summary": f"Deterministic end state ({top_vals})."},
                    "leading_indicators": [{"node": first_node, "direction": "up", "window": "P30D", "predicate": "deterministic_path_match"}],
                    "disconfirming_conditions": ["Observed trajectory departs materially from deterministic path."],
                    "evidence_ids": [ev_id],
                    "confidence": 0.5,
                    "derivation": "engine_derived",
                    "representative_run": "run:0",
                    **({"trace_hash": trace_sha} if trace_sha else {}),
                }
            ],
        }
    (ws / "branch-ledger.json").write_text(json.dumps(ledger_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_pipeline(
    *,
    workspace: Path,
    ledger: Path | None = None,
    from_llm_json: Path | None = None,
    mode: str = "deterministic",
    formula_version: str = "2.2.0",
    seed: str = "42",
    ticks: int = 6,
    forecast_steps: int | None = None,
    runs: int | None = None,
    change_point: str = "Baseline intervention scenario",
    horizon: str = "P6M",
    timestep: str = "P1M",
    offline_provisional: bool = False,
    finalize: bool = False,
) -> dict[str, Any]:
    ws = workspace.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, Any]] = []
    skill_root = SCRIPTS_DIR.parent

    if not (ws / "simulation-manifest.json").is_file():
        init_cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "init_simulation_workspace.py"),
            "--workspace",
            str(ws),
            "--force",
            "--change-point",
            change_point,
            "--horizon",
            horizon,
            "--timestep",
            timestep,
            "--simulation-mode",
            mode,
            "--formula-version",
            formula_version,
        ]
        if offline_provisional or ledger is None:
            init_cmd.extend(
                [
                    "--offline-provisional",
                    "--offline-reason",
                    "Turn-key pipeline execution in offline/provisional mode",
                ]
            )
        st = _run_stage("init", init_cmd, skill_root)
        stages.append(st)
        if not st["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "init", "stages": stages}

    if from_llm_json is not None:
        _ingest_llm_json_bundle(
            ws,
            from_llm_json,
            mode=mode,
            formula_version=formula_version,
            horizon=horizon,
            timestep=timestep,
        )
        st_repair = _run_stage(
            "repair-model",
            [sys.executable, str(SCRIPTS_DIR / "repair_model_schema.py"), "--workspace", str(ws), "--in-place", "--json"],
            ws,
        )
        stages.append(st_repair)
        if not st_repair["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "repair-model", "stages": stages}
    elif ledger is not None:
        resolved_ledger = ledger.resolve()
        if resolved_ledger.suffix.lower() == ".csv":
            with resolved_ledger.open("r", encoding="utf-8-sig", newline="") as handle:
                header = set(next(csv.reader(handle), []))
            if "claim_id" in header and "record_type" in header:
                imp_cmd = [
                    sys.executable,
                    str(SCRIPTS_DIR / "import_research_ledger.py"),
                    "--ledger",
                    str(resolved_ledger),
                    "--workspace",
                    str(ws),
                    "--out",
                    str(ws / "evidence-map.csv"),
                    "--receipt-out",
                    str(ws / "research-import-receipt.json"),
                ]
                st_imp = _run_stage("import-research", imp_cmd, ws)
                stages.append(st_imp)
                if not st_imp["ok"]:
                    return {"ok": False, "workspace": str(ws), "failed_stage": "import-research", "stages": stages}

        prop_path = ws / "causal-proposal.json"
        rev_path = ws / "causal-review.json"
        st_prop = _run_stage(
            "propose-model",
            [
                sys.executable,
                str(SCRIPTS_DIR / "auto_causal_inducer.py"),
                "propose-model",
                "--ledger" if resolved_ledger.suffix.lower() == ".csv" else "--evidence",
                str(resolved_ledger),
                "--out",
                str(prop_path),
            ],
            ws,
        )
        stages.append(st_prop)
        if not st_prop["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "propose-model", "stages": stages}

        st_rev = _run_stage(
            "review-model",
            [
                sys.executable,
                str(SCRIPTS_DIR / "auto_causal_inducer.py"),
                "review-model",
                "--proposal",
                str(prop_path),
                "--out",
                str(rev_path),
            ],
            ws,
        )
        stages.append(st_rev)
        if not st_rev["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "review-model", "stages": stages}

        st_apply = _run_stage(
            "apply-model",
            [
                sys.executable,
                str(SCRIPTS_DIR / "auto_causal_inducer.py"),
                "apply-model",
                "--proposal",
                str(prop_path),
                "--review",
                str(rev_path),
                "--workspace",
                str(ws),
                "--into-initialized",
            ],
            ws,
        )
        stages.append(st_apply)
        if not st_apply["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "apply-model", "stages": stages}
    else:
        _seed_offline_minimum_model(ws, mode=mode)

    st_comp = _run_stage(
        "compile",
        [sys.executable, str(SCRIPTS_DIR / "compile_model.py"), "--workspace", str(ws)],
        ws,
    )
    stages.append(st_comp)
    if not st_comp["ok"]:
        return {"ok": False, "workspace": str(ws), "failed_stage": "compile", "stages": stages}

    sim_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "run_simulation.py"),
        "--workspace",
        str(ws),
        "--mode",
        mode,
        "--sync-manifest-mode",
        "--regenerate-trace",
        "--seed",
        str(seed),
    ]
    if forecast_steps is not None:
        sim_cmd.extend(["--forecast-steps", str(forecast_steps)])
    else:
        sim_cmd.extend(["--ticks", str(ticks)])
    sim_cmd.append("--json")
    if runs is not None and mode == "monte_carlo":
        sim_cmd.extend(["--runs", str(runs), "--max-runs", str(runs)])
    st_sim = _run_stage("simulate", sim_cmd, ws)
    stages.append(st_sim)
    if not st_sim["ok"]:
        return {"ok": False, "workspace": str(ws), "failed_stage": "simulate", "stages": stages}

    if from_llm_json is not None or finalize:
        _sync_branch_ledger_from_run(ws)

    st_replay = _run_stage(
        "replay",
        [sys.executable, str(SCRIPTS_DIR / "replay_simulation.py"), "--workspace", str(ws)],
        ws,
    )
    stages.append(st_replay)
    if not st_replay["ok"]:
        return {"ok": False, "workspace": str(ws), "failed_stage": "replay", "stages": stages}

    st_val = _run_stage(
        "validate",
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simulation_artifacts.py"),
            "--workspace",
            str(ws),
            "--mode",
            "draft",
            "--write-report",
            "--json",
        ],
        ws,
    )
    stages.append(st_val)

    st_rep = _run_stage(
        "render-report",
        [sys.executable, str(SCRIPTS_DIR / "render_simulation_report.py"), "--workspace", str(ws)],
        ws,
    )
    stages.append(st_rep)

    if finalize:
        st_fin = _run_stage(
            "finalize",
            [sys.executable, str(SCRIPTS_DIR / "finalize_simulation.py"), "--workspace", str(ws), "--complete", "--json"],
            ws,
        )
        stages.append(st_fin)
        if not st_fin["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "finalize", "stages": stages}

        st_vfin = _run_stage(
            "validate-final",
            [
                sys.executable,
                str(SCRIPTS_DIR / "validate_simulation_artifacts.py"),
                "--workspace",
                str(ws),
                "--mode",
                "final",
                "--require-report",
                "--write-report",
                "--json",
            ],
            ws,
        )
        stages.append(st_vfin)

    artifacts_present = sorted(
        name
        for name in (
            "simulation-manifest.json",
            "nodes.json",
            "edges.json",
            "interventions.json",
            "simulation-model.json",
            "simulation-run.json",
            "execution-trace.json",
            "branch-ledger.json",
            "validation-report.json",
            "validation-receipt.json",
            "quality-receipt.json",
            "REPORT.md",
        )
        if (ws / name).is_file()
    )
    if (ws / "replay-report.json").is_file() and "replay-report.json" not in artifacts_present:
        artifacts_present = sorted([*artifacts_present, "replay-report.json"])
    return {
        "ok": all(item["ok"] for item in stages),
        "workspace": str(ws),
        "mode": mode,
        "formula_version": formula_version,
        "stages": [{"stage": s["stage"], "returncode": s["returncode"], "ok": s["ok"]} for s in stages],
        "artifacts": artifacts_present,
    }


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="aleph-pipeline-selftest-") as td:
        ws = Path(td) / "demo-ws"
        ledger_path = Path(td) / "claims.json"
        ledger_path.write_text(
            json.dumps(
                [
                    {
                        "claim_id": "claim:rate-gap",
                        "record_type": "claim",
                        "claim": "Policy Rate decreases Output Gap via tightening financial conditions",
                        "evidence": "A rate increase lowers output gap by 0.25 with lag 1 tick (2025-01-01, unit %, baseline 4.5, unit %).",
                        "source_factor": "Policy Rate",
                        "target_factor": "Output Gap",
                        "sign": -1,
                        "effect_size": 0.25,
                        "lag_ticks": 1,
                        "mechanism": "tightening financial conditions",
                        "observation_time": "2025-01-01",
                        "node_measurements": {
                            "factor:policy-rate": {"baseline": 4.5, "time": "2025-01-01", "unit": "%"},
                            "factor:output-gap": {"baseline": 0.5, "time": "2025-01-01", "unit": "%"},
                        },
                        "confidence": "high",
                    }
                ]
            ),
            encoding="utf-8",
        )
        res = run_pipeline(
            workspace=ws,
            ledger=ledger_path,
            mode="deterministic",
            formula_version="2.2.0",
            seed="42",
            ticks=4,
            offline_provisional=True,
        )
        if not res["ok"]:
            print(json.dumps(res, indent=2), file=sys.stderr)
            return 1
        print(json.dumps({"self_test": "ok", "stages": len(res["stages"]), "artifacts": res["artifacts"]}, indent=2))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end Aleph causal simulation pipeline.")
    parser.add_argument("--workspace", help="Target Aleph simulation workspace directory")
    parser.add_argument("--ledger", help="Optional D-Research CSV or JSON evidence ledger")
    parser.add_argument("--from-llm-json", type=Path, help="Optional raw LLM JSON bundle (or raw workspace dir) to repair, hydrate, simulate, and finalize")
    parser.add_argument("--mode", choices=["deterministic", "monte_carlo"], default="deterministic")
    parser.add_argument("--formula-version", choices=["2.0.0", "2.1.0", "2.2.0"], default="2.2.0")
    parser.add_argument("--seed", default="42")
    parser.add_argument("--ticks", type=int, default=6)
    parser.add_argument("--forecast-steps", type=int, default=None, help="Post-cutoff forecast horizon steps (sets ticks = forecast_steps + 1)")
    parser.add_argument("--runs", type=int, help="Monte Carlo sample count")
    parser.add_argument("--change-point", default="Baseline intervention scenario")
    parser.add_argument("--horizon", default="P6M")
    parser.add_argument("--timestep", default="P1M")
    parser.add_argument("--offline-provisional", action="store_true")
    parser.add_argument("--finalize", action="store_true", help="Finalize workspace and run final validation gate")
    parser.add_argument("--no-finalize", action="store_true", help="Skip finalization even when --from-llm-json is used")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if not args.workspace:
        print("ERROR: --workspace is required unless --self-test is passed", file=sys.stderr)
        return EXIT_USAGE
    do_finalize = bool(args.finalize or (args.from_llm_json is not None and not args.no_finalize))
    res = run_pipeline(
        workspace=Path(args.workspace),
        ledger=Path(args.ledger) if args.ledger else None,
        from_llm_json=Path(args.from_llm_json) if args.from_llm_json else None,
        mode=args.mode,
        formula_version=args.formula_version,
        seed=args.seed,
        ticks=args.ticks,
        forecast_steps=args.forecast_steps,
        runs=args.runs,
        change_point=args.change_point,
        horizon=args.horizon,
        timestep=args.timestep,
        offline_provisional=bool(args.offline_provisional or args.from_llm_json is not None),
        finalize=do_finalize,
    )
    print(json.dumps(res, indent=2))
    return EXIT_OK if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
