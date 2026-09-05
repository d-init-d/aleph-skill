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

CONTRACTS_DIR = ROOT.parents[1] / "audit-artifacts" / "contracts"
EXECUTION_TRACE_SCHEMA_PATH = CONTRACTS_DIR / "execution-trace.schema.json"


class NumericalInterventionsAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_t11_interventions_do_calculus(self) -> None:
        """T11: Intervention do(set), do(add), do(multiply), and release applied at tick T.

        do(set) severs incoming edges at tick T; do(add)/multiply adjust state; release restores incoming propagation; trace logs intervention_type.
        """
        # Node A: driver with baseline 4.0
        # Node B: state with baseline 0.0
        # Edge A -> B: strength 0.5 (incoming delta would normally be 2.0 per tick)
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="driver", baseline=4.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.5, lag_ticks=0),
        ]

        # Tick schedule:
        # Tick 0: Normal propagation (none) -> incoming delta = 2.0
        # Tick 1: do(set) on B with value 10.0 (start_tick 1, end_tick 2) -> severs incoming edge (delta = 0.0), intervention_type = do_set
        # Tick 2: release from do(set) (start_tick 2, end_tick 3) -> incoming edge restored (delta = 2.0), intervention_type = release
        # Tick 3: do(add) on B with value 5.0 (start_tick 3, end_tick 4) -> intervention_type = do_add
        # Tick 4: do(multiply) on B with value 1.5 (start_tick 4, end_tick 5) -> intervention_type = do_multiply
        model.interventions = [
            {"id": "int:set_b", "target": "node:b", "op": "set", "value": 10.0, "start_tick": 1, "end_tick": 2},
            {"id": "int:add_b", "target": "node:b", "op": "add", "value": 5.0, "start_tick": 3, "end_tick": 4},
            {"id": "int:mult_b", "target": "node:b", "op": "multiply", "value": 1.5, "start_tick": 4, "end_tick": 5},
        ]

        config = EngineConfig(mode="deterministic", seed=42)
        trace = generate_numerical_execution_trace(model, config, ticks=5)
        jsonschema.validate(instance=trace, schema=self.schema)

        steps = trace["steps"]
        b_steps = [s for s in steps if s.get("node_id") == "node:b"]
        self.assertEqual(len(b_steps), 5)

        # Tick 0: none
        s0 = b_steps[0]
        self.assertEqual(s0["tick"], 0)
        self.assertEqual(s0["state_transitions"]["intervention_type"], "none")
        self.assertAlmostEqual(s0["state_transitions"]["delta"], 2.0)

        # Tick 1: do_set (incoming severed, actual_delta = 0.0)
        s1 = b_steps[1]
        self.assertEqual(s1["tick"], 1)
        self.assertEqual(s1["state_transitions"]["intervention_type"], "do_set")
        self.assertAlmostEqual(s1["state_transitions"]["delta"], 0.0)

        # Tick 2: release (incoming restored, actual_delta = 2.0)
        s2 = b_steps[2]
        self.assertEqual(s2["tick"], 2)
        self.assertEqual(s2["state_transitions"]["intervention_type"], "release")
        self.assertAlmostEqual(s2["state_transitions"]["delta"], 2.0)

        # Tick 3: do_add
        s3 = b_steps[3]
        self.assertEqual(s3["tick"], 3)
        self.assertEqual(s3["state_transitions"]["intervention_type"], "do_add")

        # Tick 4: do_multiply
        s4 = b_steps[4]
        self.assertEqual(s4["tick"], 4)
        self.assertEqual(s4["state_transitions"]["intervention_type"], "do_multiply")


if __name__ == "__main__":
    unittest.main()
