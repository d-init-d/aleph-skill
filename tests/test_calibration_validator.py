from __future__ import annotations

import copy
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.io import canonical_hash, sha256_file, write_json_atomic  # noqa: E402
from aleph.validator import (  # noqa: E402
    artifact_integrity_hash,
    validate_branches,
    validate_calibration_artifacts,
    validate_numerical_artifacts,
    validate_workspace,
)

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def _create_raw_case(
    case_id: str,
    *,
    model_hash: str,
    formula_version: str = "2.0.0",
    prediction: float = 10.0,
    actual: float = 10.2,
    baseline: float = 8.0,
    forecast_origin: str = "2025-01-01T00:00:00Z",
    target_period: str = "2025-02-01T00:00:00Z",
    official_release_date: str = "2025-02-05T00:00:00Z",
    is_synthetic: bool = False,
    evidence_vintage: str = "2024-12-01T00:00:00Z",
) -> dict:
    case = {
        "case_id": case_id,
        "forecast_origin": forecast_origin,
        "target_period": target_period,
        "model_version": "aleph-engine-2.0",
        "model_hash": model_hash,
        "formula_version": formula_version,
        "point_prediction": prediction,
        "actual_value": actual,
        "baseline_prediction": baseline,
        "official_release_date": official_release_date,
        "is_synthetic": is_synthetic,
        "evidence": [
            {
                "id": f"ev-{case_id}",
                "vintage_date": evidence_vintage,
                "available_at": evidence_vintage,
                "source": "https://data.example.org/series",
            }
        ],
    }
    case["commitment_hash"] = canonical_hash(case)
    return case


def _setup_base_workspace(temporary_dir: str):
    workspace = Path(temporary_dir) / "workspace"
    shutil.copytree(FIXTURE, workspace)
    manifest_path = workspace / "simulation-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model = json.loads((workspace / "simulation-model.json").read_text(encoding="utf-8"))
    model_digest = model.get("model_hash")
    return workspace, manifest, manifest_path, model, model_digest


def _sync_manifest_and_model(workspace: Path, manifest: dict) -> None:
    manifest_path = workspace / "simulation-manifest.json"
    write_json_atomic(manifest_path, manifest)
    model_path = workspace / "simulation-model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    if "source_hashes" in model and isinstance(model["source_hashes"], dict):
        for rel in list(model["source_hashes"].keys()):
            p = workspace / rel
            if p.is_file():
                model["source_hashes"][rel] = artifact_integrity_hash(p, rel, manifest)
        model["source_set_hash"] = canonical_hash(model["source_hashes"])
        write_json_atomic(model_path, model)


