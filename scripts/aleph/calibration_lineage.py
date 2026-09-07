"""Resolve calibration evidence and replay each forecast before grading it.

Local hashes establish consistency, not historical authenticity. Optional host
trust receipts are loaded from outside the candidate workspace; only their
explicit scope can authorize an empirical probability-calibration claim.
"""
from __future__ import annotations

import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import EngineConfig, compile_model, config_payload, model_hash, run_deterministic
from .io import canonical_hash, load_json_secure, sha256_file
from .issues import Issue, issue
from .paths import resolve_in_workspace


def utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("expected a finite JSON number")
    return float(value)


def forecast_payload(case: dict[str, Any]) -> dict[str, Any]:
    """Commit predictions and inputs before joining realized outcomes."""
    keys = ("case_id", "forecast_origin", "target_period", "model_hash", "formula_version",
            "point_prediction", "baseline_prediction", "execution")
    payload = {key: case.get(key) for key in keys}
    payload["evidence"] = [e for e in case.get("evidence", []) if isinstance(e, dict) and e.get("role") == "input"]
    return payload


def policy_design_hash(policy: dict[str, Any]) -> str:
    """Freeze evaluation design separately from subsequently accumulated results."""
    return canonical_hash({k: v for k, v in policy.items()
                           if k not in {"policy_hash", "case_commitments", "forecast_commitments"}})


def _artifact(workspace: Path, ref: Any) -> Path:
    if not isinstance(ref, dict):
        raise ValueError("artifact reference must be an object")
    path, errors = resolve_in_workspace(workspace, str(ref.get("file_path", "")), must_exist=True, require_file=True)
    if path is None or errors:
        raise ValueError("missing or unsafe artifact path")
    digest = ref.get("sha256") or ref.get("sha256_digest")
    if not isinstance(digest, str) or sha256_file(path) != digest.removeprefix("sha256:"):
        raise ValueError("artifact digest mismatch or missing digest")
    return path


def _json(path: Path) -> dict[str, Any]:
    data, errors = load_json_secure(path)
    if errors or not isinstance(data, dict):
        raise ValueError("invalid JSON object")
    return data


