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
)

SCHEMAS_DIR = ROOT / "schemas"
EXECUTION_TRACE_SCHEMA_PATH = SCHEMAS_DIR / "execution-trace.schema.json"


class Formula21TransformsAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_t12_hysteresis_latch_saturation(self) -> None:
        """T12: Formula 2.1 non-linear edge with hysteresis latch and saturation bounds.

        Trace records transform_applied with mode=hysteresis; latch prevents spurious toggling in fixed-point iteration.
        """
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:source": Variable(id="node:source", role="driver", baseline=4.0, scale="level"),
            "node:target": Variable(id="node:target", role="state", baseline=0.0, scale="level"),
        }
        # Edge with threshold transform in hysteresis mode:
        # threshold = 5.0, theta_on = 2.0, theta_off = 1.0, saturation = 10.0
        model.edges = [
            ModelEdge(
                id="causal:source_to_target",
                source="node:source",
                target="node:target",
                sign=1,
                strength=1.0,
                lag_ticks=0,
                transform="threshold",
                transform_parameters={
                    "mode": "hysteresis",
                    "threshold": 5.0,
                    "theta_on": 2.0,
                    "theta_off": 1.0,
                },
                saturation=10.0,
            )
        ]

        # Interventions drive node:source through hysteresis cycle:
        # Tick 0: source = 4.0 -> |4 - 5| = 1.0 < theta_on (2.0) -> inactive (output = 0.0)
        # Tick 1: source = 8.0 -> |8 - 5| = 3.0 >= theta_on (2.0) -> latch activates (output = 3.0)
        # Tick 2: source = 6.5 -> |6.5 - 5| = 1.5 > theta_off (1.0) -> latch HOLDS active (output = 1.5)
        # Tick 3: source = 5.5 -> |5.5 - 5| = 0.5 <= theta_off (1.0) -> latch releases (output = 0.0)
        # Tick 4: source = 20.0 -> |20 - 5| = 15.0 >= theta_on -> active, raw = 15.0, saturated = 10 * tanh(15/10)
        model.interventions = [
            {"id": "int:s_t1", "target": "node:source", "op": "set", "value": 8.0, "start_tick": 1, "end_tick": 2},
            {"id": "int:s_t2", "target": "node:source", "op": "set", "value": 6.5, "start_tick": 2, "end_tick": 3},
            {"id": "int:s_t3", "target": "node:source", "op": "set", "value": 5.5, "start_tick": 3, "end_tick": 4},
            {"id": "int:s_t4", "target": "node:source", "op": "set", "value": 20.0, "start_tick": 4, "end_tick": 5},
        ]

        config = EngineConfig(mode="deterministic", seed=42)
        trace = generate_numerical_execution_trace(model, config, ticks=5)

        # 1. Schema compliance
        jsonschema.validate(instance=trace, schema=self.schema)

        # 2. Extract edge steps
        edge_steps = [
            s for s in trace["steps"]
            if s.get("edge_id") == "causal:source_to_target" and s.get("node_id") == "node:target"
        ]
        self.assertEqual(len(edge_steps), 5)

        # Tick 0: inactive
        s0 = edge_steps[0]
        self.assertIn("transform_applied", s0)
        self.assertEqual(s0["transform_applied"]["mode"], "hysteresis")
        self.assertFalse(s0["transform_applied"]["threshold_active_before"])
        self.assertFalse(s0["transform_applied"]["threshold_active_after"])
        self.assertAlmostEqual(s0["state_transitions"]["delta"], 0.0)

        # Tick 1: turns ON (|8 - 5| = 3.0 >= 2.0)
        s1 = edge_steps[1]
        self.assertEqual(s1["transform_applied"]["mode"], "hysteresis")
        self.assertFalse(s1["transform_applied"]["threshold_active_before"])
        self.assertTrue(s1["transform_applied"]["threshold_active_after"])
        self.assertAlmostEqual(s1["state_transitions"]["delta"], 10.0 * math.tanh(3.0 / 10.0), places=5)

        # Tick 2: latch holds ON (|6.5 - 5| = 1.5 > 1.0 theta_off)
        s2 = edge_steps[2]
        self.assertEqual(s2["transform_applied"]["mode"], "hysteresis")
        self.assertTrue(s2["transform_applied"]["threshold_active_before"])
        self.assertTrue(s2["transform_applied"]["threshold_active_after"])
        self.assertAlmostEqual(s2["state_transitions"]["delta"], 10.0 * math.tanh(1.5 / 10.0), places=5)

        # Tick 3: drops below theta_off (|5.5 - 5| = 0.5 <= 1.0) -> turns OFF
        s3 = edge_steps[3]
        self.assertEqual(s3["transform_applied"]["mode"], "hysteresis")
        self.assertTrue(s3["transform_applied"]["threshold_active_before"])
        self.assertFalse(s3["transform_applied"]["threshold_active_after"])
        self.assertAlmostEqual(s3["state_transitions"]["delta"], 0.0)

        # Tick 4: saturated output (|20 - 5| = 15.0, saturated with 10.0 * tanh(1.5))
        s4 = edge_steps[4]
        self.assertEqual(s4["transform_applied"]["mode"], "hysteresis")
        self.assertFalse(s4["transform_applied"]["threshold_active_before"])
        self.assertTrue(s4["transform_applied"]["threshold_active_after"])
        expected_saturated = 10.0 * math.tanh(15.0 / 10.0)
        self.assertAlmostEqual(s4["state_transitions"]["delta"], expected_saturated, places=5)
        self.assertLess(s4["state_transitions"]["delta"], 10.0)


if __name__ == "__main__":
    unittest.main()
