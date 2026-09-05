from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.engine import (  # noqa: E402
    EngineConfig,
    ModelEdge,
    _edge_effect,
    compile_model,
    run_deterministic,
)
from aleph.io import canonical_hash, canonical_json_bytes, write_json_atomic  # noqa: E402
from aleph.validator import validate_numerical_artifacts, validate_workspace  # noqa: E402


def _recalculate_trace_hashes(trace_data: dict) -> dict:
    """Recalculate hash_chain and replay_hash for a modified trace."""
    steps = trace_data.get("steps", [])
    prev_hash = "0" * 64
    for step in steps:
        step_data = {k: v for k, v in step.items() if k != "hash_chain"}
        step_bytes = canonical_json_bytes(step_data)
        hasher = hashlib.sha256()
        hasher.update(prev_hash.encode("utf-8") + b":" + step_bytes)
        current_hash = hasher.hexdigest()
        step["hash_chain"] = current_hash
        prev_hash = current_hash
    trace_data["replay_hash"] = prev_hash
    return trace_data


def _setup_base_simulation_workspace(workspace: Path) -> None:
    manifest = {
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
            "computational_model": "simulation-model.json",
            "replay_report": "replay-report.json",
            "branch_ledger": "branches.json",
        },
        "seed": 42,
    }
    write_json_atomic(workspace / "simulation-manifest.json", manifest)

    nodes = [
        {"id": "node:a", "name": "A", "category": "driver", "scale": "level", "domain": [-10.0, 10.0], "initial_value": 2.0},
        {"id": "node:b", "name": "B", "category": "state", "scale": "level", "domain": [-10.0, 10.0], "initial_value": 0.0},
    ]
    write_json_atomic(workspace / "nodes.json", nodes)

    edges = [
        {"id": "causal:a_to_b", "source": "node:a", "target": "node:b", "sign": 1, "strength": 0.5, "lag_ticks": 0, "transform": "linear", "transform_parameters": {}},
    ]
    write_json_atomic(workspace / "edges.json", edges)

    # Run initial simulation
    proc_run = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_simulation.py"), "--workspace", str(workspace), "--ticks", "3"],
        capture_output=True,
        text=True,
    )
    if proc_run.returncode != 0:
        raise RuntimeError(f"run_simulation failed: {proc_run.stdout}\n{proc_run.stderr}")

    proc_replay = subprocess.run(
        [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )
    if proc_replay.returncode != 0:
        raise RuntimeError(f"replay_simulation failed: {proc_replay.stdout}\n{proc_replay.stderr}")


class DeterministicReplayAcceptanceTests(unittest.TestCase):
    def test_t06_tampered_trace_replay_detection(self) -> None:
        """T06: Trace steps deleted, re-ordered, or modified with recalculated hashes.

        Replay validator re-computes propagation step-by-step; flags mathematical divergence from state transitions.
        """
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            _setup_base_simulation_workspace(workspace)

            trace_file = workspace / "execution-trace.json"
            trace_data = json.loads(trace_file.read_text(encoding="utf-8"))

            # Tamper 1: Delete a step and recalculate internal hashes
            tampered = copy.deepcopy(trace_data)
            self.assertTrue(len(tampered["steps"]) >= 2)
            tampered["steps"].pop(0)
            _recalculate_trace_hashes(tampered)
            write_json_atomic(trace_file, tampered)

            # Update simulation-run.json trace_contract digest so file hash passes, forcing replay calculation check
            run_file = workspace / "simulation-run.json"
            run_data = json.loads(run_file.read_text(encoding="utf-8"))
            trace_bytes = json.dumps(tampered, indent=2, ensure_ascii=False, sort_keys=False, allow_nan=False).encode("utf-8") + b"\n"
            run_data["trace_contract"]["sha256"] = hashlib.sha256(trace_bytes).hexdigest()
            run_data["trace_contract"]["row_count"] = len(tampered["steps"])
            run_data["contract_hash"] = canonical_hash({k: v for k, v in run_data.items() if k != "contract_hash"})
            write_json_atomic(run_file, run_data)

            # Replay must catch deleted step divergence
            proc_replay = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc_replay.returncode, 0)
            report = json.loads((workspace / "replay-report.json").read_text(encoding="utf-8"))
            self.assertFalse(report.get("match"))
            issues = report.get("issues", [])
            replay_mismatches = [i for i in issues if i.get("code") == "REPLAY_MISMATCH"]
            self.assertTrue(len(replay_mismatches) > 0)

            # Tamper 2: Modify mathematical state transition and recalculate hashes
            tampered2 = copy.deepcopy(trace_data)
            tampered2["steps"][0]["target_state_after"] = 999.0
            tampered2["steps"][0]["state_transitions"]["delta"] = 999.0
            _recalculate_trace_hashes(tampered2)
            write_json_atomic(trace_file, tampered2)

            trace_bytes2 = json.dumps(tampered2, indent=2, ensure_ascii=False, sort_keys=False, allow_nan=False).encode("utf-8") + b"\n"
            run_data["trace_contract"]["sha256"] = hashlib.sha256(trace_bytes2).hexdigest()
            run_data["trace_contract"]["row_count"] = len(tampered2["steps"])
            run_data["contract_hash"] = canonical_hash({k: v for k, v in run_data.items() if k != "contract_hash"})
            write_json_atomic(run_file, run_data)

            proc_replay2 = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc_replay2.returncode, 0)
            report2 = json.loads((workspace / "replay-report.json").read_text(encoding="utf-8"))
            self.assertFalse(report2.get("match"))
            mismatches2 = [i for i in report2.get("issues", []) if i.get("code") == "REPLAY_MISMATCH"]
            self.assertTrue(len(mismatches2) > 0)

    def test_t19_narrative_vs_trace_divergence(self) -> None:
        """T19: Analyst manually modifies narrative branch probabilities after numerical simulation run.

        Trace binding verification detects mismatch between engine trace and narrative branch values; analyst-authored flagged.
        """
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            _setup_base_simulation_workspace(workspace)

            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            trace_file = workspace / "execution-trace.json"
            trace_hash = hashlib.sha256(trace_file.read_bytes()).hexdigest()

            # Case A: Analyst modifies relative_weight to 0.7 instead of 1.0 on an engine_derived branch
            tampered_branches = {
                "schema_version": "2.0.0",
                "likelihood_mode": "relative_weight",
                "branches": [
                    {
                        "id": "branch:b1",
                        "name": "Branch 1",
                        "derivation": "engine_derived",
                        "relative_weight": 0.7,  # Altered from engine 1.0
                        "representative_run": "run:0",
                        "trace_hash": trace_hash,
                    }
                ],
            }
            write_json_atomic(workspace / "branches.json", tampered_branches)

            res = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(res.status, "fail")
            errs = [e for e in res.issues if "deterministic engine branch must bind run:0 with weight 1.0" in str(e.message)]
            self.assertTrue(len(errs) > 0, f"Expected weight mismatch error, got: {[i.to_dict() for i in res.issues]}")

            # Case B: Analyst marks derivation as analyst_authored but retains engine-derived fields (representative_run)
            analyst_authored_branches = {
                "schema_version": "2.0.0",
                "likelihood_mode": "relative_weight",
                "branches": [
                    {
                        "id": "branch:b1",
                        "name": "Branch 1",
                        "derivation": "analyst_authored",
                        "relative_weight": 1.0,
                        "representative_run": "run:0",  # Illegal for analyst_authored
                        "trace_hash": trace_hash,
                    }
                ],
            }
            write_json_atomic(workspace / "branches.json", analyst_authored_branches)
            res_b = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(res_b.status, "fail")
            track_errs = [e for e in res_b.issues if "analyst-authored branches cannot claim engine-derived metadata" in str(e.message)]
            self.assertTrue(len(track_errs) > 0, f"Expected TRACK_MISMATCH error, got: {[i.to_dict() for i in res_b.issues]}")

    def test_t21_engine_mutation_replay_catch(self) -> None:
        """T21: Intentional mathematical mutation injected into production engine propagation code during test.

        Independent replay test catches calculation mismatch; confirms test does not use mutated helper as oracle.
        """
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            _setup_base_simulation_workspace(workspace)

            # Ground truth hand-calc oracle for A=2.0, strength=0.5 -> B=1.0
            expected_golden_delta = 2.0 * 0.5  # 1.0

            # Inject intentional mathematical mutation into engine propagation helper
            import aleph.engine as engine_module

            original_edge_effect = engine_module._edge_effect
            try:
                def mutated_edge_effect(edge, strength, source_value, *args, **kwargs):
                    # Injected mutation: double the output
                    raw_out, gate = original_edge_effect(edge, strength, source_value, *args, **kwargs)
                    return raw_out * 2.0, gate

                engine_module._edge_effect = mutated_edge_effect

                nodes = json.loads((workspace / "nodes.json").read_text(encoding="utf-8"))
                edges = json.loads((workspace / "edges.json").read_text(encoding="utf-8"))
                model = compile_model(nodes, edges, [], formula_version="2.1.0")
                test_edge = model.edges[0]
                mutated_out, _ = engine_module._edge_effect(
                    test_edge, 0.5, 2.0, formula_version="2.1.0"
                )
                self.assertNotEqual(mutated_out, expected_golden_delta)
                self.assertEqual(mutated_out, expected_golden_delta * 2.0)

                # Running replay with mutated code detects divergence
                config = EngineConfig(mode="deterministic", seed=42)
                mutated_replay = run_deterministic(model, config, ticks=3, run_id=0)

                # The recorded run has run_hash from unmutated execution
                recorded_run = json.loads((workspace / "simulation-run.json").read_text(encoding="utf-8"))
                self.assertNotEqual(mutated_replay["run_hash"], recorded_run["result_hash"])
            finally:
                engine_module._edge_effect = original_edge_effect


if __name__ == "__main__":
    unittest.main()
