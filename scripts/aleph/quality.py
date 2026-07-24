"""Evidence-derived assurance tiers and diagnostic scoring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import LEGACY_FORMULA_VERSION, SCHEMA_VERSION
from .discovery import discover_d_research
from .import_ledger import import_d_research_ledger, render_evidence_csv
from .io import canonical_hash, load_json_secure, load_jsonl_secure, sha256_file
from .packets import (
    receipt_binds_ledger_artifacts,
    validate_actor_protocol,
    verify_receipt_artifact_bytes,
    verify_receipt_chain,
)
from .paths import resolve_in_workspace
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
    try:
        return bool(
            imported.get("ok")
            and imported.get("hmac_verified") is True
            and receipt.get("schema_version") == SCHEMA_VERSION
            and receipt.get("receipt_type") == "d-research-import"
            and receipt.get("mapping_contract") == "d-research-3.x-canonical"
            and receipt.get("source_contract") in {
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
    applicable = dict(SECTION_WEIGHTS)
    if material_actors == 0:
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
    calibration_body = (
        {key: value for key, value in calibration.items() if key != "report_hash"}
        if isinstance(calibration, dict)
        else {}
    )
    calibrated = bool(
        manifest.get("likelihood_mode") == "calibrated_probability"
        and calibration
        and calibration.get("status") == "pass"
        and calibration.get("policy_locked") is True
        and calibration.get("beats_baseline") is True
        and calibration.get("formula_version")
        == manifest.get("formula_version", LEGACY_FORMULA_VERSION)
        and calibration.get("case_count") == calibration.get("unique_case_count")
        and isinstance(calibration.get("case_count"), int)
        and calibration.get("case_count", 0) >= 30
        and calibration.get("report_hash") == canonical_hash(calibration_body)
        and numerical_metrics.get("calibration_present") is True
    )
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
        "roleplay_tier_sufficient": material_actors == 0 or roleplay_tier == "A",
        "sensitivity_passed": sensitivity_passed,
        "no_high_severity_warning": not high_severity_warning,
    }
    if hard_fail:
        tier: str | None = None
        assurance = "failed"
    elif all(verified_requirements.values()):
        tier = "calibrated" if calibrated else "verified"
        assurance = tier
    elif validation.get("status") == "pass":
        tier = "limited"
        assurance = "limited"
    else:
        tier = "experimental"
        assurance = "experimental"

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
        "errors": validation.get("errors", []),
        "warnings": validation.get("warnings", []),
        "metrics": validation.get("metrics", {}),
        "release_claim": tier in {"verified", "calibrated"},
        "note": "diagnostic score cannot override hard gates; excellent is a display grade only",
    }
