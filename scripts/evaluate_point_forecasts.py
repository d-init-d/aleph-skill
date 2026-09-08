"""Build a replayable retrospective CPI evaluation from a local raw CSV.

This produces computation evidence, not preregistration or historical-vintage
certification. The host must independently review source vintages and holdout
design before making empirical claims.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from aleph.calibration_lineage import forecast_payload, utc, verify_lineage
from aleph.empirical_pilot import compute_authentic_evaluation_metrics, execute_engine_cpi_forecast
from aleph.io import ResourceLimitError, canonical_hash, sha256_file, write_json_atomic
from aleph.issues import Issue


def evaluate(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    sha256_file(source)  # Enforce the same raw-file resource bound as replay.
    with source.open(encoding="utf-8-sig", newline="") as handle:
        observations = [{**row, "value": float(row["value"]), "period": row.get("period") or row.get("date")} for row in csv.DictReader(handle)]
    if len(observations) < 5:
        raise ValueError("at least five ordered observations required")
    periods = [str(o["period"]) for o in observations]
    if any(o["period"] is None for o in observations):
        raise ValueError("period or date column required")
    unique_periods = list(dict.fromkeys(periods))
    if unique_periods != sorted(unique_periods) or len(unique_periods) < 5:
        raise ValueError("at least five increasing periods required; revisions may share a period")
    grouped = {period: [i for i, value in enumerate(periods) if value == period] for period in unique_periods}
    for indices in grouped.values():
        vintages = [utc(observations[i].get("vintage_date") or observations[i]["official_release_date"]) for i in indices]
        if len(vintages) != len(set(vintages)):
            raise ValueError("duplicate period/vintage observation")
    if output.exists():
        raise ValueError("output already exists; use a new directory to preserve evidence")
    output.mkdir(parents=True)
    raw = output / "observations.csv"
    shutil.copyfile(source, raw)
    raw_digest = sha256_file(raw)
    (output / "hindcast").mkdir()
    (output / "executions").mkdir()
    cases = []
    for position in range(4, len(unique_periods)):
        previous_period, target_period = unique_periods[position-1:position+1]
        previous = min((observations[i] for i in grouped[previous_period]), key=lambda row: utc(row["official_release_date"]))
        target = min(grouped[target_period], key=lambda i: utc(observations[i].get("vintage_date") or observations[i]["official_release_date"]))
        origin = (utc(previous["official_release_date"])+timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        forecast = execute_engine_cpi_forecast(origin, observations)
        available = forecast["available_observations"]
        used = available[-4:]
        indices = [observations.index(row) for row in used]
        if len(used) != 4 or used[-1]["period"] != previous_period:
            raise ValueError("release order does not support the declared one-period forecast")
        cid = f"case-{position-3:03d}"
        execution = forecast["model_execution"]
        execution["input_bindings"] = {
            "factor:cpi_base": {"operation": "identity", "evidence_indices": [3]},
            "factor:momentum": {"operation": "mean_difference", "evidence_indices": [0, 1, 2, 3]},
        }
        path = output / "executions" / (cid + ".json")
        write_json_atomic(path, execution)
        evidence = [{"file_path": raw.name, "sha256": raw_digest, "record_index": i,
                     "role": "outcome" if i == target else "input"} for i in [*indices, target]]
        case = {"case_id": cid, "forecast_origin": origin, "target_period": observations[target]["period"],
                "model_version": "aleph-engine-2.0", "formula_version": forecast["formula_version"],
                "model_hash": forecast["model_hash"], "point_prediction": forecast["candidate_prediction"],
                "baseline_prediction": forecast["baseline_prediction"], "actual_value": observations[target]["value"],
                "official_release_date": observations[target]["official_release_date"], "evidence": evidence,
                "execution": {"file_path": path.relative_to(output).as_posix(), "sha256": sha256_file(path)}}
        cases.append(case)
        write_json_atomic(output / "hindcast" / (cid + ".json"), case)
    policy = {"policy_locked": True, "precommitted": False, "commitment_phase": "retrospective_replay",
              "baseline": "persistence", "recipe": "trailing-three-difference-plus-last-level",
              "outcome_selection": "first_supplied_vintage",
              "case_commitments": {c["case_id"]: canonical_hash(c) for c in cases},
              "forecast_commitments": {c["case_id"]: canonical_hash(forecast_payload(c)) for c in cases}}
    policy["policy_hash"] = canonical_hash(policy)
    write_json_atomic(output / "calibration-policy.json", policy)
    issues: list[Issue] = []
    lineage = verify_lineage(output, cases, policy, {}, issues)
    report = {"status": "pass" if lineage["execution_verified"] else "fail",
              "scope": "retrospective_computational_replay", "source_sha256": raw_digest,
              "lineage": lineage, "metrics": compute_authentic_evaluation_metrics(cases),
              "historical_vintage_validity": "not_established", "preregistration": "not_established",
              "probability_calibration_verified": False, "causal_validity": "not_established",
              "issues": [i.to_dict() for i in issues]}
    write_json_atomic(output / "point-evaluation.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = evaluate(args.data, args.out)
    except (ResourceLimitError, ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
        report = {"status": "fail", "issues": [{"severity": "error", "code": "INVALID_DATA", "message": str(exc)}]}
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
