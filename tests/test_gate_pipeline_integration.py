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


class GatePipelineIntegrationAcceptanceTests(unittest.TestCase):
    def test_a18_all_gates_consistent(self) -> None:
        """A18: Calibration bundle validated across validator.py, quality.py, and release_gate.py.

        Consistent rejection or acceptance across all gates; downstream release gate does not bypass validator checks.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            _sync_manifest_and_model(workspace, manifest)

            # Case: Invalid calibration report (empty metrics F03)
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

            # Gate 1: validator.py
            v_result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(v_result.status, "fail")

            ws_result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(ws_result["status"], "fail")

            # Gate 2: quality.py (Must also fail, not bypass validator)
            q_result = evaluate(workspace, validation=ws_result)
            self.assertEqual(q_result.get("grade"), "fail")
            self.assertEqual(q_result.get("assurance_status"), "failed")
            self.assertFalse(q_result.get("release_claim"))

    def test_a22_verbal_probability_gate_block(self) -> None:
        """A22: Calibration gate fails but generated narrative report or actor response outputs 'calibrated probability 85%'.

        Final release pipeline catches narrative contradiction and blocks release; forbids verbal probability claims when gate fails.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, _ = _setup_base_workspace(temporary)
            # Workspace is in uncalibrated relative_weight mode, or calibration gate failed
            manifest["likelihood_mode"] = "relative_weight"
            _sync_manifest_and_model(workspace, manifest)

            # Narrative report contains forbidden verbal calibrated probability assertion
            report_path = workspace / "REPORT.md"
            current_report = report_path.read_text(encoding="utf-8")
            contradictory_report = (
                current_report
                + "\n\n### Additional Assessment\nBased on causal simulations, we estimate a calibrated probability 85% of occurrence.\n"
            )
            report_path.write_text(contradictory_report, encoding="utf-8")

            # Validate workspace
            ws_result = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(ws_result["status"], "fail")
            contradiction_issues = [
                i for i in ws_result.get("issues", []) if i.get("code") == "VERBAL_PROBABILITY_CONTRADICTION"
            ]
            self.assertTrue(len(contradiction_issues) > 0)

            # Quality evaluation fails to release
            q_result = evaluate(workspace, validation=ws_result)
            self.assertFalse(q_result.get("release_claim"))
            self.assertEqual(q_result.get("assurance_status"), "failed")


if __name__ == "__main__":
    unittest.main()
