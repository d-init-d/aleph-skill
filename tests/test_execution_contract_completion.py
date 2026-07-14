from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    run_deterministic,
)
from aleph.execution_binding import build_trace_execution_binding  # noqa: E402
from aleph.io import write_json_atomic  # noqa: E402
from aleph.validator import validate_numerical_artifacts  # noqa: E402
from run_simulation import _commit_json_pair  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def _run(workspace: Path, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--workspace", str(workspace), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class SecureNumericalEntryPointTests(unittest.TestCase):
    def test_run_and_replay_refuse_nonstandard_json_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "run"
            shutil.copytree(FIXTURE, workspace)
            (workspace / "simulation-config.json").write_text(
                '{"timestep": NaN}\n', encoding="utf-8"
            )
            completed = _run(workspace, "run_simulation.py", "--ticks", "182")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("INVALID_ARTIFACT", completed.stdout)
            self.assertNotIn("Traceback", completed.stdout + completed.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "replay"
            shutil.copytree(FIXTURE, workspace)
            run_path = workspace / "simulation-run.json"
            raw = run_path.read_text(encoding="utf-8").replace(
                '"ticks": 182', '"ticks": NaN', 1
            )
            run_path.write_text(raw, encoding="utf-8")
            completed = _run(workspace, "replay_simulation.py")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("INVALID_ARTIFACT", completed.stdout)
            self.assertNotIn("Traceback", completed.stdout + completed.stderr)

    def test_config_ticks_refuse_string_coercion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            write_json_atomic(workspace / "simulation-config.json", {"ticks": "182"})
            completed = _run(workspace, "run_simulation.py")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("non-negative integer", completed.stdout)
            self.assertNotIn("Traceback", completed.stdout + completed.stderr)


class NumericalArtifactTransactionTests(unittest.TestCase):
    def test_pair_commit_rolls_back_when_second_stage_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.json"
            run = root / "run.json"
            model.write_text('{"old":"model"}\n', encoding="utf-8")
            run.write_text('{"old":"run"}\n', encoding="utf-8")
            before_model = model.read_bytes()
            before_run = run.read_bytes()
            real_write = write_json_atomic
            calls = 0

            def fail_second(path: Path, payload: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                real_write(path, payload)

            with patch("run_simulation.write_json_atomic", side_effect=fail_second):
                with self.assertRaises(OSError):
                    _commit_json_pair([(model, {"new": "model"}), (run, {"new": "run"})])

            self.assertEqual(model.read_bytes(), before_model)
            self.assertEqual(run.read_bytes(), before_run)
            self.assertFalse(list(root.glob(".*.stage-*")))
            self.assertFalse(list(root.glob(".*.backup-*")))

    def test_pair_commit_restores_both_targets_when_second_promote_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.json"
            run = root / "run.json"
            model.write_text('{"old":"model"}\n', encoding="utf-8")
            run.write_text('{"old":"run"}\n', encoding="utf-8")
            before_model = model.read_bytes()
            before_run = run.read_bytes()
            import run_simulation

            real_replace = run_simulation.os.replace
            promote_calls = 0

            def fail_second_promote(source: Path | str, target: Path | str) -> None:
                nonlocal promote_calls
                source_path = Path(source)
                target_path = Path(target)
                if target_path in {model, run} and ".stage-" in source_path.name:
                    promote_calls += 1
                    if promote_calls == 2:
                        raise OSError("promote failed")
                real_replace(source, target)

            with patch("run_simulation.os.replace", side_effect=fail_second_promote):
                with self.assertRaises(OSError):
                    _commit_json_pair([(model, {"new": "model"}), (run, {"new": "run"})])

            self.assertEqual(model.read_bytes(), before_model)
            self.assertEqual(run.read_bytes(), before_run)


class ExtremeNumericalInputTests(unittest.TestCase):
    @staticmethod
    def _model() -> ComputationalModel:
        return ComputationalModel(
            variables={
                "factor:x": Variable(id="factor:x", role="exogenous", baseline=1.0),
                "factor:y": Variable(id="factor:y", role="endogenous", baseline=0.0),
            },
            edges=[
                ModelEdge(
                    id="edge:xy",
                    source="factor:x",
                    target="factor:y",
                    sign=1,
                    strength=1.0,
                    lag_ticks=1,
                    lag_unit="days",
                )
            ],
        )

    def test_tiny_timestep_returns_typed_numerical_failure(self) -> None:
        result = run_deterministic(
            self._model(), EngineConfig(timestep=5e-324), ticks=2, run_id=0
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 4)
        self.assertIn("RANGE", {value["code"] for value in result["issues"]})

    def test_binding_refuses_extreme_timestep_and_huge_numeric_row(self) -> None:
        model = self._model()
        normal_config = EngineConfig()
        result = run_deterministic(model, normal_config, ticks=2, run_id=0)
        row = {
            "step": 1,
            "time": "2026-01-02",
            "edge_id": "edge:xy",
            "sample_refs": ["run:0"],
            "run_id": 0,
            "tick": 1,
            "source_tick": 0,
            "source_state": 10**400,
            "target_state": 1.0,
            "sampled_strength": 1.0,
            "input_effect": 10**400,
            "noise": 0.0,
        }
        binding, issues = build_trace_execution_binding(
            [row],
            model,
            normal_config,
            ticks=2,
            result=result,
            manifest={"temporal_frame": {"simulation_start": "2026-01-01"}},
        )
        self.assertIsNone(binding)
        self.assertTrue(issues)

        binding, issues = build_trace_execution_binding(
            [{**row, "source_state": 1.0, "input_effect": 1.0}],
            model,
            EngineConfig(timestep=1e308),
            ticks=2,
            result=result,
            manifest={"temporal_frame": {"simulation_start": "2026-01-01"}},
        )
        self.assertIsNone(binding)
        self.assertTrue(issues)


class EngineDerivedBranchBindingTests(unittest.TestCase):
    def test_analyst_branch_cannot_claim_engine_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            branches_path = workspace / "branch-ledger.json"
            branches = json.loads(branches_path.read_text(encoding="utf-8"))
            branches["branches"][0]["representative_run"] = "run:0"
            write_json_atomic(branches_path, branches)
            manifest = json.loads(
                (workspace / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertIn("TRACK_MISMATCH", {value.code for value in result.issues})

    def test_monte_carlo_engine_branches_bind_cluster_identity_and_weight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["simulation_mode"] = "monte_carlo"
            write_json_atomic(manifest_path, manifest)
            run_completed = _run(
                workspace,
                "run_simulation.py",
                "--mode",
                "monte_carlo",
                "--ticks",
                "182",
                "--runs",
                "1",
                "--max-runs",
                "1",
                "--batch-size",
                "1",
            )
            replay_completed = _run(workspace, "replay_simulation.py")
            self.assertEqual(run_completed.returncode, 0, run_completed.stdout)
            self.assertEqual(replay_completed.returncode, 0, replay_completed.stdout)

            run_contract = json.loads(
                (workspace / "simulation-run.json").read_text(encoding="utf-8")
            )
            ledger_path = workspace / "branch-ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            template = ledger["branches"][0]
            derived_branches = []
            for cluster in run_contract["result"]["summary"]["branches"]:
                branch = {
                    **template,
                    "id": cluster["id"],
                    "derivation": "engine_derived",
                    "engine_cluster_id": cluster["id"],
                    "member_count": cluster["member_count"],
                    "representative_run": cluster["representative_run"],
                    "relative_weight": cluster["relative_weight"],
                    "trace_hash": run_contract["trace_contract"]["sha256"],
                }
                derived_branches.append(branch)
            ledger["branches"] = derived_branches
            write_json_atomic(ledger_path, ledger)

            validated = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(
                validated.status,
                "pass",
                [value.to_dict() for value in validated.issues],
            )

            ledger["branches"][0]["relative_weight"] += 0.1
            write_json_atomic(ledger_path, ledger)
            tampered = validate_numerical_artifacts(workspace, manifest)
            self.assertIn("REPLAY_MISMATCH", {value.code for value in tampered.issues})


class NumericalTraceSchemaParityTests(unittest.TestCase):
    def test_numerical_schema_matches_runtime_run_ref_and_noise_contract(self) -> None:
        numerical_schema = json.loads(
            (ROOT / "schemas" / "numerical-propagation-trace-row.schema.json").read_text(
                encoding="utf-8"
            )
        )
        constraints = numerical_schema["allOf"][1]
        self.assertEqual(constraints["properties"]["noise"]["const"], 0)
        sample_refs = constraints["properties"]["sample_refs"]
        self.assertEqual(sample_refs["minItems"], 1)
        self.assertEqual(sample_refs["maxItems"], 1)
        self.assertEqual(sample_refs["items"]["pattern"], "^run:[0-9]{1,20}$")
        self.assertEqual(
            set(constraints["required"]),
            {"run_id", "tick", "source_tick", "source_state", "target_state", "sampled_strength"},
        )
        row = json.loads((FIXTURE / "propagation-trace.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["noise"], 0)
        self.assertRegex(row["sample_refs"][0], r"^run:[0-9]{1,20}$")
        self.assertTrue(set(constraints["required"]) <= set(row))


if __name__ == "__main__":
    unittest.main()
