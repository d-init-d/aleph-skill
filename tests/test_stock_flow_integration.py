from __future__ import annotations

import json
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
)

SCHEMAS_DIR = ROOT / "schemas"
EXECUTION_TRACE_SCHEMA_PATH = SCHEMAS_DIR / "execution-trace.schema.json"


class StockFlowIntegrationAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_t10_stock_flow_differential_calc(self) -> None:
        """T10: Stock-flow node with inflow rates, outflow decay, and retention factor.

        Trace records stock_flow_integrations; numerical integration matches analytical differential equation within tolerance.
        """
        # Node A (driver/inflow): level baseline 5.0
        # Node B (stock): stock baseline 10.0, retention_factor 0.8 (decay rate = 20% per tick)
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:inflow": Variable(id="node:inflow", role="driver", baseline=5.0, scale="level"),
            "node:stock": Variable(id="node:stock", role="state", baseline=10.0, scale="stock", retention=0.8),
        }
        model.edges = [
            ModelEdge(id="causal:inflow_to_stock", source="node:inflow", target="node:stock", sign=1, strength=1.0, lag_ticks=0),
        ]
        config = EngineConfig(mode="deterministic", seed=42)

        ticks = 4
        trace = generate_numerical_execution_trace(model, config, ticks=ticks)
        jsonschema.validate(instance=trace, schema=self.schema)

        # Analytical recurrence:
        # S(0) = 10.0
        # Tick 0: retained = 10.0 * 0.8 = 8.0, inflow = 5.0 -> S(1) = 13.0
        # Tick 1: retained = 13.0 * 0.8 = 10.4, inflow = 5.0 -> S(2) = 15.4
        # Tick 2: retained = 15.4 * 0.8 = 12.32, inflow = 5.0 -> S(3) = 17.32
        # Tick 3: retained = 17.32 * 0.8 = 13.856, inflow = 5.0 -> S(4) = 18.856
        expected_levels = [13.0, 15.4, 17.32, 18.856]

        steps = trace["steps"]
        stock_steps = [s for s in steps if s.get("node_id") == "node:stock"]
        self.assertEqual(len(stock_steps), ticks)

        for i, s in enumerate(stock_steps):
            self.assertIn("stock_flow_integrations", s)
            sfi = s["stock_flow_integrations"]
            self.assertEqual(sfi["stock_variable"], "node:stock")
            self.assertAlmostEqual(sfi["retention_factor"], 0.8, places=6)
            self.assertAlmostEqual(sfi["inflow_rate"], 5.0, places=6)

            expected_outflow = s["target_state_before"] * (1.0 - 0.8) / float(config.timestep)
            self.assertAlmostEqual(sfi["outflow_rate"], expected_outflow, places=6)

            expected_integrated = expected_levels[i]
            self.assertAlmostEqual(sfi["integrated_level"], expected_integrated, places=6)
            self.assertAlmostEqual(s["target_state_after"], expected_integrated, places=6)


if __name__ == "__main__":
    unittest.main()
