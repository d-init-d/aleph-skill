"""Comprehensive v2.8.0 regression test suite covering P0 and P1 upgrades."""
from __future__ import annotations

import copy
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

from aleph import PACKAGE_VERSION, VALIDATOR_VERSION  # noqa: E402
from aleph.auto_causal import propose_model, review_model  # noqa: E402
from aleph.engine import (  # noqa: E402
    EngineConfig,
    _cluster_relative,
    _regime,
    compile_model,
    generate_numerical_execution_trace,
    inspect_execution_health,
    model_from_dict,
    run_deterministic,
    run_monte_carlo,
)
from aleph.execution_binding import build_trace_execution_binding  # noqa: E402
from aleph.finalize import finalize_workspace  # noqa: E402
from aleph.formula import evaluate_edge_gate, replay_trace_row  # noqa: E402
from aleph.interactive_roleplay import generate_actor_packets, init_roleplay_session  # noqa: E402
from aleph.interval_calibration import (  # noqa: E402
    calibrate_conformal_intervals,
    causal_conformal_quantile,
    load_evaluation_cases,
    parse_levels,
)
from aleph.io import write_json_atomic  # noqa: E402
from aleph.packs import PACK_NAMES, validate_all_packs  # noqa: E402
from aleph.quality import evaluate as evaluate_quality  # noqa: E402
from aleph.schema import CHECKPOINT_FIELDS  # noqa: E402
from aleph.trace_contract import validate_execution_trace_data  # noqa: E402
from aleph.validator import (  # noqa: E402
    manifest_model_contract_payload,
    validate_edges,
    validate_nodes,
    validate_numerical_artifacts,
    validate_workspace,
)
from repair_model_schema import (  # noqa: E402
    repair_edges,
    repair_interventions,
    repair_nodes,
)

EVAL_V270_LOCAL_RUNS = ROOT.parent / "_eval_aleph" / "eval_v270" / "runs_local"


