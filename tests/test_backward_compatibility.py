from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from compile_model import compile_workspace  # noqa: E402
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

    def test_t17_formula_version_isolation(self) -> None:
        """T17: Replay runner invoked on golden formula 2.0 workspace and new formula 2.1 workspace.

        Both workspaces replay bit-exact according to respective formula versions; mixed-version workspace fails compilation.
        """
        # 1. Golden formula 2.0 workspace replays bit-exact under 2.0.0
        with tempfile.TemporaryDirectory() as temporary_20:
            workspace_20 = Path(temporary_20) / "workspace"
            shutil.copytree(FIXTURE, workspace_20)

            proc_20 = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace_20)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_20.returncode, 0, f"2.0 replay failed: {proc_20.stderr}")
            report_20 = json.loads(proc_20.stdout)
            self.assertTrue(report_20.get("match"))
            self.assertEqual(report_20.get("formula_version"), "2.0.0")

        # 2. New formula 2.1 workspace replays bit-exact under 2.1.0
        with tempfile.TemporaryDirectory() as temporary_21:
            workspace_21 = Path(temporary_21) / "workspace"
            workspace_21.mkdir(parents=True, exist_ok=True)

            manifest_21 = {
                "schema_version": "2.0.0",
                "manifest_version": "2.1.0",
                "simulation_mode": "deterministic",
                "formula_version": "2.1.0",
                "temporal_frame": {
                    "simulation_start": "2026-01-01T00:00:00Z",
                    "timestep": "1d",
                    "horizon_ticks": 3,
                },
                "artifact_paths": {
                    "nodes": "nodes.json",
                    "edges": "edges.json",
                    "run_ledger": "simulation-run.json",
                    "execution_trace": "execution-trace.json",
                    "compiled_model": "simulation-model.json",
                    "replay_report": "replay-report.json",
                },
                "seed": 42,
            }
            write_json_atomic(workspace_21 / "simulation-manifest.json", manifest_21)

            nodes_21 = [
                {"id": "node:x", "name": "Node X", "category": "driver", "scale": "level", "domain": [-100.0, 100.0], "initial_value": 5.0},
                {"id": "node:y", "name": "Node Y", "category": "state", "scale": "level", "domain": [-100.0, 100.0], "initial_value": 1.0},
            ]
            write_json_atomic(workspace_21 / "nodes.json", nodes_21)

            edges_21 = [
                {"id": "causal:x_to_y", "source": "node:x", "target": "node:y", "sign": 1, "strength": 0.5, "lag_ticks": 0, "transform": "linear", "transform_parameters": {}},
            ]
            write_json_atomic(workspace_21 / "edges.json", edges_21)

            # Run 2.1 simulation
            proc_run = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_simulation.py"), "--workspace", str(workspace_21), "--ticks", "3"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_run.returncode, 0, f"2.1 run failed: {proc_run.stderr}")

            # Replay 2.1 simulation
            proc_replay_21 = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace_21)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_replay_21.returncode, 0, f"2.1 replay failed: {proc_replay_21.stderr}")
            report_21 = json.loads(proc_replay_21.stdout)
            self.assertTrue(report_21.get("match"))
            self.assertEqual(report_21.get("formula_version"), "2.1.0")

        # 3. Mixed-version workspace fails compilation
        with tempfile.TemporaryDirectory() as temporary_mixed:
            workspace_mixed = Path(temporary_mixed) / "workspace"
            shutil.copytree(FIXTURE, workspace_mixed)
            manifest_mixed = json.loads((workspace_mixed / "simulation-manifest.json").read_text(encoding="utf-8"))
            # Manifest asserts 2.1.0 but compiled model and run ledger are 2.0.0
            manifest_mixed["formula_version"] = "2.1.0"
            write_json_atomic(workspace_mixed / "simulation-manifest.json", manifest_mixed)

            with self.assertRaises(ValueError) as ctx:
                compile_workspace(workspace_mixed)
            self.assertIn("workspace formula contracts disagree", str(ctx.exception))

    def test_i07_legacy_ledger_import(self) -> None:
        """I07: Legacy 14/19/22/23 column ledgers imported into Aleph."""
        from test_component_integration import ComponentIntegrationAcceptanceTests
        delegate = ComponentIntegrationAcceptanceTests()
        delegate.test_i07_legacy_ledger_import()


if __name__ == "__main__":
    unittest.main()
