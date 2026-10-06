"""Unit tests for the linear_auto algebraic loop solver and cycle diagnostics."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    import jsonschema
except ImportError:
    jsonschema = None

from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    generate_numerical_execution_trace,
    run_deterministic,
)
from aleph.trace_contract import validate_execution_trace_data  # noqa: E402

SCHEMAS_DIR = ROOT / "schemas"
EXECUTION_TRACE_SCHEMA_PATH = SCHEMAS_DIR / "execution-trace.schema.json"


class TestLinearAutoSolver(unittest.TestCase):
    def setUp(self) -> None:
        if EXECUTION_TRACE_SCHEMA_PATH.exists():
            self.schema = json.loads(EXECUTION_TRACE_SCHEMA_PATH.read_text(encoding="utf-8"))
        else:
            self.schema = None

    def test_converging_cycle_matches_between_solvers(self) -> None:
        """When Jacobi converges, linear_auto produces identical state within tolerance."""
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=0.5, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=0.2, lag_ticks=0),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=0.2, lag_ticks=0),
        ]

        cfg_jacobi = EngineConfig(mode="deterministic", scc_solver="jacobi")
        cfg_linear = EngineConfig(mode="deterministic", scc_solver="linear_auto")

        res_jacobi = run_deterministic(model, cfg_jacobi, ticks=3)
        res_linear = run_deterministic(model, cfg_linear, ticks=3)

        self.assertTrue(res_jacobi["ok"])
        self.assertTrue(res_linear["ok"])
        for node in ("node:a", "node:b"):
            val_j = res_jacobi["payload"]["final_state"][node]
            val_l = res_linear["payload"]["final_state"][node]
            self.assertAlmostEqual(val_j, val_l, places=6)

    def test_jacobi_divergent_affine_system_solved_by_linear_auto(self) -> None:
        """A > B: 2.0, B > A: 2.0 diverges Jacobi, but has exact analytical solution x_a = -1, x_b = -1."""
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=1.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=2.0, lag_ticks=0),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=2.0, lag_ticks=0),
        ]

        # 1. Jacobi must fail (diverges)
        cfg_jacobi = EngineConfig(mode="deterministic", scc_solver="jacobi", jacobi_max_iter=30)
        res_jacobi = run_deterministic(model, cfg_jacobi, ticks=3)
        self.assertFalse(res_jacobi["ok"])
        self.assertEqual(res_jacobi["exit_code"], 4)

        # 2. linear_auto must succeed and find exact solution (-1.0, -1.0)
        cfg_linear = EngineConfig(mode="deterministic", scc_solver="linear_auto", jacobi_max_iter=30)
        res_linear = run_deterministic(model, cfg_linear, ticks=3)
        self.assertTrue(res_linear["ok"])
        self.assertEqual(res_linear["exit_code"], 0)
        state = res_linear["payload"]["final_state"]
        self.assertAlmostEqual(state["node:a"], -1.0, places=7)
        self.assertAlmostEqual(state["node:b"], -1.0, places=7)

        # Diagnostics check
        diags = res_linear.get("scc_diagnostics", [])
        self.assertTrue(len(diags) > 0)
        last_diag = diags[-1]
        self.assertEqual(last_diag["solver"], "linear_auto")
        self.assertEqual(last_diag["method"], "linear_direct")
        self.assertTrue(last_diag["converged"])

        # Trace generation
        trace = generate_numerical_execution_trace(model, cfg_linear, ticks=3)
        self.assertEqual(trace["invalid_mass"], 0.0)
        if jsonschema is not None and self.schema is not None:
            jsonschema.validate(instance=trace, schema=self.schema)
        else:
            issues = validate_execution_trace_data(trace)
            self.assertEqual(issues, [])

    def test_singular_affine_system_detected(self) -> None:
        """Singular loop (det(I - W) = 0): A -> B: 1.0, B -> A: 1.0. linear_auto must fail gracefully."""
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=1.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=1.0, lag_ticks=0),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=1.0, lag_ticks=0),
        ]
        cfg = EngineConfig(mode="deterministic", scc_solver="linear_auto")
        res = run_deterministic(model, cfg, ticks=2)
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 4)
        diags = res.get("scc_diagnostics", [])
        self.assertTrue(any(d.get("error") == "SINGULAR" or not d.get("converged") for d in diags))

    def test_self_loop_linear_auto(self) -> None:
        """Self-loop A -> A: 1.5, baseline 1.0. 1 - 1.5 = -0.5 -> x = -2.0."""
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_a", source="node:a", target="node:a", sign=1, strength=1.5, lag_ticks=0),
        ]
        cfg = EngineConfig(mode="deterministic", scc_solver="linear_auto")
        res = run_deterministic(model, cfg, ticks=2)
        self.assertTrue(res["ok"])
        self.assertAlmostEqual(res["payload"]["final_state"]["node:a"], -2.0, places=7)

    def test_ineligible_nonlinear_scc_rejected(self) -> None:
        """Nonlinear transform in loop (logistic) cannot use affine direct solve; must report UNSUPPORTED_TRANSFORM."""
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=1.0, scale="level"),
            "node:b": Variable(id="node:b", role="state", baseline=1.0, scale="level"),
        }
        model.edges = [
            ModelEdge(
                id="causal:a_to_b",
                source="node:a",
                target="node:b",
                sign=1,
                strength=2.0,
                lag_ticks=0,
                transform="logistic",
                transform_parameters={"midpoint": 0.0, "steepness": 2.0},
            ),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=2.0, lag_ticks=0),
        ]
        cfg = EngineConfig(mode="deterministic", scc_solver="linear_auto", jacobi_max_iter=10)
        res = run_deterministic(model, cfg, ticks=2)
        self.assertFalse(res["ok"])
        diags = res.get("scc_diagnostics", [])
        self.assertTrue(any(d.get("error") == "UNSUPPORTED_TRANSFORM" for d in diags))


if __name__ == "__main__":
    unittest.main()