def _run_cli(script: str, args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _setup_quickstart_workspace(td: str, *, slug: str = "v280-test") -> Path:
    res = _run_cli(
        "quickstart_quant.py",
        [
            "--slug",
            slug,
            "--change-point",
            "Policy rate shock",
            "--horizon",
            "P6M",
            "--ticks",
            "6",
            "--out-dir",
            td,
            "--offline-provisional",
        ],
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return Path(json.loads(res.stdout)["workspace"])


class V280UpgradeRegressionTests(unittest.TestCase):
    def test_version_constants_bumped_to_2_8_0(self) -> None:
        parsed_pkg = tuple(int(part) for part in PACKAGE_VERSION.split("."))
        self.assertGreaterEqual(parsed_pkg, (2, 8, 0))
        self.assertEqual(VALIDATOR_VERSION, PACKAGE_VERSION)

    def test_manifest_model_contract_and_finalize_complete_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _setup_quickstart_workspace(td, slug="manifest-contract")
            mf_path = ws / "simulation-manifest.json"
            mf = json.loads(mf_path.read_text(encoding="utf-8"))

            # Non-contract lifecycle updates must NOT trigger STALE_ARTIFACT after compilation.
            mf["status"] = "initialized"
            mf.setdefault("execution", {})["checkpoints"] = {k: False for k in CHECKPOINT_FIELDS}
            mf["artifact_paths"]["validation_report"] = "validation-report.json"
            mf.setdefault("assumptions", []).append(
                {"id": "assumption:v280-test", "statement": "Baseline neutral premise."}
            )
            contract_before = manifest_model_contract_payload(mf)
            write_json_atomic(mf_path, mf)
            self.assertEqual(manifest_model_contract_payload(mf), contract_before)

            num_res = validate_numerical_artifacts(ws, mf)
            stale_mf_issues = [
                iss
                for iss in num_res.issues
                if iss.code == "STALE_ARTIFACT"
                and (iss.artifact == "simulation-manifest.json" or "simulation-manifest.json" in str(iss.pointer))
            ]
            self.assertEqual(stale_mf_issues, [], num_res.issues)

            # `--complete` atomically marks manifest status="completed" and checkpoints True.
            fin_cli = _run_cli("finalize_simulation.py", ["--workspace", str(ws), "--complete", "--json"])
            self.assertEqual(fin_cli.returncode, 0, fin_cli.stdout + fin_cli.stderr)
            fin_payload = json.loads(fin_cli.stdout)
            self.assertTrue(fin_payload["ok"], fin_payload)
            self.assertTrue(fin_payload["finalized"], fin_payload)
            self.assertEqual(fin_payload["validation"]["status"], "pass")

            mf_after = json.loads(mf_path.read_text(encoding="utf-8"))
            self.assertEqual(mf_after["status"], "completed")
            ck = mf_after["execution"]["checkpoints"]
            for field in CHECKPOINT_FIELDS:
                self.assertTrue(ck.get(field), f"Expected checkpoint {field} True: {ck}")

            # Failed `--complete` must roll back manifest without leaving dirty completed state.
            (ws / "REPORT.md").unlink()
            mf_roll = json.loads(mf_path.read_text(encoding="utf-8"))
            mf_roll["status"] = "initialized"
            write_json_atomic(mf_path, mf_roll)
            failed_fin = finalize_workspace(ws, require_report=True, complete=True)
            self.assertFalse(failed_fin["ok"], failed_fin)
            self.assertFalse(failed_fin["finalized"], failed_fin)
            self.assertEqual(failed_fin["validation"]["status"], "fail")
            self.assertEqual(
                json.loads(mf_path.read_text(encoding="utf-8"))["status"],
                "initialized",
            )

            # Tampering a contract field (`formula_version` or `temporal_frame`) MUST still fail STALE_ARTIFACT.
            mf_tampered = json.loads(mf_path.read_text(encoding="utf-8"))
            mf_tampered["temporal_frame"]["simulation_end"] = "2099-01-01"
            write_json_atomic(mf_path, mf_tampered)
            tamper_res = validate_numerical_artifacts(ws, mf_tampered)
            self.assertTrue(
                any(
                    iss.code == "STALE_ARTIFACT"
                    and (iss.artifact == "simulation-manifest.json" or "simulation-manifest.json" in str(iss.pointer))
                    for iss in tamper_res.issues
                ),
                tamper_res.issues,
            )

    def test_initial_value_vs_baseline_dynamics_bounds_and_roleplay_tick0(self) -> None:
        nodes = [
            {
                "id": "factor:src",
                "type": "factor",
                "role": "exogenous",
                "scale": "level",
                "baseline": 100.0,
                "initial_value": 110.0,
                "bounds": [90.0, 120.0],
            },
            {
                "id": "factor:dst",
                "type": "factor",
                "role": "endogenous",
                "scale": "level",
                "baseline": 50.0,
                "initial_value": 50.0,
                "bounds": [0.0, 100.0],
            },
        ]
        edges = [
            {
                "id": "causal:src-dst",
                "from": "factor:src",
                "to": "factor:dst",
                "relation": "increases",
                "sign": 1,
                "base_strength": 0.5,
                "lag_ticks": 0,
                "transform": "linear",
            }
        ]
        model = compile_model(nodes, edges, [], formula_version="2.2.0")
        cfg = EngineConfig(mode="deterministic")
        res = run_deterministic(model, cfg, ticks=4)
        self.assertTrue(res["ok"], res)
        # tick 0 starts from initial_value (110.0), so deviation (+10.0) propagates (+5.0) to dst -> 55.0
        self.assertAlmostEqual(res["history"][0]["factor:src"], 110.0)
        self.assertAlmostEqual(res["history"][0]["factor:dst"], 55.0)
        # initial_value != baseline without interventions should not flag ZERO_INTERVENTION_DRIFT
        health = inspect_execution_health(model, cfg, ticks=4, result=res)
        self.assertFalse(any(w.get("code") == "ZERO_INTERVENTION_DRIFT" for w in health), health)

        # Out-of-bounds initial_value must be rejected by validator
        bad_nodes = copy.deepcopy(nodes)
        bad_nodes[0]["initial_value"] = 135.0
        vn, _ = validate_nodes(
            bad_nodes,
            set(),
            {"schema_version": "2.1.0", "formula_version": "2.2.0", "simulation_mode": "deterministic"},
        )
        self.assertTrue(any("initial_value" in str(iss.pointer) for iss in vn.issues), vn.issues)

        # Interactive roleplay decision_tick == 0 applies tick=0 interventions on top of initial_value
        model_iv = compile_model(
            nodes,
            edges,
            [{"id": "iv:tick0", "target": "factor:src", "op": "add", "value": 5.0, "start_tick": 0, "end_tick": 2}],
            formula_version="2.2.0",
        )
        # Serialize & deserialize via model_from_dict roundtrip to confirm initial_value survival
        from aleph.engine import model_payload

        reloaded = model_from_dict(model_payload(model_iv))
        self.assertAlmostEqual(reloaded.variables["factor:src"].initial_value or 0.0, 110.0)

        spec = {
            "schema_version": "1.0.0",
            "session_id": "tick0-init-test",
            "simulation_id": "sim-tick0",
            "actors": ["ActorA", "ActorB"],
            "decision_ticks": [0],
            "visibility": {
                "ActorA": {"visible_nodes": ["factor:src", "factor:dst"]},
                "ActorB": {"visible_nodes": ["factor:src", "factor:dst"]},
            },
            "allowed_actions": {
                "ActorA": [
                    {"id": "noop", "name": "Hold", "cost": 0.0, "noop": True},
                    {"id": "act", "name": "Act", "cost": 1.0, "noop": False},
                ],
                "ActorB": [
                    {"id": "hold", "name": "Hold", "cost": 0.0, "noop": True},
                    {"id": "counter", "name": "Counter", "cost": 1.0, "noop": False},
                ],
            },
            "action_effects": {
                "ActorA:act": [{"target": "factor:src", "magnitude": 2.0, "lag": 0, "op": "add"}],
                "ActorB:counter": [{"target": "factor:src", "magnitude": -1.0, "lag": 0, "op": "add"}],
            },
            "payoffs": {
                "ActorA": {
                    "state_weights": {"factor:dst": 1.0},
                    "utility_table": {
                        "noop,hold": 0.0,
                        "noop,counter": -0.5,
                        "act,hold": 1.0,
                        "act,counter": 0.5,
                    },
                    "attribution": "Unit test fixture",
                },
                "ActorB": {
                    "state_weights": {"factor:dst": -1.0},
                    "utility_table": {
                        "noop,hold": 0.0,
                        "noop,counter": 0.5,
                        "act,hold": -1.0,
                        "act,counter": -0.5,
                    },
                    "attribution": "Unit test fixture",
                },
            },
        }
        session = init_roleplay_session(spec, reloaded, config=cfg)
        packets = generate_actor_packets(session, reloaded, cfg)
        vis_state = packets["ActorA"]["observed_state"]
        # At decision_tick == 0, tick=0 scheduled intervention (+5.0) applies directly on top of initial_value (110.0) -> 115.0
        self.assertAlmostEqual(vis_state["factor:src"], 115.0)
        self.assertAlmostEqual(vis_state["factor:dst"], 50.0)

    def test_edge_gate_deterministic_scc_trace_binding_and_validation(self) -> None:
        self.assertEqual(
            evaluate_edge_gate({"op": "all", "conditions": [{"node": "factor:a", "op": "gt", "value": 5.0}]}, {"factor:a": 5.0}),
            0.0,
        )
        self.assertEqual(
            evaluate_edge_gate({"op": "all", "conditions": [{"node": "factor:a", "op": "gte", "value": 5.0}]}, {"factor:a": 5.0}),
            1.0,
        )
        self.assertEqual(
            evaluate_edge_gate({"op": "veto", "conditions": [{"node": "factor:a", "op": "gt", "value": 5.0}]}, {"factor:a": 6.0}),
            0.0,
        )

        # Include a gated cycle to ensure _is_affine_eligible declines affine shortcut cleanly
        nodes = [
            {"id": "factor:trigger", "role": "exogenous", "scale": "level", "baseline": 0.0},
            {"id": "factor:target", "role": "endogenous", "scale": "level", "baseline": 10.0},
            {"id": "factor:loop", "role": "endogenous", "scale": "level", "baseline": 10.0},
        ]
        edges = [
            {
                "id": "causal:gated-in",
                "from": "factor:trigger",
                "to": "factor:target",
                "relation": "increases",
                "sign": 1,
                "base_strength": 0.5,
                "lag_ticks": 0,
                "transform": "linear",
                "gate": {"op": "all", "conditions": [{"node": "factor:trigger", "op": "gte", "value": 4.0}]},
            },
            {
                "id": "causal:cycle-a",
                "from": "factor:target",
                "to": "factor:loop",
                "relation": "increases",
                "sign": 1,
                "base_strength": 0.2,
                "lag_ticks": 0,
                "transform": "linear",
                "gate": {"op": "veto", "conditions": [{"node": "factor:trigger", "op": "lt", "value": 0.0}]},
            },
            {
                "id": "causal:cycle-b",
                "from": "factor:loop",
                "to": "factor:target",
                "relation": "increases",
                "sign": 1,
                "base_strength": 0.1,
                "lag_ticks": 0,
                "transform": "linear",
            },
        ]
        ivs = [{"id": "iv:step", "target": "factor:trigger", "op": "set", "value": 5.0, "start_tick": 2, "end_tick": 4}]
        model = compile_model(nodes, edges, ivs, formula_version="2.2.0")
        cfg = EngineConfig(mode="deterministic", scc_solver="linear_auto")
        res = run_deterministic(model, cfg, ticks=4)
        self.assertTrue(res["ok"], res)
        # Before tick 2: trigger=0 < 4 -> gate closed -> target stays at 10.0
        self.assertAlmostEqual(res["history"][0]["factor:target"], 10.0)
        self.assertAlmostEqual(res["history"][1]["factor:target"], 10.0)
        # At tick 2: trigger=5 >= 4 -> gate opens -> target moves above 10.0
        self.assertGreater(res["history"][2]["factor:target"], 12.0)

        trace = generate_numerical_execution_trace(model, cfg, ticks=4, run_id=0)
        steps = trace["steps"]
        gated_rows_t0 = [r for r in steps if r["edge_id"] == "causal:gated-in" and r["tick"] == 0]
        gated_rows_t2 = [r for r in steps if r["edge_id"] == "causal:gated-in" and r["tick"] == 2]
        self.assertEqual(gated_rows_t0[0]["gate_multiplier"], 0.0)
        self.assertEqual(gated_rows_t0[0]["state_transitions"]["delta"], 0.0)
        self.assertEqual(gated_rows_t2[0]["gate_multiplier"], 1.0)
        self.assertGreater(gated_rows_t2[0]["state_transitions"]["delta"], 0.0)

        binding, issues = build_trace_execution_binding(
            trace,
            model,
            cfg,
            ticks=4,
            result=res,
            manifest={"temporal_frame": {"simulation_start": "2026-01-01"}},
        )
        self.assertEqual(issues, [], issues)
        self.assertTrue(binding["binding_hash"])

        # Tampering gate_multiplier must fail both replay_trace_row and execution binding
        bad_trace = copy.deepcopy(trace)
        for row in bad_trace["steps"]:
            if row["edge_id"] == "causal:gated-in" and row["tick"] == 2:
                row["gate_multiplier"] = 0.0
        _, tampered_issues = build_trace_execution_binding(
            bad_trace,
            model,
            cfg,
            ticks=4,
            result=res,
            manifest={"temporal_frame": {"simulation_start": "2026-01-01"}},
        )
        self.assertTrue(
            any(iss.code == "TRACE_EXECUTION_BINDING" and "gate_multiplier" in str(iss.pointer) for iss in tampered_issues),
            tampered_issues,
        )
        bad_prop_row = {
            "step": 1,
            "edge_id": "causal:gated-in",
            "from": "factor:trigger",
            "to": "factor:target",
            "tick": 2,
            "source_tick": 2,
            "formula_version": "2.2.0",
            "sampled_strength": 0.5,
            "source_state": 5.0,
            "source_baseline": 0.0,
            "input_mode": "deviation",
            "input_effect": 5.0,
            "output_effect": 2.5,
            "gate_multiplier": 0.0,
        }
        replay_issues = replay_trace_row(
            bad_prop_row,
            edges[0],
            {n["id"] for n in nodes},
            {e["id"] for e in edges},
            pointer="$.propagation_trace[0]",
        )
        self.assertTrue(any(iss.code == "TRACE_FORMULA_MISMATCH" for iss in replay_issues), replay_issues)

        # validate_edges rejects malformed gate (empty conditions or unknown node)
        bad_edges = copy.deepcopy(edges)
        bad_edges[0]["gate"] = {"op": "all", "conditions": []}
        ve, _ = validate_edges(
            bad_edges,
            {n["id"] for n in nodes},
            set(),
            manifest={"schema_version": "2.1.0", "formula_version": "2.2.0", "simulation_mode": "deterministic"},
        )
        self.assertTrue(any("gate" in str(iss.pointer) for iss in ve.issues), ve.issues)

    def test_monte_carlo_negative_log2_regime_and_branch_cap_consolidation(self) -> None:
        self.assertEqual(_regime(0.0, 1.0), "0")
        self.assertEqual(_regime(0.4, 1.0), "+sub_mid")
        self.assertEqual(_regime(0.1, 1.0), "+sub_low")
        self.assertEqual(_regime(0.001, 1.0), "+sub_micro")
        self.assertEqual(_regime(-0.4, 1.0), "-sub_mid")
        self.assertNotIn("-1", _regime(0.4, 1.0))

        fake = [(idx, {"factor:x": float(idx)}, f"sig_{idx}") for idx in range(25)]
        clustered = _cluster_relative(fake, 25, max_branches=12)
        self.assertEqual(len(clustered), 12)
        self.assertTrue(clustered[-1]["signature"].startswith("tail_consolidated:"), clustered[-1])
        total_weight = sum(b["relative_weight"] for b in clustered)
        self.assertAlmostEqual(total_weight, 1.0, places=9)

        # End-to-end Monte Carlo run with heavy stochastic spread
        nodes = [
            {"id": "factor:a", "role": "exogenous", "scale": "level", "baseline": 10.0},
            {"id": "factor:b", "role": "endogenous", "scale": "level", "baseline": 1.0},
        ]
        edges = [
            {
                "id": "causal:ab",
                "from": "factor:a",
                "to": "factor:b",
                "relation": "increases",
                "sign": 1,
                "base_strength": 1.0,
                "lag_ticks": 0,
                "transform": "linear",
                "effect_distribution": {"distribution": "uniform", "min": 0.01, "max": 2.0},
            }
        ]
        ivs = [{"id": "iv:a", "target": "factor:a", "op": "add", "value": 8.0, "start_tick": 0, "end_tick": 3}]
        mc_model = compile_model(nodes, edges, ivs, formula_version="2.2.0")
        mc_res = run_monte_carlo(mc_model, EngineConfig(mode="monte_carlo", min_runs=60, max_runs=60, seed="mc-cap"), ticks=3)
        self.assertTrue(mc_res["ok"], mc_res)
        summary = mc_res["summary"]
        branches = summary["branches"]
        self.assertLessEqual(len(branches), 12)
        valid_mass = sum(float(b["relative_weight"]) for b in branches)
        self.assertAlmostEqual(valid_mass, float(summary["valid_mass"]), places=6)
        self.assertAlmostEqual(valid_mass + float(summary["unresolved_mass"]), 1.0, places=6)

    def test_interval_adaptation_waits_for_outcome_publication(self) -> None:
        cases = [
            {
                "case_id": f"case-{i}",
                "forecast_origin": f"2025-01-{day:02}T00:00:00Z",
                "official_release_date": f"2025-01-{release:02}T00:00:00Z",
                "point_prediction": 100.0,
                "actual_value": actual,
            }
            for i, (day, release, actual) in enumerate([
                (1, 2, 101.0),
                (2, 3, 102.0),
                (3, 4, 103.0),
                (4, 5, 104.0),
                (5, 6, 105.0),
                (6, 7, 106.0),
                (7, 20, 100.0),
                (8, 9, 100.0),
            ])
        ]
        modified = copy.deepcopy(cases)
        modified[-2]["actual_value"] = 200.0
        args = {"levels": [0.5, 0.50001], "aci_gamma": 0.5}
        original = calibrate_conformal_intervals(cases, **args)
        delayed = calibrate_conformal_intervals(modified, **args)
        self.assertEqual(original["intervals"][-1]["levels"], delayed["intervals"][-1]["levels"])
        self.assertEqual(original["intervals"][-1]["calibration_count"], 6)
        self.assertEqual(set(original["intervals"][-1]["levels"]), {"0.50", "0.50001"})
        self.assertEqual(causal_conformal_quantile([2.0, 1.0], 1.0), (2.0, True))
        self.assertEqual(causal_conformal_quantile([2.0, 1.0], 0.0), (0.0, False))
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            calibrate_conformal_intervals([cases[0], cases[0]])
        with self.assertRaisesRegex(ValueError, "post-date"):
            calibrate_conformal_intervals([dict(cases[0], official_release_date=cases[0]["forecast_origin"])])
        for invalid in ("", "0,1", "nan", [True]):
            with self.subTest(levels=invalid), self.assertRaises(ValueError):
                parse_levels(invalid)
        for options in ({"window": 0}, {"aci_gamma": 1.0}, {"min_history": False}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                calibrate_conformal_intervals(cases, **options)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "missing hindcast"):
                load_evaluation_cases(root)
            (root / "hindcast").mkdir()
            with self.assertRaisesRegex(ValueError, "no hindcast"):
                load_evaluation_cases(root)
            write_json_atomic(root / "hindcast" / "case-001.json", [])
            with self.assertRaisesRegex(ValueError, "invalid hindcast"):
                load_evaluation_cases(root)

    def test_offline_batch_reports_estimated_dates_and_validates_output_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "output"
            source = ROOT / "packs" / "economics" / "data" / "us_macro_2024_2026.json"
            args = ["--data", str(source), "--out-dir", str(destination), "--series"]
            for series in ("../outside", "brent,BRENT", "inputs", "CON"):
                result = _run_cli("run_offline_backtest.py", [*args, series])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("portable output names", result.stdout)
                self.assertFalse(destination.exists())
            result = _run_cli("run_offline_backtest.py", [*args, "brent,cpi_u,fedfunds"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads(result.stdout)
            for series in summary["series"].values():
                self.assertTrue(series["execution_verified"])
                self.assertEqual(series["release_date_basis"], "includes_estimates")

    def test_gate_conditions_fail_closed_and_preserve_threshold_boundaries(self) -> None:
        first = {"node": "factor:first", "op": "lte", "value": 2}
        second = {"node": "factor:second", "op": "lt", "value": 5}
        state = {"factor:first": 2.0, "factor:second": 5.0}
        for operation, expected in (("all", 0.0), ("any", 1.0), ("veto", 0.0)):
            with self.subTest(operation=operation):
                self.assertEqual(evaluate_edge_gate({"op": operation, "conditions": [first, second]}, state), expected)
        self.assertEqual(evaluate_edge_gate({"op": "any", "conditions": [second]}, state), 0.0)
        self.assertEqual(evaluate_edge_gate({"op": "veto", "conditions": [second]}, state), 1.0)
        for invalid in (
            [],
            {"op": "unknown", "conditions": [first]},
            {"op": "all", "conditions": []},
            {"op": "all", "conditions": ["invalid"]},
            {"op": "all", "conditions": [dict(first, op="=")]},
            {"op": "all", "conditions": [dict(first, node="factor:missing")]},
            {"op": "all", "conditions": [dict(first, value=float("inf"))]},
        ):
            with self.subTest(gate=invalid), self.assertRaises(ValueError):
                evaluate_edge_gate(invalid, state)
        for invalid_state in (True, "2", float("nan")):
            with self.subTest(state=invalid_state), self.assertRaises(ValueError):
                evaluate_edge_gate({"op": "all", "conditions": [first]}, {"factor:first": invalid_state})

    def test_trace_contract_rejects_invalid_metadata_and_nested_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = _setup_quickstart_workspace(directory, slug="trace-v280")
            trace = json.loads((workspace / "execution-trace.json").read_text())
        self.assertTrue(trace["steps"])
        self.assertEqual(validate_execution_trace_data(trace), [])

        top_level_cases = {
            "schema_version": ([], "version"),
            "model_id": ([], ""),
            "formula_version": ([], "99.0.0"),
            "generated_by": ([], ""),
            "generation_mode": ([], "unknown"),
            "execution_timestamp": (None, "2025-01-01", "invalidZ"),
            "seed": (True,),
            "run_id": ([], "no-prefix"),
            "parameters_hash": ([], "not-a-hash"),
            "total_ticks": (True, 0),
            "total_samples": (True, 0),
            "invalid_mass": ("0", -1, 2),
            "replay_hash": ([], "invalid", "f" * 64),
            "final_state_vector": ([], {"factor:x": "0"}),
            "steps": (None,),
            "unexpected": (True,),
        }
        for key, values in top_level_cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    issues = validate_execution_trace_data(dict(trace, **{key: value}))
                    self.assertTrue(any(item.pointer.startswith(f"/{key}") for item in issues), issues)
        altered_config = {"parameters_hash": "sha256:" + "f" * 64}
        self.assertIn("PARAMETERS_HASH_MISMATCH", {
            item.code for item in validate_execution_trace_data(trace, config=altered_config)
        })

        nested_cases = {
            "step": (True, 0),
            "sample_id": (True, -1, trace["total_samples"]),
            "tick": ("0", -1),
            "delivery_tick": (-1,),
            "edge_id": ([], "no-prefix", "edge:unknown"),
            "node_id": ([], "", "factor:unknown"),
            "drawn_strength": (True,),
            "source_state": (None, "0"),
            "target_state_before": (None, "0"),
            "target_state_after": (None, "0"),
            "input_mode": (None, "unknown"),
            "input_effect": (None,),
            "source_baseline": (True,),
            "sampled_parameters": (None, {"bad": [0]}),
            "stock_flow_integrations": (
                None,
                {"stock_variable": False, "retention_factor": 2, "inflow_rate": "0"},
                {"retention_factor": True},
            ),
            "transform_applied": (
                None,
                {"transform": True},
                {"transform": "unknown"},
                {"mode": False},
                {"mode": "unknown", "threshold_active_before": 1, "threshold_active_after": 0},
            ),
            "state_transitions": (
                None,
                {"delta": True, "previous_value": True, "new_value": True, "mechanism": False},
            ),
            "evidence_refs": (None, ["bad-ref", 1]),
            "hash_chain": (None, "f" * 64),
            "unknown": (True,),
        }
        first = trace["steps"][0]
        for key, values in nested_cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    altered = dict(trace, steps=[dict(first, **{key: value})])
                    issues = validate_execution_trace_data(altered, node_ids=set(), edge_by_id={})
                    self.assertTrue(any(item.pointer.startswith(f"/steps/0/{key}") for item in issues), issues)
        for missing in ("model_id", "total_ticks"):
            altered = {key: value for key, value in trace.items() if key != missing}
            self.assertIn("MISSING_FIELD", {item.code for item in validate_execution_trace_data(altered)})
        self.assertIn("MISSING_FIELD", {item.code for item in validate_execution_trace_data(dict(trace, steps=[{}]))})
        self.assertIn("TYPE", {item.code for item in validate_execution_trace_data(dict(trace, steps=[None]))})

    def test_published_monte_carlo_hashes_remain_replayable(self) -> None:
        # Reference hashes generated with the published v2.6.0 engine.
        published_hashes = {
            "2.0.0": "0e2702ad1b9c5308166955fd3179299c64d31a753daac07b2e29b465a63b357c",
            "2.1.0": "3a9233a9f2efc71bb5d2aacf680bfeb7972232a5c76730339ec4fa484e77da29",
        }
        nodes = [
            {"id": "factor:a", "role": "exogenous", "scale": "level", "baseline": 1.0},
            {"id": "factor:b", "role": "endogenous", "scale": "level", "baseline": 1.0},
        ]
        edges = [{
            "id": "causal:ab",
            "from": "factor:a",
            "to": "factor:b",
            "sign": 1,
            "base_strength": 0.3,
            "lag_ticks": 0,
            "transform": "linear",
            "effect_distribution": {"distribution": "uniform", "min": 0.01, "max": 0.4},
        }]
        config = EngineConfig(mode="monte_carlo", min_runs=30, max_runs=30, seed="legacy-replay")
        for formula, expected_hash in published_hashes.items():
            with self.subTest(formula=formula):
                model = compile_model(nodes, edges, [], formula_version=formula)
                result = run_monte_carlo(model, config, ticks=3)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["summary"]["canonical_hash"], expected_hash)

    def test_quickstart_keeps_existing_workspaces_and_honors_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = [
                "--slug", "upgrade-demo",
                "--change-point", "Example policy change",
                "--time", "2025-01-02",
                "--observation-cutoff", "2025-01-01",
                "--out-dir", directory,
                "--offline-provisional",
            ]
            invalid = _run_cli("quickstart_quant.py", [*args, "--timestep", "P0D"])
            self.assertNotEqual(invalid.returncode, 0)
            self.assertFalse((Path(directory) / "upgrade-demo").exists())

            result = _run_cli("quickstart_quant.py", args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["assurance_tier"], "limited")
            manifest = Path(output["workspace"]) / "simulation-manifest.json"
            original = manifest.read_bytes()
            self.assertEqual(json.loads(original)["temporal_frame"]["observation_cutoff"], "2025-01-01")

            repeated = _run_cli("quickstart_quant.py", args)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(manifest.read_bytes(), original)

    def test_repair_model_schema_llm_quirks_and_eval_v270_runs_local(self) -> None:
        raw_nodes = {
            "nodes": [
                {
                    "node_id": "factor:cpi_u",
                    "label": "CPI Index",
                    "kind": "auxiliary",
                    "scale": "dimensionless",
                    "baseline": 314.0,
                    "initial_state": 315.5,
                    "unit": "index",
                }
            ]
        }
        ns, n_repairs = repair_nodes(raw_nodes)
        self.assertEqual(ns[0]["id"], "factor:cpi_u")
        self.assertEqual(ns[0]["name"], "CPI Index")
        self.assertEqual(ns[0]["type"], "factor")
        self.assertEqual(ns[0]["scale"], "level")
        self.assertEqual(ns[0]["baseline"], 314.0)
        self.assertEqual(ns[0]["initial_value"], 315.5)
        self.assertIn("nodes:unwrapped_object_to_list", n_repairs)

        raw_edges = {
            "edges": [
                {
                    "edge_id": "edge:1",
                    "source": "factor:cpi_u",
                    "target": "factor:cpi_u",
                    "polarity": "negative",
                    "strength": -0.45,
                    "scale": "level",
                    "transform": "saturating_tanh",
                    "parameters": {"steepness": 2.0},
                    "weight_distribution": {"distribution": "fixed", "value": 0.45},
                    "gate": {"op": "and", "conditions": [{"node": "factor:cpi_u", "op": ">=", "value": "300"}]},
                }
            ]
        }
        es, e_repairs = repair_edges(raw_edges)
        self.assertEqual(es[0]["id"], "edge:1")
        self.assertEqual(es[0]["sign"], -1)
        self.assertEqual(es[0]["base_strength"], 0.45)
        self.assertEqual(es[0]["transform"], "linear")
        self.assertEqual(es[0]["saturation"], 1.0)
        self.assertEqual(es[0]["transform_parameters"], {"steepness": 2.0})
        self.assertEqual(es[0]["effect_distribution"], {"distribution": "fixed", "value": 0.45})
        self.assertEqual(es[0]["gate"], {"op": "all", "conditions": [{"node": "factor:cpi_u", "op": "gte", "value": 300.0}]})
        self.assertNotIn("scale", es[0])
        self.assertNotIn("polarity", es[0])

        ivs, _ = repair_interventions(
            [
                {
                    "intervention_id": "int:1",
                    "target_node_id": "factor:cpi_u",
                    "magnitude": 1.5,
                    "mode": "step",
                    "start_tick": 0,
                    "end_tick": None,
                    "rationale": "Exogenous step shock",
                }
            ],
            node_scales={"factor:cpi_u": "level"},
        )
        self.assertEqual(ivs[0]["id"], "int:1")
        self.assertEqual(ivs[0]["target"], "factor:cpi_u")
        self.assertEqual(ivs[0]["op"], "add")
        self.assertEqual(ivs[0]["value"], 1.5)
        self.assertNotIn("rationale", ivs[0])

        # Also validate against local eval runs when co-located in repo parent workspace.
        if EVAL_V270_LOCAL_RUNS.is_dir():
            files = sorted(EVAL_V270_LOCAL_RUNS.glob("*/parsed_output.json"))
            self.assertGreaterEqual(len(files), 9)
            compiled_ok = 0
            for path in files:
                doc = json.loads(path.read_text(encoding="utf-8"))
                r_nodes, _ = repair_nodes(doc["nodes"])
                r_edges, _ = repair_edges(doc["edges"])
                n_scales = {str(n.get("id")): str(n.get("scale", "level")) for n in r_nodes}
                r_ivs, _ = repair_interventions(doc["interventions"], node_scales=n_scales)
                try:
                    m = compile_model(r_nodes, r_edges, r_ivs, formula_version="2.2.0")
                    out = run_deterministic(m, EngineConfig(timestep=1.0), ticks=12)
                    if out.get("ok"):
                        compiled_ok += 1
                except ValueError:
                    pass
            # A2_qwen25_7b intentionally has an undeclared target node; remaining 8/9 compile & run.
            self.assertGreaterEqual(compiled_ok, 8)

    def test_quality_downstream_frozen_pct_units_vi_fidelity_actors_and_source_tier(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _setup_quickstart_workspace(td, slug="quality-v280")
            # 1) Dimensionless node must NOT false-positive NARRATIVE_UNIT_MISMATCH on "%".
            nodes = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            for n in nodes:
                n["unit"] = ""
            write_json_atomic(ws / "nodes.json", nodes)
            report_text = (ws / "REPORT.md").read_text(encoding="utf-8")
            (ws / "REPORT.md").write_text(
                report_text + "\n\nChỉ số phản hồi điều chỉnh 12% trong kỳ mô phỏng.\n",
                encoding="utf-8",
            )
            ev_pct = evaluate_quality(ws)
            self.assertFalse(
                any("[NARRATIVE_UNIT_MISMATCH]" in w for w in ev_pct["warnings"]),
                ev_pct["warnings"],
            )

            # 2) Vietnamese narrative fidelity mismatch (report claims surge/specific number absent from model).
            (ws / "REPORT.md").write_text(
                report_text + "\n\nBiến factor:target_outcome tăng mạnh và đạt 9999.0.\n",
                encoding="utf-8",
            )
            ev_vi = evaluate_quality(ws)
            self.assertTrue(
                any("[MODEL_NARRATIVE_FIDELITY]" in w for w in ev_vi["warnings"]),
                ev_vi["warnings"],
            )

            # 2b) Also test flat Vietnamese claim ("tăng 45%") when simulated trajectory is completely flat at baseline.
            run_doc = json.loads((ws / "simulation-run.json").read_text(encoding="utf-8"))
            orig_run_doc = copy.deepcopy(run_doc)
            flat_state = {n["id"]: float(n.get("baseline", 0.0)) for n in nodes}
            run_doc.setdefault("result", {})["history"] = [dict(flat_state), dict(flat_state)]
            run_doc["result"].setdefault("payload", {})["final_state"] = dict(flat_state)
            write_json_atomic(ws / "simulation-run.json", run_doc)
            (ws / "REPORT.md").write_text(
                "# Báo cáo mô phỏng\n\nBiến factor:target_outcome tăng 45% trong kỳ mô phỏng.\n",
                encoding="utf-8",
            )
            ev_vi_flat = evaluate_quality(ws)
            self.assertTrue(
                any("[MODEL_NARRATIVE_FIDELITY]" in w for w in ev_vi_flat["warnings"]),
                ev_vi_flat["warnings"],
            )
            write_json_atomic(ws / "simulation-run.json", orig_run_doc)

            # 3) user-provided rows must NOT count toward high_quality_direct_sources.
            (ws / "REPORT.md").write_text(report_text, encoding="utf-8")
            val_before = validate_workspace(ws)
            ev_before = val_before["check_results"]["evidence"]["metrics"]
            self.assertGreaterEqual(ev_before.get("high_quality_direct_sources", 0), 1, ev_before)
            ev_csv_path = ws / "evidence-map.csv"
            ev_csv_text = ev_csv_path.read_text(encoding="utf-8")
            ev_csv_path.write_text(
                ev_csv_text.replace("authoritative-secondary", "user-provided").replace(",primary,", ",user-provided,"),
                encoding="utf-8",
            )
            val_user = validate_workspace(ws)
            ev_counts = val_user["check_results"]["evidence"]["metrics"]
            self.assertEqual(ev_counts.get("high_quality_direct_sources", 0), 0, ev_counts)

            # 4) Strategic human/state entities without material actors -> MISSING_MATERIAL_ACTORS.
            nodes_ent = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            nodes_ent.append(
                {
                    **nodes_ent[0],
                    "id": "entity:central_bank_governor",
                    "type": "entity",
                    "name": "Central Bank Governor",
                    "details": {"entity_type": "leader"},
                }
            )
            write_json_atomic(ws / "nodes.json", nodes_ent)
            write_json_atomic(ws / "actors.json", [])
            ev_actor = evaluate_quality(ws)
            self.assertTrue(
                any("[MISSING_MATERIAL_ACTORS]" in w for w in ev_actor["warnings"]),
                ev_actor["warnings"],
            )

            # 5) Pure downstream non-intervened ungated target stuck at baseline while intervened intermediate target moved -> ENGINE_FROZEN.
            ws_df = _setup_quickstart_workspace(td, slug="quality-downstream-frozen")
            if (ws_df / "simulation-model.json").exists():
                (ws_df / "simulation-model.json").unlink()
            write_json_atomic(
                ws_df / "nodes.json",
                [
                    {"id": "factor:root", "type": "factor", "role": "exogenous", "scale": "level", "baseline": 10.0},
                    {"id": "factor:mid", "type": "factor", "role": "endogenous", "scale": "level", "baseline": 20.0},
                    {"id": "factor:down", "type": "factor", "role": "endogenous", "scale": "level", "baseline": 50.0},
                ],
            )
            write_json_atomic(
                ws_df / "edges.json",
                [
                    {
                        "id": "causal:root-mid",
                        "from": "factor:root",
                        "to": "factor:mid",
                        "relation": "increases",
                        "sign": 1,
                        "base_strength": 0.5,
                        "lag_ticks": 0,
                        "transform": "linear",
                    },
                    {
                        "id": "causal:mid-down",
                        "from": "factor:mid",
                        "to": "factor:down",
                        "relation": "increases",
                        "sign": 1,
                        "base_strength": 0.5,
                        "lag_ticks": 0,
                        "transform": "linear",
                    },
                ],
            )
            ivs_df = [{"id": "iv:mid", "target": "factor:mid", "op": "add", "value": 5.0, "start_tick": 0, "end_tick": 3}]
            write_json_atomic(ws_df / "interventions.json", ivs_df)
            df_run = json.loads((ws_df / "simulation-run.json").read_text(encoding="utf-8"))
            df_run["ticks"] = 3
            df_run["interventions"] = ivs_df
            df_run["warnings"] = []
            df_run["result"] = {
                "ok": True,
                "history": [
                    {"factor:root": 10.0, "factor:mid": 25.0, "factor:down": 50.0},
                    {"factor:root": 10.0, "factor:mid": 25.0, "factor:down": 50.0},
                    {"factor:root": 10.0, "factor:mid": 25.0, "factor:down": 50.0},
                ],
                "payload": {"final_state": {"factor:root": 10.0, "factor:mid": 25.0, "factor:down": 50.0}},
            }
            write_json_atomic(ws_df / "simulation-run.json", df_run)
            ev_df = evaluate_quality(ws_df)
            self.assertTrue(
                any("[ENGINE_FROZEN]" in w for w in ev_df["warnings"]),
                ev_df["warnings"],
            )

    def test_auto_causal_wide_csv_and_bilingual_conditional_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "climate_macro.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "period,rainfall_mm,rice_yield_pct,food_price_index,temp_celsius",
                        "2024-01,220,100.0,100.0,27.0",
                        "2024-02,160,96.0,103.5,27.8",
                        "2024-03,110,89.0,109.0,28.7",
                        "2024-04,70,81.0,117.5,29.6",
                        "2024-05,55,75.0,124.0,30.2",
                    ]
                ),
                encoding="utf-8",
            )
            prop = propose_model(csv_path)
            nodes_by_id = {n["id"]: n for n in prop["candidate_nodes"]}
            self.assertIn("factor:rainfall-mm", nodes_by_id)
            self.assertEqual(nodes_by_id["factor:rainfall-mm"]["unit"], "mm")
            self.assertEqual(nodes_by_id["factor:rice-yield-pct"]["unit"], "%")
            self.assertEqual(nodes_by_id["factor:food-price-index"]["unit"], "index")
            self.assertEqual(nodes_by_id["factor:temp-celsius"]["unit"], "degC")
            self.assertEqual(nodes_by_id["factor:rainfall-mm"]["baseline"], 220.0)
            self.assertEqual(nodes_by_id["factor:rainfall-mm"]["initial_value"], 220.0)
            self.assertTrue(any(g["gap_type"] == "correlation_not_causation" for g in prop["gaps"]))
            rev = review_model(prop)
            self.assertEqual(rev["overall_status"], "rejected")

        # Bilingual conditional EN/VI + climate & HUMINT patterns
        claims = [
            {
                "claim_id": "c_en_cond",
                "record_type": "claim",
                "claim": "If monsoon rainfall intensifies, crop yields decline via field flooding",
                "evidence": "Flooding cuts yields with a lag of 1 tick by 0.3",
                "confidence": "high",
            },
            {
                "claim_id": "c_en_unless",
                "record_type": "claim",
                "claim": "Unless external aid arrives, command cohesion collapses",
                "evidence": "Loss of resupply lowers morale with a lag of 2 ticks coefficient of 0.45",
                "confidence": "high",
            },
            {
                "claim_id": "c_vi_cond",
                "record_type": "claim",
                "claim": "Nếu El Niño kéo dài thì sản lượng lúa giảm thông qua hạn hán",
                "evidence": "Hạn hán kéo dài với độ trễ 2 tháng mức 0.35 ngày 2025-01-01",
                "confidence": "high",
            },
        ]
        cond_prop = propose_model(claims)
        edges = cond_prop["candidate_edges"]
        self.assertEqual(len(edges), 3)
        self.assertEqual((edges[0]["from"], edges[0]["to"], edges[0]["sign"]), ("factor:monsoon-rainfall", "factor:crop-yields", -1))
        self.assertEqual((edges[1]["from"], edges[1]["to"], edges[1]["sign"]), ("factor:external-aid", "factor:command-cohesion", 1))
        self.assertEqual((edges[2]["from"], edges[2]["to"], edges[2]["sign"]), ("factor:el-nino", "factor:san-luong-lua", -1))

    def test_all_seven_domain_packs_have_enriched_valid_mechanisms(self) -> None:
        pack_summary = validate_all_packs(ROOT)
        self.assertTrue(pack_summary.get("ok"), pack_summary)
        self.assertEqual(pack_summary["count"], len(PACK_NAMES))
        self.assertTrue(pack_summary["all_semantically_valid"], pack_summary)
        for name in PACK_NAMES:
            mech_doc = json.loads((ROOT / "packs" / name / "mechanisms.json").read_text(encoding="utf-8"))
            templates = mech_doc.get("templates", [])
            self.assertGreaterEqual(len(templates), 4, name)
            for tpl in templates:
                self.assertTrue(str(tpl["id"]).startswith(f"mech:{name}:"))
                self.assertGreaterEqual(len(str(tpl["description"])), 40)


if __name__ == "__main__":
    unittest.main()
