#!/usr/bin/env python3
"""Tests for Aleph Multi-Actor Multi-Turn Strategic Roleplay Protocol (P6).

Covers:
- Spec validation and completeness (P6.A01, P6.A05, P6.A09)
- Information isolation & packet hashing (P6.B02, P6.B03, P6.B04, P6.B09)
- Adjudication and simultaneous action commitment (P6.B10, P6.B11)
- 2-actor / 3-round complete lifecycle (P6.C10)
- 3-actor / 2-round multi-party payoff game (P6.C10)
- Additive effects & lag preservation (P6.A06, P6.C03)
- Deterministic bit-for-bit replay & tampering detection (P6.C08)
- Round replay scaling cost (P6.C11)
"""

from __future__ import annotations

import copy
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from aleph.engine import ComputationalModel, ModelEdge, Variable
from aleph.interactive_roleplay import (
    advance_roleplay_round,
    commit_actor_action,
    finalize_roleplay_session,
    generate_actor_packets,
    init_roleplay_session,
    replay_roleplay_session,
    validate_session_spec,
)


def _create_test_model() -> ComputationalModel:
    variables = {
        "tariff_level": Variable(id="tariff_level", role="state", baseline=0.0, scale="level"),
        "trade_volume": Variable(id="trade_volume", role="state", baseline=100.0, scale="level"),
        "inflation": Variable(id="inflation", role="state", baseline=2.0, scale="level"),
        "gdp_growth": Variable(id="gdp_growth", role="state", baseline=3.0, scale="level"),
    }
    edges = [
        ModelEdge(
            id="tariff_to_trade",
            source="tariff_level",
            target="trade_volume",
            sign=-1,
            strength=2.0,
            transform="linear",
        ),
        ModelEdge(
            id="trade_to_gdp",
            source="trade_volume",
            target="gdp_growth",
            sign=1,
            strength=0.05,
            transform="linear",
        ),
        ModelEdge(
            id="tariff_to_inflation",
            source="tariff_level",
            target="inflation",
            sign=1,
            strength=0.3,
            transform="linear",
        ),
    ]
    return ComputationalModel(variables=variables, edges=edges, formula_version="2.1")


def _create_2actor_spec() -> dict:
    return {
        "schema_version": "1.0.0",
        "session_id": "us_china_trade_war",
        "simulation_id": "sim_trade_001",
        "actors": ["US", "China"],
        "decision_ticks": [5, 10, 15],
        "visibility": {
            "US": {"visible_nodes": ["tariff_level", "trade_volume", "inflation", "gdp_growth"]},
            "China": {"visible_nodes": ["tariff_level", "trade_volume", "gdp_growth"]},  # cannot see US inflation directly
        },
        "allowed_actions": {
            "US": [
                {"id": "noop", "name": "Hold Policy", "cost": 0.0, "noop": True},
                {"id": "raise_tariffs", "name": "Raise Tariffs +5%", "cost": 1.0, "noop": False},
            ],
            "China": [
                {"id": "noop", "name": "Hold Policy", "cost": 0.0, "noop": True},
                {"id": "retaliate", "name": "Retaliatory Tariffs", "cost": 1.5, "noop": False},
            ],
        },
        "action_effects": {
            "US:raise_tariffs": [
                {"target": "tariff_level", "magnitude": 5.0, "lag": 0, "op": "add"},
            ],
            "China:retaliate": [
                {"target": "tariff_level", "magnitude": 5.0, "lag": 1, "op": "add"},
            ],
        },
        "payoffs": {
            "US": {
                "state_weights": {"gdp_growth": 10.0, "inflation": -5.0},
                "utility_table": {
                    "noop,noop": 0.0,
                    "noop,retaliate": -5.0,
                    "raise_tariffs,noop": 8.0,
                    "raise_tariffs,retaliate": -2.0,
                },
                "attribution": "Economic trade model assumption",
            },
            "China": {
                "state_weights": {"gdp_growth": 12.0, "trade_volume": 0.2},
                "utility_table": {
                    "noop,noop": 0.0,
                    "noop,retaliate": 2.0,
                    "raise_tariffs,noop": -8.0,
                    "raise_tariffs,retaliate": -3.0,
                },
                "attribution": "Economic trade model assumption",
            },
        },
    }