class CalibrationValidatorAcceptanceTests(unittest.TestCase):
    def test_a01_f03_empty_summary_rejection(self) -> None:
        """A01: Calibration report declares 30 cases, metrics={}, and lacks raw hindcast case files on disk (repro F03)."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {},  # Empty metrics
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            issue_codes = {item.code for item in result.issues}
            self.assertIn("PACK_MATURITY", issue_codes)
            self.assertIn("MISSING_ARTIFACT", issue_codes)

            # Confirm metrics empty issue
            empty_metric_issues = [
                i for i in result.issues if i.code == "PACK_MATURITY" and "empty" in i.message
            ]
            self.assertTrue(len(empty_metric_issues) > 0)

    def test_a02_fake_dataset_digest_rejection(self) -> None:
        """A02: Calibration summary has valid schema formatting and recomputed self-hash, but references fabricated dataset digests."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_bundle"] = "calibration-bundle.json"
            _sync_manifest_and_model(workspace, manifest)

            bundle = {
                "schema_version": "1.0.0",
                "bundle_id": "bundle:fake-digest-test",
                "domain": "economics",
                "target_variable": "inflation",
                "target_horizon": "1m",
                "temporal_cutoff": "2025-01-01T00:00:00Z",
                "policy_manifest": {
                    "policy_id": "policy:econ-1",
                    "policy_hash": "sha256:" + "a" * 64,
                    "precommitted": True,
                    "commitment_timestamp": "2024-12-01T00:00:00Z",
                    "evaluation_type": "retrospective",
                    "model_class": "structural_causal",
                    "baseline_model": "persistence",
                    "metrics_to_evaluate": ["mae"],
                    "thresholds": {"max_mae": 2.0},
                },
                "dataset_snapshots": [
                    {
                        "dataset_id": "ds:fake",
                        "source_title": "Fake Data",
                        "source_url": "https://example.org/data",
                        "vintage_date": "2024-12-01",
                        "access_date": "2024-12-01T00:00:00Z",
                        "file_path": "data/missing_snapshot.csv",
                        "sha256_digest": "sha256:" + "f" * 64,
                        "is_synthetic": False,
                    }
                ],
                "per_case_predictions": [],
                "realized_outcomes": [],
                "recomputed_metrics": {},
                "uncertainty_bounds": {},
                "temporal_leakage_audit": {},
                "assurance_status": "calibrated",
            }
            bundle["bundle_hash"] = canonical_hash(bundle)
            write_json_atomic(workspace / "calibration-bundle.json", bundle)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertIn("MISSING_ARTIFACT", {i.code for i in result.issues})

    def test_a03_valid_raw_cases_positive(self) -> None:
        """A03: Workspace provides valid raw hindcast cases, matching realized outcomes, and precommitted policy."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)

            raw_cases = []
            case_commitments = {}
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    prediction=10.0 + (i % 3) * 0.1,
                    actual=10.0 + (i % 3) * 0.1 + 0.2,  # MAE ~ 0.2
                    baseline=8.0,  # Baseline MAE ~ 2.2
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                )
                raw_cases.append(c)
                case_commitments[cid] = c["commitment_hash"]
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Policy
            policy = {
                "precommitted": True,
                "commitment_version": "aleph-hindcast-commitment-v3",
                "case_commitments": case_commitments,
            }
            policy["policy_hash"] = canonical_hash(policy)
            write_json_atomic(workspace / "calibration-policy.json", policy)

            hindcast_digest = canonical_hash([canonical_hash(c) for c in raw_cases])
            outcome_digest = canonical_hash([c.get("actual_value") for c in raw_cases])

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": policy["policy_hash"],
                "hindcast_digest": hindcast_digest,
                "outcome_digest": outcome_digest,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2, "rmse": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "pass", [i.to_dict() for i in result.issues])
            self.assertTrue(result.metrics.get("calibration_verified"))
            self.assertTrue(result.metrics.get("beats_baseline"))
            self.assertEqual(result.metrics.get("assurance_status"), "calibrated")

    def test_a04_case_count_mismatch(self) -> None:
        """A04: Summary declares case_count=30, unique_case_count=30, but raw case directory contains only 20 cases."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(20):  # Only 20 cases
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,  # Declares 30
                "unique_case_count": 30,
                "metrics": {"mae": 0.5},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertIn("PACK_MATURITY", {i.code for i in result.issues})
            count_mismatches = [
                i for i in result.issues if i.code == "PACK_MATURITY" and "case_count" in str(i.pointer)
            ]
            self.assertTrue(len(count_mismatches) > 0)

    def test_a05_duplicate_sample_inflation(self) -> None:
        """A05: Identical prediction observations duplicated under different case IDs to inflate sample count."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # Duplicating observation signatures for i >= 15
                base_idx = i % 15
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    prediction=10.0 + base_idx * 0.1,
                    actual=10.5 + base_idx * 0.1,
                    target_period=f"2025-02-{base_idx+1:02d}T00:00:00Z",
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.5},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            inflation_issues = [
                i for i in result.issues if "ARTIFICIAL_SAMPLE_INFLATION" in str(i.message)
            ]
            self.assertTrue(len(inflation_issues) > 0)

    def test_a06_nan_infinity_malformed_outcomes(self) -> None:
        """A06: Raw prediction cases contain NaN, Infinity, missing outcomes, or boolean types masquerading as numeric values."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                if i == 5:
                    c["actual_value"] = True  # Boolean
                elif i == 6:
                    c["point_prediction"] = None  # Missing
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.5},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            type_issues = [i for i in result.issues if i.code in {"TYPE", "MISSING_FIELD"}]
            self.assertTrue(len(type_issues) > 0)

    def test_a07_recomputed_metric_discrepancy(self) -> None:
        """A07: Summary declares beats_baseline=true and mae=0.5, but independent recomputation yields mae=2.5 (worse than baseline 1.0)."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # candidate error is 2.5, baseline error is 1.0 -> candidate does NOT beat baseline
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    prediction=10.0,
                    actual=12.5,
                    baseline=11.5,
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.5},  # Fake declared MAE
                "beats_baseline": True,   # Falsely claims true
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertIn("REPLAY_MISMATCH", {i.code for i in result.issues})
            self.assertFalse(result.metrics.get("beats_baseline"))

    def test_a08_post_commitment_modification(self) -> None:
        """A08: Predictions or ground-truth outcomes modified after policy commitment hash was signed."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            raw_cases = []
            case_commitments = {}
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest, target_period=f"2025-02-{i+1:02d}T00:00:00Z")
                raw_cases.append(c)
                case_commitments[cid] = c["commitment_hash"]
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Policy
            policy = {
                "precommitted": True,
                "commitment_version": "aleph-hindcast-commitment-v3",
                "case_commitments": case_commitments,
            }
            policy["policy_hash"] = canonical_hash(policy)
            write_json_atomic(workspace / "calibration-policy.json", policy)

            # Tamper with case-000 after commitment
            raw_cases[0]["point_prediction"] = 999.9
            raw_cases[0]["commitment_hash"] = canonical_hash(raw_cases[0])
            write_json_atomic(hindcast_dir / "case-000.json", raw_cases[0])

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": policy["policy_hash"],
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            stale_issues = [
                i for i in result.issues if i.code == "STALE_ARTIFACT" and "case_commitments" in str(i.pointer)
            ]
            self.assertTrue(len(stale_issues) > 0)

    def test_a09_policy_tampering_demotion(self) -> None:
        """A09: Evaluation thresholds or case list modified ex-post after running hindcasts."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest, target_period=f"2025-02-{i+1:02d}T00:00:00Z")
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Policy with modified body (tampered thresholds without updating policy_hash)
            policy = {
                "precommitted": True,
                "commitment_version": "aleph-hindcast-commitment-v3",
                "thresholds": {"max_mae": 5.0},  # Tampered ex-post
                "policy_hash": "a" * 64,         # Stale signed hash
            }
            write_json_atomic(workspace / "calibration-policy.json", policy)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": policy["policy_hash"],
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            tamper_issues = [i for i in result.issues if i.code == "POLICY_THRESHOLD_TAMPERING"]
            self.assertTrue(len(tamper_issues) > 0)

    def test_a12_model_hash_binding_mismatch(self) -> None:
        """A12: Case prediction references mismatched model_hash or formula_version differing from policy manifest."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # case references mismatched model_hash
                c = _create_raw_case(
                    cid,
                    model_hash="bad" + "0" * 61,
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            track_issues = [
                i for i in result.issues if i.code == "TRACK_MISMATCH" and "model_hash" in str(i.pointer)
            ]
            self.assertTrue(len(track_issues) > 0)

    def test_a13_point_to_probability_extrapolation(self) -> None:
        """A13: Model calibrated for continuous point forecast on inflation (MAE) requests calibrated_probability for geopolitical branch."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["scope"] = {"domain": "economics"}
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"

            branch_data = {
                "schema_version": "2.0.0",
                "likelihood_mode": "calibrated_probability",
                "calibrated": True,
                "unresolved_mass": 0.0,
                "branches": [
                    {
                        "id": "branch:conflict",
                        "title": "Geopolitical conflict escalation",
                        "domain": "geopolitics",  # Geopolitical branch
                        "probability": 0.75,
                        "causal_mechanism_ids": ["causal:rate-to-gap"],
                        "driver_actor_ids": ["actor:governor"],
                        "evidence_ids": ["evidence:macro-series"],
                        "state_factors": ["factor:policy-rate"],
                    }
                ],
                "calibration": {
                    "method": "continuous_point_forecast",
                    "sample_count": 30,
                    "interval": [0.0, 1.0],
                    "calibration_policy_ref": "calibration-policy.json",
                    "model_version": "aleph-engine-2.0",
                    "formula_version": "2.0.0",
                    "model_hash": model_digest,
                    "hindcast_report_ref": "calibration-report.json",
                    "target_variable": "inflation",
                    "domain": "economics",
                },
            }
            result = validate_branches(
                branch_data,
                {"causal:rate-to-gap"},
                {"actor:governor"},
                {"evidence:macro-series"},
                manifest,
                {"factor:policy-rate"},
            )
            self.assertEqual(result.status, "fail")
            scope_issues = [i for i in result.issues if i.code == "SCOPE_MISMATCH"]
            self.assertTrue(len(scope_issues) > 0)

    def test_a14_inferior_to_baseline_uncalibrated(self) -> None:
        """A14: Hindcast completes 30 cases but recomputed MAE is inferior to registered persistence baseline."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # candidate error is 3.0, baseline error is 1.0 -> inferior to baseline
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    prediction=10.0,
                    actual=13.0,
                    baseline=12.0,
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "uncalibrated",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 3.0},
                "beats_baseline": False,  # Honestly false
                "assurance_status": "uncalibrated",
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            # Recomputation confirms beats_baseline is false and sets assurance_status="uncalibrated"
            self.assertFalse(result.metrics.get("beats_baseline"))
            self.assertEqual(result.metrics.get("assurance_status"), "uncalibrated")
            self.assertFalse(result.metrics.get("calibration_verified"))

    def test_a15_synthetic_fixtures_experimental(self) -> None:
        """A15: Calibration bundle includes synthetic fixtures with maturity=experimental."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    is_synthetic=True,  # Synthetic provenance
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertTrue(result.metrics.get("is_synthetic"))
            self.assertFalse(result.metrics.get("calibration_verified"))
            self.assertEqual(result.metrics.get("assurance_status"), "synthetic_experimental")

    def test_a16_overlapping_horizon_effective_sample(self) -> None:
        """A16: Hindcast evaluation uses rolling horizons with overlapping forecast periods."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_bundle"] = "calibration-bundle.json"
            _sync_manifest_and_model(workspace, manifest)

            # 30 cases with target_horizon = '3m'
            preds = []
            outs = []
            for i in range(30):
                cid = f"case:{i:03d}"
                preds.append({
                    "case_id": cid,
                    "forecast_origin": "2025-01-01T00:00:00Z",
                    "target_period": f"2025-04-{i+1:02d}T00:00:00Z",
                    "model_version": "aleph-engine-2.0",
                    "model_hash": model_digest,
                    "formula_version": "2.0.0",
                    "input_snapshot_digests": ["sha256:" + "0" * 64],
                    "point_prediction": 10.0,
                })
                outs.append({
                    "case_id": cid,
                    "target_period": f"2025-04-{i+1:02d}T00:00:00Z",
                    "actual_value": 10.2,
                    "official_release_date": "2025-05-01T00:00:00Z",
                })

            bundle = {
                "schema_version": "1.0.0",
                "bundle_id": "bundle:overlapping-test",
                "domain": "economics",
                "target_variable": "inflation",
                "target_horizon": "3m",  # 3-month horizon
                "temporal_cutoff": "2025-01-01T00:00:00Z",
                "policy_manifest": {
                    "policy_id": "policy:p1",
                    "policy_hash": "sha256:" + "0" * 64,
                    "precommitted": True,
                    "commitment_timestamp": "2024-12-01T00:00:00Z",
                    "evaluation_type": "retrospective",
                    "model_class": "structural",
                    "baseline_model": "persistence",
                    "metrics_to_evaluate": ["mae"],
                    "thresholds": {"max_mae": 1.0},
                },
                "dataset_snapshots": [],
                "per_case_predictions": preds,
                "realized_outcomes": outs,
                "recomputed_metrics": {},
                "uncertainty_bounds": {},
                "temporal_leakage_audit": {},
                "assurance_status": "uncalibrated",
            }
            bundle["bundle_hash"] = canonical_hash(bundle)
            write_json_atomic(workspace / "calibration-bundle.json", bundle)

            result = validate_numerical_artifacts(workspace, manifest)
            recomputed = result.metrics.get("recomputed_metrics") or {}
            eff_sample = recomputed.get("effective_sample_size")
            # For 30 observations with 3-period horizon, effective sample size must be adjusted to ~10.0
            self.assertIsNotNone(eff_sample)
            self.assertLess(eff_sample, 30.0)
            self.assertEqual(eff_sample, 10.0)

    def test_a21_internal_hmac_attestation_type(self) -> None:
        """A21: Calibration summary signed by internal HMAC key."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            summary = {
                "schema_version": "2.0.0",
                "status": "uncalibrated",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "a" * 64,
                "hmac_signature": "internal-key-signed:" + "e" * 64,
                "hindcast_digest": "0" * 64,
                "outcome_digest": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.5},
                "beats_baseline": False,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.metrics.get("attestation_type"), "internal_hmac")


if __name__ == "__main__":
    unittest.main()
