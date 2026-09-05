from __future__ import annotations

import json
import os
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

from aleph.io import canonical_hash, write_json_atomic  # noqa: E402


class PackagingRelocatabilityAcceptanceTests(unittest.TestCase):
    def test_t18_git_free_standalone_execution(self) -> None:
        """T18: Simulation execution executed from unzipped standalone package archive without .git repository.

        Engine runs cold-start trace generation and replay cleanly without Git dependencies.
        """
        with tempfile.TemporaryDirectory() as temporary_dir:
            temp_path = Path(temporary_dir)
            standalone_skill = temp_path / "standalone_aleph_skill"
            standalone_skill.mkdir(parents=True, exist_ok=True)

            # Copy essential skill distribution directories and files (no .git)
            shutil.copytree(ROOT / "scripts", standalone_skill / "scripts")
            if (ROOT / "schemas").is_dir():
                shutil.copytree(ROOT / "schemas", standalone_skill / "schemas")
            for top_file in ["SKILL.md", "distribution-manifest.json", "pyproject.toml"]:
                src_f = ROOT / top_file
                if src_f.is_file():
                    shutil.copy2(src_f, standalone_skill / top_file)

            # Explicitly verify NO .git exists anywhere in standalone tree
            self.assertFalse((standalone_skill / ".git").exists())
            self.assertFalse((temp_path / ".git").exists())

            # Create cold-start numerical workspace inside standalone environment
            workspace = standalone_skill / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            manifest = {
                "schema_version": "2.0.0",
                "manifest_version": "2.1.0",
                "simulation_mode": "deterministic",
                "formula_version": "2.1.0",
                "temporal_frame": {
                    "simulation_start": "2026-01-01T00:00:00Z",
                    "timestep": "1d",
                    "horizon_ticks": 4,
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
            write_json_atomic(workspace / "simulation-manifest.json", manifest)

            nodes = [
                {"id": "node:inflow", "name": "Inflow", "category": "driver", "scale": "level", "domain": [-100.0, 100.0], "initial_value": 8.0},
                {"id": "node:stock", "name": "Stock", "category": "state", "scale": "stock", "domain": [-100.0, 100.0], "initial_value": 0.0, "retention": 0.8},
            ]
            write_json_atomic(workspace / "nodes.json", nodes)

            edges = [
                {"id": "causal:flow_to_stock", "source": "node:inflow", "target": "node:stock", "sign": 1, "strength": 0.5, "lag_ticks": 0, "transform": "linear", "transform_parameters": {}},
            ]
            write_json_atomic(workspace / "edges.json", edges)

            # 1. Run simulation cold-start from standalone without git
            run_script = standalone_skill / "scripts" / "run_simulation.py"
            env = dict(os.environ)
            # Remove any GIT environment variables to guarantee git-free isolation
            for git_var in list(env.keys()):
                if git_var.startswith("GIT_"):
                    del env[git_var]

            proc_run = subprocess.run(
                [sys.executable, str(run_script), "--workspace", str(workspace), "--ticks", "4"],
                capture_output=True,
                text=True,
                cwd=str(standalone_skill),
                env=env,
            )
            self.assertEqual(proc_run.returncode, 0, f"Cold-start run failed: {proc_run.stdout}\n{proc_run.stderr}")

            trace_file = workspace / "execution-trace.json"
            self.assertTrue(trace_file.is_file())
            trace_json = json.loads(trace_file.read_text(encoding="utf-8"))
            self.assertEqual(trace_json["generation_mode"], "engine_derived")
            self.assertEqual(len(trace_json["steps"]), 4)
            self.assertTrue(bool(trace_json.get("replay_hash")))

            # 2. Replay simulation from standalone without git
            replay_script = standalone_skill / "scripts" / "replay_simulation.py"
            proc_replay = subprocess.run(
                [sys.executable, str(replay_script), "--workspace", str(workspace)],
                capture_output=True,
                text=True,
                cwd=str(standalone_skill),
                env=env,
            )
            self.assertEqual(proc_replay.returncode, 0, f"Standalone replay failed: {proc_replay.stdout}\n{proc_replay.stderr}")
            replay_json = json.loads(proc_replay.stdout)
            self.assertTrue(replay_json.get("match"))
            self.assertTrue(replay_json.get("trace_contract_ok"))
            self.assertTrue(replay_json.get("trace_ok"))

    def test_i11_archive_smoke_execution(self) -> None:
        """I11: Release archive unzipped into isolated environment and smoke tested."""
        with tempfile.TemporaryDirectory() as temporary_dir:
            temp_path = Path(temporary_dir)
            isolated_root = temp_path / "aleph_isolated"
            isolated_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / "scripts", isolated_root / "scripts")
            if (ROOT / "schemas").is_dir():
                shutil.copytree(ROOT / "schemas", isolated_root / "schemas")

            # Quick smoke test of validator module import and issue generation
            smoke_code = (
                "from aleph.issues import issue; "
                "from aleph.paths import resolve_in_workspace; "
                "from aleph.engine import ComputationalModel; "
                "m = ComputationalModel(); "
                "print('SMOKE_OK')"
            )
            proc = subprocess.run(
                [sys.executable, "-c", smoke_code],
                capture_output=True,
                text=True,
                cwd=str(isolated_root),
                env={**os.environ, "PYTHONPATH": str(isolated_root / "scripts")},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("SMOKE_OK", proc.stdout)

    def test_i12_packaging_schema_inclusion(self) -> None:
        """I12: Runtime packaging build requires schema sidecars."""
        with tempfile.TemporaryDirectory() as temporary_dir:
            incomplete_package = Path(temporary_dir) / "no_schemas"
            incomplete_package.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / "scripts", incomplete_package / "scripts")
            # Missing schemas/ directory or missing contracts
            validator_script = incomplete_package / "scripts" / "validate_skill_package.py"
            if validator_script.is_file():
                proc = subprocess.run(
                    [sys.executable, str(validator_script), str(incomplete_package)],
                    capture_output=True,
                    text=True,
                )
                # Should fail when schemas or critical files are missing
                self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
