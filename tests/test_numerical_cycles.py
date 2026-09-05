from __future__ import annotations

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
    run_deterministic,
)

SCHEMAS_DIR = ROOT / "schemas"
EXECUTION_TRACE_SCHEMA_PATH = SCHEMAS_DIR / "execution-trace.schema.json"


class NumericalCyclesAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_t08_zero_lag_cycle_convergence(self) -> None:
        """T08: Zero-lag feedback cycle with converging vs non-converging dynamics.

        Converging cycle resolves within residual tolerance; non-converging cycle is logged under invalid_mass without corrupting state vector.
        """
        # Part A: Converging zero-lag cycle (spectral radius < 1)
        # Node A: baseline 1.0, Node B: baseline 0.5
        # A -> B: 0.2, B -> A: 0.2
        converging_model = ComputationalModel(formula_version="2.1.0")
        converging_model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.5, scale="level"),
        }
        converging_model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.2, lag_ticks=0),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=0.2, lag_ticks=0),
        ]
        config_conv = EngineConfig(mode="deterministic", seed=42, jacobi_max_iter=100)
        trace_conv = generate_numerical_execution_trace(converging_model, config_conv, ticks=3)
        jsonschema.validate(instance=trace_conv, schema=self.schema)

        self.assertEqual(trace_conv["invalid_mass"], 0.0)
        final_state = trace_conv.get("final_state_vector", {})
        self.assertTrue(math.isfinite(final_state.get("node:a", float("nan"))))
        self.assertTrue(math.isfinite(final_state.get("node:b", float("nan"))))

        # Part B: Divergent / non-converging zero-lag cycle (spectral radius > 1)
        # A -> B: 2.0, B -> A: 2.0
        divergent_model = ComputationalModel(formula_version="2.1.0")
        divergent_model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=1.0, scale="level"),
        }
        divergent_model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=2.0, lag_ticks=0),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=2.0, lag_ticks=0),
        ]
        config_div = EngineConfig(mode="deterministic", seed=42, jacobi_max_iter=20)
        trace_div = generate_numerical_execution_trace(divergent_model, config_div, ticks=3)
        jsonschema.validate(instance=trace_div, schema=self.schema)

        # Must report invalid_mass = 1.0 due to non-convergence
        self.assertEqual(trace_div["invalid_mass"], 1.0)
        # State vector must not contain NaN or Inf
        for node_id, val in trace_div.get("final_state_vector", {}).items():
            self.assertTrue(math.isfinite(val), f"Node {node_id} has non-finite state {val}")

        run_result = run_deterministic(divergent_model, config_div, ticks=3, run_id=0)
        self.assertFalse(run_result["ok"])
        self.assertEqual(run_result["exit_code"], 4)


if __name__ == "__main__":
    unittest.main()
