"""Synthetic protocol tests. These fixtures are not empirical calibration evidence."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from aleph.calibration_lineage import (  # noqa: E402
    forecast_payload,
    policy_design_hash,
    replay_case,
    verify_lineage,
)
from aleph.empirical_pilot import (  # noqa: E402
    compute_authentic_evaluation_metrics,
    execute_engine_cpi_forecast,
)
from aleph.io import canonical_hash, sha256_file, write_json_atomic  # noqa: E402
from aleph.validator import validate_calibration_artifacts  # noqa: E402


def fixture(workspace: Path, count: int = 30) -> tuple[list[dict], dict, dict]:
    observations = []
    for i in range(count + 4):
        year, month = 2020 + i // 12, 1 + i % 12
        observations.append({"period": f"{year}-{month:02d}-01", "value": 100.0 + i,
                             "official_release_date": f"{year}-{month:02d}-05T00:00:00Z",
                             "vintage_date": f"{year}-{month:02d}-05T00:00:00Z"})
    path = workspace / "observations.json"
    write_json_atomic(path, {"observations": observations, "provenance": "synthetic"})
    digest = sha256_file(path)
    cases = []
    for i in range(count):
        origin = observations[i+3]["official_release_date"].replace("05T", "06T")
        forecast = execute_engine_cpi_forecast(origin, observations)
        execution = forecast["model_execution"]
        execution["input_bindings"] = {
            "factor:cpi_base": {"operation": "identity", "evidence_indices": [3]},
            "factor:momentum": {"operation": "mean_difference", "evidence_indices": [0, 1, 2, 3]},
        }
        execution_path = workspace / f"execution-{i:03d}.json"
        write_json_atomic(execution_path, execution)
        evidence = [{"file_path": path.name, "sha256": digest, "record_index": j,
                     "role": "outcome" if j == i+4 else "input"} for j in range(i,i+5)]
        case = {"case_id": f"case-{i:03d}", "forecast_origin": origin,
                "target_period": observations[i+4]["period"],
                "point_prediction": forecast["candidate_prediction"],
                "baseline_prediction": forecast["baseline_prediction"],
                "actual_value": observations[i+4]["value"],
                "official_release_date": observations[i+4]["official_release_date"],
                "formula_version": "2.0.0", "model_hash": forecast["model_hash"],
                "model_version": "aleph-engine-2.0", "is_synthetic": True,
                "evidence": evidence, "execution": {"file_path": execution_path.name, "sha256": sha256_file(execution_path)}}
        cases.append(case)
    policy = {"policy_locked": True, "forecast_commitments": {c["case_id"]: canonical_hash(forecast_payload(c)) for c in cases},
              "case_commitments": {c["case_id"]: canonical_hash(c) for c in cases},
              "thresholds": {"max_mae": 1.0}}
    policy["policy_hash"] = canonical_hash(policy)
    write_json_atomic(workspace / "calibration-policy.json", policy)
    (workspace / "hindcast").mkdir()
    for c in cases:
        write_json_atomic(workspace / "hindcast" / (c["case_id"] + ".json"), c)
    summary = {"schema_version": "2.0.0", "status": "pass", "policy_locked": True,
               "model_version": "aleph-engine-2.0", "formula_version": "2.0.0",
               "model_hash": cases[0]["model_hash"], "model_mode": "rolling", "config_hash": "0"*64,
               "policy_hash": policy["policy_hash"], "hindcast_digest": canonical_hash(cases),
               "outcome_digest": canonical_hash([c["actual_value"] for c in cases]),
               "case_count": count, "unique_case_count": count,
               "metrics": {"mae": 0.0, "rmse": 0.0}, "beats_baseline": True}
    summary["report_hash"] = canonical_hash(summary)
    return cases, policy, summary


class CalibrationLineageTests(unittest.TestCase):
    def test_valid_rolling_replay_and_synthetic_assurance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, _, summary = fixture(ws)
            self.assertEqual(len({c["model_hash"] for c in cases}), 30)
            issues = []
            result = validate_calibration_artifacts(ws, {"likelihood_mode": "relative_weight"}, summary,
                cases[0]["model_hash"], "2.0.0", {"model_version": "aleph-engine-2.0"}, issues)
            self.assertTrue(result["execution_verified"], [i.to_dict() for i in issues])
            self.assertFalse(any(i.severity == "error" for i in issues), [i.to_dict() for i in issues])
            self.assertFalse(result["calibration_verified"])
            self.assertEqual(result["assurance_status"], "synthetic_experimental")

    def test_mutated_case_bindings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, _, _ = fixture(ws, 1)
            original = cases[0]
            mutations = {
                "model": lambda c: c.update(model_hash="a"*64),
                "prediction": lambda c: c.update(point_prediction=c["point_prediction"]+1),
                "baseline": lambda c: c.update(baseline_prediction=0),
                "outcome": lambda c: c.update(actual_value=999),
                "formula": lambda c: c.update(formula_version="9.0.0"),
                "id_only": lambda c: c.update(evidence=[{"id": "invented"}]),
                "source_digest": lambda c: c["evidence"][0].update(sha256="0"*64),
                "wrong_record": lambda c: c["evidence"][0].update(record_index=3),
                "source_path": lambda c: c["evidence"][0].update(file_path="../outside.json"),
                "future_input": lambda c: c["evidence"][0].update(record_index=4),
                "target": lambda c: c.update(target_period="2099-01-01"),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    case = copy.deepcopy(original)
                    mutate(case)
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        replay_case(ws, case)

    def test_model_artifact_changes_fail_even_when_rehashed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, _, _ = fixture(ws, 1)
            case = cases[0]
            from aleph.io import load_json_secure
            path = ws / case["execution"]["file_path"]
            execution, _ = load_json_secure(path)
            for field, value in (("result_hash", "b"*64), ("ticks", 0), ("config", {}), ("output_decimals", True)):
                with self.subTest(field=field):
                    changed = copy.deepcopy(execution)
                    changed[field] = value
                    write_json_atomic(path, changed)
                    case["execution"]["sha256"] = sha256_file(path)
                    with self.assertRaises(ValueError):
                        replay_case(ws, case)

    def test_forecast_commitment_excludes_outcome_but_binds_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cases, _, _ = fixture(Path(td), 1)
            case = cases[0]
            before = canonical_hash(forecast_payload(case))
            case["actual_value"] = 999
            self.assertEqual(before, canonical_hash(forecast_payload(case)))
            case["point_prediction"] = 999
            self.assertNotEqual(before, canonical_hash(forecast_payload(case)))

    def test_candidate_cannot_supply_own_trust_store(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, policy, summary = fixture(ws, 1)
            with patch.dict(os.environ, {"ALEPH_CALIBRATION_TRUST_STORE": str(ws/"trust.json")}):
                issues = []
                result = verify_lineage(ws, cases, policy, summary, issues)
            self.assertTrue(result["execution_verified"])
            self.assertFalse(result["historical_provenance_verified"])
            self.assertIn("outside", result["trust_reason"])

    def test_point_metrics_never_manufacture_probabilities(self) -> None:
        cases = [{"case_id": str(i), "point_prediction": i, "actual_value": i,
                  "baseline_prediction": i+1} for i in range(40)]
        result = compute_authentic_evaluation_metrics(cases, confidence_level=0.9)
        self.assertTrue(result["beats_baseline"])
        self.assertIsNone(result["candidate_metrics"]["brier_score"])
        self.assertIsNone(result["paired_difference"]["p_value"])
        self.assertFalse(result["probability_calibration_verified"])
        self.assertEqual(result["uncertainty_bounds"]["confidence_level"], 0.9)
        self.assertEqual(result["uncertainty_bounds"]["metric_confidence_intervals"]["delta_mae"], [-1, -1])

    def test_external_receipts_bind_design_sources_and_forecast_time(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws = root / "candidate"
            ws.mkdir()
            cases, policy, summary = fixture(ws, 1)
            summary["calibration_scope"] = "test-protocol-only"
            case = cases[0]
            receipt = {"contract_version": "aleph-calibration-trust-1", "scope": summary["calibration_scope"],
                       "policy_design_hash": policy_design_hash(policy), "policy_committed_at": "2019-01-01T00:00:00Z",
                       "source_digests": [case["evidence"][0]["sha256"]],
                       "forecast_commitments": {case["case_id"]: {"hash": canonical_hash(forecast_payload(case)), "committed_at": case["forecast_origin"]}},
                       "point_in_time_verified": True, "holdout_verified": True}
            path = root / "host-test-receipts.json"
            mutations = {
                "positive": lambda r: None,
                "wrong_scope": lambda r: r.update(scope="other"),
                "wrong_policy": lambda r: r.update(policy_design_hash="a"*64),
                "late_design": lambda r: r.update(policy_committed_at="2099-01-01"),
                "untrusted_source": lambda r: r.update(source_digests=[]),
                "late_forecast": lambda r: r["forecast_commitments"][case["case_id"]].update(committed_at="2099-01-01"),
                "changed_forecast": lambda r: r["forecast_commitments"][case["case_id"]].update(hash="b"*64),
                "unreviewed_holdout": lambda r: r.update(holdout_verified=False),
            }
            with patch.dict(os.environ, {"ALEPH_CALIBRATION_TRUST_STORE": str(path)}):
                for name, mutate in mutations.items():
                    with self.subTest(name=name):
                        changed = copy.deepcopy(receipt)
                        mutate(changed)
                        write_json_atomic(path, changed)
                        result = verify_lineage(ws, cases, policy, summary, [])
                        self.assertTrue(result["execution_verified"])
                        self.assertEqual(result["historical_provenance_verified"], name == "positive")

    def test_design_hash_does_not_require_future_outcomes(self) -> None:
        policy = {"thresholds": {"max_mae": 1.0}, "recipe": "frozen"}
        before = policy_design_hash(policy)
        policy.update(case_commitments={"future": "hash"}, forecast_commitments={"later": "hash"}, policy_hash="current")
        self.assertEqual(before, policy_design_hash(policy))
        policy["thresholds"]["max_mae"] = 2.0
        self.assertNotEqual(before, policy_design_hash(policy))

    def test_root_role_cannot_remove_source_binding(self) -> None:
        from aleph.engine import EngineConfig, compile_model, model_hash, run_deterministic
        from aleph.io import load_json_secure

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, _, _ = fixture(ws, 1)
            case = cases[0]
            path = ws/case["execution"]["file_path"]
            execution, _ = load_json_secure(path)
            for node in execution["nodes"]:
                if node["id"] == "factor:momentum":
                    node["role"] = "endogenous"
            model = compile_model(execution["nodes"], execution["edges"], formula_version=case["formula_version"])
            execution["result_hash"] = canonical_hash(run_deterministic(model, EngineConfig(**execution["config"]), ticks=1))
            case["model_hash"] = model_hash(model)
            write_json_atomic(path, execution)
            case["execution"]["sha256"] = sha256_file(path)
            self.assertTrue(replay_case(ws, case)["replay_verified"])
            del execution["input_bindings"]["factor:momentum"]
            write_json_atomic(path, execution)
            case["execution"]["sha256"] = sha256_file(path)
            with self.assertRaisesRegex(ValueError, "graph-root"):
                replay_case(ws, case)

    def test_graph_records_cannot_be_silently_discarded(self) -> None:
        from aleph.io import load_json_secure

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, _, _ = fixture(ws, 1)
            case = cases[0]
            path = ws/case["execution"]["file_path"]
            original, _ = load_json_secure(path)
            for kind in ("duplicate_node", "unknown_edge", "duplicate_edge", "bad_binding"):
                with self.subTest(kind=kind):
                    execution = copy.deepcopy(original)
                    if kind == "duplicate_node":
                        execution["nodes"].append(copy.deepcopy(execution["nodes"][0]))
                    elif kind == "unknown_edge":
                        execution["edges"].append({"id": "bad", "source": "missing", "target": "missing"})
                    elif kind == "duplicate_edge":
                        execution["edges"].append(copy.deepcopy(execution["edges"][0]))
                    else:
                        execution["input_bindings"]["factor:cpi_base"] = []
                    write_json_atomic(path, execution)
                    case["execution"]["sha256"] = sha256_file(path)
                    with self.assertRaises(ValueError):
                        replay_case(ws, case)

    def test_oversized_execution_becomes_structured_issue(self) -> None:
        from aleph.io import DEFAULT_MAX_FILE_BYTES

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cases, policy, summary = fixture(ws, 1)
            path = ws/"oversized.json"
            with path.open("wb") as handle:
                handle.seek(DEFAULT_MAX_FILE_BYTES)
                handle.write(b"0")
            cases[0]["execution"] = {"file_path": path.name, "sha256": "0"*64}
            issues = []
            result = verify_lineage(ws, cases, policy, summary, issues)
            self.assertFalse(result["execution_verified"])
            self.assertIn("RESOURCE_LIMIT", {i.code for i in issues})

    def test_vintage_order_uses_instants_not_offset_strings(self) -> None:
        observations = [
            {"period": "2020-01-01", "value": 102.0, "official_release_date": "2020-01-01T00:00:00Z", "vintage_date": "2020-01-02T03:00:00Z"},
            {"period": "2020-01-01", "value": 101.0, "official_release_date": "2020-01-01T00:00:00Z", "vintage_date": "2020-01-02T09:30:00+07:00"},
        ]
        forecast = execute_engine_cpi_forecast("2020-01-03T00:00:00Z", observations)
        self.assertEqual(forecast["baseline_prediction"], 102.0)

    def test_point_evaluation_preserves_persistence_precision(self) -> None:
        from evaluate_point_forecasts import evaluate

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            source = ws/"raw.csv"
            source.write_text("period,value,official_release_date\n" + "\n".join(
                f"2020-0{i}-01,{100+i+0.12345},2020-0{i}-05T00:00:00Z" for i in range(1, 7)), encoding="utf-8")
            report = evaluate(source, ws/"evaluation")
            self.assertEqual(report["status"], "pass", report["issues"])
            self.assertEqual(report["lineage"]["verified_case_count"], 2)

    def test_point_evaluation_uses_only_available_revisions(self) -> None:
        import json

        from evaluate_point_forecasts import evaluate

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            source = ws/"revisions.csv"
            rows = []
            for i in range(1, 7):
                rows.append(f"2020-0{i}-01,{100+i},2020-0{i}-05T00:00:00Z,")
                rows.append(f"2020-0{i}-01,999,2020-0{i}-05T00:00:00Z,2021-01-01T00:00:00Z")
            source.write_text("period,value,official_release_date,vintage_date\n" + "\n".join(rows), encoding="utf-8")
            report = evaluate(source, ws/"evaluation")
            self.assertEqual(report["status"], "pass", report["issues"])
            case = json.loads((ws/"evaluation/hindcast/case-001.json").read_text(encoding="utf-8"))
            self.assertEqual(case["baseline_prediction"], 104)
            self.assertEqual(case["point_prediction"], 105)
            self.assertEqual(case["actual_value"], 105)
            self.assertFalse(report["probability_calibration_verified"])

    def test_same_vintage_offsets_cannot_hide_conflicting_values(self) -> None:
        observations = [
            {"period": "2020-01-01", "value": 102.0, "official_release_date": "2020-01-01T00:00:00Z", "vintage_date": "2020-01-02T03:00:00Z"},
            {"period": "2020-01-01", "value": 101.0, "official_release_date": "2020-01-01T00:00:00Z", "vintage_date": "2020-01-02T10:00:00+07:00"},
        ]
        with self.assertRaisesRegex(ValueError, "conflicting"):
            execute_engine_cpi_forecast("2020-01-03T00:00:00Z", observations)


if __name__ == "__main__":
    unittest.main()
