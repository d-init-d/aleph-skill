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
from aleph.quality import evaluate  # noqa: E402
from aleph.validator import (  # noqa: E402
    artifact_integrity_hash,
    validate_numerical_artifacts,
    validate_workspace,
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


class BackwardCompatibilityAcceptanceTests(unittest.TestCase):
    def test_a17_uncalibrated_simulation_supported(self) -> None:
        """A17: Valid causal simulation workspace without calibration bundle executed under standard relative-weight mode.

        Simulation runs normally; relative weights calculated without forcing synthetic calibration or failing valid uncalibrated simulations.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, _ = _setup_base_workspace(temporary)
            # Manifest has relative_weight mode, no calibration bundle
            manifest["likelihood_mode"] = "relative_weight"
            manifest["artifact_paths"].pop("calibration_report", None)
            manifest["artifact_paths"].pop("calibration_bundle", None)
            _sync_manifest_and_model(workspace, manifest)

            # Validate numerical artifacts
            num_result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(num_result.status, "pass", [i.to_dict() for i in num_result.issues])
            self.assertFalse(num_result.metrics.get("calibration_present"))

            # Full workspace validation passes cleanly
            ws_result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(ws_result["status"], "pass", ws_result.get("errors"))

            # Quality evaluation grants verified / limited without failing
            q_result = evaluate(workspace, validation=ws_result)
            self.assertIn(q_result.get("assurance_status"), {"verified", "limited"})

    def test_a19_legacy_schema_2_0_semantics(self) -> None:
        """A19: Legacy schema 2.0 calibration artifacts read and evaluated alongside formula 2.1 workspaces.

        Legacy artifacts parsed correctly under 2.0 semantics; cannot be promoted to 2.1 without explicit migration.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            # Workspace configured for formula 2.1
            manifest["formula_version"] = "2.1.0"
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            # Legacy 2.0 calibration artifact with formula_version="2.0.0" and schema_version="2.0.0"
            legacy_summary = {
                "schema_version": "2.0.0",
                "status": "pass",
                "policy_locked": True,
                "model_version": "aleph-engine-2.0",
                "formula_version": "2.0.0",  # Legacy formula
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
            legacy_summary["report_hash"] = canonical_hash(legacy_summary)
            write_json_atomic(workspace / "calibration-report.json", legacy_summary)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            # Legacy 2.0 cannot be promoted to 2.1 without migration
            mismatch_issues = [
                i for i in result.issues if i.code in {"TRACK_MISMATCH", "REPLAY_MISMATCH"} and "formula_version" in str(i.pointer)
            ]
            self.assertTrue(len(mismatch_issues) > 0)


if __name__ == "__main__":
    unittest.main()
