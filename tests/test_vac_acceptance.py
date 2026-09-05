from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.io import canonical_hash, write_json_atomic  # noqa: E402
from aleph.quality import evaluate  # noqa: E402
from aleph.validator import (  # noqa: E402
    artifact_integrity_hash,
    validate_branches,
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
    available_at: str | None = None,
    observation_id: str | None = None,
    features: dict | None = None,
) -> dict:
    case: dict = {
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
                "available_at": available_at or evidence_vintage,
                "source": "https://data.example.org/series",
            }
        ],
    }
    if observation_id is not None:
        case["observation_id"] = observation_id
    if features is not None:
        case["features"] = features
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


class VACAcceptanceTestSuite(unittest.TestCase):
    """Authoritative test suite for Aleph Calibration Hardening (VAC01 - VAC22)."""

    def test_vac01_repro_cr04_missing_source_policy(self) -> None:
        """VAC01: Repro CR04: 30 cases present on disk, but missing calibration-policy.json.

        Expected: Numerical gate does not pass calibrated; structured issues cite missing policy artifact.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            raw_cases = []
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                raw_cases.append(c)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Deliberately DO NOT create workspace / "calibration-policy.json"
            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "hindcast_digest": canonical_hash([canonical_hash(c) for c in raw_cases]),
                "outcome_digest": canonical_hash([c.get("actual_value") for c in raw_cases]),
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2, "rmse": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            self.assertNotEqual(result.metrics.get("assurance_status"), "calibrated")
            missing_artifacts = [i for i in result.issues if i.code == "MISSING_ARTIFACT" and "policy" in (i.artifact + i.pointer + i.message).lower()]
            self.assertTrue(len(missing_artifacts) > 0)

    def test_vac02_unlocked_or_tampered_policy_hash(self) -> None:
        """VAC02: Policy missing, unlocked, or policy_hash does not match canonical bytes."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Policy has tampered hash
            policy = {
                "policy_locked": True,
                "precommitted": True,
                "policy_hash": "0" * 64,  # Bad hash!
                "thresholds": {"max_mae": 1.0},
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
                "policy_hash": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            tamper_issues = [i for i in result.issues if i.code == "POLICY_THRESHOLD_TAMPERING"]
            self.assertTrue(len(tamper_issues) > 0)

    def test_vac03_snapshot_list_missing_or_bad_digest(self) -> None:
        """VAC03: Dataset snapshot list missing, empty, or file sha256 mismatch."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_bundle"] = "calibration-bundle.json"
            _sync_manifest_and_model(workspace, manifest)

            # Snapshot file on disk
            snap_file = workspace / "data" / "source.csv"
            snap_file.parent.mkdir(parents=True, exist_ok=True)
            snap_file.write_text("a,b,c\n1,2,3\n", encoding="utf-8")

            bundle = {
                "schema_version": "1.0.0",
                "bundle_id": "bundle:bad-digest",
                "domain": "economics",
                "target_variable": "inflation",
                "policy_manifest": {
                    "policy_locked": True,
                    "precommitted": True,
                    "thresholds": {"max_mae": 1.0},
                },
                "dataset_snapshots": [
                    {
                        "dataset_id": "ds:test",
                        "file_path": "data/source.csv",
                        "sha256_digest": "sha256:" + "f" * 64,  # Fabricated digest
                    }
                ],
                "per_case_predictions": [],
                "realized_outcomes": [],
            }
            bundle["policy_manifest"]["policy_hash"] = canonical_hash(
                {k: v for k, v in bundle["policy_manifest"].items() if k != "policy_hash"}
            )
            bundle["bundle_hash"] = canonical_hash(bundle)
            write_json_atomic(workspace / "calibration-bundle.json", bundle)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            self.assertTrue(any(i.code in ("STALE_ARTIFACT", "MISSING_ARTIFACT") for i in result.issues))

    def test_vac04_baseline_missing_or_non_numeric(self) -> None:
        """VAC04: Baseline prediction missing or non-numeric; must not default to 0.0."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                if i == 0:
                    del c["baseline_prediction"]  # Missing baseline!
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            missing_base = [i for i in result.issues if i.code == "MISSING_FIELD" and "baseline_prediction" in i.pointer]
            self.assertTrue(len(missing_base) > 0)
            self.assertIn("cannot default missing baseline to 0.0", missing_base[0].message)

    def test_vac05_invalid_data_types_or_unknown_fields(self) -> None:
        """VAC05: Invalid data types in calibration artifacts; structured error without traceback."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": "yes",  # String instead of bool!
                "case_count": "thirty",  # String instead of int!
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            type_issues = [i for i in result.issues if i.code == "TYPE"]
            self.assertTrue(len(type_issues) > 0)

    def test_vac06_metric_recomputation_discrepancy(self) -> None:
        """VAC06: Candidate MAE is accurate but RMSE or baseline is declared incorrectly."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest, prediction=10.0, actual=10.2, baseline=8.0)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
            write_json_atomic(workspace / "calibration-policy.json", policy)

            # Recomputed MAE=0.2, RMSE=0.2. Summary declares fake RMSE=0.01!
            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": policy["policy_hash"],
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2, "rmse": 0.01},  # Fake RMSE!
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            rmse_issues = [i for i in result.issues if i.code == "REPLAY_MISMATCH" and "metrics.rmse" in i.pointer]
            self.assertTrue(len(rmse_issues) > 0)

    def test_vac07_false_policy_hash_or_unverified_hmac(self) -> None:
        """VAC07: False policy_hash or unverified HMAC string must not grant internal_hmac."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
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
                "policy_hash": "a" * 64,
                "hmac_signature": "unverified-random-string",  # Unverified string!
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertNotEqual(result.metrics.get("attestation_type"), "internal_hmac")
            self.assertFalse(result.metrics.get("calibration_verified"))

    def test_vac08_hmac_production_vs_test_key(self) -> None:
        """VAC08: HMAC signed with test key grants test_only_hmac, not empirical calibrated status."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "hmac_signature": "test-key-signed:" + "e" * 64,
                "key_scope": "test",
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2, "rmse": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.metrics.get("attestation_type"), "test_only_hmac")
            self.assertFalse(result.metrics.get("calibration_verified"))
            self.assertNotEqual(result.metrics.get("assurance_status"), "calibrated")

    def test_vac09_case_bytes_tampered_rehash_detection(self) -> None:
        """VAC09: Modify case file bytes but keep commitment_hash unchanged; rehash must catch tamper."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            case_commits = {}
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                if i == 0:
                    old_c_hash = c["commitment_hash"]
                    c["point_prediction"] = 99.0  # Tampered prediction, but keep old commitment_hash
                    c["commitment_hash"] = old_c_hash
                case_commits[cid] = c["commitment_hash"]
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True, "case_commitments": case_commits}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            stale_issues = [i for i in result.issues if i.code == "STALE_ARTIFACT"]
            self.assertTrue(len(stale_issues) > 0)

    def test_vac10_threshold_tamper_invalidates_precommit(self) -> None:
        """VAC10: Candidate raises threshold ex-post and self-rehashes; precommitment check detects tamper."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            # Precommitment hash was registered before running hindcasts
            orig_policy = {"policy_locked": True, "precommitted": True, "thresholds": {"max_mae": 0.1}}
            orig_pol_hash = canonical_hash(orig_policy)
            manifest["precommitted_policy_hash"] = orig_pol_hash
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest, prediction=10.0, actual=10.2)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Candidate tampered threshold to 10.0 and recomputed policy_hash
            tampered_policy = {"policy_locked": True, "precommitted": True, "thresholds": {"max_mae": 10.0}}
            tampered_policy["policy_hash"] = canonical_hash(tampered_policy)
            write_json_atomic(workspace / "calibration-policy.json", tampered_policy)

            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": tampered_policy["policy_hash"],
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2, "rmse": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            tamper_issues = [i for i in result.issues if i.code == "POLICY_THRESHOLD_TAMPERING"]
            self.assertTrue(len(tamper_issues) > 0)

    def test_vac11_missing_or_invalid_origin_date(self) -> None:
        """VAC11: Missing or timezone-naive forecast origin rejected; temporal check not skipped."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                if i == 0:
                    c["forecast_origin"] = "2025-01-01 00:00:00"  # Missing timezone Z or offset!
                    c["commitment_hash"] = canonical_hash(c)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            cutoff_issues = [i for i in result.issues if i.code in ("PACKET_CUTOFF", "MISSING_FIELD")]
            self.assertTrue(len(cutoff_issues) > 0)

    def test_vac12_release_after_origin_lookahead_leakage(self) -> None:
        """VAC12: Vintage is prior to origin but actual release/availability is after origin."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    forecast_origin="2025-01-01T00:00:00Z",
                    evidence_vintage="2024-12-01T00:00:00Z",
                    available_at="2025-06-01T00:00:00Z" if i == 0 else "2024-12-01T00:00:00Z",  # Look-ahead leakage!
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            leakage_issues = [i for i in result.issues if i.code == "PACKET_CUTOFF"]
            self.assertTrue(len(leakage_issues) > 0)

    def test_vac13_future_aggregate_feature_leakage(self) -> None:
        """VAC13: Feature aggregate window end extends past forecast origin."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                feats = None
                if i == 0:
                    feats = {"macro_agg": {"window_end": "2025-05-01T00:00:00Z"}}  # After origin 2025-01-01
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    forecast_origin="2025-01-01T00:00:00Z",
                    features=feats,
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            agg_issues = [i for i in result.issues if "FEATURE_AGGREGATE_LEAKAGE" in i.message]
            self.assertTrue(len(agg_issues) > 0)

    def test_vac14_duplicate_observation_sample_inflation(self) -> None:
        """VAC14: Cases with distinct case_ids pointing to duplicate observation_id."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # Duplicate observation_id for cases 0 and 1
                obs_id = "obs-same-item" if i < 2 else f"obs-{i}"
                c = _create_raw_case(cid, model_hash=model_digest, observation_id=obs_id)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))
            inf_issues = [i for i in result.issues if "ARTIFICIAL_SAMPLE_INFLATION" in i.message]
            self.assertTrue(len(inf_issues) > 0)

    def test_vac15_equal_numeric_values_distinct_observations(self) -> None:
        """VAC15: Distinct observations that happen to have equal numeric values are not flagged as duplicates."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # Distinct target period, but all have point_prediction=10.0 and actual_value=10.2
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                    prediction=10.0,
                    actual=10.2,
                    baseline=8.0,
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2, "rmse": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            # Must NOT flag inflation
            inf_issues = [i for i in result.issues if "ARTIFICIAL_SAMPLE_INFLATION" in i.message]
            self.assertEqual(len(inf_issues), 0)
            self.assertEqual(result.metrics.get("unique_case_count"), 30)

    def test_vac16_rolling_overlap_effective_sample_size(self) -> None:
        """VAC16: Rolling/overlapping dependence adjusts effective sample size downwards."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_bundle"] = "calibration-bundle.json"
            _sync_manifest_and_model(workspace, manifest)

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
                    "point_prediction": 10.0,
                })
                outs.append({
                    "case_id": cid,
                    "target_period": f"2025-04-{i+1:02d}T00:00:00Z",
                    "actual_value": 10.2,
                    "baseline_prediction": 8.0,
                    "official_release_date": "2025-05-01T00:00:00Z",
                })

            bundle = {
                "schema_version": "1.0.0",
                "bundle_id": "bundle:rolling-test",
                "domain": "economics",
                "target_variable": "inflation",
                "target_horizon": "3m",
                "policy_manifest": {
                    "policy_locked": True,
                    "precommitted": True,
                    "thresholds": {"max_mae": 1.0},
                },
                "dataset_snapshots": [],
                "per_case_predictions": preds,
                "realized_outcomes": outs,
            }
            bundle["policy_manifest"]["policy_hash"] = canonical_hash(
                {k: v for k, v in bundle["policy_manifest"].items() if k != "policy_hash"}
            )
            bundle["bundle_hash"] = canonical_hash(bundle)
            write_json_atomic(workspace / "calibration-bundle.json", bundle)

            result = validate_numerical_artifacts(workspace, manifest)
            recomputed = result.metrics.get("recomputed_metrics") or {}
            eff_sample = recomputed.get("effective_sample_size")
            self.assertIsNotNone(eff_sample)
            self.assertLess(eff_sample, 30.0)
            self.assertEqual(eff_sample, 10.0)

    def test_vac17_candidate_loses_to_baseline_uncalibrated(self) -> None:
        """VAC17: Candidate has inferior MAE compared to baseline; valid result but uncalibrated."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                # Prediction error is 5.0, baseline error is only 0.2
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    prediction=15.0,
                    actual=10.0,
                    baseline=10.2,
                )
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
            write_json_atomic(workspace / "calibration-policy.json", policy)

            summary = {
                "schema_version": "2.0.0",
                "status": "uncalibrated",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": policy["policy_hash"],
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 5.0, "rmse": 5.0},
                "beats_baseline": False,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertFalse(result.metrics.get("beats_baseline"))
            self.assertFalse(result.metrics.get("calibration_verified"))
            self.assertEqual(result.metrics.get("assurance_status"), "uncalibrated")

    def test_vac18_synthetic_fixture_labeled_empirical(self) -> None:
        """VAC18: Synthetic fixture with 30 cases must not be elevated to calibrated empirical status."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest, is_synthetic=True)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {"policy_locked": True, "precommitted": True}
            policy["policy_hash"] = canonical_hash(policy)
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

    def test_vac19_domain_or_horizon_scope_mismatch(self) -> None:
        """VAC19: Calibration scope (domain/target/horizon) mismatch prevents promotion."""
        manifest = {
            "likelihood_mode": "calibrated_probability",
            "artifact_paths": {"calibration_report": "calibration-report.json"},
            "scope": {
                "domain": "geopolitics",
                "target_variable": "conflict_escalation",
                "target_horizon": "6m",
            },
        }
        calibration = {
            "domain": "economics",  # Mismatch against geopolitics!
            "target_variable": "inflation",
            "target_horizon": "1m",
            "metrics": {"brier_score": 0.1},
        }
        branch_ledger = {
            "schema_version": "2.0.0",
            "likelihood_mode": "calibrated_probability",
            "calibrated": True,
            "calibration": {
                "method": "historical_hindcast",
                "sample_count": 30,
                "interval": [0.0, 1.0],
                "calibration_policy_ref": "calibration-policy.json",
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": "0" * 64,
                "hindcast_report_ref": "calibration-report.json",
                "domain": "economics",
                "target_variable": "inflation",
                "target_horizon": "1m",
            },
            "branches": [
                {"id": "b1", "probability": 0.6, "domain": "geopolitics"},
                {"id": "b2", "probability": 0.4, "domain": "geopolitics"},
            ],
            "unresolved_mass": 0.0,
        }

        res = validate_branches(branch_ledger, manifest=manifest, calibration=calibration)
        scope_issues = [i for i in res.issues if i.code == "SCOPE_MISMATCH"]
        self.assertTrue(len(scope_issues) > 0)

    def test_vac20_continuous_mae_not_event_probability(self) -> None:
        """VAC20: Continuous MAE cannot authorize calibrated_probability for event branches."""
        manifest = {
            "likelihood_mode": "calibrated_probability",
            "artifact_paths": {"calibration_report": "calibration-report.json"},
            "scope": {"domain": "economics", "target_variable": "inflation"},
        }
        calibration = {
            "domain": "economics",
            "target_variable": "inflation",
            "evaluation_type": "point_forecast",
            "metrics": {"mae": 0.2, "rmse": 0.25},
        }
        branch_ledger = {
            "schema_version": "2.0.0",
            "likelihood_mode": "calibrated_probability",
            "calibrated": True,
            "calibration": {
                "method": "historical_hindcast",
                "sample_count": 30,
                "interval": [0.0, 1.0],
                "calibration_policy_ref": "calibration-policy.json",
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": "0" * 64,
                "hindcast_report_ref": "calibration-report.json",
                "domain": "economics",
                "target_variable": "inflation",
                "evaluation_type": "point_forecast",
                "metrics": {"mae": 0.2, "rmse": 0.25},
            },
            "branches": [
                {"id": "b1", "probability": 0.7, "domain": "economics"},
                {"id": "b2", "probability": 0.3, "domain": "economics"},
            ],
            "unresolved_mass": 0.0,
        }

        res = validate_branches(branch_ledger, manifest=manifest, calibration=calibration)
        mismatch_issues = [
            i for i in res.issues if i.code == "SCOPE_MISMATCH" and "continuous MAE" in i.message
        ]
        self.assertTrue(len(mismatch_issues) > 0)

    def test_vac21_valid_positive_calibration_e2e(self) -> None:
        """VAC21: Valid positive end-to-end calibration achieves calibrated empirical assurance."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            raw_cases = []
            case_commits = {}
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(
                    cid,
                    model_hash=model_digest,
                    prediction=10.0 + (i % 3) * 0.1,
                    actual=10.0 + (i % 3) * 0.1 + 0.2,
                    baseline=8.0,
                    target_period=f"2025-02-{i+1:02d}T00:00:00Z",
                )
                raw_cases.append(c)
                case_commits[cid] = c["commitment_hash"]
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            policy = {
                "policy_locked": True,
                "precommitted": True,
                "case_commitments": case_commits,
                "thresholds": {"max_mae": 1.0},
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
                "metrics": {"mae": 0.2, "rmse": 0.2, "baseline_mae": 2.3, "baseline_rmse": 2.3014},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "pass", [i.to_dict() for i in result.issues])
            self.assertTrue(result.metrics.get("calibration_verified"))
            self.assertTrue(result.metrics.get("beats_baseline"))
            self.assertEqual(result.metrics.get("assurance_status"), "calibrated")

    def test_vac22_error_propagation_across_downstream_gates(self) -> None:
        """VAC22: Error on calibration policy propagates across validator, quality, and release gates."""
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, _, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = _create_raw_case(cid, model_hash=model_digest)
                write_json_atomic(hindcast_dir / f"{cid}.json", c)

            # Policy has tampering error
            policy = {
                "policy_locked": False,  # Unlocked policy error!
                "policy_hash": "0" * 64,
            }
            write_json_atomic(workspace / "calibration-policy.json", policy)

            # Forged summary claims pass and beats_baseline: true
            summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",
                "model_hash": model_digest,
                "config_hash": "0" * 64,
                "policy_hash": "0" * 64,
                "case_count": 30,
                "unique_case_count": 30,
                "metrics": {"mae": 0.2},
                "beats_baseline": True,
            }
            summary["report_hash"] = canonical_hash(summary)
            write_json_atomic(workspace / "calibration-report.json", summary)

            # Gate 1: Validator
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertFalse(result.metrics.get("calibration_verified"))

            ws_result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(ws_result["status"], "fail")

            # Gate 2: Quality evaluator
            q_result = evaluate(workspace, validation=ws_result)
            self.assertEqual(q_result.get("grade"), "fail")
            self.assertFalse(q_result.get("calibrated", False))


if __name__ == "__main__":
    unittest.main()
