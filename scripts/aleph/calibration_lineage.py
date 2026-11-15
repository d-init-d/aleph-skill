"""Resolve calibration evidence and replay each forecast before grading it.

Local hashes establish consistency, not historical authenticity. Optional host
trust receipts are loaded from outside the candidate workspace; only their
explicit scope can authorize an empirical probability-calibration claim.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import EngineConfig, compile_model, config_payload, model_hash, run_deterministic
from .io import ResourceLimitError, canonical_hash, load_json_secure, sha256_file
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
    vintage = row.get("vintage_date") or release
    if utc(vintage) < utc(release):
        raise ValueError("observation vintage precedes its initial release")
    period = row.get("period") or row.get("target_period") or row.get("date")
    if not isinstance(period, str) or not period:
        raise ValueError("observation lacks period")
    return {"value": value, "official_release_date": release, "vintage_date": vintage, "period": period}


def _required_inputs(nodes: Any, edges: Any) -> set[str]:
    """Validate graph identity and bind root variables regardless of role labels."""
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        raise ValueError("execution nodes/edges must be arrays with a nonempty model")
    identifiers: set[str] = set()
    exogenous: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise ValueError("model node requires a nonempty string id")
        identifier = node["id"]
        if identifier in identifiers:
            raise ValueError("duplicate model node id")
        identifiers.add(identifier)
        if node.get("role", "endogenous") not in ("exogenous", "endogenous"):
            raise ValueError("unsupported model node role")
        if node.get("role") == "exogenous":
            exogenous.add(identifier)
    targets: set[str] = set()
    edge_ids: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str) or not edge["id"]:
            raise ValueError("model edge requires a nonempty string id")
        if edge["id"] in edge_ids:
            raise ValueError("duplicate model edge id")
        edge_ids.add(edge["id"])
        source, target = edge.get("from", edge.get("source")), edge.get("to", edge.get("target"))
        if not isinstance(source, str) or not isinstance(target, str) or source not in identifiers or target not in identifiers:
            raise ValueError("model edge references an unknown variable")
        for primary, alias in (("from", "source"), ("to", "target")):
            if primary in edge and alias in edge and edge[primary] != edge[alias]:
                raise ValueError("model edge endpoint aliases disagree")
        targets.add(target)
    return exogenous | (identifiers - targets)


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
    required_inputs = _required_inputs(nodes, execution.get("edges"))
    bound_values: dict[str, float] = {}
    for variable, binding in bindings.items():
        if not isinstance(binding, dict):
            raise ValueError("input binding must be an object")
        indices = binding.get("evidence_indices")
        if not isinstance(indices, list) or not indices:
            raise ValueError("input binding requires distinct evidence indices")
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(evidence) or evidence[i]["role"] != "input" for i in indices):
            raise ValueError("model input references non-input evidence")
        if len(set(indices)) != len(indices):
            raise ValueError("input binding requires distinct evidence indices")
        values = [observations[i]["value"] for i in indices]
        if binding.get("operation") == "identity" and len(values) == 1:
            bound_values[variable] = values[0]
        elif binding.get("operation") == "mean_difference" and len(values) >= 2:
            periods = [observations[i]["period"] for i in indices]
            if periods != sorted(set(periods)):
                raise ValueError("momentum inputs must have distinct increasing periods")
            bound_values[variable] = sum(b - a for a, b in zip(values[:-1], values[1:], strict=True)) / (len(values) - 1)
        elif binding.get("operation") == "damped_difference" and len(values) >= 2:
            periods = [observations[i]["period"] for i in indices]
            if periods != sorted(set(periods)):
                raise ValueError("damped_difference inputs must have distinct increasing periods")
            damping = binding.get("damping", 1.0)
            if isinstance(damping, bool) or not isinstance(damping, (int, float)) or not math.isfinite(damping) or not (0.0 <= float(damping) <= 1.0):
                raise ValueError("damped_difference requires damping in [0, 1]")
            bound_values[variable] = float(damping) * (sum(b - a for a, b in zip(values[:-1], values[1:], strict=True)) / (len(values) - 1))
        elif binding.get("operation") == "trailing_mean_deviation" and len(values) >= 2:
            periods = [observations[i]["period"] for i in indices]
            if periods != sorted(set(periods)):
                raise ValueError("trailing_mean_deviation inputs must have distinct increasing periods")
            bound_values[variable] = (sum(values) / len(values)) - values[-1]
        else:
            raise ValueError("unsupported input binding operation")
    if set(bound_values) != required_inputs:
        raise ValueError("all and only exogenous and graph-root variables must be bound to source observations")
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
        except ResourceLimitError as exc:
            issues.append(exc.issue)
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
        except (ResourceLimitError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
            trust_reason = str(exc)
    return {"execution_verified": execution_ok, "verified_case_count": len(verified),
            "historical_provenance_verified": trusted, "attestation_type": "host_trust_receipt" if trusted else "unattested",
            "trust_reason": trust_reason, "causal_validity": "not_established"}


def _sigmoid(z: float) -> float:
    """Numerically stable logistic sigmoid."""
    if z >= 0.0:
        exp_neg = math.exp(-min(z, 60.0))
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(max(z, -60.0))
    return exp_pos / (1.0 + exp_pos)


def _validate_scores_and_outcomes(
    scores: Sequence[int | float], outcomes: Sequence[int | float | bool]
) -> tuple[list[float], list[float]]:
    if not scores or len(scores) != len(outcomes):
        raise ValueError("scores and outcomes must be non-empty lists of equal length")
    clean_scores = [number(s) for s in scores]
    clean_outcomes: list[float] = []
    for raw_y in outcomes:
        if isinstance(raw_y, bool):
            clean_outcomes.append(1.0 if raw_y else 0.0)
        else:
            y_val = number(raw_y)
            if not 0.0 <= y_val <= 1.0:
                raise ValueError("outcomes must lie in [0, 1]")
            clean_outcomes.append(y_val)
    return clean_scores, clean_outcomes


def compute_calibration_diagnostics(
    probs: Sequence[int | float],
    outcomes: Sequence[int | float | bool],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    """Compute Brier score, log-loss, Expected Calibration Error (ECE), and reliability bins."""
    clean_probs, clean_outcomes = _validate_scores_and_outcomes(probs, outcomes)
    for p in clean_probs:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"probability must lie in [0, 1], got {p}")
    n = len(clean_probs)
    brier = sum((p - y) ** 2 for p, y in zip(clean_probs, clean_outcomes, strict=True)) / n
    eps = 1e-15
    log_loss_sum = 0.0
    for p, y in zip(clean_probs, clean_outcomes, strict=True):
        pc = min(1.0 - eps, max(eps, p))
        log_loss_sum -= y * math.log(pc) + (1.0 - y) * math.log(1.0 - pc)
    log_loss = log_loss_sum / n

    num_bins = max(1, int(bins))
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(num_bins)]
    for p, y in zip(clean_probs, clean_outcomes, strict=True):
        idx = min(num_bins - 1, max(0, int(p * num_bins)))
        buckets[idx].append((p, y))

    reliability_bins: list[dict[str, Any]] = []
    ece = 0.0
    for idx, bucket in enumerate(buckets):
        if not bucket:
            continue
        cnt = len(bucket)
        avg_conf = sum(p for p, _ in bucket) / cnt
        emp_freq = sum(y for _, y in bucket) / cnt
        gap = abs(avg_conf - emp_freq)
        ece += (cnt / n) * gap
        reliability_bins.append(
            {
                "bin_index": idx,
                "lower": round(idx / num_bins, 4),
                "upper": round((idx + 1) / num_bins, 4),
                "count": cnt,
                "avg_confidence": round(avg_conf, 6),
                "empirical_frequency": round(emp_freq, 6),
                "abs_gap": round(gap, 6),
            }
        )

    return {
        "count": n,
        "brier_score": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "ece": round(ece, 6),
        "reliability_bins": reliability_bins,
    }


def fit_platt_scaling(
    raw_scores: Sequence[int | float],
    outcomes: Sequence[int | float | bool],
    *,
    max_iter: int = 200,
    lr: float = 0.1,
    l2: float = 1e-4,
) -> dict[str, Any]:
    """Fit Platt logistic scaling p = sigmoid(a * score + b) via regularized Newton-Raphson."""
    scores, y_raw = _validate_scores_and_outcomes(raw_scores, outcomes)
    is_binary = all(y in (0.0, 1.0) for y in y_raw)
    if is_binary:
        n_pos = sum(y_raw)
        n_neg = len(y_raw) - n_pos
        t_pos = (n_pos + 1.0) / (n_pos + 2.0)
        t_neg = 1.0 / (n_neg + 2.0)
        targets = [t_pos if y == 1.0 else t_neg for y in y_raw]
    else:
        targets = list(y_raw)

    a = 0.0
    b = 0.0
    reg = max(1e-8, float(l2))

    def obj(curr_a: float, curr_b: float) -> float:
        total = 0.5 * reg * (curr_a * curr_a + curr_b * curr_b)
        for s, t in zip(scores, targets, strict=True):
            p = min(1.0 - 1e-15, max(1e-15, _sigmoid(curr_a * s + curr_b)))
            total -= t * math.log(p) + (1.0 - t) * math.log(1.0 - p)
        return total

    current_loss = obj(a, b)
    for _ in range(max(10, int(max_iter))):
        g_a = reg * a
        g_b = reg * b
        h_aa = reg
        h_ab = 0.0
        h_bb = reg
        for s, t in zip(scores, targets, strict=True):
            p = _sigmoid(a * s + b)
            diff = p - t
            w = max(1e-12, p * (1.0 - p))
            g_a += diff * s
            g_b += diff
            h_aa += w * s * s
            h_ab += w * s
            h_bb += w
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-18:
            step_a = -lr * g_a
            step_b = -lr * g_b
        else:
            step_a = -(h_bb * g_a - h_ab * g_b) / det
            step_b = -(-h_ab * g_a + h_aa * g_b) / det

        if max(abs(step_a), abs(step_b)) < 1e-10:
            break

        scale = 1.0
        accepted = False
        while scale >= 1e-6:
            cand_a = a + scale * step_a
            cand_b = b + scale * step_b
            cand_loss = obj(cand_a, cand_b)
            if cand_loss <= current_loss + 1e-12:
                a, b, current_loss = cand_a, cand_b, cand_loss
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            break

    calibrated = [round(_sigmoid(a * s + b), 6) for s in scores]
    diagnostics = compute_calibration_diagnostics(calibrated, y_raw)
    return {
        "method": "platt_scaling",
        "a": round(a, 8),
        "b": round(b, 8),
        "calibrated_probs": calibrated,
        "diagnostics": diagnostics,
    }


def fit_isotonic_or_binned_calibration(
    raw_scores: Sequence[int | float],
    outcomes: Sequence[int | float | bool],
    *,
    method: str = "isotonic",
    bins: int = 10,
) -> dict[str, Any]:
    """Fit non-parametric PAVA isotonic regression or empirical histogram binning."""
    scores, y_vals = _validate_scores_and_outcomes(raw_scores, outcomes)
    norm_method = method.strip().lower()
    if norm_method not in {"isotonic", "binned", "histogram_binning"}:
        raise ValueError(f"unsupported calibration method: {method}")

    if norm_method == "isotonic":
        indexed = sorted(
            zip(scores, y_vals, range(len(scores)), strict=True),
            key=lambda item: (item[0], item[2]),
        )
        # Merge exact score ties before running Pool-Adjacent-Violators Algorithm
        blocks: list[list[Any]] = []
        for s_val, y_val, orig_idx in indexed:
            if blocks and blocks[-1][3] == s_val:
                blocks[-1][0] += y_val
                blocks[-1][1] += 1
                blocks[-1][4].append(orig_idx)
            else:
                blocks.append([y_val, 1, s_val, s_val, [orig_idx]])

        pava: list[list[Any]] = []
        for blk in blocks:
            pava.append(blk)
            while len(pava) >= 2:
                prev_mean = pava[-2][0] / pava[-2][1]
                curr_mean = pava[-1][0] / pava[-1][1]
                if prev_mean <= curr_mean:
                    break
                right = pava.pop()
                left = pava[-1]
                left[0] += right[0]
                left[1] += right[1]
                left[3] = right[3]
                left[4].extend(right[4])

        calibrated = [0.0] * len(scores)
        intervals: list[dict[str, Any]] = []
        for sum_y, cnt, min_s, max_s, indices in pava:
            prob = round(min(1.0, max(0.0, sum_y / cnt)), 6)
            for idx in indices:
                calibrated[idx] = prob
            intervals.append(
                {
                    "min_score": min_s,
                    "max_score": max_s,
                    "count": cnt,
                    "probability": prob,
                }
            )
        return {
            "method": "isotonic",
            "intervals": intervals,
            "calibrated_probs": calibrated,
            "diagnostics": compute_calibration_diagnostics(calibrated, y_vals, bins=bins),
        }

    num_bins = max(1, int(bins))
    min_s = min(scores)
    max_s = max(scores)
    span = max_s - min_s
    bin_sums = [0.0] * num_bins
    bin_counts = [0] * num_bins
    assigned_bins: list[int] = []
    for s_val, y_val in zip(scores, y_vals, strict=True):
        if span <= 0.0:
            b_idx = 0
        else:
            b_idx = min(num_bins - 1, max(0, int(((s_val - min_s) / span) * num_bins)))
        bin_sums[b_idx] += y_val
        bin_counts[b_idx] += 1
        assigned_bins.append(b_idx)

    global_prior = sum(y_vals) / len(y_vals)
    bin_probs = [
        round((bin_sums[i] + global_prior) / (bin_counts[i] + 1.0), 6) if bin_counts[i] > 0 else round(global_prior, 6)
        for i in range(num_bins)
    ]
    calibrated = [bin_probs[b_idx] for b_idx in assigned_bins]
    bin_table = [
        {
            "bin_index": i,
            "lower": round(min_s + (span * i / num_bins), 6),
            "upper": round(min_s + (span * (i + 1) / num_bins), 6),
            "count": bin_counts[i],
            "probability": bin_probs[i],
        }
        for i in range(num_bins)
    ]
    return {
        "method": "histogram_binning",
        "bins": bin_table,
        "calibrated_probs": calibrated,
        "diagnostics": compute_calibration_diagnostics(calibrated, y_vals, bins=num_bins),
    }


def bayesian_update_branches(
    priors: dict[str, float],
    likelihoods: dict[str, float] | list[dict[str, float]],
) -> dict[str, Any]:
    """Perform exact single-step or sequential Bayesian posterior updating across scenario branches."""
    if not isinstance(priors, dict) or not priors:
        raise ValueError("priors must be a non-empty mapping of branch_id -> prior weight")
    branches = sorted(priors.keys())
    clean_priors: dict[str, float] = {}
    for branch in branches:
        val = number(priors[branch])
        if val < 0.0:
            raise ValueError(f"prior for branch '{branch}' must be non-negative")
        clean_priors[branch] = val
    prior_total = sum(clean_priors.values())
    if prior_total <= 0.0:
        raise ValueError("prior total weight must be positive")
    prior_norm = {b: v / prior_total for b, v in clean_priors.items()}

    waves = [likelihoods] if isinstance(likelihoods, dict) else list(likelihoods)
    if not waves:
        raise ValueError("likelihoods must be a dict or non-empty list of dicts")

    current = dict(prior_norm)
    cum_likelihood = {b: 1.0 for b in branches}
    for wave_idx, wave in enumerate(waves):
        if not isinstance(wave, dict):
            raise ValueError(f"likelihood wave {wave_idx} must be a dict")
        unnormalized: dict[str, float] = {}
        for b in branches:
            lk = number(wave.get(b, 1.0))
            if lk < 0.0:
                raise ValueError(f"likelihood for branch '{b}' in wave {wave_idx} must be non-negative")
            cum_likelihood[b] *= lk
            unnormalized[b] = current[b] * lk
        wave_total = sum(unnormalized.values())
        if wave_total <= 0.0:
            raise ValueError(f"Zero-mass collapse in Bayesian update wave {wave_idx}: all branch likelihoods zero")
        current = {b: v / wave_total for b, v in unnormalized.items()}

    log_bayes_factors: dict[str, float] = {}
    for b in branches:
        l_b = cum_likelihood[b]
        other_mass = sum(prior_norm[o] for o in branches if o != b)
        if other_mass > 0.0:
            l_not_b = sum(prior_norm[o] * cum_likelihood[o] for o in branches if o != b) / other_mass
        else:
            l_not_b = l_b
        if l_b > 0.0 and l_not_b > 0.0:
            log_bayes_factors[b] = round(math.log(l_b / l_not_b), 6)
        elif l_b == 0.0:
            log_bayes_factors[b] = float("-inf")
        else:
            log_bayes_factors[b] = float("inf")

    posteriors_rounded = {b: round(v, 6) for b, v in current.items()}
    map_branch = max(branches, key=lambda b: (current[b], b))
    return {
        "priors": {b: round(v, 6) for b, v in prior_norm.items()},
        "posteriors": posteriors_rounded,
        "log_bayes_factors": {
            b: (v if math.isfinite(v) else str(v)) for b, v in log_bayes_factors.items()
        },
        "map_branch": map_branch,
        "waves_applied": len(waves),
    }


def _calibration_self_test() -> dict[str, Any]:
    scores = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]
    outcomes = [0, 0, 0, 0, 1, 1, 1, 1]
    platt = fit_platt_scaling(scores, outcomes)
    assert platt["a"] > 0.0
    assert platt["diagnostics"]["brier_score"] < 0.15
    iso = fit_isotonic_or_binned_calibration(scores, outcomes, method="isotonic")
    assert all(
        iso["calibrated_probs"][i] <= iso["calibrated_probs"][i + 1]
        for i in range(len(iso["calibrated_probs"]) - 1)
    )
    bayes = bayesian_update_branches(
        {"bull": 0.4, "base": 0.4, "bear": 0.2},
        [{"bull": 0.7, "base": 0.3, "bear": 0.1}, {"bull": 0.6, "base": 0.2, "bear": 0.1}],
    )
    assert bayes["map_branch"] == "bull"
    assert abs(sum(bayes["posteriors"].values()) - 1.0) < 1e-4
    return {"ok": True, "platt_a": platt["a"], "ece": platt["diagnostics"]["ece"], "map_branch": bayes["map_branch"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aleph Calibration & Bayesian Posterior CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fit = sub.add_parser("fit-calibration", help="Fit Platt or isotonic/binned calibration")
    p_fit.add_argument("--input", required=True, help="JSON file with scores and outcomes arrays")
    p_fit.add_argument("--method", choices=["platt", "isotonic", "binned"], default="platt")
    p_fit.add_argument("--bins", type=int, default=10)

    p_diag = sub.add_parser("diagnostics", help="Compute Brier, log-loss, and ECE metrics")
    p_diag.add_argument("--input", required=True, help="JSON file with probs and outcomes arrays")
    p_diag.add_argument("--bins", type=int, default=10)

    p_bayes = sub.add_parser("update-posteriors", help="Bayesian branch posterior updating")
    p_bayes.add_argument("--input", required=True, help="JSON file with priors and likelihoods")

    sub.add_parser("self-test", help="Run built-in calibration self-test")

    args = parser.parse_args(argv)
    if args.cmd == "self-test":
        print(json.dumps(_calibration_self_test(), indent=2))
        return 0
    payload, _ = load_json_secure(Path(args.input))
    if not isinstance(payload, dict):
        raise ValueError("calibration input JSON must be an object")
    if args.cmd == "fit-calibration":
        if args.method == "platt":
            out = fit_platt_scaling(payload["scores"], payload["outcomes"])
        else:
            out = fit_isotonic_or_binned_calibration(
                payload["scores"], payload["outcomes"], method=args.method, bins=args.bins
            )
    elif args.cmd == "diagnostics":
        out = compute_calibration_diagnostics(payload["probs"], payload["outcomes"], bins=args.bins)
    else:
        out = bayesian_update_branches(payload["priors"], payload["likelihoods"])
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
