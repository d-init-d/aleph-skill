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
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    compile_model,
    run_deterministic,
    run_monte_carlo,
)
from aleph.packs import evaluate_hindcast_case, evidence_snapshot_hash  # noqa: E402
from aleph.sensitivity import one_at_a_time  # noqa: E402


class FinalEngineHardeningTests(unittest.TestCase):
    @staticmethod
    def _two_node_model(*, lag_distribution: dict | None = None) -> ComputationalModel:
        nodes = [
            {"id": "x", "role": "exogenous", "baseline": 1.0},
            {"id": "y", "role": "endogenous", "baseline": 0.0},
        ]
        edge = {
            "id": "xy",
            "from": "x",
            "to": "y",
            "sign": 1,
            "base_strength": 1.0,
            "transform": "linear",
        }
        if lag_distribution is not None:
            edge["lag_distribution"] = lag_distribution
        return compile_model(nodes, [edge])

    def test_scc_convergence_uses_fixed_point_residual(self) -> None:
        model = ComputationalModel(
            variables={"x": Variable(id="x", role="endogenous", baseline=1.0)},
            edges=[ModelEdge(id="xx", source="x", target="x", sign=1, strength=2.0)],
        )
        result = run_deterministic(
            model,
            EngineConfig(jacobi_relax=1e-12, jacobi_max_iter=10),
            ticks=1,
        )
        self.assertFalse(result["ok"])
        self.assertIn("NONCONVERGENCE", {item["code"] for item in result["issues"]})

    def test_fixed_day_lag_uses_timestep_in_mc_and_deterministic_modes(self) -> None:
        model = self._two_node_model(lag_distribution={"type": "fixed", "fixed": "P14D"})
        deterministic = run_deterministic(
            model,
            EngineConfig(mode="deterministic", timestep=7.0),
            ticks=3,
        )
        mc_sample = run_deterministic(
            model,
            EngineConfig(mode="monte_carlo", timestep=7.0, min_runs=1, max_runs=1),
            ticks=3,
        )
        self.assertEqual(deterministic["history"], mc_sample["history"])
        self.assertEqual(mc_sample["history"][2]["y"], 1.0)

    def test_invalid_effect_distributions_fail_compilation(self) -> None:
        nodes = [
            {"id": "x", "role": "exogenous", "baseline": 1.0},
            {"id": "y", "role": "endogenous", "baseline": 0.0},
        ]
        invalid = [
            {"distribution": "uniform", "min": 2.0, "max": 1.0},
            {"distribution": "normal", "mean": 1.0, "sd": -5.0},
            {"distribution": "triangular", "min": 2.0, "mode": 0.0, "max": 1.0},
            {"distribution": "unknown", "value": 1.0},
            {"distribution": "fixed", "value": float("nan")},
        ]
        for distribution in invalid:
            edge = {
                "id": "xy",
                "from": "x",
                "to": "y",
                "sign": 1,
                "base_strength": 1.0,
                "transform": "linear",
                "effect_distribution": distribution,
            }
            with self.subTest(distribution=distribution), self.assertRaises(ValueError):
                compile_model(nodes, [edge])

    def test_invalid_mc_config_has_hash_and_cli_exits_structurally(self) -> None:
        model = self._two_node_model()
        result = run_monte_carlo(
            model,
            EngineConfig(mode="monte_carlo", min_runs=10, max_runs=5),
            ticks=1,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["summary"]["canonical_hash"]), 64)
        non_finite = run_monte_carlo(
            model,
            EngineConfig(mode="monte_carlo", timestep=float("nan"), min_runs=1, max_runs=1),
            ticks=1,
        )
        self.assertFalse(non_finite["ok"])
        self.assertEqual(len(non_finite["summary"]["canonical_hash"]), 64)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["simulation_mode"] = "monte_carlo"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_simulation.py"),
                    "--workspace",
                    str(workspace),
                    "--mode",
                    "monte_carlo",
                    "--runs",
                    "10",
                    "--max-runs",
                    "5",
                    "--ticks",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 4, completed.stderr + completed.stdout)
            self.assertNotIn("Traceback", completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(len(payload["summary"]["canonical_hash"]), 64)

    def test_replay_binds_nonempty_trace_digest_and_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_simulation.py"), "--workspace", str(workspace), "--ticks", "182"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            contract = json.loads((workspace / "simulation-run.json").read_text(encoding="utf-8"))
            self.assertGreater(contract["trace_contract"]["row_count"], 0)
            baseline = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(baseline.returncode, 0, baseline.stderr + baseline.stdout)
            (workspace / "propagation-trace.jsonl").write_text("", encoding="utf-8")
            tampered = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(tampered.returncode, 0)
            report = json.loads(tampered.stdout)
            self.assertFalse(report["trace_contract_ok"])
            self.assertFalse(report["trace_ok"])
            self.assertFalse(report["match"])

    def test_run_and_replay_reject_invalid_trace_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            trace_path = workspace / "propagation-trace.jsonl"
            row = json.loads(trace_path.read_text(encoding="utf-8"))
            row["hash_chain"] = "0" * 64
            trace_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            refused = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_simulation.py"), "--workspace", str(workspace), "--ticks", "182"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("REPLAY_MISMATCH", refused.stdout)

    def test_run_and_replay_use_manifest_declared_trace_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            custom_trace = workspace / "traces" / "custom-propagation.jsonl"
            custom_trace.parent.mkdir()
            (workspace / "propagation-trace.jsonl").replace(custom_trace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifact_paths"]["propagation_trace"] = "traces/custom-propagation.jsonl"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_simulation.py"), "--workspace", str(workspace), "--ticks", "182"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            contract = json.loads((workspace / "simulation-run.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["trace_contract"]["path"], "traces/custom-propagation.jsonl")

            replay = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            report = json.loads(replay.stdout)
            self.assertTrue(report["trace_contract_ok"])
            self.assertTrue(report["trace_ok"])
            self.assertTrue(report["match"])


class FinalHindcastSensitivityHardeningTests(unittest.TestCase):
    def test_mixed_timezone_forms_are_normalized_to_utc(self) -> None:
        case_path = ROOT / "packs" / "economics" / "hindcast" / "case-001.json"
        case = json.loads(case_path.read_text(encoding="utf-8"))
        case["evidence"][0]["available_at"] = "2019-01-01T00:00:00Z"
        case["evidence_snapshot_hash"] = evidence_snapshot_hash(case["evidence"])
        result = evaluate_hindcast_case(
            case,
            policy={"precommitted": True, "commitment_version": "aleph-hindcast-commitment-v3"},
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["policy_locked"])

    def test_hindcast_snapshot_and_policy_commitment_detect_tampering(self) -> None:
        pack = ROOT / "packs" / "economics"
        case = json.loads((pack / "hindcast" / "case-001.json").read_text(encoding="utf-8"))
        policy = json.loads((pack / "calibration-policy.json").read_text(encoding="utf-8"))
        valid = evaluate_hindcast_case(case, policy=policy)
        self.assertTrue(valid["ok"], valid)
        self.assertTrue(valid["policy_locked"])

        evidence_tamper = copy.deepcopy(case)
        evidence_tamper["evidence"][0]["id"] = "evidence:tampered"
        stale = evaluate_hindcast_case(evidence_tamper, policy=policy)
        self.assertFalse(stale["ok"])
        self.assertIn("STALE_ARTIFACT", {item["code"] for item in stale["issues"]})

        model_tamper = copy.deepcopy(case)
        model_tamper["model"]["edges"][0]["base_strength"] = 0.75
        commitment_mismatch = evaluate_hindcast_case(model_tamper, policy=policy)
        self.assertFalse(commitment_mismatch["ok"])
        self.assertIn("STALE_ARTIFACT", {item["code"] for item in commitment_mismatch["issues"]})

        formula_tamper = copy.deepcopy(case)
        formula_tamper["formula_version"] = "2.0.0"
        formula_mismatch = evaluate_hindcast_case(formula_tamper, policy=policy)
        self.assertFalse(formula_mismatch["ok"])
        self.assertIn("STALE_ARTIFACT", {item["code"] for item in formula_mismatch["issues"]})

        observation_tamper = copy.deepcopy(case)
        target = next(iter(observation_tamper["observations"]))
        observation_tamper["observations"][target] = 0.5
        observation_mismatch = evaluate_hindcast_case(observation_tamper, policy=policy)
        self.assertFalse(observation_mismatch["ok"])
        self.assertIn("STALE_ARTIFACT", {item["code"] for item in observation_mismatch["issues"]})

    def test_oat_clamps_to_bounds_and_reports_actual_deltas(self) -> None:
        seen: list[float] = []

        def evaluate(values: dict[str, float]) -> float:
            seen.append(values["x"])
            return values["x"]

        result = one_at_a_time(
            {"x": 0.0},
            evaluate,
            delta=0.1,
            bounds={"x": (0.0, 1.0)},
        )
        self.assertTrue(all(0.0 <= value <= 1.0 for value in seen))
        effect = result["effects"]["x"]
        self.assertEqual(effect["down_value"], 0.0)
        self.assertEqual(effect["down_delta"], 0.0)
        self.assertEqual(effect["up_delta"], 0.1)


if __name__ == "__main__":
    unittest.main()
