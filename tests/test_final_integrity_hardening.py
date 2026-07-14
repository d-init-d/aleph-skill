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

from aleph.io import write_json_atomic  # noqa: E402
from aleph.validator import validate_branches, validate_numerical_artifacts  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


class NumericalIntegrityHardeningTests(unittest.TestCase):
    def test_numerical_mode_requires_model_run_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key in ("computational_model", "run_ledger", "replay_report"):
                manifest["artifact_paths"].pop(key)
            write_json_atomic(manifest_path, manifest)
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertIn("MISSING_ARTIFACT", {item.code for item in result.issues})

    def test_fabricated_replay_flags_cannot_replace_reexecution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest = json.loads(
                (workspace / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            run = json.loads((workspace / "simulation-run.json").read_text(encoding="utf-8"))
            write_json_atomic(
                workspace / "replay-report.json",
                {
                    "contract_hash_ok": True,
                    "model_hash_ok": True,
                    "config_hash_ok": True,
                    "result_hash_ok": True,
                    "trace_ok": True,
                    "match": True,
                    "recorded_contract_hash": run["contract_hash"],
                },
            )
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertTrue({"MISSING_FIELD", "REPLAY_MISMATCH"} <= {item.code for item in result.issues})

    def test_calibration_mode_and_report_must_match_full_contract(self) -> None:
        manifest = json.loads((FIXTURE / "simulation-manifest.json").read_text(encoding="utf-8"))
        ledger = json.loads((FIXTURE / "branch-ledger.json").read_text(encoding="utf-8"))
        calibrated_manifest = copy.deepcopy(manifest)
        calibrated_manifest["likelihood_mode"] = "calibrated_probability"
        branch_result = validate_branches(
            ledger,
            {"causal:rate-to-gap"},
            {"actor:governor"},
            {"evidence:macro-series", "evidence:policy-statute"},
            calibrated_manifest,
            {"factor:policy-rate", "factor:output-gap", "context:open-economy"},
        )
        self.assertEqual(branch_result.status, "fail")
        self.assertIn("TRACK_MISMATCH", {item.code for item in branch_result.issues})

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
            write_json_atomic(manifest_path, manifest)
            write_json_atomic(
                workspace / "calibration-report.json",
                {"status": "pass", "policy_locked": True},
            )
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            self.assertTrue({"MISSING_FIELD", "STALE_ARTIFACT"} <= {item.code for item in result.issues})


if __name__ == "__main__":
    unittest.main()
