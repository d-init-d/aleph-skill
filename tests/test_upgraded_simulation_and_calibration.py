"""Tests for upgraded Aleph simulation physics, SCC solver, auto-causal, roleplay, adjudication, and calibration."""

from __future__ import annotations

import math
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.auto_causal import propose_model  # noqa: E402
from aleph.calibration_lineage import (  # noqa: E402
    bayesian_update_branches,
    compute_calibration_diagnostics,
    fit_isotonic_or_binned_calibration,
    fit_platt_scaling,
)
from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    compile_model,
    generate_numerical_execution_trace,
    model_hash,
    run_deterministic,
    run_monte_carlo,
)
from aleph.interactive_roleplay import (  # noqa: E402
    advance_roleplay_round,
    commit_actor_action,
    evaluate_actor_utility,
    generate_actor_packets,
    init_roleplay_session,
    replay_roleplay_session,
    suggest_round_commitments,
    validate_session_spec,
)
from aleph.packets import adjudicate  # noqa: E402


def _pearson_corr(xs: list[float], ys: list[float]) -> float:
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (denx * deny) if denx > 0.0 and deny > 0.0 else 0.0


class TestUpgradedSimulationAndCalibration(unittest.TestCase):
    def test_trace_preserves_transform_modes_and_computes_decay_constant(self) -> None:
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:src": Variable(id="node:src", role="driver", baseline=2.0, scale="flow"),
            "node:stock": Variable(id="node:stock", role="state", baseline=10.0, scale="stock", retention=0.8),
            "node:gate_out": Variable(id="node:gate_out", role="state", baseline=0.0, scale="level"),
        }
        model.edges = [
            ModelEdge(
                id="causal:src_to_stock",
                source="node:src",
                target="node:stock",
                sign=1,
                strength=1.0,
                lag_ticks=0,
                transform="threshold",
                transform_parameters={"mode": "above", "threshold": 1.0},
                integration="rate",
            ),
            ModelEdge(
                id="causal:src_to_deadband",
                source="node:src",
                target="node:gate_out",
                sign=1,
                strength=1.0,
                lag_ticks=0,
                transform="threshold",
                transform_parameters={"mode": "deadband", "threshold": 0.5, "deadband": 0.5},
            ),
        ]
        trace = generate_numerical_execution_trace(model, EngineConfig(mode="deterministic"), ticks=2)
        stock_steps = [s for s in trace["steps"] if s["edge_id"] == "causal:src_to_stock"]
        deadband_steps = [s for s in trace["steps"] if s["edge_id"] == "causal:src_to_deadband"]
        self.assertTrue(stock_steps)
        self.assertTrue(deadband_steps)
        self.assertEqual(stock_steps[0]["transform_applied"]["mode"], "above")
        self.assertEqual(deadband_steps[0]["transform_applied"]["mode"], "deadband")
        self.assertAlmostEqual(stock_steps[0]["stock_flow_integrations"]["decay_constant"], 0.2, places=6)

    def test_bounded_affine_scc_solver_converges_on_bounded_cycle(self) -> None:
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:a": Variable(id="node:a", role="state", baseline=2.0, scale="level", bounds=(0.0, 3.0)),
            "node:b": Variable(id="node:b", role="state", baseline=1.0, scale="level", bounds=(0.0, 10.0)),
        }
        model.edges = [
            ModelEdge(id="causal:a_to_b", source="node:a", target="node:b", sign=1, strength=1.5, lag_ticks=0),
            ModelEdge(id="causal:b_to_a", source="node:b", target="node:a", sign=1, strength=1.5, lag_ticks=0),
        ]
        # Unbounded fixed point has rho(W) > 1 (Jacobi diverges); bounded system clamps node:a=3.0 => node:b=1.0+4.5=5.5
        res_explicit = run_deterministic(model, EngineConfig(mode="deterministic", scc_solver="bounded_affine"), ticks=2)
        self.assertTrue(res_explicit["ok"])
        self.assertAlmostEqual(res_explicit["payload"]["final_state"]["node:a"], 3.0, places=6)
        self.assertAlmostEqual(res_explicit["payload"]["final_state"]["node:b"], 5.5, places=6)
        self.assertEqual(res_explicit["scc_diagnostics"][-1]["solver"], "bounded_affine")

        res_auto = run_deterministic(model, EngineConfig(mode="deterministic", scc_solver="linear_auto"), ticks=2)
        self.assertTrue(res_auto["ok"])
        self.assertAlmostEqual(res_auto["payload"]["final_state"]["node:b"], 5.5, places=6)

    def test_correlated_monte_carlo_shocks_and_hash_invariance(self) -> None:
        base_spec = {
            "variables": [
                {"id": "factor:driver", "role": "driver", "scale": "level", "baseline": 1.0},
                {"id": "factor:out_a", "role": "state", "scale": "level", "baseline": 0.0},
                {"id": "factor:out_b", "role": "state", "scale": "level", "baseline": 0.0},
            ],
            "edges": [
                {
                    "id": "causal:e1",
                    "from": "factor:driver",
                    "to": "factor:out_a",
                    "sign": 1,
                    "base_strength": 1.0,
                    "lag_ticks": 0,
                    "effect_distribution": {"distribution": "normal", "mean": 1.0, "sd": 0.4},
                },
                {
                    "id": "causal:e2",
                    "from": "factor:driver",
                    "to": "factor:out_b",
                    "sign": 1,
                    "base_strength": 1.0,
                    "lag_ticks": 0,
                    "effect_distribution": {"distribution": "normal", "mean": 1.0, "sd": 0.4},
                },
            ],
        }
        m_indep = compile_model(base_spec["variables"], base_spec["edges"], [], formula_version="2.1.0")
        corr_edges = [dict(e, shock_group="macro_liquidity", shock_correlation=0.9) for e in base_spec["edges"]]
        m_corr = compile_model(base_spec["variables"], corr_edges, [], formula_version="2.1.0")
        self.assertNotEqual(model_hash(m_indep), model_hash(m_corr))

        out_a: list[float] = []
        out_b: list[float] = []
        cfg = EngineConfig(mode="monte_carlo", seed="corr-test", min_runs=60, max_runs=60, batch_size=30)
        for rid in range(60):
            r = run_deterministic(m_corr, cfg, ticks=1, run_id=rid)
            self.assertTrue(r["ok"])
            out_a.append(r["payload"]["final_state"]["factor:out_a"])
            out_b.append(r["payload"]["final_state"]["factor:out_b"])
        self.assertGreater(_pearson_corr(out_a, out_b), 0.65)
        mc_res = run_monte_carlo(m_corr, cfg, ticks=1)
        self.assertTrue(mc_res["ok"])

    def test_auto_causal_structured_and_vietnamese_patterns(self) -> None:
        rows = [
            {
                "evidence_id": "ev_struct",
                "record_type": "claim",
                "claim": "Structured liquidity shock expands credit spread.",
                "source_factor": "interbank_rate",
                "target_factor": "credit_spread",
                "polarity": "positive",
                "mechanism": "funding squeeze",
                "lag_ticks": 2,
                "effect_size": 0.45,
                "confidence": "high",
            },
            {
                "evidence_id": "ev_vi",
                "record_type": "claim",
                "claim": "Lạm phát kỳ vọng làm suy giảm sức mua thực tế",
                "confidence": "medium",
            },
        ]
        proposal = propose_model(rows, proposal_id="prop:upgraded")
        edges = {e["id"]: e for e in proposal["candidate_edges"]}
        self.assertIn("causal:interbank-rate-to-credit-spread", edges)
        e1 = edges["causal:interbank-rate-to-credit-spread"]
        self.assertEqual(e1["mechanism"], "funding squeeze")
        self.assertEqual(e1["lag_ticks"], 2)
        self.assertEqual(e1["effect_size"], 0.45)

        self.assertIn("causal:lam-phat-ky-vong-to-suc-mua-thuc-te", edges)
        self.assertEqual(edges["causal:lam-phat-ky-vong-to-suc-mua-thuc-te"]["sign"], -1)

    def test_roleplay_parameterized_utility_extended_effects_and_advisor(self) -> None:
        model = ComputationalModel(formula_version="2.1.0")
        model.variables = {
            "node:market": Variable(id="node:market", role="state", baseline=10.0, scale="level"),
        }
        spec = {
            "schema_version": "1.0.0",
            "session_id": "sess_upg_01",
            "simulation_id": "sim_upg_01",
            "allow_extended_effects": True,
            "allow_parameterized_payoffs": True,
            "actors": ["firm_a", "firm_b"],
            "decision_ticks": [1],
            "allowed_actions": {
                "firm_a": [{"id": "hold", "noop": True, "cost": 0.0}, {"id": "expand", "noop": False, "cost": 1.0}],
                "firm_b": [{"id": "hold", "noop": True, "cost": 0.0}, {"id": "cut", "noop": False, "cost": 0.5}],
            },
            "action_effects": {
                "firm_a:hold": [],
                "firm_b:hold": [],
                "firm_a:expand": [{"target": "node:market", "magnitude": 1.5, "op": "multiply", "lag": 0}],
                "firm_b:cut": [{"target": "node:market", "magnitude": -2.0, "op": "add", "lag": 0}],
            },
            "payoffs": {
                "firm_a": {"state_weights": {"node:market": 1.0}, "default_utility": 2.0, "action_weights": {"firm_a:expand": 1.0}},
                "firm_b": {"state_weights": {"node:market": -0.5}, "default_utility": 1.0, "action_weights": {"firm_b:cut": 3.0}},
            },
        }
        ok, errs = validate_session_spec(spec, model)
        self.assertTrue(ok, errs)
        session = init_roleplay_session(spec, model)
        generate_actor_packets(session, model)
        advice = suggest_round_commitments(session, model)
        self.assertEqual(advice["recommendations"]["firm_a"]["action_id"], "expand")
        commit_actor_action(session, **advice["recommendations"]["firm_a"]["commit_payload"])
        commit_actor_action(session, **advice["recommendations"]["firm_b"]["commit_payload"])
        rec = advance_roleplay_round(session, model)
        self.assertEqual(rec["payoffs"]["firm_a"], evaluate_actor_utility(
            spec["payoffs"]["firm_a"], rec["committed_actions"], spec["actors"], rec["state_snapshot"], 1.0, actor_id="firm_a"
        ))
        replay = replay_roleplay_session(session, model)
        self.assertTrue(replay["ok"] and replay["match"])

    def test_adjudicate_evidence_weights_and_calibration_lineage(self) -> None:
        hyps = [
            {"id": "h1", "action": "hike", "relative_weight": 0.4},
            {"id": "h2", "action": "hold", "relative_weight": 0.4},
        ]
        adj = adjudicate(
            hyps,
            method="bayesian_evidence",
            evidence_scores={
                "h1": [{"reliability": 0.9, "relevance": 0.8, "corroboration": 1.2, "contradiction_penalty": 0.05}],
                "h2": [{"reliability": 0.4, "relevance": 0.5, "corroboration": 1.0, "contradiction_penalty": 0.3}],
            },
            prior_weights={"h1": 1.1, "h2": 0.9},
        )
        self.assertEqual(adj["winning_hypothesis_id"], "h1")
        self.assertAlmostEqual(sum(adj["posterior_confidence"].values()), 1.0, places=5)

        scores = [-2.0, -1.2, -0.5, 0.4, 1.1, 2.3]
        outcomes = [0, 0, 0, 1, 1, 1]
        platt = fit_platt_scaling(scores, outcomes)
        self.assertGreater(platt["a"], 0.0)
        self.assertLess(platt["diagnostics"]["brier_score"], 0.15)
        iso = fit_isotonic_or_binned_calibration(scores, outcomes, method="isotonic")
        self.assertEqual(len(iso["calibrated_probs"]), len(scores))
        diag = compute_calibration_diagnostics(platt["calibrated_probs"], outcomes)
        self.assertIn("ece", diag)
        bayes = bayesian_update_branches(
            {"bull": 0.3, "base": 0.5, "bear": 0.2},
            [{"bull": 0.8, "base": 0.3, "bear": 0.1}],
        )
        self.assertEqual(bayes["map_branch"], "bull")
        self.assertAlmostEqual(sum(bayes["posteriors"].values()), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
