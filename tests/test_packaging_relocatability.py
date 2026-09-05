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

from aleph.io import write_json_atomic  # noqa: E402


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

    def test_vpk01_clean_clone_no_external_audit_refs(self) -> None:
        """VPK01: Clean clone contains only committed files and has no sibling audit references.

        Verifies that no test file references parent or external audit-artifacts paths.
        """
        tests_dir = ROOT / "tests"
        external_refs: list[str] = []
        for py_file in tests_dir.rglob("*.py"):
            if py_file.name == "test_packaging_relocatability.py":
                continue
            text = py_file.read_text(encoding="utf-8")
            for idx, line in enumerate(text.splitlines(), start=1):
                if "audit-artifacts" in line and "parents" in line:
                    external_refs.append(f"{py_file.name}:{idx}: {line.strip()}")
        self.assertEqual(
            external_refs,
            [],
            "Found external audit-artifacts references violating self-containment:\n" + "\n".join(external_refs),
        )

    def test_vpk02_extracted_archive_cold_start_unittest(self) -> None:
        """VPK02: Aleph full extracted archive runs cold-start unittest without FileNotFoundError.

        Verifies CR07 resolution: tests in isolated directory execute cleanly without external schema paths.
        """
        with tempfile.TemporaryDirectory() as temporary_dir:
            isolated = Path(temporary_dir) / "isolated_aleph"
            isolated.mkdir(parents=True, exist_ok=True)

            # Copy self-contained repo files into isolated directory (guaranteed no parent audit-artifacts)
            shutil.copytree(ROOT / "scripts", isolated / "scripts")
            shutil.copytree(ROOT / "schemas", isolated / "schemas")
            shutil.copytree(ROOT / "tests", isolated / "tests")
            for f in ["SKILL.md", "pyproject.toml", "distribution-manifest.json"]:
                if (ROOT / f).is_file():
                    shutil.copy2(ROOT / f, isolated / f)

            # Run cold start unittest inside isolated directory
            env = dict(os.environ)
            env["PYTHONPATH"] = str(isolated / "scripts")
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_numerical_cold_start.py"],
                cwd=str(isolated),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"Unittest in isolated directory failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
            self.assertNotIn("FileNotFoundError", proc.stderr)
            self.assertIn("OK", proc.stderr)

    def test_vpk03_declared_extras_dependencies(self) -> None:
        """VPK03: Environment declares required dependencies/extras in pyproject.toml."""
        pyproject_file = ROOT / "pyproject.toml"
        self.assertTrue(pyproject_file.is_file())
        text = pyproject_file.read_text(encoding="utf-8")
        self.assertIn("jsonschema", text)
        self.assertIn("[project.optional-dependencies]", text)

    def test_vpk04_runtime_profile_minimal_closure(self) -> None:
        """VPK04: Runtime profile has minimal closure without requiring dev dependencies."""
        with tempfile.TemporaryDirectory() as temporary_dir:
            rt_dir = Path(temporary_dir) / "runtime"
            rt_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / "scripts", rt_dir / "scripts")
            shutil.copytree(ROOT / "schemas", rt_dir / "schemas")

            # Standard library import test of core runtime modules without dev dependencies
            code = (
                "import sys; sys.path.insert(0, 'scripts'); "
                "import aleph; "
                "import aleph.engine; "
                "import aleph.validator; "
                "import aleph.paths; "
                "print('RUNTIME_CLOSURE_OK')"
            )
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=str(rt_dir),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("RUNTIME_CLOSURE_OK", proc.stdout)

    def test_vpk05_full_profile_comprehensive_suite(self) -> None:
        """VPK05: Full profile contains comprehensive test suite, release tooling, and schemas."""
        tests_dir = ROOT / "tests"
        schemas_dir = ROOT / "schemas"
        scripts_dir = ROOT / "scripts"
        self.assertTrue(tests_dir.is_dir())
        self.assertTrue(schemas_dir.is_dir())
        self.assertTrue(scripts_dir.is_dir())

        test_files = list(tests_dir.glob("test_*.py"))
        self.assertGreaterEqual(len(test_files), 20, f"Expected full suite (>= 20 files), found {len(test_files)}")
        self.assertTrue((scripts_dir / "build_release_assets.py").is_file())
        self.assertTrue((scripts_dir / "validate_skill_package.py").is_file())

    def test_vpk09_profile_schema_closure_verification(self) -> None:
        """VPK09: Profile schema closure: all catalog-advertised schemas exist on disk."""
        schemas_dir = ROOT / "schemas"
        for catalog_name in ["schema-catalog.json", "schema-catalog-2.1.json"]:
            cat_path = schemas_dir / catalog_name
            self.assertTrue(cat_path.is_file(), f"Missing catalog {catalog_name}")
            data = json.loads(cat_path.read_text(encoding="utf-8"))
            for art_name, rel_schema in data.get("artifacts", {}).items():
                target = schemas_dir / rel_schema
                self.assertTrue(target.is_file(), f"Catalog {catalog_name} artifact '{art_name}' references missing {rel_schema}")

    def test_vpk10_unicode_path_and_whitespace_execution(self) -> None:
        """VPK10: Execution in workspace paths containing Unicode characters and whitespace."""
        with tempfile.TemporaryDirectory() as temporary_dir:
            unicode_ws = Path(temporary_dir) / "Không Gian Thử Nghiệm 2026"
            unicode_ws.mkdir(parents=True, exist_ok=True)

            manifest = {
                "schema_version": "2.0.0",
                "manifest_version": "2.1.0",
                "simulation_mode": "deterministic",
                "formula_version": "2.1.0",
                "temporal_frame": {
                    "simulation_start": "2026-01-01T00:00:00Z",
                    "timestep": "1d",
                    "horizon_ticks": 2,
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
            write_json_atomic(unicode_ws / "simulation-manifest.json", manifest)
            nodes = [
                {"id": "node:x", "name": "Biến X", "category": "driver", "scale": "level", "domain": [-10.0, 10.0], "initial_value": 5.0},
                {"id": "node:y", "name": "Biến Y", "category": "state", "scale": "level", "domain": [-10.0, 10.0], "initial_value": 0.0},
            ]
            write_json_atomic(unicode_ws / "nodes.json", nodes)
            edges = [
                {"id": "causal:x_to_y", "source": "node:x", "target": "node:y", "sign": 1, "strength": 0.5, "lag_ticks": 0, "transform": "linear", "transform_parameters": {}},
            ]
            write_json_atomic(unicode_ws / "edges.json", edges)

            run_script = ROOT / "scripts" / "run_simulation.py"
            proc = subprocess.run(
                [sys.executable, str(run_script), "--workspace", str(unicode_ws), "--ticks", "2"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, f"Run in Unicode path failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
            self.assertTrue((unicode_ws / "execution-trace.json").is_file())


if __name__ == "__main__":
    unittest.main()
