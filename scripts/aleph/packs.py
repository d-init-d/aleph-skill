"""Semantic validation and executable gates for data-only domain packs."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from .engine import (
    EngineConfig,
    compile_model,
    config_payload,
    model_hash,
    run_deterministic,
    run_monte_carlo,
)
from .io import canonical_hash, sha256_file
from .issues import Issue, issue

PACK_NAMES = ("economics", "policy", "history", "climate", "healthcare", "technology", "geopolitics")
MATURITY = frozenset({"experimental", "validated", "calibrated"})
RELATIONS = {"increases": 1, "decreases": -1, "enables": 1, "inhibits": -1, "correlates": 0}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def discover_pack_roots(
    *, explicit: list[Path] | None = None, config_path: Path | None = None, skill_root: Path | None = None
) -> list[Path]:
    roots: list[Path] = list(explicit or [])
    if config_path and config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            roots.extend(Path(value) for value in config.get("domain_packs", []) if isinstance(value, str))
        except (OSError, json.JSONDecodeError):
            pass
    roots.extend(Path(value) for value in os.environ.get("ALEPH_DOMAIN_PACKS", "").split(os.pathsep) if value.strip())
    if skill_root and (skill_root / "packs").is_dir():
        roots.append(skill_root / "packs")
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _json(path: Path, issues: list[Issue]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append(issue("INVALID_ARTIFACT", artifact=path.name, message=str(exc)))
        return None


def _validate_variables(data: Any, name: str, issues: list[Issue]) -> set[str]:
    if not isinstance(data, dict) or data.get("pack") != name or not isinstance(data.get("variables"), list):
        issues.append(issue("SCHEMA", artifact="variables.json", message="expected {pack, variables:[...]}"))
        return set()
    identifiers: set[str] = set()
    for index, variable in enumerate(data["variables"]):
        pointer = f"/variables/{index}"
        if not isinstance(variable, dict):
            issues.append(issue("TYPE", artifact="variables.json", pointer=pointer, message="variable must be object"))
            continue
        identifier = variable.get("id")
        if not isinstance(identifier, str) or not identifier.startswith(f"{name}:"):
            issues.append(issue("SCHEMA", artifact="variables.json", pointer=f"{pointer}/id", message="domain-prefixed id required"))
        elif identifier in identifiers:
            issues.append(issue("DUPLICATE_ID", artifact="variables.json", pointer=f"{pointer}/id", actual=identifier))
        else:
            identifiers.add(identifier)
        if variable.get("role") not in {"endogenous", "exogenous", "observable", "intervention"}:
            issues.append(issue("ENUM", artifact="variables.json", pointer=f"{pointer}/role", actual=variable.get("role")))
        if variable.get("datatype") not in {"continuous", "integer", "binary", "categorical"}:
            issues.append(issue("ENUM", artifact="variables.json", pointer=f"{pointer}/datatype", actual=variable.get("datatype")))
        if not isinstance(variable.get("unit"), str) or not variable.get("unit"):
            issues.append(issue("MISSING_FIELD", artifact="variables.json", pointer=f"{pointer}/unit", message="unit required"))
        bounds = variable.get("bounds")
        baseline = variable.get("baseline")
        if not isinstance(bounds, list) or len(bounds) != 2 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in bounds):
            issues.append(issue("TYPE", artifact="variables.json", pointer=f"{pointer}/bounds", message="finite [min,max] required"))
        elif (
            bounds[0] > bounds[1]
            or isinstance(baseline, bool)
            or not isinstance(baseline, (int, float))
            or not math.isfinite(baseline)
            or not bounds[0] <= baseline <= bounds[1]
        ):
            issues.append(issue("RANGE", artifact="variables.json", pointer=pointer, message="baseline must lie within ordered bounds"))
    return identifiers


def _validate_mechanisms(data: Any, name: str, issues: list[Issue]) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("templates"), list) or not data["templates"]:
        issues.append(issue("SCHEMA", artifact="mechanisms.json", message="non-empty templates list required"))
        return
    for index, mechanism in enumerate(data["templates"]):
        pointer = f"/templates/{index}"
        if not isinstance(mechanism, dict):
            issues.append(issue("TYPE", artifact="mechanisms.json", pointer=pointer, message="object required"))
            continue
        relation = mechanism.get("relation")
        sign = mechanism.get("sign")
        if relation not in RELATIONS or sign not in {-1, 0, 1} or RELATIONS.get(relation) != sign:
            issues.append(issue("RELATION", artifact="mechanisms.json", pointer=pointer, message="relation/sign mismatch"))
        if not str(mechanism.get("id", "")).startswith(f"mech:{name}:"):
            issues.append(issue("SCHEMA", artifact="mechanisms.json", pointer=f"{pointer}/id", message="domain mechanism id required"))
        if len(str(mechanism.get("description", ""))) < 40:
            issues.append(issue("MECHANISM", artifact="mechanisms.json", pointer=f"{pointer}/description", message="mechanism description too short"))


def _validate_priors(data: Any, name: str, issues: list[Issue]) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("parameters"), list) or not data["parameters"]:
        issues.append(issue("SCHEMA", artifact="priors.json", message="non-empty parameters list required"))
        return
    for index, parameter in enumerate(data["parameters"]):
        pointer = f"/parameters/{index}"
        if not isinstance(parameter, dict) or not str(parameter.get("id", "")).startswith(f"prior:{name}:"):
            issues.append(issue("SCHEMA", artifact="priors.json", pointer=pointer, message="domain prior id required"))
            continue
        distribution = parameter.get("distribution")
        if distribution not in {"fixed", "uniform", "triangular", "normal"}:
            issues.append(issue("ENUM", artifact="priors.json", pointer=f"{pointer}/distribution", actual=distribution))
        if distribution in {"uniform", "triangular"}:
            low, high = parameter.get("min"), parameter.get("max")
            if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or low >= high:
                issues.append(issue("RANGE", artifact="priors.json", pointer=pointer, message="ordered finite min/max required"))


def _fixture_model(raw: dict[str, Any]) -> tuple[Any, EngineConfig, int]:
    raw_model_data = raw.get("model")
    model_data: dict[str, Any] = raw_model_data if isinstance(raw_model_data, dict) else {}
    raw_nodes = model_data.get("nodes")
    raw_edges = model_data.get("edges")
    raw_interventions = model_data.get("interventions")
    model = compile_model(
        raw_nodes if isinstance(raw_nodes, list) else [],
        raw_edges if isinstance(raw_edges, list) else [],
        raw_interventions if isinstance(raw_interventions, list) else [],
    )
    raw_config_data = raw.get("config")
    config_data: dict[str, Any] = raw_config_data if isinstance(raw_config_data, dict) else {}
    allowed = set(EngineConfig.__dataclass_fields__)
    config = EngineConfig(**{key: value for key, value in config_data.items() if key in allowed})
    return model, config, int(raw.get("ticks", 1))


def _run_fixture(path: Path, issues: list[Issue]) -> bool:
    raw = _json(path, issues)
    if not isinstance(raw, dict) or raw.get("schema_version") != "2.0.0" or raw.get("mode") not in {"deterministic", "monte_carlo"}:
        issues.append(issue("SCHEMA", artifact=str(path), message="executable fixture schema/mode required"))
        return False
    try:
        model, config, ticks = _fixture_model(raw)
        if not model.variables or not model.edges:
            raise ValueError("fixture model must contain variables and edges")
        config.mode = str(raw["mode"])
        if config.mode == "deterministic":
            result = run_deterministic(model, config, ticks=ticks)
            if not result["ok"]:
                raise ValueError("deterministic fixture did not converge")
            expected = raw.get("expected_final_state")
            if not isinstance(expected, dict):
                raise ValueError("expected_final_state object required")
            tolerance = float(raw.get("tolerance", 1e-9))
            actual = result["payload"]["final_state"]
            for key, value in expected.items():
                if key not in actual or not math.isclose(float(actual[key]), float(value), abs_tol=tolerance, rel_tol=tolerance):
                    raise ValueError(f"unexpected final state for {key}")
        else:
            result = run_monte_carlo(model, config, ticks=ticks)
            if not result["ok"]:
                raise ValueError("Monte Carlo fixture failed hard gates")
            expected = raw.get("expected")
            if not isinstance(expected, dict):
                raise ValueError("Monte Carlo expected object required")
            if result["summary"]["mass_balance_error"] > float(expected.get("max_mass_error", 1e-12)):
                raise ValueError("Monte Carlo mass is not conserved")
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(issue("PACK_MATURITY", artifact=str(path), message=f"fixture failed: {exc}"))
        return False
    return True


def _utc_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evidence_snapshot_hash(evidence: list[dict[str, Any]]) -> str:
    """Return an order-independent canonical hash of a hindcast evidence snapshot."""
    ordered = sorted(evidence, key=lambda row: (str(row.get("id", "")), canonical_hash(row)))
    return canonical_hash(ordered)


def hindcast_commitment_payload(
    case: dict[str, Any],
    *,
    model_digest: str,
    config_digest: str,
    snapshot_digest: str,
    ticks: int,
) -> dict[str, Any]:
    observations = case.get("observations")
    baselines = case.get("baselines")
    return {
        "commitment_version": "aleph-hindcast-commitment-v2",
        "case_id": case.get("case_id"),
        "outcome_dataset": case.get("outcome_dataset"),
        "cutoff": case.get("cutoff"),
        "model_hash": model_digest,
        "config_hash": config_digest,
        "ticks": ticks,
        "evidence_snapshot_hash": snapshot_digest,
        "observations": observations if isinstance(observations, dict) else {},
        "baselines": baselines if isinstance(baselines, dict) else {},
    }


def evaluate_hindcast_case(case: dict[str, Any], *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    problems: list[Issue] = []
    cutoff = case.get("cutoff")
    try:
        cutoff_dt = _utc_datetime(cutoff)
    except (TypeError, ValueError):
        cutoff_dt = None
        problems.append(issue("SCHEMA", pointer="/cutoff", message="ISO-8601 cutoff required"))

    raw_evidence = case.get("evidence")
    evidence_rows: list[dict[str, Any]] = []
    if not isinstance(raw_evidence, list):
        problems.append(issue("TYPE", pointer="/evidence", message="evidence must be an array"))
    else:
        for index, evidence in enumerate(raw_evidence):
            if not isinstance(evidence, dict) or not evidence.get("available_at"):
                problems.append(issue("SCHEMA", pointer=f"/evidence/{index}", message="available_at required"))
                continue
            evidence_rows.append(evidence)
            try:
                available = _utc_datetime(evidence["available_at"])
                if cutoff_dt and available > cutoff_dt:
                    problems.append(
                        issue(
                            "PACKET_CUTOFF",
                            pointer=f"/evidence/{index}",
                            actual=evidence.get("id"),
                            message="post-cutoff leakage",
                        )
                    )
            except (TypeError, ValueError):
                problems.append(issue("SCHEMA", pointer=f"/evidence/{index}/available_at", message="ISO-8601 required"))
    declared_snapshot = case.get("evidence_snapshot_hash")
    actual_snapshot = evidence_snapshot_hash(evidence_rows)
    if not isinstance(declared_snapshot, str) or not HEX64.fullmatch(declared_snapshot):
        problems.append(issue("SCHEMA", pointer="/evidence_snapshot_hash", message="64-char SHA-256 required"))
    elif declared_snapshot != actual_snapshot:
        problems.append(
            issue(
                "STALE_ARTIFACT",
                pointer="/evidence_snapshot_hash",
                expected=actual_snapshot,
                actual=declared_snapshot,
                message="evidence snapshot digest mismatch",
            )
        )
    if policy is not None:
        if policy.get("precommitted") is not True:
            problems.append(issue("PACK_MATURITY", pointer="/policy/precommitted", message="precommitted policy required"))
        if policy.get("commitment_version") != "aleph-hindcast-commitment-v2":
            problems.append(
                issue(
                    "PACK_MATURITY",
                    pointer="/policy/commitment_version",
                    expected="aleph-hindcast-commitment-v2",
                    actual=policy.get("commitment_version"),
                    message="current hindcast commitment contract required",
                )
            )

    raw_model_data = case.get("model")
    model_data: dict[str, Any] = raw_model_data if isinstance(raw_model_data, dict) else {}
    raw_nodes = model_data.get("nodes")
    raw_edges = model_data.get("edges")
    raw_interventions = model_data.get("interventions")
    try:
        model = compile_model(
            raw_nodes if isinstance(raw_nodes, list) else [],
            raw_edges if isinstance(raw_edges, list) else [],
            raw_interventions if isinstance(raw_interventions, list) else [],
        )
    except (TypeError, ValueError) as exc:
        problems.append(issue("SCHEMA", pointer="/model", message=str(exc)))
        return {"ok": False, "issues": [value.to_dict() for value in problems]}
    observations = case.get("observations")
    baselines = case.get("baselines")
    if not model.variables or not isinstance(observations, dict) or not observations or not isinstance(baselines, dict):
        problems.append(issue("SCHEMA", message="inline model, observations, and baselines are required"))
    raw_config_data = case.get("config")
    config_data: dict[str, Any] = raw_config_data if isinstance(raw_config_data, dict) else {}
    allowed = set(EngineConfig.__dataclass_fields__)
    try:
        config = EngineConfig(**{key: value for key, value in config_data.items() if key in allowed})
        config.mode = "deterministic"
        ticks = int(case.get("ticks", 1))
    except (TypeError, ValueError) as exc:
        problems.append(issue("SCHEMA", pointer="/config", message=str(exc)))
        return {"ok": False, "issues": [value.to_dict() for value in problems]}
    model_digest = model_hash(model)
    config_digest = canonical_hash(config_payload(config))
    commitment_payload = hindcast_commitment_payload(
        case,
        model_digest=model_digest,
        config_digest=config_digest,
        snapshot_digest=actual_snapshot,
        ticks=ticks,
    )
    commitment_digest = canonical_hash(commitment_payload)
    policy_locked = False
    if policy is not None and policy.get("precommitted") is True:
        commitments = policy.get("case_commitments")
        case_id = case.get("case_id")
        if commitments is not None and not isinstance(commitments, dict):
            problems.append(issue("TYPE", pointer="/policy/case_commitments", message="must be object"))
        elif isinstance(commitments, dict):
            expected_commitment = commitments.get(case_id)
            policy_locked = isinstance(expected_commitment, str) and expected_commitment == commitment_digest
            if not policy_locked:
                problems.append(
                    issue(
                        "STALE_ARTIFACT",
                        pointer=f"/policy/case_commitments/{case_id}",
                        expected=commitment_digest,
                        actual=expected_commitment,
                        message="hindcast policy commitment mismatch",
                    )
                )
    if problems:
        return {"ok": False, "issues": [value.to_dict() for value in problems]}

    result = run_deterministic(model, config, ticks=ticks)
    observations = cast(dict[str, Any], observations)
    baselines = cast(dict[str, Any], baselines)
    predictions = {key: result["payload"]["final_state"].get(key) for key in observations}
    if not result["ok"] or any(value is None for value in predictions.values()) or any(key not in baselines for key in observations):
        return {"ok": False, "issues": [issue("PACK_MATURITY", message="hindcast model/output invalid").to_dict()]}
    try:
        errors = [float(predictions[key]) - float(observations[key]) for key in sorted(observations)]
        baseline_errors = [float(baselines[key]) - float(observations[key]) for key in sorted(observations)]
    except (TypeError, ValueError) as exc:
        return {"ok": False, "issues": [issue("TYPE", pointer="/observations", message=str(exc)).to_dict()]}
    metrics = {
        "mae": sum(abs(value) for value in errors) / len(errors),
        "rmse": math.sqrt(sum(value * value for value in errors) / len(errors)),
        "baseline_mae": sum(abs(value) for value in baseline_errors) / len(baseline_errors),
        "baseline_rmse": math.sqrt(sum(value * value for value in baseline_errors) / len(baseline_errors)),
    }
    return {
        "ok": True,
        "case_id": case.get("case_id"),
        "cutoff": cutoff,
        "evidence_snapshot_hash": actual_snapshot,
        "model_version": "aleph-engine-2.0",
        "model_hash": model_digest,
        "config_hash": config_digest,
        "commitment_hash": commitment_digest,
        "predictions": predictions,
        "observations": observations,
        "metrics": metrics,
        "beats_baseline": metrics["mae"] < metrics["baseline_mae"],
        "policy_locked": policy_locked,
    }


def validate_pack(pack_dir: Path) -> dict[str, Any]:
    issues: list[Issue] = []
    manifest_path = pack_dir / "pack-manifest.json"
    manifest = _json(manifest_path, issues) if manifest_path.is_file() else None
    if not isinstance(manifest, dict):
        if not manifest_path.is_file():
            issues.append(issue("MISSING_ARTIFACT", artifact=str(pack_dir), message="pack-manifest.json missing"))
        return {"ok": False, "issues": [value.to_dict() for value in issues], "maturity": None}
    name = manifest.get("name")
    if name not in PACK_NAMES or name != pack_dir.name:
        issues.append(issue("ENUM", pointer="/name", actual=name, expected=pack_dir.name, message="pack name mismatch"))
    declared = manifest.get("maturity")
    if declared not in MATURITY:
        issues.append(issue("ENUM", pointer="/maturity", actual=declared))
        declared = "experimental"
    for key in ("version", "spdx", "license", "source_url", "description"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            issues.append(issue("MISSING_FIELD", pointer=f"/{key}", message="non-empty manifest metadata required"))
    if "example.org" in str(manifest.get("source_url", "")):
        issues.append(issue("SOURCE_QUALITY", pointer="/source_url", message="placeholder source URL forbidden"))
    required = ("variables.json", "mechanisms.json", "priors.json", "calibration-policy.json", "safety-notes.md")
    for filename in required:
        if not (pack_dir / filename).is_file():
            issues.append(issue("MISSING_ARTIFACT", artifact=filename, message="required pack file missing"))
    if any(pack_dir.rglob("*.py")):
        issues.append(issue("PACK_MATURITY", message="executable Python is forbidden in data-only packs"))
    variables = _json(pack_dir / "variables.json", issues) if (pack_dir / "variables.json").is_file() else None
    mechanisms = _json(pack_dir / "mechanisms.json", issues) if (pack_dir / "mechanisms.json").is_file() else None
    priors = _json(pack_dir / "priors.json", issues) if (pack_dir / "priors.json").is_file() else None
    policy = _json(pack_dir / "calibration-policy.json", issues) if (pack_dir / "calibration-policy.json").is_file() else None
    _validate_variables(variables, str(name), issues)
    _validate_mechanisms(mechanisms, str(name), issues)
    _validate_priors(priors, str(name), issues)
    if (
        not isinstance(policy, dict)
        or policy.get("precommitted") is not True
        or policy.get("commitment_version") != "aleph-hindcast-commitment-v2"
        or not isinstance(policy.get("metrics"), list)
        or not isinstance(policy.get("min_oos_cases"), int)
        or policy.get("min_oos_cases", 0) < 30
        or not isinstance(policy.get("case_commitments"), dict)
        or not policy.get("case_commitments")
    ):
        issues.append(issue("SCHEMA", artifact="calibration-policy.json", message="precommitted metrics policy required"))
    safety_text = ""
    if (pack_dir / "safety-notes.md").is_file():
        try:
            safety_text = (pack_dir / "safety-notes.md").read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(issue("INVALID_ARTIFACT", artifact="safety-notes.md", message=str(exc)))
    if name == "healthcare" and not ("population" in safety_text and "patient" in safety_text):
        issues.append(issue("PACK_MATURITY", artifact="safety-notes.md", message="healthcare pack must state population scope and patient prohibition"))
    if name == "geopolitics" and not ("operational" in safety_text and "targeting" in safety_text):
        issues.append(issue("PACK_MATURITY", artifact="safety-notes.md", message="geopolitics pack must forbid operational targeting"))

    datasets_ok = True
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        issues.append(issue("MISSING_FIELD", pointer="/datasets", message="at least one hashed dataset required"))
        datasets_ok = False
    else:
        for index, dataset in enumerate(datasets):
            if not isinstance(dataset, dict) or not all(dataset.get(key) for key in ("path", "sha256", "spdx", "provenance")):
                issues.append(issue("SCHEMA", pointer=f"/datasets/{index}", message="path, sha256, spdx, provenance required"))
                datasets_ok = False
                continue
            relative = str(dataset["path"])
            path = (pack_dir / relative).resolve()
            try:
                path.relative_to(pack_dir.resolve())
            except ValueError:
                issues.append(issue("PATH_ESCAPE", pointer=f"/datasets/{index}/path", actual=relative))
                datasets_ok = False
                continue
            if not path.is_file() or not HEX64.fullmatch(str(dataset["sha256"])):
                issues.append(issue("INVALID_ARTIFACT", artifact=relative, message="dataset or SHA-256 invalid"))
                datasets_ok = False
            elif sha256_file(path) != dataset["sha256"]:
                issues.append(issue("STALE_ARTIFACT", artifact=relative, expected=dataset["sha256"], actual=sha256_file(path)))
                datasets_ok = False

    fixture_results = {}
    for mode in ("deterministic", "monte_carlo"):
        fixture_path = pack_dir / "fixtures" / f"{mode}.json"
        fixture_results[mode] = fixture_path.is_file() and _run_fixture(fixture_path, issues)
        if not fixture_path.is_file():
            issues.append(issue("MISSING_ARTIFACT", artifact=str(fixture_path), message="executable fixture missing"))
    hindcast_results = []
    for path in sorted((pack_dir / "hindcast").glob("*.json")) if (pack_dir / "hindcast").is_dir() else []:
        case = _json(path, issues)
        result = evaluate_hindcast_case(case, policy=policy) if isinstance(case, dict) else {"ok": False}
        hindcast_results.append({"path": path.name, **result})
        if not result.get("ok"):
            issues.append(issue("PACK_MATURITY", artifact=str(path), message="hindcast case failed"))

    semantic_ok = not any(value.severity == "error" for value in issues)
    validated_gate = semantic_ok and datasets_ok and all(fixture_results.values())
    min_cases = int(policy.get("min_oos_cases", 30)) if isinstance(policy, dict) else 30
    non_synthetic = all(dataset.get("provenance") != "synthetic" for dataset in datasets if isinstance(dataset, dict)) if isinstance(datasets, list) else False
    case_ids = [value.get("case_id") for value in hindcast_results]
    unique_cases = len(case_ids) == len(set(case_ids)) and all(isinstance(value, str) and value for value in case_ids)
    outcome_datasets = [value.get("outcome_dataset") for value in hindcast_results]
    outcomes_independent = bool(outcome_datasets) and all(
        isinstance(value, dict)
        and all(isinstance(value.get(key), str) and value.get(key) for key in ("source", "sha256", "observed_at"))
        and HEX64.fullmatch(str(value.get("sha256"))) is not None
        for value in outcome_datasets
    )
    thresholds = policy.get("thresholds") if isinstance(policy, dict) else None
    threshold_metrics = policy.get("metrics") if isinstance(policy, dict) else None
    thresholds_pass = bool(
        isinstance(thresholds, dict)
        and isinstance(threshold_metrics, list)
        and threshold_metrics
        and all(
            isinstance(thresholds.get(metric), (int, float))
            and not isinstance(thresholds.get(metric), bool)
            and math.isfinite(float(thresholds[metric]))
            and all(
                isinstance((value.get("metrics") or {}).get(metric), (int, float))
                and float((value.get("metrics") or {})[metric]) <= float(thresholds[metric])
                for value in hindcast_results
            )
            for metric in threshold_metrics
        )
    )
    calibrated_gate = (
        validated_gate
        and non_synthetic
        and len(hindcast_results) >= min_cases
        and unique_cases
        and outcomes_independent
        and thresholds_pass
        and all(
            value.get("ok") and value.get("policy_locked") and value.get("beats_baseline") is True
            for value in hindcast_results
        )
    )
    gate_maturity = "calibrated" if calibrated_gate else ("validated" if validated_gate else "experimental")
    rank = {"experimental": 0, "validated": 1, "calibrated": 2}
    if rank.get(str(declared), 0) > rank[gate_maturity]:
        issues.append(issue("PACK_MATURITY", pointer="/maturity", actual=declared, expected=gate_maturity, message="declared maturity exceeds executable evidence"))
    maturity = str(declared) if rank.get(str(declared), 0) <= rank[gate_maturity] else gate_maturity
    ok = not any(value.severity == "error" for value in issues)
    return {
        "ok": ok,
        "name": name,
        "maturity": maturity,
        "gate_maturity": gate_maturity,
        "declared_maturity": declared,
        "can_emit_probability": maturity == "calibrated" and calibrated_gate,
        "fixture_results": fixture_results,
        "hindcast_cases": len(hindcast_results),
        "issues": [value.to_dict() for value in issues],
        "path": str(pack_dir),
    }


def validate_all_packs(skill_root: Path) -> dict[str, Any]:
    root = skill_root / "packs"
    if not root.is_dir():
        return {"ok": False, "packs": [], "count": 0, "error": "no packs directory"}
    results = [
        validate_pack(root / name)
        if (root / name).is_dir()
        else {"ok": False, "name": name, "maturity": None, "issues": [issue("MISSING_ARTIFACT", message=f"pack {name} missing").to_dict()]}
        for name in PACK_NAMES
    ]
    return {
        "ok": all(value.get("ok") for value in results),
        "packs": results,
        "count": len(results),
        "all_validated": all(value.get("maturity") in {"validated", "calibrated"} for value in results),
        "all_semantically_valid": all(value.get("ok") for value in results),
    }


def refuse_uncalibrated_probability(pack_maturity: str, field_name: str = "probability") -> Issue | None:
    if pack_maturity != "calibrated":
        return issue("PACK_PROBABILITY", pointer=field_name, actual=pack_maturity, message="uncalibrated pack cannot emit probability")
    return None
