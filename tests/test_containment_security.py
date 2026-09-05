from __future__ import annotations

import copy
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


class ContainmentSecurityAcceptanceTests(unittest.TestCase):
    def test_a20_path_traversal_containment(self) -> None:
        """A20: Calibration case references path traversal ('../../secrets.json') or absolute path outside workspace.

        Validator containment checks reject out-of-workspace references; enforces strict path containment.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_bundle"] = "calibration-bundle.json"
            _sync_manifest_and_model(workspace, manifest)

            # Bundle references path traversal outside workspace
            bundle = {
                "schema_version": "1.0.0",
                "bundle_id": "bundle:traversal-test",
                "domain": "economics",
                "target_variable": "inflation",
                "target_horizon": "1m",
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
                "dataset_snapshots": [
                    {
                        "dataset_id": "ds:escape",
                        "source_title": "Secrets Escape",
                        "source_url": "https://example.org/secrets",
                        "vintage_date": "2024-12-01",
                        "access_date": "2024-12-01T00:00:00Z",
                        "file_path": "../../secrets.json",  # Traversal attempt
                        "sha256_digest": "sha256:" + "0" * 64,
                        "is_synthetic": False,
                    }
                ],
                "per_case_predictions": [],
                "realized_outcomes": [],
                "recomputed_metrics": {},
                "uncertainty_bounds": {},
                "temporal_leakage_audit": {},
                "assurance_status": "uncalibrated",
            }
            bundle["bundle_hash"] = canonical_hash(bundle)
            write_json_atomic(workspace / "calibration-bundle.json", bundle)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            escape_issues = [i for i in result.issues if i.code in {"PATH_ESCAPE", "INVALID_PATH"}]
            self.assertTrue(len(escape_issues) > 0)


if __name__ == "__main__":
    unittest.main()
