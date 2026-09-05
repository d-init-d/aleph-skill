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
from aleph.packs import evaluate_hindcast_case  # noqa: E402
from aleph.validator import (  # noqa: E402
    artifact_integrity_hash,
    validate_numerical_artifacts,
)

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


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


class TemporalLeakageAcceptanceTests(unittest.TestCase):
    def test_a10_temporal_leakage_vintage_cutoff(self) -> None:
        """A10: Input dataset snapshot has vintage_date or publication_date occurring after case forecast_origin (temporal leakage).

        Temporal leakage audit flags VINTAGE_AFTER_CUTOFF; halts calibration certification.
        """
        # Test 1: evaluate_hindcast_case directly
        hindcast_case = {
            "case_id": "case-leak-001",
            "cutoff": "2024-06-01T00:00:00Z",
            "formula_version": "2.0.0",
            "model": {
                "nodes": [{"id": "factor:a", "type": "factor", "initial_value": 1.0}],
                "edges": [],
            },
            "observations": {"factor:a": 1.0},
            "baselines": {"factor:a": 0.5},
            "evidence": [
                {
                    "id": "ev-leak",
                    "vintage_date": "2024-07-01T00:00:00Z",  # Leakage: after cutoff 2024-06-01
                    "available_at": "2024-07-01T00:00:00Z",
                    "source": "https://data.example.org/revised-series",
                }
            ],
            "evidence_snapshot_hash": "0" * 64,
        }
        res = evaluate_hindcast_case(hindcast_case)
        self.assertFalse(res["ok"])
        leak_issues = [
            i for i in res["issues"] if "VINTAGE_AFTER_CUTOFF" in i.get("message", "") or i.get("code") == "PACKET_CUTOFF"
        ]
        self.assertTrue(len(leak_issues) > 0)

        # Test 2: In workspace with validate_numerical_artifacts
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = {
                    "case_id": cid,
                    "forecast_origin": "2024-06-01T00:00:00Z",
                    "target_period": f"2024-07-{i+1:02d}T00:00:00Z",
                    "model_version": "aleph-engine-2.0",
                    "model_hash": model_digest,
                    "formula_version": "2.0.0",
                    "point_prediction": 10.0,
                    "actual_value": 10.2,
                    "baseline_prediction": 8.0,
                    "official_release_date": "2024-08-01T00:00:00Z",
                    "evidence": [
                        {
                            "id": f"ev-{cid}",
                            "vintage_date": "2024-07-01T00:00:00Z" if i == 0 else "2024-05-01T00:00:00Z",
                            "source": "https://data.example.org/series",
                        }
                    ],
                }
                c["commitment_hash"] = canonical_hash(c)
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
            leakage_issues = [
                i for i in result.issues if "VINTAGE_AFTER_CUTOFF" in i.message
            ]
            self.assertTrue(len(leakage_issues) > 0)
            self.assertFalse(result.metrics.get("calibration_verified"))

    def test_a11_revised_series_vintage_caveat(self) -> None:
        """A11: Revised economic series used for historical hindcast without point-in-time vintage data.

        Validator records as-of limitation caveat; prohibits claim of leak-free hindcast.
        """
        # Test 1: evaluate_hindcast_case records caveat
        hindcast_case = {
            "case_id": "case-revised-001",
            "cutoff": "2024-06-01T00:00:00Z",
            "formula_version": "2.0.0",
            "model": {
                "nodes": [{"id": "factor:a", "type": "factor", "initial_value": 1.0}],
                "edges": [],
            },
            "observations": {"factor:a": 1.0},
            "baselines": {"factor:a": 0.5},
            "evidence": [
                {
                    "id": "ev-revised",
                    "available_at": "2024-05-01T00:00:00Z",
                    "access_date": "2024-05-01T00:00:00Z",
                    "is_revised": True,  # Revised series without vintage_date
                    "source": "https://data.example.org/revised-series",
                }
            ],
            "evidence_snapshot_hash": "0" * 64,
        }
        res = evaluate_hindcast_case(hindcast_case)
        caveat_issues = [
            i for i in res["issues"] if i.get("code") == "VINTAGE_LIMITATION"
        ]
        self.assertTrue(len(caveat_issues) > 0)

        # Test 2: In workspace
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            hindcast_dir = workspace / "hindcast"
            hindcast_dir.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                cid = f"case-{i:03d}"
                c = {
                    "case_id": cid,
                    "forecast_origin": "2024-06-01T00:00:00Z",
                    "target_period": f"2024-07-{i+1:02d}T00:00:00Z",
                    "model_version": "aleph-engine-2.0",
                    "model_hash": model_digest,
                    "formula_version": "2.0.0",
                    "point_prediction": 10.0,
                    "actual_value": 10.2,
                    "baseline_prediction": 8.0,
                    "official_release_date": "2024-08-01T00:00:00Z",
                    "evidence": [
                        {
                            "id": f"ev-{cid}",
                            "access_date": "2024-05-01T00:00:00Z",
                            "is_revised": True,  # Revised without vintage_date
                            "source": "https://data.example.org/series",
                        }
                    ],
                }
                c["commitment_hash"] = canonical_hash(c)
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
            caveats = [
                i for i in result.issues if i.code == "VINTAGE_LIMITATION"
            ]
            self.assertTrue(len(caveats) > 0)


if __name__ == "__main__":
    unittest.main()
