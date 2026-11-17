"""Regression tests for Aleph Skill v2.9.0 upgrades and contracts."""

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

import research_gateway  # noqa: E402
from aleph import PACKAGE_VERSION, VALIDATOR_VERSION  # noqa: E402
from aleph.auto_causal import _compact_factor_phrase, propose_model  # noqa: E402
from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    compile_model,
    model_payload,
    run_deterministic,
    run_monte_carlo,
)
from aleph.interactive_roleplay import (  # noqa: E402
    advance_roleplay_round,
    commit_actor_action,
    generate_actor_packets,
    init_roleplay_session,
    simulate_roleplay_branches,
    validate_session_spec,
)
from aleph.packs import validate_pack  # noqa: E402


class TestV290UpgradeRegressions(unittest.TestCase):
    def test_version_constants_bumped_to_2_9_0(self) -> None:
        self.assertEqual(PACKAGE_VERSION, "2.9.0")
        self.assertEqual(VALIDATOR_VERSION, "2.9.0")

    def test_component_lock_v380_and_gateway_routes_unlocked(self) -> None:
        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["version"], "3.8.0")
        self.assertEqual(lock["components"]["d-research"]["version"], "3.8.0")
        self.assertEqual(lock["components"]["d-research"]["source_tag"], "v3.8.0")

        for route_name in ("research:execution-evidence", "research:grounding"):
            route = research_gateway.COMMAND_ROUTES.get(route_name)
            self.assertIsNotNone(route, f"Missing gateway route: {route_name}")
            self.assertFalse(route["disabled"], f"Route should be unlocked in v2.9.0: {route_name}")

        for route_name in ("research:execution-evidence", "research:grounding"):
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "research_gateway.py"), route_name, "--", "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_formula_220_retention_mode_level_inertia_and_legacy_replay(self) -> None:
        stock_node = {
            "id": "stock:capital",
            "name": "Capital Stock",
            "type": "factor",
            "role": "state",
            "scale": "stock",
            "baseline": 100.0,
            "retention": 0.9,
            "retention_unit": "per_tick",
        }
        # Formula 2.2.0 defaults stocks to retention_mode="baseline": unshocked equilibrium holds.
        model_220 = compile_model([stock_node], [], formula_version="2.2.0")
        det_220 = run_deterministic(model_220, EngineConfig(seed="42", timestep=1.0), ticks=4)
        self.assertTrue(det_220["ok"], det_220["issues"])
        self.assertAlmostEqual(det_220["payload"]["final_state"]["stock:capital"], 100.0, places=6)

        # Opting into retention_mode="zero" under Formula 2.2.0 recovers zero-attractor decay.
        stock_zero = {**stock_node, "retention_mode": "zero"}
        model_220_zero = compile_model([stock_zero], [], formula_version="2.2.0")
        det_220_zero = run_deterministic(model_220_zero, EngineConfig(seed="42", timestep=1.0), ticks=4)
        self.assertTrue(det_220_zero["ok"], det_220_zero["issues"])
        self.assertAlmostEqual(det_220_zero["payload"]["final_state"]["stock:capital"], 100.0 * (0.9**4), places=6)

        # Formula 2.1.0 replay preserves legacy zero-attractor stock semantics & exact hash.
        model_210 = compile_model([stock_node], [], formula_version="2.1.0")
        det_210 = run_deterministic(model_210, EngineConfig(seed="42", timestep=1.0), ticks=4)
        model_210_explicit = compile_model([stock_zero], [], formula_version="2.1.0")
        det_210_explicit = run_deterministic(model_210_explicit, EngineConfig(seed="42", timestep=1.0), ticks=4)
        self.assertTrue(det_210["ok"], det_210["issues"])
        self.assertAlmostEqual(det_210["payload"]["final_state"]["stock:capital"], 100.0 * (0.9**4), places=6)
        compiled_payload = model_payload(model_210)
        self.assertNotIn("retention_mode", compiled_payload["variables"]["stock:capital"])
        self.assertEqual(det_210["history"], det_210_explicit["history"])

        # Level node inertia=0.5 smoothly damps initial disequilibrium back toward baseline.
        level_inertia = {
            "id": "factor:rate",
            "name": "Policy Rate",
            "type": "factor",
            "role": "state",
            "scale": "level",
            "baseline": 100.0,
            "initial_value": 120.0,
            "inertia": 0.5,
        }
        model_inertia = compile_model([level_inertia], [], formula_version="2.2.0")
        det_inertia = run_deterministic(model_inertia, EngineConfig(seed="42", timestep=1.0), ticks=4)
        self.assertTrue(det_inertia["ok"], det_inertia["issues"])
        traj = [tick_row["factor:rate"] for tick_row in det_inertia["history"]]
        self.assertAlmostEqual(traj[0], 120.0, places=6)
        self.assertAlmostEqual(traj[1], 110.0, places=6)
        self.assertAlmostEqual(traj[2], 105.0, places=6)
        self.assertAlmostEqual(traj[3], 102.5, places=6)

    def test_stochastic_shocks_deterministic_tail_class_monte_carlo(self) -> None:
        nodes = [
            {
                "id": "factor:demand",
                "name": "Demand",
                "type": "factor",
                "role": "state",
                "scale": "level",
                "baseline": 100.0,
            }
        ]
        shocks = [
            {
                "id": "shock:tail",
                "target": "factor:demand",
                "op": "add",
                "probability_per_tick": 0.25,
                "magnitude": 25.0,
                "start_tick": 1,
                "end_tick": 3,
            }
        ]
        model = compile_model(nodes, [], formula_version="2.2.0", stochastic_shocks=shocks)
        cfg = EngineConfig(seed="42", mode="monte_carlo", min_runs=20, max_runs=40, batch_size=20)
        mc_a = run_monte_carlo(model, cfg, ticks=4)
        mc_b = run_monte_carlo(model, cfg, ticks=4)

        self.assertTrue(mc_a["ok"], mc_a["issues"])
        branches = mc_a["summary"]["branches"]
        self.assertTrue(
            any(b.get("tail") is True and b.get("tail_class") == "tail_shock" for b in branches),
            f"Expected tail_shock branch, got: {branches}",
        )
        self.assertEqual(mc_a["summary"]["canonical_hash"], mc_b["summary"]["canonical_hash"])

    def test_auto_causal_csv_chronological_baseline_and_clause_compaction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aleph-v290-csv-") as td:
            csv_path = Path(td) / "series.csv"
            csv_path.write_text(
                "date,policy_rate,output_gap\n"
                "2025-01-01,50.0,0.5\n"
                "2025-02-01,20.0,2.0\n"
                "2025-03-01,65.0,-0.4\n"
                "2025-04-01,80.0,-1.2\n",
                encoding="utf-8",
            )
            proposal = propose_model(csv_path)
            by_id = {node["id"]: node for node in proposal["candidate_nodes"]}
            # First row value (vals[0] == 50.0) is used as baseline, not min (20.0) or last (80.0).
            self.assertAlmostEqual(by_id["factor:policy-rate"]["baseline"], 50.0, places=6)
            self.assertTrue(
                any(edge.get("relation") == "correlation" for edge in proposal["candidate_edges"]),
                "Wide CSV inference must emit correlation candidate edges",
            )
            self.assertTrue(
                any(gap.get("gap_type") == "correlation_not_causation" for gap in proposal["gaps"]),
                "Wide CSV inference must flag correlation_not_causation in gaps",
            )

        verbose_clause = (
            "central bank emergency liquidity facility expansion that stabilizes interbank funding spreads"
        )
        compact, raw = _compact_factor_phrase(verbose_clause)
        self.assertEqual(raw, verbose_clause)
        self.assertLessEqual(len(compact.split()), 6)
        self.assertLessEqual(len(compact), 56)

        claim_prop = propose_model(
            [
                {
                    "claim_id": "c1",
                    "claim": (
                        "Central bank emergency liquidity facility expansion that stabilizes interbank funding "
                        "increases credit growth by 0.4"
                    ),
                    "confidence": "high",
                }
            ]
        )
        descs = [n.get("description", "") for n in claim_prop["candidate_nodes"]]
        names = [n["name"] for n in claim_prop["candidate_nodes"]]
        self.assertTrue(any(len(n.split()) <= 6 for n in names), names)
        self.assertTrue(
            any("central bank emergency liquidity facility expansion that stabilizes interbank funding" in d.lower() for d in descs),
            descs,
        )

    def test_economics_pack_is_calibrated_30_cases(self) -> None:
        report = validate_pack(ROOT / "packs" / "economics")
        self.assertEqual(report["maturity"], "calibrated")
        self.assertTrue(report["can_emit_probability"])
        self.assertEqual(report["hindcast_cases"], 30)
        self.assertEqual(report["issues"], [])

    def test_roleplay_objectives_synthesize_weights_and_simulate_branches(self) -> None:
        variables = {
            "gdp": Variable(id="gdp", role="state", baseline=100.0, scale="level"),
            "inflation": Variable(id="inflation", role="state", baseline=2.0, scale="level"),
        }
        edges = [
            ModelEdge(id="e1", source="gdp", target="inflation", sign=1, strength=0.1, transform="linear")
        ]
        model = ComputationalModel(variables=variables, edges=edges, formula_version="2.2.0")
        spec = {
            "schema_version": "1.0.0",
            "session_id": "obj_branch_test",
            "simulation_id": "sim_obj_001",
            "actors": ["Gov", "Market"],
            "decision_ticks": [1],
            "allowed_actions": {
                "Gov": [
                    {"id": "noop", "name": "Hold", "cost": 0.0, "noop": True},
                    {"id": "stimulus", "name": "Stimulate", "cost": 1.0, "noop": False},
                ],
                "Market": [
                    {"id": "noop", "name": "Hold", "cost": 0.0, "noop": True},
                    {"id": "reprice", "name": "Reprice", "cost": 0.5, "noop": False},
                ],
            },
            "action_effects": {
                "Gov:stimulus": [{"target": "gdp", "magnitude": 4.0, "lag": 0, "op": "add"}],
                "Market:reprice": [{"target": "inflation", "magnitude": 1.2, "lag": 0, "op": "add"}],
            },
            "payoffs": {
                "Gov": {
                    "objectives": [
                        {"target": "gdp", "direction": "increase", "weight": 2.0},
                        {"target": "inflation", "direction": "decrease", "weight": 3.0},
                    ],
                    "attribution": "Macro stabilization assumption",
                },
                "Market": {
                    "objectives": {"gdp": 1.5, "inflation": -1.0},
                    "attribution": "Investor mandate assumption",
                },
            },
        }
        ok, errs = validate_session_spec(spec, model, allow_parameterized_payoffs=True)
        self.assertTrue(ok, errs)
        session = init_roleplay_session(spec, model)
        self.assertAlmostEqual(session["spec"]["payoffs"]["Gov"]["state_weights"]["gdp"], 2.0)
        self.assertAlmostEqual(session["spec"]["payoffs"]["Gov"]["state_weights"]["inflation"], -3.0)
        self.assertAlmostEqual(session["spec"]["payoffs"]["Market"]["state_weights"]["gdp"], 1.5)

        packets = generate_actor_packets(session, model)
        commit_actor_action(session, "Gov", "stimulus", packets["Gov"]["packet_hash"])
        commit_actor_action(session, "Market", "noop", packets["Market"]["packet_hash"])
        advance_roleplay_round(session, model)

        branches_res = simulate_roleplay_branches(session, model, max_branches=4, ticks=3)
        self.assertEqual(branches_res["likelihood_mode"], "relative_weight")
        self.assertFalse(branches_res["calibrated"])
        branch_ids = [b["id"] for b in branches_res["branches"]]
        self.assertIn("branch:committed", branch_ids)
        self.assertGreater(len(branch_ids), 1)

    def test_run_pipeline_self_test(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "run_pipeline.py"), "--self-test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload.get("self_test"), "ok")
        self.assertEqual(payload.get("stages"), 9)


if __name__ == "__main__":
    unittest.main()
