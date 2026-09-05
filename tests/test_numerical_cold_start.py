from __future__ import annotations

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

import jsonschema  # noqa: E402
from aleph.io import canonical_hash, write_json_atomic  # noqa: E402
from aleph.trace_contract import validate_execution_trace_data  # noqa: E402

CONTRACTS_DIR = ROOT.parents[1] / "audit-artifacts" / "contracts"
EXECUTION_TRACE_SCHEMA_PATH = CONTRACTS_DIR / "execution-trace.schema.json"


class NumericalColdStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_t01_cold_start_trace_generation(self) -> None:
        """T01: Cold-start numerical workspace containing only model definition without any pre-authored trace files (F07).

        Engine executes simulation, automatically generates execution-trace.json conforming to schema,
        and passes replay verification.
        """
        with tempfile.TemporaryDirectory() as temporary_dir:
            workspace = Path(temporary_dir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            manifest = {
                "schema_version": "2.0.0",
                "manifest_version": "2.1.0",
                "simulation_mode": "deterministic",
                "formula_version": "2.1.0",
                "temporal_frame": {
                    "simulation_start": "2026-01-01T00:00:00Z",
                    "timestep": "1d",
                    "horizon_ticks": 5,
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
                {
                    "id": "node:signal_a",
                    "name": "Signal A",
                    "category": "driver",
                    "scale": "level",
                    "domain": [-100.0, 100.0],
                    "initial_value": 10.0,
                },
                {
                    "id": "node:response_b",
                    "name": "Response B",
                    "category": "state",
                    "scale": "level",
                    "domain": [-100.0, 100.0],
                    "initial_value": 0.0,
                },
                {
                    "id": "node:outcome_c",
                    "name": "Outcome C",
                    "category": "outcome",
                    "scale": "level",
                    "domain": [-100.0, 100.0],
                    "initial_value": 0.0,
                },
            ]
            write_json_atomic(workspace / "nodes.json", nodes)

            edges = [
                {
                    "id": "causal:a_to_b",
                    "source": "node:signal_a",
                    "target": "node:response_b",
                    "sign": 1,
                    "strength": 0.5,
                    "lag_ticks": 0,
                    "transform": "linear",
                    "transform_parameters": {},
                },
                {
                    "id": "causal:b_to_c",
                    "source": "node:response_b",
                    "target": "node:outcome_c",
                    "sign": 1,
                    "strength": 0.4,
                    "lag_ticks": 0,
                    "transform": "linear",
                    "transform_parameters": {},
                },
            ]
            write_json_atomic(workspace / "edges.json", edges)

            # Confirm no trace files exist before running (cold-start)
            self.assertFalse((workspace / "execution-trace.json").exists())
            self.assertFalse((workspace / "propagation-trace.jsonl").exists())

            # 1. Execute run_simulation.py
            cmd_run = [
                sys.executable,
                str(SCRIPTS / "run_simulation.py"),
                "--workspace",
                str(workspace),
                "--ticks",
                "5",
            ]
            proc_run = subprocess.run(cmd_run, capture_output=True, text=True)
            self.assertEqual(proc_run.returncode, 0, f"run_simulation stdout: {proc_run.stdout}\nstderr: {proc_run.stderr}")

            # 2. Check that execution-trace.json was generated
            trace_path = workspace / "execution-trace.json"
            self.assertTrue(trace_path.is_file(), "execution-trace.json was not generated")
            trace_data = json.loads(trace_path.read_text(encoding="utf-8"))

            # 3. Validate against schema
            jsonschema.validate(instance=trace_data, schema=self.schema)

            # 4. Check trace contract rules
            self.assertEqual(trace_data.get("generation_mode"), "engine_derived")
            self.assertEqual(trace_data.get("formula_version"), "2.1.0")
            self.assertEqual(trace_data.get("schema_version"), "2.1.0")
            self.assertTrue(len(trace_data.get("steps", [])) > 0)

            node_ids = {n["id"] for n in nodes}
            edge_by_id = {e["id"]: e for e in edges}
            semantic_issues = validate_execution_trace_data(
                trace_data,
                node_ids=node_ids,
                edge_by_id=edge_by_id,
                manifest=manifest,
            )
            self.assertEqual(len(semantic_issues), 0, [i.to_dict() for i in semantic_issues])

            # 5. Execute replay_simulation.py
            cmd_replay = [
                sys.executable,
                str(SCRIPTS / "replay_simulation.py"),
                "--workspace",
                str(workspace),
            ]
            proc_replay = subprocess.run(cmd_replay, capture_output=True, text=True)
            self.assertEqual(proc_replay.returncode, 0, f"replay_simulation stdout: {proc_replay.stdout}\nstderr: {proc_replay.stderr}")

            report_path = workspace / "replay-report.json"
            self.assertTrue(report_path.is_file())
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report_data.get("match"), f"Replay did not match: {report_data}")
            self.assertTrue(report_data.get("trace_ok"))
            self.assertTrue(report_data.get("trace_execution_binding_ok"))


if __name__ == "__main__":
    unittest.main()