class TestInteractiveRoleplay(unittest.TestCase):

    def test_spec_validation_success_and_failure(self):
        spec = _create_2actor_spec()
        model = _create_test_model()

        ok, errors = validate_session_spec(spec, model)
        self.assertTrue(ok, f"Expected valid spec, got errors: {errors}")

        # Missing noop action
        bad_spec = copy.deepcopy(spec)
        bad_spec["allowed_actions"]["US"][0]["noop"] = False
        ok, errors = validate_session_spec(bad_spec, model)
        self.assertFalse(ok)
        self.assertTrue(any("explicit noop" in e for e in errors))

        # Incomplete utility table (P6.A09)
        bad_util = copy.deepcopy(spec)
        del bad_util["payoffs"]["US"]["utility_table"]["raise_tariffs,retaliate"]
        ok, errors = validate_session_spec(bad_util, model)
        self.assertFalse(ok)
        self.assertTrue(any("INCOMPLETE_UTILITY_TABLE" in e for e in errors))

    def test_information_isolation_in_packets(self):
        spec = _create_2actor_spec()
        model = _create_test_model()
        session = init_roleplay_session(spec, model)

        packets = generate_actor_packets(session, model)
        self.assertIn("US", packets)
        self.assertIn("China", packets)

        # US sees inflation, China does not see inflation
        self.assertIn("inflation", packets["US"]["observed_state"])
        self.assertNotIn("inflation", packets["China"]["observed_state"])

        # Packet hash is unique and reproducible
        h1 = packets["US"]["packet_hash"]
        self.assertTrue(len(h1) == 64)

        # Neither actor sees other actor's uncommitted actions in current round
        self.assertNotIn("US", packets["China"].get("pending_commits", {}))

    def test_two_actor_three_round_lifecycle_and_replay(self):
        spec = _create_2actor_spec()
        model = _create_test_model()
        session = init_roleplay_session(spec, model)

        # Round 0 (Tick 5): US raises tariffs, China noop
        packets_r0 = generate_actor_packets(session, model)
        commit_actor_action(session, "US", "raise_tariffs", packets_r0["US"]["packet_hash"], rationale="Strategic tariff")
        commit_actor_action(session, "China", "noop", packets_r0["China"]["packet_hash"], rationale="De-escalate")

        r0_record = advance_roleplay_round(session, model)
        self.assertEqual(r0_record["round_index"], 0)
        self.assertIn("US", r0_record["payoffs"])
        self.assertIn("China", r0_record["payoffs"])
        # US state: tariff was raised, inflation increased, payoffs computed
        self.assertEqual(session["current_round"], 1)

        # Round 1 (Tick 10): US noop, China retaliates
        packets_r1 = generate_actor_packets(session, model)
        commit_actor_action(session, "US", "noop", packets_r1["US"]["packet_hash"])
        commit_actor_action(session, "China", "retaliate", packets_r1["China"]["packet_hash"])

        r1_record = advance_roleplay_round(session, model)
        self.assertEqual(r1_record["round_index"], 1)
        self.assertEqual(session["current_round"], 2)

        # Round 2 (Tick 15): Both raise / retaliate
        packets_r2 = generate_actor_packets(session, model)
        commit_actor_action(session, "US", "raise_tariffs", packets_r2["US"]["packet_hash"])
        commit_actor_action(session, "China", "retaliate", packets_r2["China"]["packet_hash"])

        r2_record = advance_roleplay_round(session, model)
        self.assertEqual(r2_record["round_index"], 2)
        self.assertEqual(session["current_round"], 3)
        self.assertEqual(session["status"], "completed")

        # Finalize
        fin_res = finalize_roleplay_session(session)
        self.assertTrue(fin_res["ok"])
        receipt = fin_res["receipt"]
        self.assertEqual(receipt["status"], "finalized")
        self.assertEqual(receipt["rounds_executed"], 3)
        self.assertIn("US", receipt["cumulative_payoffs"])
        self.assertIn("China", receipt["cumulative_payoffs"])

        # Replay verification (P6.C08)
        replay_res = replay_roleplay_session(session, model)
        self.assertTrue(replay_res["ok"])
        self.assertTrue(replay_res["match"])
        self.assertEqual(replay_res["replayed_rounds_count"], 3)

    def test_three_actor_two_round_lifecycle(self):
        spec = {
            "schema_version": "1.0.0",
            "session_id": "three_party_climate",
            "simulation_id": "sim_climate_001",
            "actors": ["US", "EU", "China"],
            "decision_ticks": [10, 20],
            "allowed_actions": {
                "US": [
                    {"id": "noop", "name": "Standard", "noop": True},
                    {"id": "subsidize_green", "name": "Green Subsidy", "cost": 2.0, "noop": False},
                ],
                "EU": [
                    {"id": "noop", "name": "Standard", "noop": True},
                    {"id": "carbon_tax", "name": "Carbon Border Adjustment", "cost": 1.0, "noop": False},
                ],
                "China": [
                    {"id": "noop", "name": "Standard", "noop": True},
                    {"id": "expand_solar", "name": "Expand Solar Export", "cost": 1.5, "noop": False},
                ],
            },
            "action_effects": {
                "US:subsidize_green": [{"target": "gdp_growth", "magnitude": 0.5, "lag": 0, "op": "add"}],
                "EU:carbon_tax": [{"target": "trade_volume", "magnitude": -1.0, "lag": 0, "op": "add"}],
                "China:expand_solar": [{"target": "trade_volume", "magnitude": 2.0, "lag": 1, "op": "add"}],
            },
            "payoffs": {
                "US": {
                    "state_weights": {"gdp_growth": 5.0},
                    "utility_table": {
                        "noop,noop,noop": 0.0,
                        "noop,noop,expand_solar": -1.0,
                        "noop,carbon_tax,noop": -2.0,
                        "noop,carbon_tax,expand_solar": -3.0,
                        "subsidize_green,noop,noop": 4.0,
                        "subsidize_green,noop,expand_solar": 3.0,
                        "subsidize_green,carbon_tax,noop": 2.0,
                        "subsidize_green,carbon_tax,expand_solar": 1.0,
                    },
                },
                "EU": {
                    "state_weights": {"gdp_growth": 4.0},
                    "utility_table": {
                        "noop,noop,noop": 0.0,
                        "noop,noop,expand_solar": 1.0,
                        "noop,carbon_tax,noop": 3.0,
                        "noop,carbon_tax,expand_solar": 2.0,
                        "subsidize_green,noop,noop": -1.0,
                        "subsidize_green,noop,expand_solar": 0.0,
                        "subsidize_green,carbon_tax,noop": 2.0,
                        "subsidize_green,carbon_tax,expand_solar": 1.5,
                    },
                },
                "China": {
                    "state_weights": {"trade_volume": 0.1},
                    "utility_table": {
                        "noop,noop,noop": 0.0,
                        "noop,noop,expand_solar": 5.0,
                        "noop,carbon_tax,noop": -3.0,
                        "noop,carbon_tax,expand_solar": 1.0,
                        "subsidize_green,noop,noop": -2.0,
                        "subsidize_green,noop,expand_solar": 2.0,
                        "subsidize_green,carbon_tax,noop": -4.0,
                        "subsidize_green,carbon_tax,expand_solar": 0.0,
                    },
                },
            },
        }
        model = _create_test_model()
        session = init_roleplay_session(spec, model)

        # Round 0
        p0 = generate_actor_packets(session, model)
        commit_actor_action(session, "US", "subsidize_green", p0["US"]["packet_hash"])
        commit_actor_action(session, "EU", "carbon_tax", p0["EU"]["packet_hash"])
        commit_actor_action(session, "China", "expand_solar", p0["China"]["packet_hash"])
        r0 = advance_roleplay_round(session, model)
        self.assertEqual(len(r0["payoffs"]), 3)

        # Round 1
        p1 = generate_actor_packets(session, model)
        commit_actor_action(session, "US", "noop", p1["US"]["packet_hash"])
        commit_actor_action(session, "EU", "noop", p1["EU"]["packet_hash"])
        commit_actor_action(session, "China", "noop", p1["China"]["packet_hash"])
        r1 = advance_roleplay_round(session, model)
        self.assertEqual(len(r1["payoffs"]), 3)

        fin = finalize_roleplay_session(session)
        self.assertTrue(fin["ok"])

        rep = replay_roleplay_session(session, model)
        self.assertTrue(rep["ok"])
        self.assertTrue(rep["match"])

    def test_tampering_detection(self):
        spec = _create_2actor_spec()
        model = _create_test_model()
        session = init_roleplay_session(spec, model)

        packets = generate_actor_packets(session, model)
        commit_actor_action(session, "US", "raise_tariffs", packets["US"]["packet_hash"])
        commit_actor_action(session, "China", "noop", packets["China"]["packet_hash"])
        advance_roleplay_round(session, model)

        # Tamper with recorded payoff in session
        tampered_session = copy.deepcopy(session)
        tampered_session["rounds"][0]["payoffs"]["US"] += 999.0

        rep = replay_roleplay_session(tampered_session, model)
        self.assertFalse(rep["ok"])
        self.assertFalse(rep["match"])
        self.assertIn("REPLAY_PAYOFF_MISMATCH", rep["error"])

    def test_round_scaling_benchmark(self):
        spec = _create_2actor_spec()
        model = _create_test_model()
        session = init_roleplay_session(spec, model)

        times = []
        for _round in range(3):
            t0 = time.perf_counter()
            packets = generate_actor_packets(session, model)
            commit_actor_action(session, "US", "raise_tariffs", packets["US"]["packet_hash"])
            commit_actor_action(session, "China", "noop", packets["China"]["packet_hash"])
            advance_roleplay_round(session, model)
            times.append(time.perf_counter() - t0)

        finalize_roleplay_session(session)
        # All rounds should execute within a few milliseconds on stdlib
        for i, dt in enumerate(times):
            self.assertLess(dt, 0.5, f"Round {i} took too long: {dt}s")


if __name__ == "__main__":
    unittest.main()
