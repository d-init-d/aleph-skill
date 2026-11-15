"""Evidence-derived assurance tiers and diagnostic scoring."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
from .discovery import discover_d_research
from .import_ledger import (
    import_d_research_ledger,
    render_evidence_csv,
    render_import_audit_json,
)
from .io import canonical_hash, load_json_secure, load_jsonl_secure, sha256_file
from .packets import (
    receipt_binds_ledger_artifacts,
    validate_actor_protocol,
    verify_receipt_artifact_bytes,
    verify_receipt_chain,
)
from .paths import resolve_in_workspace
from .schema import parse_duration_seconds, parse_timestep_seconds
from .validator import validate_workspace

SECTION_WEIGHTS = {
    "structural_integrity": 15.0,
    "evidence_provenance": 20.0,
    "causal_semantics_replay": 25.0,
    "actor_roleplay_discipline": 15.0,
    "branch_uncertainty_calibration": 15.0,
    "report_reproducibility": 10.0,
}


def _load_optional_json(workspace: Path, relative: Any) -> dict[str, Any] | None:
    if not isinstance(relative, str):
        return None
    path, path_issues = resolve_in_workspace(workspace, relative, must_exist=True, require_file=True)
    if path_issues or path is None:
        return None
    data, load_issues = load_json_secure(path)
    return data if not load_issues and isinstance(data, dict) else None


def _manifest(workspace: Path) -> dict[str, Any]:
    data, issues = load_json_secure(workspace / "simulation-manifest.json")
    return data if not issues and isinstance(data, dict) else {}


def _d_research_verified(
    workspace: Path,
    manifest: dict[str, Any],
    *,
    hmac_key: bytes | None = None,
) -> bool:
    raw_execution = manifest.get("execution")
    execution: dict[str, Any] = raw_execution if isinstance(raw_execution, dict) else {}
    raw_research = execution.get("d_research")
    research: dict[str, Any] = raw_research if isinstance(raw_research, dict) else {}
    if research.get("invoked") is not True or research.get("status") not in {"imported", "verified"}:
        return False
    if research.get("package_major") != 3 or hmac_key is None:
        return False
    ledger_ref = research.get("ledger_ref")
    if not isinstance(ledger_ref, str):
        return False
    ledger_path, issues = resolve_in_workspace(workspace, ledger_ref, must_exist=True, require_file=True)
    if ledger_path is None or issues:
        return False
    raw_paths = manifest.get("artifact_paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
    receipt = _load_optional_json(workspace, paths.get("research_import_receipt"))
    evidence_path, evidence_issues = resolve_in_workspace(
        workspace,
        str(paths.get("evidence_map", "evidence-map.csv")),
        must_exist=True,
        require_file=True,
    )
    if receipt is None or evidence_path is None or evidence_issues:
        return False
    receipt_hash = receipt.get("receipt_hash")
    receipt_body = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if receipt_hash != canonical_hash(receipt_body):
        return False
    identity = receipt.get("d_research_identity")
    binding = receipt.get("component_binding")
    if not isinstance(identity, dict) or identity.get("identity_verified") is not True:
        return False
    # Prefer portable component binding; never re-discover via stale absolute path
    # for bundled components.
    from .component_registry import (
        COMPONENT_URI,
        ComponentError,
        resolve_component,
        skill_root_from,
        verify_component_lock,
    )

    skill_root = skill_root_from()
    verification = verify_component_lock(skill_root=skill_root)
    if not verification.ok:
        return False
    try:
        resolution = resolve_component(COMPONENT_URI, skill_root=skill_root, require_verified=True)
    except ComponentError:
        return False
    helper = Path(resolution.root) / "scripts" / "evidence_ledger.py"
    discovery = discover_d_research(skill_root=skill_root)
    if discovery.get("status") != "available" or not helper.is_file():
        return False
    try:
        helper_digest = sha256_file(helper)
        # A bundled receipt is a provenance claim, not merely a helper hash.
        # Require every immutable identity and content field emitted by the
        # component resolver. Missing fields (including tag/commit provenance)
        # must fail closed; an external legacy receipt can therefore never be
        # promoted to ``verified`` by this path.
        expected_binding = resolution.binding()
        binding_ok = isinstance(binding, dict) and all(
            binding.get(key) == value for key, value in expected_binding.items()
        )
        identity_matches = bool(
            binding_ok
            and identity.get("name") == discovery.get("name")
            and identity.get("package_name") == discovery.get("package_name")
            and identity.get("package_version") == discovery.get("package_version") == resolution.package_version
            and identity.get("package_major") == discovery.get("package_major") == 3
            and identity.get("path") == COMPONENT_URI
            and identity.get("ledger_helper_sha256") == helper_digest
        )
    except OSError:
        return False
    if not identity_matches:
        return False
    sidecar_ref = receipt.get("hmac_sidecar_ref")
    if not isinstance(sidecar_ref, str):
        return False
    sidecar_path, sidecar_issues = resolve_in_workspace(
        workspace,
        sidecar_ref,
        must_exist=True,
        require_file=True,
    )
    if sidecar_path is None or sidecar_issues:
        return False
    imported = import_d_research_ledger(
        ledger_path,
        hmac_sidecar=sidecar_path,
        hmac_key=hmac_key,
        package_major=3,
    )
    evidence_rows = imported.get("evidence_rows")
    if not isinstance(evidence_rows, list):
        return False
    expected_evidence = render_evidence_csv(evidence_rows)
    audit_fields = {
        "audit_ref",
        "audit_sha256",
        "evidence_count",
        "lead_count",
        "audit_count",
    }
    audit_fields_present = audit_fields & set(receipt)
    audit_ok = True
    if audit_fields_present:
        if audit_fields_present != audit_fields:
            return False
        audit_ref = receipt.get("audit_ref")
        if not isinstance(audit_ref, str):
            return False
        audit_path, audit_issues = resolve_in_workspace(
            workspace,
            audit_ref,
            must_exist=True,
            require_file=True,
        )
        if audit_path is None or audit_issues:
            return False
        expected_audit = render_import_audit_json(
            imported,
            schema_version=str(receipt.get("schema_version")),
        )
        try:
            audit_ok = bool(
                audit_path.read_bytes() == expected_audit
                and receipt.get("audit_sha256") == sha256_file(audit_path)
                and receipt.get("evidence_count") == len(evidence_rows)
                and receipt.get("lead_count") == len(imported.get("lead_rows") or [])
                and receipt.get("audit_count") == len(imported.get("audit_rows") or [])
            )
        except OSError:
            return False
    try:
        return bool(
            imported.get("ok")
            and imported.get("hmac_verified") is True
            and receipt.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS
            and receipt.get("receipt_type") == "d-research-import"
            and receipt.get("mapping_contract") == "d-research-3.x-canonical"
            and receipt.get("source_contract") in {
                "d-research-policy-37",
                "d-research-record-type-23",
                "d-research-provenance-22",
                "d-research-social-19",
                "d-research-legacy-14",
            }
            and receipt.get("source_contract") == imported.get("source_contract")
            and receipt.get("raw_preserved") is True
            and receipt.get("hmac_verified") is True
            and receipt.get("package_major") == 3
            and receipt.get("ledger_ref") == ledger_ref
            and receipt.get("evidence_map_ref") == paths.get("evidence_map", "evidence-map.csv")
            and receipt.get("raw_sha256") == imported.get("raw_sha256") == sha256_file(ledger_path)
            and receipt.get("canonical_sha256") == imported.get("canonical_sha256")
            and receipt.get("evidence_map_sha256") == sha256_file(evidence_path)
            and evidence_path.read_bytes() == expected_evidence
            and receipt.get("hmac_sidecar_sha256") == sha256_file(sidecar_path)
            and audit_ok
        )
    except OSError:
        return False


def _receipt_values(workspace: Path, reference: Any) -> list[dict[str, Any]]:
    if not isinstance(reference, str):
        return []
    path, path_issues = resolve_in_workspace(workspace, reference, must_exist=True, require_file=True)
    if path is None or path_issues:
        return []
    data, load_issues = load_json_secure(path)
    if load_issues:
        return []
    if isinstance(data, list):
        values = data
    elif isinstance(data, dict) and isinstance(data.get("receipts"), list):
        values = data["receipts"]
    else:
        values = [data]
    return [value for value in values if isinstance(value, dict)]


def _roleplay_tier(
    workspace: Path,
    manifest: dict[str, Any],
    material_actors: int,
    *,
    hmac_key: bytes | None = None,
) -> str:
    if material_actors == 0:
        return "N/A"
    raw_paths = manifest.get("artifact_paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
    ledger_ref = paths.get("human_track_ledger", "human-track-ledger.jsonl")
    path, issues = resolve_in_workspace(workspace, str(ledger_ref), must_exist=True, require_file=True)
    if path is None or issues:
        return "D"
    rows, row_issues = load_jsonl_secure(path)
    if row_issues or not rows:
        return "D"
    actors_ref = paths.get("actors", "actors.json")
    actors_path, actor_path_issues = resolve_in_workspace(
        workspace,
        str(actors_ref),
        must_exist=True,
        require_file=True,
    )
    if actors_path is None or actor_path_issues:
        return "D"
    actors_data, actor_load_issues = load_json_secure(actors_path)
    if actor_load_issues or not isinstance(actors_data, list):
        return "D"
    material_actor_values = [
        actor
        for actor in actors_data
        if isinstance(actor, dict) and actor.get("materiality") == "material"
    ]
    actor_by_id = {
        str(actor.get("id")): actor
        for actor in material_actor_values
        if isinstance(actor.get("id"), str)
    }
    if len(material_actor_values) != material_actors or len(actor_by_id) != material_actors:
        return "D"
    actor_ids = {str(row.get("actor_id")) for row in rows if row.get("track") == "roleplay"}
    if len(actor_ids) != material_actors or actor_ids != set(actor_by_id):
        return "D"
    unsigned_valid = True
    hmac_valid = hmac_key is not None
    have_references = True
    protocol_issues = validate_actor_protocol(
        actors_data,
        rows,
        manifest=manifest,
        workspace=workspace,
    )
    semantic_valid = not any(item.severity == "error" for item in protocol_issues)
    for actor_id in actor_ids:
        research_rows = [row for row in rows if row.get("actor_id") == actor_id and row.get("track") == "research"]
        roleplay_rows = [row for row in rows if row.get("actor_id") == actor_id and row.get("track") == "roleplay"]
        if len(research_rows) != 1 or len(roleplay_rows) != 1:
            return "D"
        research_row, roleplay_row = research_rows[0], roleplay_rows[0]
        receipts: list[dict[str, Any]] = []
        references: set[str] = set()
        for row in (research_row, roleplay_row):
            reference = row.get("receipt_ref")
            if not isinstance(reference, str) or not reference.strip():
                have_references = False
            else:
                references.add(reference)
        for reference in sorted(references):
            values = _receipt_values(workspace, reference)
            if not values:
                have_references = False
            receipts.extend(values)
        execution_ids = [str(value.get("execution_id")) for value in receipts]
        if len(execution_ids) != len(set(execution_ids)):
            unsigned_valid = False
            hmac_valid = False
            continue
        by_execution = {str(value.get("execution_id")): value for value in receipts}
        selected = [
            by_execution.get(str(research_row.get("execution_id"))),
            by_execution.get(str(roleplay_row.get("execution_id"))),
        ]
        if not all(isinstance(value, dict) for value in selected):
            unsigned_valid = False
            hmac_valid = False
            continue
        chain = [value for value in selected if isinstance(value, dict)]
        if any(
            receipt.get("id") != row.get("receipt_id")
            or receipt.get("receipt_hash") != row.get("receipt_hash")
            or not receipt_binds_ledger_artifacts(receipt, row)
            for receipt, row in zip(chain, (research_row, roleplay_row), strict=True)
        ):
            unsigned_valid = False
            hmac_valid = False
            continue
        artifact_issues = [
            item
            for receipt in chain
            for item in verify_receipt_artifact_bytes(receipt, workspace)
        ]
        if any(item.severity == "error" for item in artifact_issues):
            unsigned_valid = False
            hmac_valid = False
            continue
        unsigned = verify_receipt_chain(
            chain,
            research_id=str(research_row.get("execution_id")),
            roleplay_id=str(roleplay_row.get("execution_id")),
            require_hmac=False,
        )
        signed = verify_receipt_chain(
            chain,
            research_id=str(research_row.get("execution_id")),
            roleplay_id=str(roleplay_row.get("execution_id")),
            hmac_key=hmac_key,
            require_hmac=True,
        )
        unsigned_valid = unsigned_valid and bool(unsigned.get("ok"))
        hmac_valid = hmac_valid and bool(signed.get("ok"))
    if semantic_valid and have_references and hmac_valid:
        return "A"
    if semantic_valid and have_references and unsigned_valid:
        return "B"
    return "C"


_DIMENSIONLESS_UNITS = {
    "",
    "dimensionless",
    "unitless",
    "index",
    "score",
    "normalized",
    "ratio",
    "points",
}
_MONETARY_UNIT_TOKENS = (
    "$",
    "usd",
    "eur",
    "gbp",
    "vnd",
    "dollar",
    "euro",
    "billion",
    "million",
    "trillion",
    "currency",
    "bbl",
)
_PCT_UNIT_TOKENS = ("%", "percent", "pct", "bps", "basis", "rate", "yield")
_PROBABILITY_PREFIX_RE = re.compile(
    r"(?:probabilit|posterior|likelihood|confidence|coverage|weight|credible|interval|level|chance|odds)\b",
    re.I,
)


def _load_optional_json_any(workspace: Path, relative: Any) -> Any:
    if not isinstance(relative, str):
        return None
    path, path_issues = resolve_in_workspace(workspace, relative, must_exist=True, require_file=True)
    if path_issues or path is None:
        return None
    data, load_issues = load_json_secure(path)
    return data if not load_issues else None


def _extract_nodes_and_edges(
    workspace: Path,
    paths: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cm_doc = _load_optional_json(
        workspace,
        paths.get("computational_model") or paths.get("model_ir") or "simulation-model.json",
    )
    nodes_raw = _load_optional_json_any(workspace, paths.get("nodes") or "nodes.json")
    edges_raw = _load_optional_json_any(workspace, paths.get("edges") or paths.get("causal_edges") or "edges.json")

    if isinstance(nodes_raw, list):
        nodes = [n for n in nodes_raw if isinstance(n, dict)]
    elif isinstance(nodes_raw, dict) and isinstance(nodes_raw.get("nodes"), list):
        nodes = [n for n in nodes_raw["nodes"] if isinstance(n, dict)]
    elif isinstance(cm_doc, dict) and isinstance(cm_doc.get("variables"), list):
        nodes = [n for n in cm_doc["variables"] if isinstance(n, dict)]
    elif isinstance(cm_doc, dict) and isinstance(cm_doc.get("nodes"), list):
        nodes = [n for n in cm_doc["nodes"] if isinstance(n, dict)]
    else:
        nodes = []

    if isinstance(cm_doc, dict) and isinstance(cm_doc.get("edges"), list):
        edges = [e for e in cm_doc["edges"] if isinstance(e, dict)]
    elif isinstance(edges_raw, list):
        edges = [e for e in edges_raw if isinstance(e, dict)]
    elif isinstance(edges_raw, dict) and isinstance(edges_raw.get("edges"), list):
        edges = [e for e in edges_raw["edges"] if isinstance(e, dict)]
    else:
        edges = []

    interventions = (
        [i for i in cm_doc["interventions"] if isinstance(i, dict)]
        if isinstance(cm_doc, dict) and isinstance(cm_doc.get("interventions"), list)
        else []
    )
    return nodes, edges, interventions


def _run_history(result: Any, payload: Any) -> list[Any]:
    result_history = result.get("history") if isinstance(result, dict) else None
    if isinstance(result_history, list):
        return result_history
    payload_history = payload.get("history") if isinstance(payload, dict) else None
    return payload_history if isinstance(payload_history, list) else []


def _detect_engine_frozen(
    workspace: Path,
    manifest: dict[str, Any],
    paths: dict[str, Any],
) -> dict[str, Any] | None:
    if manifest.get("simulation_mode") == "qualitative":
        return None
    run_ledger = _load_optional_json(workspace, paths.get("run_ledger") or "simulation-run.json")
    if not isinstance(run_ledger, dict):
        return None
    ticks = run_ledger.get("ticks")
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 1:
        return None
    result = run_ledger.get("result")
    payload = result.get("payload") if isinstance(result, dict) else None
    final_state = payload.get("final_state") if isinstance(payload, dict) else None
    history = _run_history(result, payload)

    nodes, edges, cm_interventions = _extract_nodes_and_edges(workspace, paths)
    run_interventions = run_ledger.get("interventions")
    interventions = run_interventions if isinstance(run_interventions, list) else cm_interventions
    if not edges or not isinstance(final_state, dict):
        return None

    baselines: dict[str, float] = {}
    initials: dict[str, float] = {}
    for n in nodes:
        nid = str(n.get("id") or "")
        if not nid:
            continue
        base_v = n.get("baseline")
        if base_v is None and isinstance(n.get("state_before"), dict):
            base_v = n["state_before"].get("value")
        if base_v is None:
            base_v = n.get("initial_value", 0.0)
        init_v = n.get("initial_value", base_v)
        if not isinstance(base_v, bool) and isinstance(base_v, (int, float)):
            baselines[nid] = float(base_v)
        if not isinstance(init_v, bool) and isinstance(init_v, (int, float)):
            initials[nid] = float(init_v)

    run_warnings = run_ledger.get("warnings")
    if isinstance(run_warnings, list) and any(
        isinstance(w, dict) and w.get("code") == "ENGINE_FROZEN"
        for w in run_warnings
    ):
        return {
            "severity": "warning",
            "code": "ENGINE_FROZEN",
            "message": "Numerical simulation shows zero state propagation on downstream targets despite edges/interventions.",
            "pointer": str(paths.get("run_ledger") or "simulation-run.json"),
        }

    timestep_days = 1.0
    run_cfg = run_ledger.get("config")
    if isinstance(run_cfg, dict):
        raw_step = run_cfg.get("timestep")
        if not isinstance(raw_step, bool) and isinstance(raw_step, (int, float)) and float(raw_step) > 0:
            timestep_days = float(raw_step)
    elif isinstance(manifest.get("temporal_frame"), dict) and manifest["temporal_frame"].get("timestep"):
        sec = parse_timestep_seconds(str(manifest["temporal_frame"]["timestep"]))
        if sec is not None and sec > 0:
            timestep_days = sec / 86400.0

    def _effective_lag_ticks(edge: dict[str, Any]) -> int:
        raw_lag = edge.get("lag_ticks")
        lag_unit = str(edge.get("lag_unit") or "ticks").strip().lower()
        if isinstance(raw_lag, (int, float)) and not isinstance(raw_lag, bool):
            val = float(raw_lag)
            if lag_unit == "days":
                return max(0, int(math.ceil(val / max(1e-9, timestep_days))))
            return max(0, int(round(val)))
        lag_dist = edge.get("lag_distribution")
        if isinstance(lag_dist, dict) and lag_dist.get("type") == "fixed":
            fixed = lag_dist.get("fixed")
            if isinstance(fixed, (int, float)) and not isinstance(fixed, bool):
                val = float(fixed)
                if lag_unit == "days":
                    return max(0, int(math.ceil(val / max(1e-9, timestep_days))))
                return max(0, int(round(val)))
            if isinstance(fixed, str):
                sec = parse_duration_seconds(fixed)
                if sec is not None:
                    return max(0, int(math.ceil((sec / 86400.0) / max(1e-9, timestep_days))))
        return 0

    has_perturbation = bool(interventions) or any(
        abs(initials.get(nid, 0.0) - baselines.get(nid, 0.0)) > 1e-9 for nid in baselines
    )
    active_edges = [
        e for e in edges
        if abs(float(e.get("strength", e.get("base_strength", 0.0)) or 0.0)) > 1e-12
    ]
    if not active_edges:
        return None

    lag_exceeds = all(_effective_lag_ticks(e) >= ticks for e in active_edges)
    target_ids = {str(e.get("target") or e.get("to") or "") for e in active_edges} - {""}
    present_targets = [tid for tid in target_ids if tid in final_state]
    targets_unchanged = bool(present_targets) and all(
        abs(float(final_state[tid]) - baselines.get(tid, 0.0)) <= 1e-12
        for tid in present_targets
    )
    targets_moved_in_history = any(
        isinstance(h, dict)
        and any(
            tid in h
            and isinstance(h[tid], (int, float))
            and not isinstance(h[tid], bool)
            and abs(float(h[tid]) - baselines.get(tid, 0.0)) > 1e-12
            for tid in present_targets
        )
        for h in history
    )
    history_constant = (
        len(history) >= 2
        and all(h == history[0] for h in history[1:])
    )
    intervened_ids = {
        str(iv.get("target") or iv.get("target_node_id") or "")
        for iv in interventions
        if isinstance(iv, dict)
    } - {""}
    moved_nodes = {
        nid
        for nid in baselines
        if (
            nid in final_state
            and not isinstance(final_state[nid], bool)
            and isinstance(final_state[nid], (int, float))
            and abs(float(final_state[nid]) - baselines[nid]) > 1e-12
        )
        or any(
            isinstance(h, dict)
            and nid in h
            and not isinstance(h[nid], bool)
            and isinstance(h[nid], (int, float))
            and abs(float(h[nid]) - baselines[nid]) > 1e-12
            for h in history
        )
    }
    gated_targets = {
        str(e.get("target") or e.get("to") or "")
        for e in active_edges
        if e.get("gate") is not None
    } - {""}
    pure_downstream_targets = [
        str(e.get("target") or e.get("to") or "")
        for e in active_edges
        if e.get("gate") is None
        and str(e.get("source") or e.get("from") or "") in moved_nodes
        and str(e.get("target") or e.get("to") or "") in final_state
        and str(e.get("target") or e.get("to") or "") not in intervened_ids
        and str(e.get("target") or e.get("to") or "") not in gated_targets
    ]
    downstream_frozen = bool(pure_downstream_targets) and ticks >= 2 and all(
        abs(float(final_state[tid]) - baselines.get(tid, 0.0)) <= 1e-12
        and not any(
            isinstance(h, dict)
            and tid in h
            and isinstance(h[tid], (int, float))
            and not isinstance(h[tid], bool)
            and abs(float(h[tid]) - baselines.get(tid, 0.0)) > 1e-12
            for h in history
        )
        for tid in pure_downstream_targets
    )
    if (
        (lag_exceeds and has_perturbation and targets_unchanged and not targets_moved_in_history)
        or (has_perturbation and targets_unchanged and not targets_moved_in_history and ticks >= 2)
        or (history_constant and targets_unchanged and not targets_moved_in_history and ticks >= 2)
        or downstream_frozen
    ):
        return {
            "severity": "warning",
            "code": "ENGINE_FROZEN",
            "message": "Numerical simulation shows zero state propagation on downstream targets despite edges/interventions.",
            "pointer": str(paths.get("run_ledger") or "simulation-run.json"),
        }
    return None


def _detect_narrative_unit_mismatch(
    workspace: Path,
    paths: dict[str, Any],
) -> dict[str, Any] | None:
    report_rel = paths.get("final_report") or paths.get("report") or "REPORT.md"
    report_path, report_issues = resolve_in_workspace(workspace, report_rel, must_exist=True, require_file=True)
    if report_issues or report_path is None:
        return None
    nodes, _, _ = _extract_nodes_and_edges(workspace, paths)
    if not nodes:
        return None

    node_units = {
        str(n.get("unit") or n.get("units") or "").strip().lower()
        for n in nodes
    }
    all_dimensionless = bool(node_units) and node_units.issubset(_DIMENSIONLESS_UNITS)
    has_monetary_unit = any(any(tok in u for tok in _MONETARY_UNIT_TOKENS) for u in node_units)
    has_pct_unit = any(any(tok in u for tok in _PCT_UNIT_TOKENS) for u in node_units)
    if not all_dimensionless and has_monetary_unit and has_pct_unit:
        return None

    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    mismatch_reasons: list[str] = []
    if not has_monetary_unit and re.search(
        r"(?:\$[0-9][0-9,.]*(?:\s*(?:billion|million|trillion|thousand|bn|mn|b|m|t))?\b|\bUSD\s*[0-9][0-9,.]*|\b[0-9][0-9,.]*\s*(?:billion|million|trillion)\s*(?:USD|dollars)?\b)",
        text,
        re.I,
    ):
        mismatch_reasons.append("monetary amounts cited without monetary node units")
    if not has_pct_unit and re.search(r"\b[+-]?[0-9]+(?:\.[0-9]+)?\s*(?:bps|basis points)\b", text, re.I):
        mismatch_reasons.append("basis-point claims cited without rate/bps node units")
    if mismatch_reasons:
        return {
            "severity": "warning",
            "code": "NARRATIVE_UNIT_MISMATCH",
            "message": "Narrative report asserts calibrated/real-world units inconsistent with model node units: " + "; ".join(mismatch_reasons),
            "pointer": str(report_rel),
        }
    return None


def _detect_model_narrative_fidelity(
    workspace: Path,
    manifest: dict[str, Any],
    paths: dict[str, Any],
) -> dict[str, Any] | None:
    if manifest.get("simulation_mode") == "qualitative":
        return None
    report_rel = paths.get("final_report") or paths.get("report") or "REPORT.md"
    report_path, report_issues = resolve_in_workspace(workspace, report_rel, must_exist=True, require_file=True)
    if report_issues or report_path is None:
        return None
    run_ledger = _load_optional_json(workspace, paths.get("run_ledger") or "simulation-run.json")
    if not isinstance(run_ledger, dict):
        return None
    result = run_ledger.get("result")
    if not isinstance(result, dict):
        return None
    payload = result.get("payload")
    final_state = payload.get("final_state") if isinstance(payload, dict) else None
    raw_summary = result.get("summary")
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    history = _run_history(result, payload)
    if not isinstance(final_state, dict) or not final_state:
        return None
    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text_lower = text.lower()
    nodes, edges, interventions = _extract_nodes_and_edges(workspace, paths)

    baselines: dict[str, float] = {}
    names: dict[str, str] = {}
    for n in nodes:
        nid = str(n.get("id") or "")
        if not nid:
            continue
        if isinstance(n.get("name"), str) and n["name"].strip():
            names[nid] = n["name"].strip()
        base_v = n.get("baseline")
        if base_v is None and isinstance(n.get("state_before"), dict):
            base_v = n["state_before"].get("value")
        if base_v is None:
            base_v = n.get("initial_value", 0.0)
        if not isinstance(base_v, bool) and isinstance(base_v, (int, float)) and math.isfinite(float(base_v)):
            baselines[nid] = float(base_v)

    ignored_inputs: set[float] = set()
    cp = manifest.get("change_point")
    if isinstance(cp, dict) and not isinstance(cp.get("magnitude"), bool) and isinstance(cp.get("magnitude"), (int, float)):
        ignored_inputs.add(float(cp["magnitude"]))
    for iv in interventions:
        if isinstance(iv, dict):
            for k in ("value", "magnitude"):
                v = iv.get(k)
                if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v)):
                    ignored_inputs.add(float(v))

    mismatches: list[str] = []

    # 1. Undisclosed numerical divergence / non-convergence
    issues_list = [i for i in (result.get("issues") or []) if isinstance(i, dict)]
    warn_list = [w for w in (run_ledger.get("warnings") or []) if isinstance(w, dict)]
    has_divergence_signal = (
        result.get("status") in {"failed", "error", "diverged"}
        or summary.get("diverged") is True
        or summary.get("converged") is False
        or any(
            item.get("code") in {"STATE_DIVERGENCE", "NONCONVERGENCE", "ITERATION_LIMIT_EXCEEDED", "EVENT_STORM"}
            for item in (*issues_list, *warn_list)
        )
        or any(
            isinstance(val, (int, float))
            and not isinstance(val, bool)
            and (not math.isfinite(float(val)) or abs(float(val)) >= 1e12)
            for val in final_state.values()
        )
    )
    if has_divergence_signal:
        disclosure_tokens = (
            "diverg",
            "unstable",
            "non-converg",
            "nonconverg",
            "convergence fail",
            "failed to converge",
            "blow-up",
            "blowup",
            "explod",
            "overflow",
            "state_divergence",
            "nonconvergence",
            "iteration_limit_exceeded",
            "limit exceeded",
            "phân kỳ",
            "bất ổn",
            "mất ổn định",
            "không ổn định",
            "không hội tụ",
            "vượt giới hạn",
            "tràn số",
        )
        if not any(tok in text_lower for tok in disclosure_tokens):
            mismatches.append("undisclosed numerical divergence or non-convergence in REPORT.md")

    # 2. Flat-baseline contradiction
    comparable_ids = [
        nid
        for nid, fval in final_state.items()
        if nid in baselines and isinstance(fval, (int, float)) and not isinstance(fval, bool) and math.isfinite(float(fval))
    ]
    all_final_flat = bool(comparable_ids) and all(
        abs(float(final_state[nid]) - baselines[nid]) <= 1e-9 for nid in comparable_ids
    )
    any_history_moved = any(
        isinstance(h, dict)
        and any(
            nid in h
            and isinstance(h[nid], (int, float))
            and not isinstance(h[nid], bool)
            and abs(float(h[nid]) - baselines[nid]) > 1e-9
            for nid in comparable_ids
        )
        for h in history
    )
    if all_final_flat and not any_history_moved and edges:
        claims_movement = bool(
            re.search(
                r"(?<!\w)(?:rose|fell|increased|decreased|declined|grew|dropped|surged|spiked|jumped|soared|collapsed|plummeted|shifted|tăng|giảm|sụt\s+giảm|suy\s+giảm|leo\s+thang|bùng\s+phát|lao\s+dốc)\s+(?:sharply|significantly|substantially|markedly|dramatically|steeply|rapidly|mạnh|nhanh|vọt|sâu|đáng\s+kể|rõ\s+rệt|đột\s+biến|by\s+[+-]?\d|(?:tới|lên|xuống|bằng)\s+[+-]?\d|[+-]?\d+(?:\.\d+)?\s*%)",
                text_lower,
            )
        )
        disclaims_flat = any(
            tok in text_lower
            for tok in (
                "unchanged",
                "zero propagation",
                "no change",
                "remained at baseline",
                "flat",
                "engine_frozen",
                "không đổi",
                "giữ nguyên",
                "không lan truyền",
                "đóng băng",
                "đi ngang",
            )
        )
        if claims_movement and not disclaims_flat:
            mismatches.append("narrative claims significant modeled movement although all simulated states remained flat at baseline")

    # 3. Explicit variable numeric or directional mismatches
    lines = text.splitlines()
    for nid in comparable_ids:
        final_val = float(final_state[nid])
        base_val = baselines[nid]
        net_delta = final_val - base_val
        idents = [nid]
        short_id = nid.split(":", 1)[1] if ":" in nid else ""
        if len(short_id) >= 4 and not short_id.isdigit():
            idents.append(short_id.replace("-", " "))
            idents.append(short_id)
        if nid in names and len(names[nid]) >= 4:
            idents.append(names[nid])
        # Deduplicate while preserving order
        seen_id: set[str] = set()
        unique_idents: list[str] = []
        for ident in idents:
            low = ident.lower()
            if low not in seen_id:
                seen_id.add(low)
                unique_idents.append(ident)
        ident_pat = "|".join(re.escape(i) for i in unique_idents)
        num_re = re.compile(
            rf"`?(?<!\w)(?:{ident_pat})(?!\w)`?(?:\s*\([^)]{{1,40}}\))?(?:\s+(?:đã|sẽ|vẫn|cũng|tiếp\s+tục|(?:tăng|giảm)(?:\s+(?:mạnh|vọt|nhanh|sâu))?\s+và))?\s*(?:=|final(?:_state)?(?:\s+value)?\s*(?:is|was|=|of)|ended\s+at|reached|rose\s+to|fell\s+to|dropped\s+to|settled\s+at|is\s+projected\s+to\s+reach|đạt(?:\s+mức|\s+tới|\s+ngưỡng)?|lên(?:\s+mức|\s+tới)|tăng\s+(?:lên|tới)|giảm\s+(?:xuống|về)|xuống(?:\s+mức|\s+tới)?|về\s+mức|chạm(?:\s+mức|\s+ngưỡng)?|ở\s+(?:mức|ngưỡng)|kết\s+thúc\s+(?:tại|ở)|dự\s+báo\s+(?:đạt|lên|xuống))\s*([+-]?\d+(?:\.\d+)?)",
            re.IGNORECASE,
        )
        up_re = re.compile(
            rf"`?(?<!\w)(?:{ident_pat})(?!\w)`?[^.\n]{{0,50}}(?<!not )(?<!không )(?<!chưa )(?<!\w)(?:increased|rose|grew|surged|spiked|jumped|soared|expanded|climbed|tăng|tăng\s+mạnh|tăng\s+vọt|tăng\s+trưởng|bật\s+tăng|đi\s+lên|leo\s+thang|bùng\s+phát|mở\s+rộng|vượt\s+mức|dâng\s+cao)(?!\w)",
            re.IGNORECASE,
        )
        down_re = re.compile(
            rf"`?(?<!\w)(?:{ident_pat})(?!\w)`?[^.\n]{{0,50}}(?<!not )(?<!không )(?<!chưa )(?<!\w)(?:decreased|fell|declined|dropped|collapsed|plummeted|contracted|shrank|giảm|giảm\s+mạnh|giảm\s+sâu|sụt\s+giảm|suy\s+giảm|suy\s+thoái|đi\s+xuống|tụt\s+dốc|lao\s+dốc|thu\s+hẹp|đổ\s+vỡ|hạ\s+nhiệt)(?!\w)",
            re.IGNORECASE,
        )
        recorded_for_node = False
        for line in lines:
            l_low = line.lower()
            if any(skip in l_low for skip in ("baseline", "change point", "counterfactual", "disconfirm", "branch", "alternative", "if ", "could ", "might ")):
                continue
            for m in num_re.finditer(line):
                try:
                    claimed = float(m.group(1))
                except ValueError:
                    continue
                if any(abs(claimed - ig) <= 1e-6 * max(1.0, abs(ig)) for ig in ignored_inputs):
                    continue
                if abs(claimed - base_val) <= max(0.1, 0.05 * max(1.0, abs(base_val))):
                    continue
                if abs(claimed - final_val) > max(0.5, 0.25 * max(1.0, abs(final_val))):
                    mismatches.append(f"{nid} reported {claimed} vs simulated final_state {round(final_val, 4)}")
                    recorded_for_node = True
                    break
            if recorded_for_node:
                break
            if abs(net_delta) > 1e-6 * max(1.0, abs(base_val)):
                has_up = bool(up_re.search(line))
                has_down = bool(down_re.search(line))
                if has_up and not has_down and net_delta < 0:
                    mismatches.append(f"{nid} described as increasing despite negative simulated delta ({net_delta:+.4g})")
                    break
                if has_down and not has_up and net_delta > 0:
                    mismatches.append(f"{nid} described as decreasing despite positive simulated delta ({net_delta:+.4g})")
                    break

    if mismatches:
        return {
            "severity": "warning",
            "code": "MODEL_NARRATIVE_FIDELITY",
            "message": "Narrative report contradicts simulated execution results: " + "; ".join(mismatches[:5]),
            "pointer": str(report_rel),
        }
    return None


_STRATEGIC_ENTITY_TYPES = frozenset(
    {"person", "leader", "government", "faction", "military", "state", "party", "organization"}
)
_STRATEGIC_DOMAIN_TOKENS = (
    "history",
    "geopolitic",
    "conflict",
    "war",
    "military",
    "crisis",
    "regime",
    "policy",
    "diploma",
    "protest",
    "security",
)


def _detect_missing_material_actors(
    workspace: Path,
    manifest: dict[str, Any],
    paths: dict[str, Any],
    material_actors: int,
) -> dict[str, Any] | None:
    if material_actors > 0:
        return None
    nodes, _, _ = _extract_nodes_and_edges(workspace, paths)
    actors_data = _load_optional_json_any(workspace, paths.get("actors") or "actors.json")
    non_example_entities = [
        node
        for node in nodes
        if isinstance(node, dict)
        and node.get("type") == "entity"
        and str(node.get("id") or "") not in {"", "entity:example"}
    ]
    strategic_typed = [
        node
        for node in non_example_entities
        if isinstance(node.get("details"), dict)
        and str(node["details"].get("entity_type") or "").lower() in _STRATEGIC_ENTITY_TYPES
    ]
    raw_scope = manifest.get("scope")
    scope = raw_scope if isinstance(raw_scope, dict) else {}
    domain_text = " ".join(
        str(part)
        for part in (
            manifest.get("domain"),
            scope.get("domain"),
            scope.get("topic"),
            scope.get("question"),
        )
        if isinstance(part, str)
    ).lower()
    strategic_domain = any(tok in domain_text for tok in _STRATEGIC_DOMAIN_TOKENS)
    all_actors_demoted = (
        isinstance(actors_data, list)
        and len(actors_data) > 0
        and any(isinstance(a, dict) and a.get("materiality") == "non_material" for a in actors_data)
        and (bool(non_example_entities) or strategic_domain)
    )
    if strategic_typed or (strategic_domain and non_example_entities) or all_actors_demoted:
        sample_ids = [str(n.get("id")) for n in (strategic_typed or non_example_entities)[:4]]
        return {
            "severity": "warning",
            "code": "MISSING_MATERIAL_ACTORS",
            "message": (
                "Strategic/human decision-making entities are present in the causal graph without any material actors "
                f"or roleplay tracks (entities={sample_ids or 'declared'})."
            ),
            "pointer": str(paths.get("actors") or "actors.json"),
        }
    return None


def evaluate(
    workspace: Path,
    *,
    validation: dict[str, Any] | None = None,
    final_receipt_verified: bool = False,
    actor_receipt_hmac_key: bytes | None = None,
    d_research_hmac_key: bytes | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    validation = validation or validate_workspace(workspace, mode="final", require_report=True)
    hard_fail = validation.get("status") != "pass"
    error_codes = set(validation.get("error_codes") or [])
    check_results = validation.get("check_results") or {}
    manifest = _manifest(workspace)
    raw_paths = manifest.get("artifact_paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}

    actor_metrics = (check_results.get("actor_protocol") or {}).get("metrics") or {}
    material_actors = int(actor_metrics.get("material_actors", 0) or 0)
    missing_actors_issue = _detect_missing_material_actors(workspace, manifest, paths, material_actors)
    missing_strategic_actors = missing_actors_issue is not None
    applicable = dict(SECTION_WEIGHTS)
    if material_actors == 0 and not missing_strategic_actors:
        applicable.pop("actor_roleplay_discipline", None)

    scores: dict[str, float] = {}
    path_ok = (check_results.get("paths") or {}).get("status") == "pass"
    manifest_ok = (check_results.get("manifest") or {}).get("status") == "pass"
    resource_ok = (check_results.get("resources") or {}).get("status") == "pass"
    stale_check = check_results.get("stale") or {}
    receipt_verified = final_receipt_verified or bool((stale_check.get("metrics") or {}).get("receipts_verified"))
    scores["structural_integrity"] = (
        applicable["structural_integrity"] if path_ok and manifest_ok and resource_ok and not hard_fail else 0.0
    )

    evidence = check_results.get("evidence") or {}
    evidence_metrics = evidence.get("metrics") or {}
    direct_ratio = float(evidence_metrics.get("direct_source_ratio", 0.0) or 0.0)
    if evidence.get("status") == "pass":
        scores["evidence_provenance"] = applicable["evidence_provenance"] * min(1.0, 0.5 + direct_ratio / 2)
    else:
        scores["evidence_provenance"] = 0.0

    trace_ok = (check_results.get("trace") or {}).get("status") == "pass"
    edge_ok = (check_results.get("edges") or {}).get("status") == "pass"
    temporal_ok = (check_results.get("temporal") or {}).get("status") == "pass"
    numerical = check_results.get("numerical_artifacts") or {}
    numerical_metrics = numerical.get("metrics") or {}
    numerical_replay_ok = manifest.get("simulation_mode") == "qualitative" or bool(
        numerical.get("status") == "pass"
        and numerical_metrics.get("model_present") is True
        and numerical_metrics.get("run_present") is True
        and numerical_metrics.get("replay_present") is True
        and numerical_metrics.get("independent_replay_passed") is True
    )
    scores["causal_semantics_replay"] = (
        applicable["causal_semantics_replay"]
        if trace_ok and edge_ok and temporal_ok and numerical_replay_ok
        else 0.0
    )

    roleplay_tier = _roleplay_tier(
        workspace,
        manifest,
        material_actors,
        hmac_key=actor_receipt_hmac_key,
    )
    if material_actors:
        actor_ok = (check_results.get("actor_protocol") or {}).get("status") == "pass"
        actor_fraction = 1.0 if roleplay_tier in {"A", "B"} else 0.6 if roleplay_tier == "C" else 0.0
        scores["actor_roleplay_discipline"] = applicable["actor_roleplay_discipline"] * actor_fraction if actor_ok else 0.0

    branch = check_results.get("branches") or {}
    near_duplicates = int((branch.get("metrics") or {}).get("near_duplicates", 0) or 0)
    scores["branch_uncertainty_calibration"] = (
        applicable["branch_uncertainty_calibration"] if branch.get("status") == "pass" and not near_duplicates
        else applicable["branch_uncertainty_calibration"] * 0.4 if branch.get("status") == "pass"
        else 0.0
    )
    report_ok = (check_results.get("report") or {}).get("status") == "pass"
    scores["report_reproducibility"] = applicable["report_reproducibility"] if report_ok else 0.0

    denominator = sum(applicable.values()) or 100.0
    diagnostic_score = round(100.0 * sum(scores.values()) / denominator, 2)
    d_research_verified = _d_research_verified(
        workspace,
        manifest,
        hmac_key=d_research_hmac_key,
    )
    simulation_mode = manifest.get("simulation_mode")
    sensitivity = _load_optional_json(workspace, paths.get("sensitivity_report"))
    sensitivity_passed = simulation_mode == "qualitative" or bool(
        sensitivity
        and sensitivity.get("degraded") is not True
        and isinstance(sensitivity.get("report_hash"), str)
        and isinstance(sensitivity.get("analysis"), dict)
    )
    calibration = _load_optional_json(workspace, paths.get("calibration_report"))
    if calibration is None:
        calibration = _load_optional_json(workspace, paths.get("calibration_bundle"))
    hash_key = "bundle_hash" if isinstance(calibration, dict) and "bundle_hash" in calibration else "report_hash"
    calibration_body = (
        {key: value for key, value in calibration.items() if key not in ("report_hash", "bundle_hash")}
        if isinstance(calibration, dict)
        else {}
    )
    cal_hash_ok = bool(
        calibration
        and hash_key in calibration
        and calibration.get(hash_key) == canonical_hash(calibration_body)
    )
    calibrated = bool(
        manifest.get("likelihood_mode") == "calibrated_probability"
        and numerical_metrics.get("calibration_verified") is True
        and not numerical_metrics.get("is_synthetic", False)
        and numerical_metrics.get("beats_baseline") is True
        and cal_hash_ok
    )
    raw_execution = manifest.get("execution")
    execution_cfg: dict[str, Any] = raw_execution if isinstance(raw_execution, dict) else {}
    raw_rc = execution_cfg.get("research_control")
    offline_provisional = bool(raw_rc.get("offline_provisional")) if isinstance(raw_rc, dict) else False

    validation_issues = [dict(item) for item in (validation.get("issues") or []) if isinstance(item, dict)]
    existing_codes = {str(item.get("code") or "") for item in validation_issues}
    extra_warnings: list[dict[str, Any]] = []
    if "ENGINE_FROZEN" not in existing_codes:
        frozen_issue = _detect_engine_frozen(workspace, manifest, paths)
        if frozen_issue is not None:
            extra_warnings.append(frozen_issue)
            validation_issues.append(frozen_issue)
    if "NARRATIVE_UNIT_MISMATCH" not in existing_codes:
        unit_issue = _detect_narrative_unit_mismatch(workspace, paths)
        if unit_issue is not None:
            extra_warnings.append(unit_issue)
            validation_issues.append(unit_issue)
    if "MODEL_NARRATIVE_FIDELITY" not in existing_codes:
        fidelity_issue = _detect_model_narrative_fidelity(workspace, manifest, paths)
        if fidelity_issue is not None:
            extra_warnings.append(fidelity_issue)
            validation_issues.append(fidelity_issue)
    if missing_actors_issue is not None and "MISSING_MATERIAL_ACTORS" not in existing_codes:
        extra_warnings.append(missing_actors_issue)
        validation_issues.append(missing_actors_issue)
        existing_codes.add("MISSING_MATERIAL_ACTORS")
    if "ZERO_INTERVENTION_DRIFT" not in existing_codes:
        run_ledger_doc = _load_optional_json(workspace, paths.get("run_ledger") or "simulation-run.json")
        if isinstance(run_ledger_doc, dict):
            for rw in run_ledger_doc.get("warnings") or []:
                if isinstance(rw, dict) and rw.get("code") == "ZERO_INTERVENTION_DRIFT":
                    drift_issue = {
                        "severity": "warning",
                        "code": "ZERO_INTERVENTION_DRIFT",
                        "message": str(rw.get("message") or "non-intervention run drifted from declared baselines under Formula 2.2.0"),
                        "pointer": str(rw.get("pointer") or paths.get("run_ledger") or "simulation-run.json"),
                    }
                    extra_warnings.append(drift_issue)
                    validation_issues.append(drift_issue)
                    existing_codes.add("ZERO_INTERVENTION_DRIFT")
                    break

    errors_detail = [item for item in validation_issues if item.get("severity") == "error"]
    warnings_detail = [item for item in validation_issues if item.get("severity") == "warning"]
    errors_text = list(validation.get("errors", []))
    warnings_text = list(validation.get("warnings", []))
    for item in extra_warnings:
        pointer = item.get("pointer")
        loc = f" ({pointer})" if pointer else ""
        warnings_text.append(f"[{item.get('code')}] {item.get('message')}{loc}")

    high_severity_warning = bool(
        near_duplicates
        or error_codes
        & {
            "TRACE_FORMULA_MISMATCH",
            "TRACE_AMPLIFICATION",
            "REPLAY_MISMATCH",
            "STALE_ARTIFACT",
            "HMAC_TAMPER",
            "LEDGER_TAMPER",
        }
    )

    verified_requirements = {
        "validation_passed": not hard_fail,
        "receipt_verified": receipt_verified,
        "d_research_verified": d_research_verified,
        "semantic_replay_passed": trace_ok and edge_ok and temporal_ok and numerical_replay_ok,
        "roleplay_tier_sufficient": (material_actors == 0 and not missing_strategic_actors) or roleplay_tier == "A",
        "sensitivity_passed": sensitivity_passed,
        "no_high_severity_warning": not high_severity_warning,
    }
    if hard_fail:
        tier: str | None = None
        assurance = "failed"
    elif all(verified_requirements.values()):
        if calibrated:
            tier = "calibrated"
            assurance = "calibrated"
        elif numerical_metrics.get("is_synthetic"):
            tier = "experimental"
            assurance = "synthetic_experimental"
        elif numerical_metrics.get("calibration_present") and not numerical_metrics.get("beats_baseline"):
            tier = "uncalibrated"
            assurance = "uncalibrated"
        else:
            tier = "verified"
            assurance = tier
    elif validation.get("status") == "pass":
        if numerical_metrics.get("is_synthetic"):
            tier = "limited"
            assurance = "synthetic_experimental"
        elif numerical_metrics.get("calibration_present") and not numerical_metrics.get("beats_baseline"):
            tier = "limited"
            assurance = "uncalibrated"
        else:
            tier = "limited"
            assurance = "limited"
    else:
        tier = "experimental"
        assurance = "experimental"

    if offline_provisional and tier in {"verified", "calibrated"}:
        tier = "limited"
        assurance = "limited"

    grade = (
        "fail" if hard_fail
        else "excellent" if diagnostic_score >= 90 and tier in {"verified", "calibrated"}
        else "partial" if diagnostic_score >= 70
        else "fail"
    )
    quality_gates = {
        **verified_requirements,
        "hard_gates_passed": not hard_fail,
        "near_duplicate_ok": not near_duplicates,
        "offline_provisional": offline_provisional,
        "score_is_diagnostic_only": True,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(workspace),
        "score": diagnostic_score,
        "diagnostic_score": diagnostic_score,
        "grade": grade,
        "assurance_tier": tier,
        "assurance_status": assurance,
        "quality_gates": quality_gates,
        "roleplay_tier": roleplay_tier,
        "sections": {key: round(value, 2) for key, value in scores.items()},
        "section_weights": applicable,
        "validation_status": validation.get("status"),
        "error_codes": sorted(error_codes),
        "errors": errors_text,
        "warnings": warnings_text,
        "errors_detail": errors_detail,
        "warnings_detail": warnings_detail,
        "metrics": validation.get("metrics", {}),
        "release_claim": tier in {"verified", "calibrated"},
        "note": "diagnostic score cannot override hard gates; excellent is a display grade only",
    }
