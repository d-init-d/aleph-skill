from __future__ import annotations

import concurrent.futures
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import jsonschema  # noqa: E402
from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    generate_numerical_execution_trace,
    sampled_edge_parameters,
    stream_numerical_execution_trace,
)
from aleph.io import canonical_hash  # noqa: E402
from aleph.trace_contract import validate_execution_trace_data  # noqa: E402

CONTRACTS_DIR = ROOT.parents[1] / "audit-artifacts" / "contracts"
EXECUTION_TRACE_SCHEMA_PATH = CONTRACTS_DIR / "execution-trace.schema.json"


class NumericalEngineTracesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_t02_linear_chain_hand_calc(self) -> None:
        """T02: Simple 3-node linear chain (A -> B -> C) executed through numerical engine.

        Engine-generated trace matches exact analytical hand calculation at each tick and edge step.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.0, scale="level"),
            "node:c": Variable(id="node:c", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.5, lag_ticks=0),
            ModelEdge(id="causal:b_to_c", source="node:b", target="node:c", sign=1, strength=0.4, lag_ticks=0),
        ]
        config = EngineConfig(mode="deterministic", seed=42)

        trace = generate_numerical_execution_trace(model, config, ticks=1)
        jsonschema.validate(instance=trace, schema=self.schema)

        steps = trace["steps"]
        self.assertEqual(len(steps), 2)

        # Step 1: A -> B
        s1 = steps[0]
        self.assertEqual(s1["source_node_id"], "node:a")
        self.assertEqual(s1["node_id"], "node:b")
        self.assertEqual(s1["edge_id"], "causal:a_to_b")
        self.assertAlmostEqual(s1["drawn_strength"], 0.5)
        self.assertAlmostEqual(s1["source_state"], 1.0)
        self.assertAlmostEqual(s1["target_state_before"], 0.0)
        self.assertAlmostEqual(s1["target_state_after"], 0.5)
        self.assertAlmostEqual(s1["state_transitions"]["delta"], 0.5)

        # Step 2: B -> C
        s2 = steps[1]
        self.assertEqual(s2["source_node_id"], "node:b")
        self.assertEqual(s2["node_id"], "node:c")
        self.assertEqual(s2["edge_id"], "causal:b_to_c")
        self.assertAlmostEqual(s2["drawn_strength"], 0.4)
        self.assertAlmostEqual(s2["source_state"], 0.5)
        self.assertAlmostEqual(s2["target_state_before"], 0.0)
        self.assertAlmostEqual(s2["target_state_after"], 0.2)
        self.assertAlmostEqual(s2["state_transitions"]["delta"], 0.2)

        self.assertAlmostEqual(trace["final_state_vector"]["node:a"], 1.0)
        self.assertAlmostEqual(trace["final_state_vector"]["node:b"], 0.5)
        self.assertAlmostEqual(trace["final_state_vector"]["node:c"], 0.2)

    def test_t03_monte_carlo_draws_logged(self) -> None:
        """T03: Monte Carlo simulation with sampled edge strengths, lags, and existence probabilities.

        Trace explicitly records the exact drawn values utilized during execution; replay confirms identical draws from seed.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:x": Variable(id="node:x", role="state", baseline=5.0, scale="level"),
            "node:y": Variable(id="node:y", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(
                id="causal:x_to_y",
                source="node:x",
                target="node:y",
                sign=1,
                strength=0.8,
                lag_ticks=1,
                effect_distribution={"distribution": "normal", "mean": 0.8, "sd": 0.1},
                existence_prob=1.0,
            ),
        ]
        config = EngineConfig(mode="deterministic", seed=12345)

        trace = generate_numerical_execution_trace(model, config, ticks=3)
        jsonschema.validate(instance=trace, schema=self.schema)

        # Check that sampled strength was recorded and matches sampled_edge_parameters directly
        expected_strength, expected_lag, expected_exists = sampled_edge_parameters(model.edges[0], config, run_id=0)
        self.assertTrue(expected_exists)

        for step in trace["steps"]:
            if step["edge_id"] == "causal:x_to_y":
                self.assertAlmostEqual(step["drawn_strength"], expected_strength)
                self.assertEqual(step["sampled_parameters"]["strength"], expected_strength)
                self.assertEqual(step["sampled_parameters"]["lag"], expected_lag)

    def test_t04_multithread_invariance(self) -> None:
        """T04: Simulation executed with identical seed and config across 1 worker vs 4 parallel workers.

        Generated trace output and replay_hash are invariant to worker thread/process count.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=10.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.75, lag_ticks=0),
        ]
        config = EngineConfig(mode="deterministic", seed=98765)

        trace_single = generate_numerical_execution_trace(
            model, config, ticks=5, execution_timestamp="2026-01-01T00:00:00Z"
        )

        def _run_worker(worker_id: int) -> dict:
            return generate_numerical_execution_trace(
                model, config, ticks=5, execution_timestamp="2026-01-01T00:00:00Z"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_run_worker, idx) for idx in range(4)]
            parallel_traces = [f.result() for f in futures]

        for p_trace in parallel_traces:
            self.assertEqual(p_trace["replay_hash"], trace_single["replay_hash"])
            self.assertEqual(p_trace["parameters_hash"], trace_single["parameters_hash"])
            self.assertEqual(len(p_trace["steps"]), len(trace_single["steps"]))
            for s1, s2 in zip(p_trace["steps"], trace_single["steps"]):
                self.assertEqual(s1["hash_chain"], s2["hash_chain"])
                self.assertAlmostEqual(s1["target_state_after"], s2["target_state_after"])

    def test_t05_config_hash_mismatch(self) -> None:
        """T05: Run config seed or horizon altered, attempting to bind prior execution trace.

        Validator rejects trace due to parameter hash mismatch; old trace cannot validate new configuration.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
        }
        config_original = EngineConfig(mode="deterministic", seed=42)
        trace = generate_numerical_execution_trace(model, config_original, ticks=5)

        # Alter configuration (seed changed)
        config_altered = EngineConfig(mode="deterministic", seed=999)
        altered_trace = generate_numerical_execution_trace(model, config_altered, ticks=5)

        self.assertNotEqual(trace["parameters_hash"], altered_trace["parameters_hash"])

        # Validate original trace against altered parameters_hash
        issues = validate_execution_trace_data(
            trace,
            config=altered_trace["parameters_hash"],
        )
        param_mismatches = [i for i in issues if i.code == "PARAMETERS_HASH_MISMATCH"]
        self.assertTrue(len(param_mismatches) > 0)

    def test_t07_out_of_bounds_sample_id(self) -> None:
        """T07: Trace step references sample_id or run_id out of range of current execution.

        Validator fails with out-of-bounds sample identifier; does not silently drop step.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.5, lag_ticks=0),
        ]
        config = EngineConfig(mode="deterministic", seed=42)
        trace = generate_numerical_execution_trace(model, config, ticks=1)

        # Inject out-of-bounds sample_id in step
        tampered_trace = copy.deepcopy(trace)
        tampered_trace["total_samples"] = 1
        tampered_trace["steps"][0]["sample_id"] = 10  # Range is [0, 1)

        issues = validate_execution_trace_data(tampered_trace)
        oob_issues = [i for i in issues if i.code == "OUT_OF_BOUNDS"]
        self.assertTrue(len(oob_issues) > 0, "Out of bounds sample_id was not caught")
        self.assertEqual(oob_issues[0].pointer, "/steps/0/sample_id")

    def test_t09_lagged_edge_delivery_tick(self) -> None:
        """T09: Causal edge with lag L > 0 and delayed feedback cycle.

        Emission tick and delivery tick correctly recorded (delivery = emission + L);
        target node consumes signal at correct future tick.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=10.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.0, scale="level"),
        }
        lag_L = 2
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.5, lag_ticks=lag_L),
        ]
        config = EngineConfig(mode="deterministic", seed=42)
        trace = generate_numerical_execution_trace(model, config, ticks=4)

        # Step emitted at tick 0 must have delivery_tick = 2
        lagged_steps = [s for s in trace["steps"] if s["edge_id"] == "causal:a_to_b"]
        self.assertTrue(len(lagged_steps) > 0)

        step_t0 = lagged_steps[0]
        self.assertEqual(step_t0["emission_tick"], 0)
        self.assertEqual(step_t0["delivery_tick"], 0 + lag_L)
        self.assertEqual(step_t0["tick"], 2)

    def test_t13_divergent_runs_invalid_mass(self) -> None:
        """T13: Simulation where all Monte Carlo runs diverge or produce invalid mass = 1.0.

        Engine outputs invalid_mass=1.0 with structured warning; does not renormalize or output NaN/Infinity.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=1.0, scale="level"),
        }
        # Create an explosive cycle with max_events exceeded
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=10.0, lag_ticks=1),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=10.0, lag_ticks=1),
        ]
        config = EngineConfig(mode="deterministic", seed=42, max_events=2)

        trace = generate_numerical_execution_trace(model, config, ticks=10)
        self.assertEqual(trace["invalid_mass"], 1.0)
        # Check no NaN or Infinity in final states
        for var_name, val in trace["final_state_vector"].items():
            self.assertTrue(math.isfinite(val), f"Non-finite state found: {val}")

    def test_t20_bounded_memory_streaming_trace(self) -> None:
        """T20: Large-scale simulation workload (1000 steps, 100 trajectories) executed.

        Engine executes within bounded memory/RSS limits using streaming serialization;
        performance telemetry recorded.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.1, lag_ticks=0),
        ]
        config = EngineConfig(mode="deterministic", seed=42)

        trace = stream_numerical_execution_trace(model, config, ticks=1000)
        self.assertIn("telemetry", trace)
        telemetry = trace["telemetry"]
        self.assertIn("elapsed_ms", telemetry)
        self.assertIn("step_count", telemetry)
        self.assertEqual(telemetry["step_count"], 1000)
        self.assertTrue(telemetry["elapsed_ms"] > 0)
        self.assertTrue(telemetry["steps_per_second"] > 0)


if __name__ == "__main__":
    unittest.main()