def resolve_observation(workspace: Path, ref: dict[str, Any]) -> dict[str, Any]:
    """Resolve a value/date/period from an immutable raw tabular snapshot."""
    path = _artifact(workspace, ref)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows: Any = list(csv.DictReader(handle))
    elif path.suffix.lower() == ".json":
        rows = _json(path).get("observations")
    else:
        raise ValueError("observation snapshot must be CSV or JSON")
    index = ref.get("record_index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0 or not isinstance(rows, list) or index >= len(rows):
        raise ValueError("invalid observation record index")
    row = rows[index]
    if not isinstance(row, dict):
        raise ValueError("observation must be an object")
    value = row.get("value")
    # CSV has a textual wire format; JSON observations require actual numbers.
    if path.suffix.lower() == ".csv" and isinstance(value, str):
        value = float(value)
    value = number(value)
    release = row.get("official_release_date")
    vintage = row.get("vintage_date", release)
    utc(release)
    utc(vintage)
    period = row.get("period") or row.get("target_period") or row.get("date")
    if not isinstance(period, str) or not period:
        raise ValueError("observation lacks period")
    return {"value": value, "official_release_date": release, "vintage_date": vintage, "period": period}


def replay_case(workspace: Path, case: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a forecast from its own inputs/model; rolling is never a bypass."""
    execution = _json(_artifact(workspace, case.get("execution")))
    if execution.get("contract_version") != "aleph-case-replay-1":
        raise ValueError("unsupported case execution contract")
    evidence = case.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("case has no resolvable evidence")
    observations = [resolve_observation(workspace, e) for e in evidence]
    origin = utc(case.get("forecast_origin"))
    for ref, observation in zip(evidence, observations, strict=True):
        if ref.get("role") == "input":
            if max(utc(observation["official_release_date"]), utc(observation["vintage_date"])) > origin:
                raise ValueError("input release or vintage post-dates forecast origin")
        elif ref.get("role") != "outcome":
            raise ValueError("evidence role must be input or outcome")
    outcomes = [o for e, o in zip(evidence, observations, strict=True) if e["role"] == "outcome"]
    if len(outcomes) != 1:
        raise ValueError("exactly one realized outcome is required")
    outcome = outcomes[0]
    if outcome["period"] != case.get("target_period") or utc(outcome["official_release_date"]) <= origin:
        raise ValueError("outcome period/release differs from forecast target")
    if utc(outcome["official_release_date"]) != utc(case.get("official_release_date")) or outcome["value"] != number(case.get("actual_value")):
        raise ValueError("outcome value/release does not match raw snapshot")
    nodes = execution.get("nodes")
    bindings = execution.get("input_bindings")
    if not isinstance(nodes, list) or not isinstance(bindings, dict):
        raise ValueError("model nodes/input bindings missing")
    bound_values: dict[str, float] = {}
    for variable, binding in bindings.items():
        indices = binding.get("evidence_indices")
        if not isinstance(indices, list) or not indices or len(set(indices)) != len(indices):
            raise ValueError("input binding requires distinct evidence indices")
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(evidence) or evidence[i]["role"] != "input" for i in indices):
            raise ValueError("model input references non-input evidence")
        values = [observations[i]["value"] for i in indices]
        if binding.get("operation") == "identity" and len(values) == 1:
            bound_values[variable] = values[0]
        elif binding.get("operation") == "mean_difference" and len(values) >= 2:
            periods = [observations[i]["period"] for i in indices]
            if periods != sorted(set(periods)):
                raise ValueError("momentum inputs must have distinct increasing periods")
            bound_values[variable] = sum(b - a for a, b in zip(values[:-1], values[1:], strict=True)) / (len(values) - 1)
        else:
            raise ValueError("unsupported input binding operation")
    exogenous = {n["id"] for n in nodes if n.get("role") == "exogenous"}
    if set(bound_values) != exogenous:
        raise ValueError("all and only exogenous variables must be bound to source observations")
    for node in nodes:
        if node["id"] in bound_values:
            value = bound_values[node["id"]]
            if not math.isclose(number(node.get("initial_value")), value, abs_tol=1e-10, rel_tol=0) or not math.isclose(number(node.get("baseline")), value, abs_tol=1e-10, rel_tol=0):
                raise ValueError("model initial state differs from bound inputs")
    formula = str(case.get("formula_version", ""))
    model = compile_model(nodes, execution.get("edges", []), execution.get("interventions", []), formula_version=formula)
    if model_hash(model) != case.get("model_hash"):
        raise ValueError("case model hash differs from independently compiled model")
    config = EngineConfig(**execution.get("config", {}))
    if config.mode != "deterministic" or config_payload(config) != execution.get("config"):
        raise ValueError("case requires a complete deterministic engine configuration")
    ticks = execution.get("ticks")
    if isinstance(ticks, bool) or not isinstance(ticks, int) or not 1 <= ticks <= 1000:
        raise ValueError("invalid case replay ticks")
    result = run_deterministic(model, config, ticks=ticks)
    if not result.get("ok") or canonical_hash(result) != execution.get("result_hash"):
        raise ValueError("case engine result failed replay binding")
    value = number(result["payload"]["final_state"][execution["output_variable"]])
    decimals = execution.get("output_decimals")
    if decimals is not None:
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 12:
            raise ValueError("invalid rounding precision")
        value = round(value, decimals)
    baseline_var = execution.get("baseline_variable")
    if baseline_var not in bound_values or number(case.get("baseline_prediction")) != bound_values[baseline_var]:
        raise ValueError("baseline must equal its bound persistence observation")
    if not math.isclose(value, number(case.get("point_prediction")), abs_tol=1e-10, rel_tol=0):
        raise ValueError("case prediction differs from engine replay")
    return {"replay_verified": True, "model_hash": model_hash(model), "result_hash": canonical_hash(result),
            "forecast_commitment": canonical_hash(forecast_payload(case))}


def verify_lineage(workspace: Path, cases: list[dict[str, Any]], policy: dict[str, Any], data: dict[str, Any], issues: list[Issue]) -> dict[str, Any]:
    verified: dict[str, Any] = {}
    commitments = policy.get("forecast_commitments", {})
    for case in cases:
        cid = str(case.get("case_id", ""))
        try:
            replay = replay_case(workspace, case)
            if not isinstance(commitments, dict) or commitments.get(cid) != replay["forecast_commitment"]:
                raise ValueError("forecast commitment missing or changed")
            verified[cid] = replay
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, OverflowError) as exc:
            issues.append(issue("CALIBRATION_LINEAGE", pointer=f"cases/{cid}/execution", message=str(exc)))
    execution_ok = bool(cases) and len(verified) == len(cases)
    trusted = False
    trust_reason = "host trust receipts unavailable"
    trust_path = os.environ.get("ALEPH_CALIBRATION_TRUST_STORE")
    if execution_ok and trust_path:
        try:
            path = Path(trust_path).resolve()
            if path.is_relative_to(workspace.resolve()):
                raise ValueError("trust store must be controlled outside candidate workspace")
            receipts = _json(path)
            if receipts.get("contract_version") != "aleph-calibration-trust-1" or receipts.get("scope") != data.get("calibration_scope"):
                raise ValueError("trust receipt scope mismatch")
            if receipts.get("policy_design_hash") != policy_design_hash(policy):
                raise ValueError("policy differs from externally frozen policy")
            if utc(receipts.get("policy_committed_at")) > min(utc(c["forecast_origin"]) for c in cases):
                raise ValueError("evaluation design was not frozen before the first origin")
            source_digests = receipts.get("source_digests", [])
            for case in cases:
                cid = str(case["case_id"])
                receipt = receipts["forecast_commitments"][cid]
                if receipt["hash"] != verified[cid]["forecast_commitment"] or utc(receipt["committed_at"]) > utc(case["forecast_origin"]):
                    raise ValueError("forecast was not externally committed before origin")
                if any(e["sha256"].removeprefix("sha256:") not in source_digests for e in case["evidence"]):
                    raise ValueError("source bytes/vintages absent from trusted receipt set")
            trusted = receipts.get("point_in_time_verified") is True and receipts.get("holdout_verified") is True
            trust_reason = "host receipt verified" if trusted else "holdout or vintage review missing"
        except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
            trust_reason = str(exc)
    return {"execution_verified": execution_ok, "verified_case_count": len(verified),
            "historical_provenance_verified": trusted, "attestation_type": "host_trust_receipt" if trusted else "unattested",
            "trust_reason": trust_reason, "causal_validity": "not_established"}
