"""Comprehensive regression and edge-case test suite for Aleph Skill v2.7.0."""
from __future__ import annotations

import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.engine import (  # noqa: E402
    EngineConfig,
    compile_model,
    generate_numerical_execution_trace,
    model_hash,
    run_deterministic,
)
from aleph.execution_binding import build_trace_execution_binding  # noqa: E402
from aleph.io import canonical_hash, write_json_atomic  # noqa: E402
from aleph.quality import evaluate  # noqa: E402
from aleph.validator import validate_workspace  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"
REAL_MACRO_DATA = ROOT.parent / "_eval_aleph" / "datasets" / "real_macro_2024_2026.json"


def _run_cli(script: str, args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _patch_evidence_file(ws: Path) -> None:
    """Create minimal valid offline-provisional evidence + nodes/actors if needed."""
    manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "completed"
    manifest["simulation_mode"] = "deterministic"
    manifest["formula_version"] = "2.1.0"
    rc = manifest["execution"]["research_control"]
    rc["saturation_reached"] = False
    rc["offline_provisional"] = True
    rc["offline_reason"] = "Offline benchmark with frozen pre-cutoff evidence."
    manifest["execution"]["d_research"] = {"status": "unavailable", "invoked": False}
    manifest["execution"]["subagents"] = {
        "status": "unavailable",
        "tool": None,
        "detection_method": "offline-test",
        "fallback_reason": "Offline evaluation mode",
    }
    manifest["execution"]["research_quality"] = "limited"
    write_json_atomic(ws / "simulation-manifest.json", manifest)


class TraceExecutionBindingRegressionTests(unittest.TestCase):
    """P0 #1: Engine trace generation and execution binding invariants."""

    def test_multi_edge_delayed_plus_zero_lag_and_multiply_intervention_bind(self) -> None:
        model = compile_model(
            nodes=[
                {"id": "factor:a", "baseline": 2.0, "role": "exogenous"},
                {"id": "factor:b", "baseline": 3.0, "role": "exogenous"},
                {"id": "factor:c", "baseline": 1.0, "role": "endogenous", "bounds": [0.0, 5.0]},
            ],
            edges=[
                {"id": "causal:a-c", "from": "factor:a", "to": "factor:c", "sign": 1, "base_strength": 0.5, "lag_ticks": 0},
                {"id": "causal:b-c-lag", "from": "factor:b", "to": "factor:c", "sign": 1, "base_strength": 0.4, "lag_ticks": 1},
            ],
            interventions=[
                {"id": "int:boost", "target": "factor:c", "op": "multiply", "value": 1.5, "start_tick": 1, "end_tick": 3}
            ],
            formula_version="2.1.0",
        )
        config = EngineConfig(mode="deterministic", seed="bind-test")
        result = run_deterministic(model, config, ticks=4, run_id=0)
        self.assertTrue(result["ok"], result)

        trace = generate_numerical_execution_trace(model, config, ticks=4, run_id=0)
        binding, issues = build_trace_execution_binding(
            trace, model, config, ticks=4, result=result, manifest={}
        )
        self.assertEqual(issues, [], [i.to_dict() for i in issues])
        self.assertIsNotNone(binding)

    def test_set_intervention_blocks_edges_cleanly_without_binding_error(self) -> None:
        model = compile_model(
            nodes=[
                {"id": "factor:x", "baseline": 10.0, "role": "exogenous"},
                {"id": "factor:y", "baseline": 0.0, "role": "endogenous"},
                {"id": "factor:z", "baseline": 0.0, "role": "endogenous"},
            ],
            edges=[
                {"id": "causal:x-y", "from": "factor:x", "to": "factor:y", "sign": 1, "base_strength": 0.5, "lag_ticks": 0},
                {"id": "causal:x-z-lag", "from": "factor:x", "to": "factor:z", "sign": 1, "base_strength": 0.3, "lag_ticks": 1},
            ],
            interventions=[
                {"id": "int:pin-y", "target": "factor:y", "op": "set", "value": 4.0, "start_tick": 1, "end_tick": 3},
                {"id": "int:pin-z", "target": "factor:z", "op": "set", "value": 2.0, "start_tick": 2, "end_tick": 4},
            ],
            formula_version="2.1.0",
        )
        config = EngineConfig(mode="deterministic", seed="blocked-test")
        result = run_deterministic(model, config, ticks=5, run_id=0)
        trace = generate_numerical_execution_trace(model, config, ticks=5, run_id=0)
        for step in trace["steps"]:
            if step["node_id"] == "factor:y" and step["delivery_tick"] in (1, 2):
                self.assertEqual(step["state_transitions"]["intervention_type"], "do_set")
                self.assertAlmostEqual(step["state_transitions"]["delta"], 0.0)
            if step["node_id"] == "factor:z" and step["delivery_tick"] in (2, 3):
                self.assertEqual(step["state_transitions"]["intervention_type"], "do_set")
                self.assertAlmostEqual(step["state_transitions"]["delta"], 0.0)
        binding, issues = build_trace_execution_binding(
            trace, model, config, ticks=5, result=result, manifest={}
        )
        self.assertEqual(issues, [], [i.to_dict() for i in issues])
        self.assertIsNotNone(binding)

    def test_zero_edge_model_produces_valid_empty_trace_and_binding(self) -> None:
        model = compile_model(
            nodes=[
                {"id": "factor:x", "baseline": 5.0, "role": "exogenous"},
                {"id": "factor:y", "baseline": 0.0, "role": "endogenous"},
            ],
            edges=[],
            interventions=[
                {"id": "int:pin-all", "target": "factor:y", "op": "set", "value": 7.0, "start_tick": 0, "end_tick": 3},
            ],
            formula_version="2.1.0",
        )
        config = EngineConfig(mode="deterministic", seed="all-blocked")
        result = run_deterministic(model, config, ticks=3, run_id=0)
        trace = generate_numerical_execution_trace(model, config, ticks=3, run_id=0)
        self.assertEqual(trace["steps"], [])
        binding, issues = build_trace_execution_binding(
            trace, model, config, ticks=3, result=result, manifest={}
        )
        self.assertEqual(issues, [], [i.to_dict() for i in issues])
        self.assertIsNotNone(binding)


class InterventionTimestepAndRetentionTests(unittest.TestCase):
    """P0 #2: Interventions, temporal_frame.timestep, retention_unit, and lag horizon."""

    def test_unknown_intervention_op_and_empty_window_fail_closed(self) -> None:
        nodes = [{"id": "factor:a", "baseline": 1.0}]
        with self.assertRaises(ValueError):
            compile_model(
                nodes, [],
                [{"id": "int:bad-op", "target": "factor:a", "op": "delta", "value": 1.0, "start_tick": 0}],
            )
        with self.assertRaises(ValueError):
            compile_model(
                nodes, [],
                [{"id": "int:empty-win", "target": "factor:a", "op": "add", "value": 1.0, "start_tick": 2, "end_tick": 2}],
            )

    def test_change_point_and_matching_intervention_not_doubled(self) -> None:
        from compile_model import load_interventions

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            manifest = {
                "artifact_paths": {"interventions": "interventions.json"},
                "change_point": {
                    "target": "factor:oil",
                    "op": "add",
                    "magnitude": 10.0,
                    "start_tick": 0,
                },
            }
            write_json_atomic(
                ws / "interventions.json",
                [{"id": "int:oil-shock", "target": "factor:oil", "op": "add", "value": 10.0, "start_tick": 0}],
            )
            loaded = load_interventions(ws, manifest)
            self.assertEqual(len(loaded), 1)

    def test_retention_unit_per_tick_prevents_monthly_collapse_and_preserves_21_hash(self) -> None:
        n_default = [{"id": "factor:s", "scale": "stock", "baseline": 100.0, "retention": 0.9}]
        n_per_day = [{"id": "factor:s", "scale": "stock", "baseline": 100.0, "retention": 0.9, "retention_unit": "per_day"}]
        n_per_tick = [{"id": "factor:s", "scale": "stock", "baseline": 100.0, "retention": 0.9, "retention_unit": "per_tick"}]
        m_default = compile_model(n_default, [], formula_version="2.1.0")
        m_per_day = compile_model(n_per_day, [], formula_version="2.1.0")
        m_per_tick = compile_model(n_per_tick, [], formula_version="2.1.0")
        self.assertEqual(model_hash(m_default), model_hash(m_per_day))
        cfg = EngineConfig(mode="deterministic", timestep=30.0)
        res_day = run_deterministic(m_default, cfg, ticks=1)
        res_tick = run_deterministic(m_per_tick, cfg, ticks=1)
        self.assertAlmostEqual(res_day["payload"]["final_state"]["factor:s"], 100.0 * (0.9 ** 30.0), places=5)
        self.assertAlmostEqual(res_tick["payload"]["final_state"]["factor:s"], 90.0, places=6)

    def test_manifest_temporal_frame_timestep_p1m_resolves_monthly_run_and_avoids_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            init_res = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--slug", "monthly-sim",
                    "--time", "2025-01-01",
                    "--horizon", "P12M",
                    "--timestep", "P1M",
                    "--out-dir", str(out),
                ],
            )
            self.assertEqual(init_res.returncode, 0, init_res.stdout + init_res.stderr)
            ws = Path(init_res.stdout.strip().splitlines()[-1])
            write_json_atomic(ws / "nodes.json", [
                {"id": "factor:example", "type": "factor", "name": "Oil", "status": "fact", "timeline": "observed_baseline",
                 "probability": None, "confidence": 0.9, "time": "2025-01-01", "description": "Brent",
                 "state_before": {"summary": "Base", "value": 10.0},
                 "trigger": {"kind": "context", "description": "Shock"}, "mechanism": "Exogenous price shock driver.",
                 "state_after": {"summary": "Moved"}, "evidence_ids": ["evidence:example"], "lag": "P0D",
                 "role": "exogenous", "baseline": 10.0},
                {"id": "factor:target", "type": "factor", "name": "CPI", "status": "inference", "timeline": "simulated_branch",
                 "probability": None, "confidence": 0.8, "time": "2025-01-01", "description": "CPI",
                 "state_before": {"summary": "Base", "value": 0.0},
                 "trigger": {"kind": "factor_change", "description": "Oil pass-through"}, "mechanism": "Pass-through after 1 month.",
                 "state_after": {"summary": "Moved"}, "evidence_ids": ["evidence:example"], "lag": "P1M",
                 "role": "endogenous", "baseline": 0.0},
            ])
            write_json_atomic(ws / "edges.json", [
                {
                    "id": "causal:example-increases-target",
                    "from": "factor:example",
                    "to": "factor:target",
                    "relation": "increases",
                    "sign": 1,
                    "base_strength": 0.5,
                    "evidence_confidence": 0.8,
                    "mechanism": "Oil shock transmits into CPI after a one-month retail fuel adjustment lag.",
                    "lag_distribution": {"type": "fixed", "fixed": "P1M"},
                    "context_modifiers": [],
                    "evidence": ["evidence:example"],
                    "status": "admitted",
                    "transform": "linear",
                }
            ])
            manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest["simulation_mode"] = "deterministic"
            manifest["change_point"]["target"] = "factor:example"
            manifest["change_point"]["magnitude"] = 0.0
            manifest["artifact_paths"].pop("propagation_trace", None)
            manifest["artifact_paths"]["execution_trace"] = "execution-trace.json"
            write_json_atomic(ws / "simulation-manifest.json", manifest)

            run_res = _run_cli("run_simulation.py", ["--workspace", str(ws), "--mode", "deterministic"])
            self.assertEqual(run_res.returncode, 0, run_res.stdout + run_res.stderr)
            run_data = json.loads(run_res.stdout)
            self.assertAlmostEqual(run_data["config"]["timestep"], 30.0)
            self.assertEqual(run_data["ticks"], 12)
            self.assertGreater(run_data["result"]["payload"]["final_state"]["factor:target"], 0.0)


class Formula220RegressionTests(unittest.TestCase):
    """P0 #3: Formula 2.2.0 deviation/log_ratio semantics, cycles, and divergence."""

    def test_three_node_nonzero_baseline_zero_intervention_no_drift(self) -> None:
        nodes = [
            {"id": "factor:rate", "baseline": 4.5, "role": "endogenous"},
            {"id": "factor:cpi", "baseline": 300.0, "role": "endogenous"},
            {"id": "factor:spending", "baseline": 100.0, "role": "endogenous"},
        ]
        edges = [
            {"id": "causal:rate-cpi", "from": "factor:rate", "to": "factor:cpi", "sign": -1, "base_strength": 0.3, "lag_ticks": 0, "input_mode": "deviation"},
            {"id": "causal:cpi-spend", "from": "factor:cpi", "to": "factor:spending", "sign": 1, "base_strength": 0.5, "lag_ticks": 1, "input_mode": "deviation"},
        ]
        model = compile_model(nodes, edges, formula_version="2.2.0")
        cfg = EngineConfig(mode="deterministic")
        res = run_deterministic(model, cfg, ticks=12)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["payload"]["final_state"]["factor:rate"], 4.5)
        self.assertAlmostEqual(res["payload"]["final_state"]["factor:cpi"], 300.0)
        self.assertAlmostEqual(res["payload"]["final_state"]["factor:spending"], 100.0)

        # Now apply +1.0 intervention on rate
        shocked = compile_model(
            nodes, edges,
            interventions=[{"id": "int:hike", "target": "factor:rate", "op": "add", "value": 1.0, "start_tick": 0}],
            formula_version="2.2.0",
        )
        res_shock = run_deterministic(shocked, cfg, ticks=4)
        self.assertTrue(res_shock["ok"])
        self.assertAlmostEqual(res_shock["payload"]["final_state"]["factor:cpi"], 299.7)
        self.assertAlmostEqual(res_shock["payload"]["final_state"]["factor:spending"], 99.85)
        trace = generate_numerical_execution_trace(shocked, cfg, ticks=4)
        binding, issues = build_trace_execution_binding(trace, shocked, cfg, ticks=4, result=res_shock, manifest={})
        self.assertEqual(issues, [], [i.to_dict() for i in issues])
        self.assertEqual(binding["version"], "aleph-trace-execution-binding-v3")

    def test_scc_deviation_zero_drift_and_analytic_response(self) -> None:
        nodes = [
            {"id": "factor:a", "baseline": 10.0, "role": "endogenous"},
            {"id": "factor:b", "baseline": 100.0, "role": "endogenous"},
        ]
        edges = [
            {"id": "causal:a-b", "from": "factor:a", "to": "factor:b", "sign": 1, "base_strength": 0.5, "lag_ticks": 0, "input_mode": "deviation"},
            {"id": "causal:b-a", "from": "factor:b", "to": "factor:a", "sign": 1, "base_strength": 0.4, "lag_ticks": 0, "input_mode": "deviation"},
        ]
        cfg = EngineConfig(mode="deterministic", scc_solver="linear_auto")
        m0 = compile_model(nodes, edges, formula_version="2.2.0")
        r0 = run_deterministic(m0, cfg, ticks=3)
        self.assertTrue(r0["ok"])
        self.assertAlmostEqual(r0["payload"]["final_state"]["factor:a"], 10.0)
        self.assertAlmostEqual(r0["payload"]["final_state"]["factor:b"], 100.0)

        m1 = compile_model(
            nodes, edges,
            interventions=[{"id": "int:a", "target": "factor:a", "op": "add", "value": 1.0, "start_tick": 0}],
            formula_version="2.2.0",
        )
        r1 = run_deterministic(m1, cfg, ticks=1)
        self.assertTrue(r1["ok"])
        # dA = 1 + 0.4 * dB, dB = 0.5 * dA => dA = 1 / (1 - 0.2) = 1.25, dB = 0.625 (within Jacobi tolerance)
        self.assertAlmostEqual(r1["payload"]["final_state"]["factor:a"], 11.25, places=4)
        self.assertAlmostEqual(r1["payload"]["final_state"]["factor:b"], 100.625, places=4)
        # Force direct linear_auto Gaussian fallback (jacobi_max_iter=1 triggers fallback)
        r1_direct = run_deterministic(
            m1,
            EngineConfig(mode="deterministic", scc_solver="linear_auto", jacobi_max_iter=1),
            ticks=1,
        )
        self.assertTrue(r1_direct["ok"])
        self.assertAlmostEqual(r1_direct["payload"]["final_state"]["factor:a"], 11.25, places=10)
        self.assertAlmostEqual(r1_direct["payload"]["final_state"]["factor:b"], 100.625, places=10)

    def test_log_ratio_zero_baseline_refused_and_runtime_nonpositive_diverges_safely(self) -> None:
        with self.assertRaises(ValueError):
            compile_model(
                [{"id": "factor:x", "baseline": 0.0}, {"id": "factor:y", "baseline": 1.0}],
                [{"id": "causal:x-y", "from": "factor:x", "to": "factor:y", "sign": 1, "base_strength": 0.5, "input_mode": "log_ratio"}],
                formula_version="2.2.0",
            )
        m = compile_model(
            [{"id": "factor:x", "baseline": 10.0}, {"id": "factor:y", "baseline": 100.0}],
            [{"id": "causal:x-y", "from": "factor:x", "to": "factor:y", "sign": 1, "base_strength": 0.5, "transform": "elasticity", "input_mode": "log_ratio"}],
            interventions=[{"id": "int:neg", "target": "factor:x", "op": "set", "value": -5.0, "start_tick": 1}],
            formula_version="2.2.0",
        )
        res = run_deterministic(m, EngineConfig(mode="deterministic"), ticks=3)
        self.assertFalse(res["ok"])
        self.assertEqual(res["exit_code"], 4)

    def test_state_divergence_guard_triggers_on_unbounded_explosion_not_large_vnd_baseline(self) -> None:
        m_expl = compile_model(
            [{"id": "factor:a", "baseline": 1.0}, {"id": "factor:b", "baseline": 1.0}],
            [
                {"id": "causal:a-b", "from": "factor:a", "to": "factor:b", "sign": 1, "base_strength": 1e5, "lag_ticks": 1},
                {"id": "causal:b-a", "from": "factor:b", "to": "factor:a", "sign": 1, "base_strength": 1e5, "lag_ticks": 1},
            ],
            formula_version="2.1.0",
        )
        res_expl = run_deterministic(m_expl, EngineConfig(mode="deterministic"), ticks=8)
        self.assertFalse(res_expl["ok"])
        self.assertEqual(res_expl["exit_code"], 4)
        self.assertTrue(any(i["code"] == "STATE_DIVERGENCE" for i in res_expl["issues"]))

        m_vnd = compile_model(
            [{"id": "factor:gdp_vnd", "baseline": 1e16}, {"id": "factor:tax_vnd", "baseline": 1.5e15}],
            [{"id": "causal:gdp-tax", "from": "factor:gdp_vnd", "to": "factor:tax_vnd", "sign": 1, "base_strength": 0.15, "input_mode": "deviation"}],
            formula_version="2.2.0",
        )
        res_vnd = run_deterministic(m_vnd, EngineConfig(mode="deterministic"), ticks=5)
        self.assertTrue(res_vnd["ok"])


class ColdStartInteropAndOfflineFinalTests(unittest.TestCase):
    """P0 #4 & #5: Unified trace path resolution + offline --mode final provisional workflow."""

    def test_cold_start_execution_trace_renders_scores_and_validates_final_offline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "cold-ws"
            shutil.copytree(FIXTURE, ws)
            manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest["formula_version"] = "2.1.0"
            manifest["simulation_mode"] = "deterministic"
            manifest["artifact_paths"].pop("propagation_trace", None)
            manifest["artifact_paths"]["execution_trace"] = "execution-trace.json"
            manifest["execution"]["research_control"]["saturation_reached"] = False
            manifest["execution"]["research_control"]["offline_provisional"] = True
            manifest["execution"]["research_control"]["offline_reason"] = "Offline test without internet research."
            manifest["execution"]["research_control"]["unresolved_critical_gaps"] = [
                "gap:offline-provisional-no-live-retrieval"
            ]
            manifest["execution"]["d_research"] = {"status": "unavailable", "invoked": False}
            manifest["execution"]["subagents"]["status"] = "unavailable"
            manifest["execution"]["research_quality"] = "limited"
            write_json_atomic(ws / "simulation-manifest.json", manifest)
            for stale in ("propagation-trace.jsonl", "simulation-run.json", "simulation-model.json", "validation-report.json", "replay-report.json", "REPORT.md"):
                (ws / stale).unlink(missing_ok=True)

            self.assertEqual(_run_cli("run_simulation.py", ["--workspace", str(ws), "--ticks", "5"]).returncode, 0)
            self.assertEqual(_run_cli("replay_simulation.py", ["--workspace", str(ws)]).returncode, 0)
            render_res = _run_cli("render_simulation_report.py", ["--workspace", str(ws)])
            self.assertEqual(render_res.returncode, 0, render_res.stdout + render_res.stderr)
            self.assertIn("Offline Provisional", (ws / "REPORT.md").read_text(encoding="utf-8"))
            bf_res = _run_cli("score_butterfly.py", ["--workspace", str(ws)])
            self.assertEqual(bf_res.returncode, 0, bf_res.stdout + bf_res.stderr)
            val_res = _run_cli("validate_simulation_artifacts.py", ["--workspace", str(ws), "--mode", "final", "--write-report"])
            self.assertEqual(val_res.returncode, 0, val_res.stdout + val_res.stderr)
            qual = evaluate(ws)
            self.assertIn(qual["assurance_tier"], ("limited", "experimental"))
            self.assertNotIn(qual["assurance_tier"], ("verified", "calibrated"))

    def test_online_non_provisional_still_fails_final_when_unsaturated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "online-unsat"
            shutil.copytree(FIXTURE, ws)
            manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest["execution"]["research_control"]["saturation_reached"] = False
            manifest["execution"]["research_control"].pop("offline_provisional", None)
            write_json_atomic(ws / "simulation-manifest.json", manifest)
            val = validate_workspace(ws, mode="final", require_report=False, verify_integrity=False)
            self.assertEqual(val["status"], "fail")


class ForecastingCalibrationPacksQualityAndAutoCausalTests(unittest.TestCase):
    """P1 #6-#10: Forecasting, conformal calibration, tails, health/fidelity, packs, auto_causal, DX."""

    def test_adaptive_point_forecast_beats_persistence_on_brent_and_calibrates_intervals(self) -> None:
        if not REAL_MACRO_DATA.is_file():
            self.skipTest("real_macro_2024_2026.json absent")
        data = json.loads(REAL_MACRO_DATA.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            csv_path = ws / "brent.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                w = csv.DictWriter(handle, fieldnames=["period", "value", "official_release_date", "vintage_date"])
                w.writeheader()
                for row in data["monthly"]:
                    if row["brent"] is None:
                        continue
                    ym = row["ym"]
                    y, m = map(int, ym.split("-"))
                    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
                    rel = f"{ny:04d}-{nm:02d}-05T00:00:00Z"
                    w.writerow({"period": ym, "value": row["brent"], "official_release_date": rel, "vintage_date": rel})
            out_dir = ws / "eval"
            res = _run_cli(
                "evaluate_point_forecasts.py",
                ["--data", str(csv_path), "--out", str(out_dir), "--model", "adaptive"],
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            report = json.loads((out_dir / "point-evaluation.json").read_text(encoding="utf-8"))
            self.assertTrue(report["metrics"]["beats_baseline"], report["metrics"])

            cal_out = ws / "intervals.json"
            cal_res = _run_cli(
                "calibrate_intervals.py",
                ["--evaluation-dir", str(out_dir), "--out", str(cal_out)],
            )
            self.assertEqual(cal_res.returncode, 0, cal_res.stdout + cal_res.stderr)
            cal_data = json.loads(cal_out.read_text(encoding="utf-8"))
            self.assertEqual(cal_data["status"], "pass")
            self.assertGreaterEqual(cal_data["coverage"]["0.80"]["empirical_coverage"], 0.65)

    def test_tail_shock_branch_schema_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "tail-ws"
            shutil.copytree(FIXTURE, ws)
            bl = json.loads((ws / "branch-ledger.json").read_text(encoding="utf-8"))
            bl["tail_coverage"] = True
            bl["branches"][-1]["tail"] = True
            bl["branches"][-1]["tail_class"] = "tail_shock"
            write_json_atomic(ws / "branch-ledger.json", bl)
            val = validate_workspace(ws, mode="final", require_report=False, verify_integrity=False)
            self.assertEqual(val["status"], "pass", val)

    def test_quality_flags_engine_frozen_and_narrative_unit_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "health-ws"
            shutil.copytree(FIXTURE, ws)
            # Replace nodes with dimensionless normalized nodes and write contradictory $6.25B report
            (ws / "REPORT.md").write_text(
                "# Report\n\nTotal economic loss reaches **$6.25 billion** in Q4.\n",
                encoding="utf-8",
            )
            # Freeze final_state to baselines while claiming 10 ticks
            run_data = json.loads((ws / "simulation-run.json").read_text(encoding="utf-8"))
            run_data["result"]["payload"]["final_state"] = {
                n["id"]: n.get("baseline", 0.0) for n in json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            }
            run_data["result"]["history"] = [dict(run_data["result"]["payload"]["final_state"])] * max(1, run_data["ticks"])
            write_json_atomic(ws / "simulation-run.json", run_data)
            qual = evaluate(ws)
            codes = {i["code"] for i in qual.get("warnings_detail", []) + qual.get("errors_detail", [])}
            self.assertIn("ENGINE_FROZEN", codes)
            self.assertIn("NARRATIVE_UNIT_MISMATCH", codes)

    def test_economics_pack_is_validated_or_calibrated(self) -> None:
        from aleph.packs import validate_pack

        res = validate_pack(ROOT / "packs" / "economics")
        self.assertTrue(res["ok"], res)
        self.assertIn(res["maturity"], ("validated", "calibrated"), res)
        self.assertGreaterEqual(res["hindcast_cases"], 30)

    def test_auto_causal_vietnamese_slug_and_into_initialized(self) -> None:
        from aleph.auto_causal import _slugify, propose_model

        self.assertEqual(_slugify("Lãi suất điều hành đồng USD"), "lai-suat-dieu-hanh-dong-usd")
        proposal = propose_model([
            {"evidence_id": "ev:1", "record_type": "claim", "claim": "Chỉ thị không liên quan gì cả."},
            {"evidence_id": "ev:2", "record_type": "claim", "claim": "Giá dầu tăng mạnh đẩy lạm phát tiêu dùng tăng."},
        ])
        self.assertEqual(len(proposal["candidate_edges"]), 1)
        for node in proposal["candidate_nodes"]:
            self.assertNotIn("source-factor", node["id"])
            self.assertNotIn("target-factor", node["id"])

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            init_res = _run_cli("init_simulation_workspace.py", ["--slug", "merge-target", "--out-dir", str(out)])
            self.assertEqual(init_res.returncode, 0)
            ws = Path(init_res.stdout.strip().splitlines()[-1])
            ledger_csv = out / "claims.csv"
            ledger_csv.write_text(
                "evidence_id,record_type,claim,confidence,source_tier\n"
                "evidence:c1,claim,Higher crude oil prices increase headline inflation.,0.8,primary\n",
                encoding="utf-8",
            )
            prop_path = out / "prop.json"
            rev_path = out / "rev.json"
            self.assertEqual(_run_cli("auto_causal_inducer.py", ["propose", "--ledger", str(ledger_csv), "--out", str(prop_path)]).returncode, 0)
            self.assertEqual(_run_cli("auto_causal_inducer.py", ["review", "--proposal", str(prop_path), "--out", str(rev_path)]).returncode, 0)
            app_res = _run_cli(
                "auto_causal_inducer.py",
                ["apply", "--proposal", str(prop_path), "--review", str(rev_path), "--workspace", str(ws), "--into-initialized"],
            )
            self.assertEqual(app_res.returncode, 0, app_res.stdout + app_res.stderr)

    def test_quickstart_quant_and_repair_model_schema_cli(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            qs = _run_cli(
                "quickstart_quant.py",
                [
                    "--slug", "qs-macro",
                    "--domain", "economics",
                    "--change-point", "Oil price shock +10 USD",
                    "--timestep", "P1M",
                    "--horizon", "P6M",
                    "--out-dir", str(out),
                    "--offline-provisional",
                    "--offline-reason", "Quickstart smoke test offline.",
                ],
            )
            self.assertEqual(qs.returncode, 0, qs.stdout + qs.stderr)
            payload = json.loads(qs.stdout)
            ws = Path(payload["workspace"])
            self.assertEqual(payload["validation_status"], "pass")
            self.assertTrue((ws / "REPORT.md").is_file())

            # Test repair_model_schema.py on noisy LLM-style nodes/edges/interventions
            dirty = out / "dirty"
            dirty.mkdir()
            write_json_atomic(dirty / "nodes.json", [
                {"id": "factor:a", "initial_value": 10, "units": "USD", "scale": "delta"},
                {"id": "factor:b", "baseline": 0, "scale": "level"},
            ])
            write_json_atomic(dirty / "edges.json", [
                {"id": "edge:ab", "source": "factor:a", "target": "factor:b", "strength": 0.5, "sign": "+", "lag": 1, "transform": "tanh"},
                {"id": "edge:bc", "source": "factor:b", "target": "factor:a", "weight": 0.2, "sign": -1, "transform": "sigmoid"},
                {"id": "e1", "from": "factor:a", "to": "factor:b", "base_strength": 0.1, "sign": 1, "lag_ticks": 1},
                {"id": "causal:edge:e2", "from": "factor:b", "to": "factor:a", "base_strength": 0.1, "sign": -1, "lag_ticks": 1},
                {"id": "e1", "from": "factor:a", "to": "factor:b", "base_strength": 0.05, "sign": 1, "lag_ticks": 2},
            ])
            write_json_atomic(dirty / "interventions.json", [
                {"id": "iv:1", "target": "factor:a", "op": "mul", "value": 1.2, "start_tick": 0, "end_tick": 3.0, "release_policy": "decay"},
                {"id": "iv:2", "target": "factor:b", "op": "set", "value": 5.0, "start_tick": 1, "end_tick": 2, "release_policy": "reset_baseline"},
                {"id": "iv:zero", "target": "factor:a", "op": "add", "value": 1.0, "start_tick": 0, "end_tick": 0},
            ])
            rep = _run_cli("repair_model_schema.py", ["--workspace", str(dirty), "--in-place", "--json"])
            self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
            repaired_nodes = json.loads((dirty / "nodes.json").read_text(encoding="utf-8"))
            self.assertEqual(repaired_nodes[0]["unit"], "USD")
            self.assertNotIn("units", repaired_nodes[0])
            self.assertEqual(repaired_nodes[0]["baseline"], 10.0)
            self.assertEqual(repaired_nodes[0]["initial_value"], 10.0)
            self.assertEqual(repaired_nodes[0]["scale"], "flow")
            repaired_edges = json.loads((dirty / "edges.json").read_text(encoding="utf-8"))
            self.assertEqual(repaired_edges[0]["from"], "factor:a")
            self.assertEqual(repaired_edges[0]["to"], "factor:b")
            self.assertEqual(repaired_edges[0]["base_strength"], 0.5)
            self.assertEqual(repaired_edges[0]["lag_ticks"], 1)
            self.assertEqual(repaired_edges[0]["transform"], "linear")
            self.assertEqual(repaired_edges[0]["saturation"], 1.0)
            for alias in ("source", "target", "strength", "lag"):
                self.assertNotIn(alias, repaired_edges[0])
            self.assertEqual(repaired_edges[1]["transform"], "logistic")
            import re
            edge_id_re = re.compile(r"^(causal|edge):[0-9a-zA-Z_-]+$")
            self.assertEqual(repaired_edges[2]["id"], "edge:e1")
            self.assertEqual(repaired_edges[3]["id"], "causal:e2")
            repaired_edge_ids = [item["id"] for item in repaired_edges]
            self.assertEqual(len(set(repaired_edge_ids)), len(repaired_edge_ids))
            for eid in repaired_edge_ids:
                self.assertRegex(eid, edge_id_re)
            repaired_ivs = json.loads((dirty / "interventions.json").read_text(encoding="utf-8"))
            self.assertEqual(repaired_ivs[0]["op"], "multiply")
            self.assertEqual(repaired_ivs[0]["end_tick"], 3)
            self.assertEqual(repaired_ivs[0]["release_policy"], "retain")
            self.assertEqual(repaired_ivs[1]["release_policy"], "retain")
            self.assertEqual(repaired_ivs[2]["end_tick"], 1)

    def test_v270_loophole_hunt_regressions(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # 1) init_simulation_workspace.py --formula-version 2.2.0 must stamp propagation-trace.jsonl
            init_out = root / "init_out"
            init_res = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--slug", "init220",
                    "--change-point", "Test 2.2.0 workspace initialization",
                    "--time", "2026-01-01",
                    "--horizon", "P6M",
                    "--timestep", "P1M",
                    "--domain", "economics",
                    "--geography", "US",
                    "--formula-version", "2.2.0",
                    "--offline-provisional",
                    "--offline-reason", "regression test",
                    "--out-dir", str(init_out),
                ],
            )
            self.assertEqual(init_res.returncode, 0, init_res.stdout + init_res.stderr)
            init_ws = Path(init_res.stdout.strip().splitlines()[-1]).resolve()
            trace_rows = [
                json.loads(line)
                for line in (init_ws / "propagation-trace.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(trace_rows)
            self.assertTrue(all(row.get("formula_version") == "2.2.0" for row in trace_rows))
            cmp_init = _run_cli("compile_model.py", ["--workspace", str(init_ws)])
            self.assertEqual(cmp_init.returncode, 0, cmp_init.stdout + cmp_init.stderr)
            val_draft = _run_cli("validate_simulation_artifacts.py", ["--workspace", str(init_ws), "--mode", "draft", "--json"])
            self.assertEqual(val_draft.returncode, 0, val_draft.stdout + val_draft.stderr)
            val_payload = json.loads(val_draft.stdout)
            self.assertEqual(val_payload.get("status"), "pass", val_payload)

            # 2) Unmodified quickstart_quant.py (lag_unit="days" with P1M & moving target) must NOT trigger ENGINE_FROZEN
            qs_out = root / "qs_out"
            qs_res = _run_cli(
                "quickstart_quant.py",
                [
                    "--slug", "qs-lag-check",
                    "--domain", "economics",
                    "--change-point", "Quickstart lag unit verification",
                    "--time", "2026-01-01",
                    "--timestep", "P1M",
                    "--horizon", "P6M",
                    "--ticks", "6",
                    "--formula-version", "2.2.0",
                    "--offline-provisional",
                    "--offline-reason", "regression test",
                    "--out-dir", str(qs_out),
                ],
            )
            self.assertEqual(qs_res.returncode, 0, qs_res.stdout + qs_res.stderr)
            qs_ws = qs_out / "qs-lag-check"
            qual_res = _run_cli("evaluate_simulation_quality.py", ["--workspace", str(qs_ws), "--json"])
            self.assertEqual(qual_res.returncode, 0, qual_res.stdout + qual_res.stderr)
            qual_payload = json.loads(qual_res.stdout)
            self.assertFalse(
                any("ENGINE_FROZEN" in w for w in (qual_payload.get("warnings") or [])),
                qual_payload.get("warnings"),
            )

            # 3) Editing nodes.json + recompiling while stale execution-trace.json sits on disk must fail closed
            nodes = json.loads((qs_ws / "nodes.json").read_text(encoding="utf-8"))
            nodes[0]["baseline"] = float(nodes[0]["baseline"]) + 25.0
            nodes[0]["unit"] = "MUTATED_UNIT"
            write_json_atomic(qs_ws / "nodes.json", nodes)
            cmp_mut = _run_cli("compile_model.py", ["--workspace", str(qs_ws)])
            self.assertEqual(cmp_mut.returncode, 0, cmp_mut.stdout + cmp_mut.stderr)
            run_stale = _run_cli(
                "run_simulation.py",
                ["--workspace", str(qs_ws), "--mode", "deterministic", "--ticks", "6", "--json"],
            )
            self.assertNotEqual(run_stale.returncode, 0, run_stale.stdout + run_stale.stderr)
            self.assertIn("PARAMETERS_HASH_MISMATCH", run_stale.stdout + run_stale.stderr)

            # 4) Replaced nodes.json + explicit interventions.json in initialized workspace must not fail on template factor:example
            tpl_ws = Path(
                _run_cli(
                    "init_simulation_workspace.py",
                    [
                        "--slug", "tpl-replace",
                        "--change-point", "Oil spike",
                        "--time", "2026-01-01",
                        "--horizon", "P6M",
                        "--timestep", "P1M",
                        "--domain", "economics",
                        "--geography", "US",
                        "--formula-version", "2.2.0",
                        "--offline-provisional",
                        "--offline-reason", "regression test",
                        "--out-dir", str(root / "tpl_out"),
                    ],
                ).stdout.strip().splitlines()[-1]
            ).resolve()
            tpl_mf = json.loads((tpl_ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(tpl_mf["change_point"]["target"], "factor:example")
            tpl_mf["simulation_mode"] = "deterministic"
            tpl_mf["artifact_paths"].pop("propagation_trace", None)
            tpl_mf["artifact_paths"]["execution_trace"] = "execution-trace.json"
            tpl_mf["artifact_paths"]["computational_model"] = "simulation-model.json"
            tpl_mf["artifact_paths"]["run_ledger"] = "simulation-run.json"
            tpl_mf["artifact_paths"]["replay_report"] = "replay-report.json"
            (tpl_ws / "propagation-trace.jsonl").unlink(missing_ok=True)
            write_json_atomic(tpl_ws / "simulation-manifest.json", tpl_mf)
            write_json_atomic(tpl_ws / "nodes.json", [
                {"id": "factor:oil", "name": "Oil", "scale": "level", "role": "exogenous", "baseline": 70.0},
                {"id": "factor:cpi", "name": "CPI", "scale": "level", "role": "endogenous", "baseline": 310.0},
            ])
            write_json_atomic(tpl_ws / "edges.json", [
                {
                    "id": "edge:oil_cpi",
                    "from": "factor:oil",
                    "to": "factor:cpi",
                    "relation": "increases",
                    "sign": 1,
                    "base_strength": 0.2,
                    "lag_ticks": 1,
                    "transform": "linear",
                }
            ])
            write_json_atomic(tpl_ws / "interventions.json", [
                {"id": "i1", "target": "factor:oil", "op": "add", "value": 10.0, "start_tick": 0, "end_tick": 2}
            ])

            # 4a) Even before repair_model_schema, compile + run + replay succeed without template factor:example leak
            self.assertEqual(_run_cli("compile_model.py", ["--workspace", str(tpl_ws)]).returncode, 0)
            self.assertEqual(
                _run_cli("run_simulation.py", ["--workspace", str(tpl_ws), "--mode", "deterministic", "--ticks", "6", "--json"]).returncode,
                0,
            )
            self.assertEqual(_run_cli("replay_simulation.py", ["--workspace", str(tpl_ws), "--json"]).returncode, 0)

            # 4b) repair_model_schema --in-place re-anchors template change_point.target away from factor:example
            rep_tpl = _run_cli("repair_model_schema.py", ["--workspace", str(tpl_ws), "--in-place", "--json"])
            self.assertEqual(rep_tpl.returncode, 0, rep_tpl.stdout + rep_tpl.stderr)
            tpl_mf_repaired = json.loads((tpl_ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(tpl_mf_repaired["change_point"]["target"], "factor:oil")
            self.assertEqual(tpl_mf_repaired["change_point"]["magnitude"], 0.0)

            # 4c) A genuine non-template typo in change_point.target must still fail closed
            tpl_mf_repaired["change_point"]["target"] = "factor:nonexistent_typo"
            tpl_mf_repaired["change_point"]["magnitude"] = 1.0
            write_json_atomic(tpl_ws / "simulation-manifest.json", tpl_mf_repaired)
            cmp_bad_cp = _run_cli("compile_model.py", ["--workspace", str(tpl_ws)])
            self.assertNotEqual(cmp_bad_cp.returncode, 0, cmp_bad_cp.stdout + cmp_bad_cp.stderr)
            self.assertIn("target must reference a model variable", cmp_bad_cp.stdout + cmp_bad_cp.stderr)

    def test_economics_hindcast_cases_nonzero_level_and_degenerate_pack_rejected(self) -> None:
        from aleph.packs import evaluate_hindcast_case, validate_pack

        econ_dir = ROOT / "packs" / "economics"
        cand_maes: list[float] = []
        base_maes: list[float] = []
        for idx in range(1, 31):
            case = json.loads((econ_dir / "hindcast" / f"case-{idx:03d}.json").read_text(encoding="utf-8"))
            ev = evaluate_hindcast_case(case)
            self.assertTrue(ev["ok"], f"case-{idx:03d}: {ev}")
            preds = ev["predictions"]
            self.assertTrue(any(abs(float(v)) > 10.0 for v in preds.values()), f"case-{idx:03d} collapsed to zero: {preds}")
            cand_maes.append(float(ev["metrics"]["mae"]))
            base_maes.append(float(ev["metrics"]["baseline_mae"]))
        self.assertLess(sum(cand_maes) / len(cand_maes), sum(base_maes) / len(base_maes))
        self.assertTrue(validate_pack(econ_dir)["ok"])

        with tempfile.TemporaryDirectory() as td:
            bad_pack = Path(td) / "economics"
            shutil.copytree(econ_dir, bad_pack)
            bad_pol = json.loads((bad_pack / "calibration-policy.json").read_text(encoding="utf-8"))
            for idx in range(1, 31):
                cp = bad_pack / "hindcast" / f"case-{idx:03d}.json"
                case = json.loads(cp.read_text(encoding="utf-8"))
                for edge in case["model"]["edges"]:
                    edge.pop("input_mode", None)
                case["model_hash"] = model_hash(
                    compile_model(
                        case["model"]["nodes"],
                        case["model"]["edges"],
                        case["model"].get("interventions", []),
                        formula_version=case["model"].get("formula_version", "2.2.0"),
                    )
                )
                write_json_atomic(cp, case)
                cid = case["case_id"]
                if isinstance(bad_pol.get("case_commitments"), dict) and cid in bad_pol["case_commitments"]:
                    bad_pol["case_commitments"][cid] = canonical_hash(case)
            write_json_atomic(bad_pack / "calibration-policy.json", bad_pol)
            bad_res = validate_pack(bad_pack)
            self.assertFalse(bad_res["ok"], bad_res)
            self.assertTrue(any(i.get("code") == "PACK_MATURITY" for i in bad_res["issues"]), bad_res)

    def test_compile_model_skips_template_change_point_without_interventions_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            init = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--slug",
                    "no-iv",
                    "--change-point",
                    "Oil spike",
                    "--time",
                    "2026-01-01",
                    "--horizon",
                    "P6M",
                    "--out-dir",
                    td,
                ],
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            ws = Path(init.stdout.strip())
            write_json_atomic(ws / "nodes.json", [{"id": "factor:oil", "role": "exogenous", "baseline": 70.0}])
            write_json_atomic(ws / "edges.json", [])
            iv_file = ws / "interventions.json"
            if iv_file.exists():
                iv_file.unlink()
            cmp_ok = _run_cli("compile_model.py", ["--workspace", str(ws)])
            self.assertEqual(cmp_ok.returncode, 0, cmp_ok.stdout + cmp_ok.stderr)
            compiled = json.loads((ws / "simulation-model.json").read_text(encoding="utf-8"))
            self.assertEqual(compiled["interventions"], [])

            mf = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            mf["change_point"]["target"] = "factor:typo_missing"
            write_json_atomic(ws / "simulation-manifest.json", mf)
            cmp_bad = _run_cli("compile_model.py", ["--workspace", str(ws)])
            self.assertNotEqual(cmp_bad.returncode, 0)

    def test_init_deterministic_and_sensitivity_honors_timestep_engine_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            init = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--slug",
                    "sens-p1m",
                    "--change-point",
                    "Tariff",
                    "--time",
                    "2026-01-01",
                    "--horizon",
                    "P6M",
                    "--timestep",
                    "P1M",
                    "--simulation-mode",
                    "deterministic",
                    "--out-dir",
                    td,
                ],
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            ws = Path(init.stdout.strip())
            mf = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(mf["simulation_mode"], "deterministic")
            self.assertEqual(mf["artifact_paths"]["execution_trace"], "execution-trace.json")
            self.assertNotIn("propagation_trace", mf["artifact_paths"])
            self.assertFalse((ws / "propagation-trace.jsonl").exists())

            mf["engine_config"] = {"timestep": "P1M", "scc_solver": "linear_auto"}
            write_json_atomic(ws / "simulation-manifest.json", mf)
            write_json_atomic(
                ws / "nodes.json",
                [
                    {"id": "factor:tariff", "type": "factor", "role": "exogenous", "scale": "level", "baseline": 2.0},
                    {"id": "factor:cpi", "type": "factor", "role": "endogenous", "scale": "level", "baseline": 100.0},
                ],
            )
            write_json_atomic(
                ws / "edges.json",
                [
                    {
                        "id": "causal:t-cpi",
                        "from": "factor:tariff",
                        "to": "factor:cpi",
                        "relation": "increases",
                        "sign": 1,
                        "base_strength": 0.5,
                        "lag_ticks": 30,
                        "lag_unit": "days",
                        "transform": "linear",
                        "input_mode": "deviation",
                    }
                ],
            )
            write_json_atomic(
                ws / "interventions.json",
                [{"id": "iv:1", "target": "factor:tariff", "op": "add", "value": 4.0, "start_tick": 0, "end_tick": None}],
            )
            write_json_atomic(
                ws / "sensitivity-config.json",
                {
                    "method": "oat",
                    "ticks": 6,
                    "output": {"variable": "factor:cpi"},
                    "parameters": [
                        {
                            "id": "param:t_cpi",
                            "edge_id": "causal:t-cpi",
                            "min": 0.1,
                            "max": 0.9,
                            "baseline": 0.5,
                        }
                    ],
                },
            )
            self.assertEqual(_run_cli("compile_model.py", ["--workspace", str(ws)]).returncode, 0)
            self.assertEqual(_run_cli("run_simulation.py", ["--workspace", str(ws), "--ticks", "6", "--json"]).returncode, 0)
            sens = _run_cli("run_sensitivity.py", ["--workspace", str(ws), "--json"])
            self.assertEqual(sens.returncode, 0, sens.stdout + sens.stderr)
            sens_report = json.loads((ws / "sensitivity-report.json").read_text(encoding="utf-8"))
            effects = sens_report.get("analysis", {}).get("effects", {})
            max_delta = max((float(entry.get("abs_max", 0.0)) for entry in effects.values()), default=0.0)
            self.assertGreater(max_delta, 0.1, sens_report)

    def test_manifest_engine_config_trace_conflict_and_offline_gaps_banner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            shutil.copytree(FIXTURE, ws)
            mf = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            mf["simulation_mode"] = "deterministic"
            mf["formula_version"] = "2.2.0"
            mf["artifact_paths"].pop("propagation_trace", None)
            mf["artifact_paths"]["execution_trace"] = "execution-trace.json"
            mf["engine_config"] = {"timestep": "P7D", "scc_solver": "linear_auto", "jacobi_max_iter": 150}
            mf["execution"]["d_research"] = {"status": "unavailable", "invoked": False}
            mf["execution"]["research_control"].update(
                {
                    "saturation_reached": False,
                    "offline_provisional": True,
                    "offline_reason": "offline unit test",
                    "unresolved_critical_gaps": ["gap:q2-trade-series"],
                }
            )
            write_json_atomic(ws / "simulation-manifest.json", mf)
            for fname in ("execution-trace.json", "simulation-run.json", "replay-report.json", "simulation-model.json"):
                p = ws / fname
                if p.exists():
                    p.unlink()
            self.assertEqual(_run_cli("run_simulation.py", ["--workspace", str(ws), "--ticks", "4", "--json"]).returncode, 0)
            self.assertEqual(_run_cli("replay_simulation.py", ["--workspace", str(ws), "--json"]).returncode, 0)
            run_doc = json.loads((ws / "simulation-run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_doc["config"]["timestep"], 7.0)
            self.assertEqual(run_doc["config"]["scc_solver"], "linear_auto")
            self.assertEqual(run_doc["config"]["jacobi_max_iter"], 150)

            rep = _run_cli("render_simulation_report.py", ["--workspace", str(ws), "--out", str(ws / "REPORT.md")])
            self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
            report_text = (ws / "REPORT.md").read_text(encoding="utf-8")
            self.assertIn("gap:q2-trade-series", report_text)

            val_res = validate_workspace(ws, mode="final", require_report=True)
            self.assertTrue(
                any(
                    i.get("code") == "OFFLINE_PROVISIONAL" and i.get("actual") == ["gap:q2-trade-series"]
                    for i in val_res["issues"]
                ),
                val_res["issues"],
            )

            # Reject unknown engine_config field
            bad_mf = dict(mf)
            bad_mf["engine_config"] = {**mf["engine_config"], "bogus_field": 123}
            write_json_atomic(ws / "simulation-manifest.json", bad_mf)
            val_bad = validate_workspace(ws, mode="draft")
            self.assertTrue(
                any(i.get("code") == "UNKNOWN_FIELD" and "engine_config" in str(i.get("pointer") or "") for i in val_bad["issues"]),
                val_bad["issues"],
            )

            # Reject populated conflicting trace aliases
            mf["artifact_paths"]["propagation_trace"] = "propagation-trace.jsonl"
            write_json_atomic(ws / "simulation-manifest.json", mf)
            (ws / "propagation-trace.jsonl").write_text('{"step": 1, "edge_id": "causal:real"}\n', encoding="utf-8")
            val_conflict = validate_workspace(ws, mode="draft")
            self.assertTrue(any(i.get("code") == "TRACE_ALIAS_CONFLICT" for i in val_conflict["issues"]), val_conflict["issues"])

    def test_auto_causal_five_bilingual_pilot_claims(self) -> None:
        from aleph.auto_causal import propose_model

        claims = [
            {"evidence_id": "ev-01", "record_type": "claim", "claim": "Broad US import tariffs increase core goods inflation by 0.6 over 2 quarters.", "source_tier": "tier1"},
            {"evidence_id": "ev-02", "record_type": "claim", "claim": "Higher Brent crude oil prices raise headline CPI inflation with a 1-month lag.", "source_tier": "tier1"},
            {"evidence_id": "ev-03", "record_type": "claim", "claim": "Persistent above-target CPI inflation causes Federal Reserve tightening via rate hikes.", "source_tier": "tier1"},
            {"evidence_id": "ev-04", "record_type": "claim", "claim": "Thuế quan đối ứng làm giảm kim ngạch thương mại song phương với độ trễ 3 tháng.", "source_tier": "tier2"},
            {"evidence_id": "ev-05", "record_type": "claim", "claim": "If Hormuz transit is disrupted beyond 30 days while strategic reserves are low, shipping insurance spikes nonlinearly and forces OPEC+ spare capacity activation.", "source_tier": "tier2"},
            {"evidence_id": "ev-06", "record_type": "claim", "claim": "Thuế quan tăng đẩy lạm phát tăng ngay lập tức.", "source_tier": "tier2"},
        ]
        prop = propose_model(claims)
        node_ids = {n["id"] for n in prop["candidate_nodes"]}
        for expected_id in (
            "factor:broad-us-import-tariffs",
            "factor:core-goods-inflation",
            "factor:brent-crude-oil-prices",
            "factor:headline-cpi-inflation",
            "factor:persistent-above-target-cpi-inflation",
            "factor:federal-reserve-tightening",
            "factor:thue-quan-doi-ung",
            "factor:kim-ngach-thuong-mai-song-phuong",
            "factor:lam-phat",
        ):
            self.assertIn(expected_id, node_ids, node_ids)
        for bad_id in node_ids:
            self.assertNotIn("by-0", bad_id)
            self.assertNotIn("1-month-lag", bad_id)
            self.assertNotIn("do-tre-3-thang", bad_id)
            self.assertNotIn("ngay-lap-tuc", bad_id)

        edges_by_ev = {e["evidence_ids"][0]: e for e in prop["candidate_edges"] if e.get("evidence_ids")}
        self.assertEqual(edges_by_ev["ev-01"]["effect_size"], 0.6)
        self.assertEqual(edges_by_ev["ev-01"]["lag_ticks"], 180)
        self.assertEqual(edges_by_ev["ev-02"]["lag_ticks"], 30)
        self.assertEqual(edges_by_ev["ev-03"]["mechanism"], "rate hikes")
        self.assertEqual(edges_by_ev["ev-04"]["sign"], -1)
        self.assertEqual(edges_by_ev["ev-04"]["lag_ticks"], 90)

    def test_repair_model_schema_sign_iso_lag_dry_run_warnings_and_stub_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            init = _run_cli(
                "init_simulation_workspace.py",
                ["--slug", "rep-test", "--change-point", "Shock", "--time", "2026-01-01", "--horizon", "P6M", "--out-dir", td],
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            ws = Path(init.stdout.strip())
            mf = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            mf["simulation_mode"] = "deterministic"
            write_json_atomic(ws / "simulation-manifest.json", mf)

            raw_nodes = [
                {"id": "factor:cpi_index", "name": "CPI Index", "scale": "stock", "baseline": 2.5},
                {"id": "factor:fed_rate", "name": "Fed Rate", "scale": "level", "unit": "%", "baseline": -3.0},
                {"id": "factor:brent_oil", "name": "Brent Crude Oil", "scale": "level", "unit": "USD", "baseline": 900.0},
            ]
            raw_edges = [
                {"id": "edge:neg_rel", "from": "factor:fed_rate", "to": "factor:cpi_index", "relation": "decreases", "strength": 0.4, "lag_ticks": "P1M"},
                {"id": "edge:neg_val", "from": "factor:brent_oil", "to": "factor:cpi_index", "relation": "increases", "strength": -0.2, "lag_ticks": "2"},
            ]
            write_json_atomic(ws / "nodes.json", raw_nodes)
            write_json_atomic(ws / "edges.json", raw_edges)

            # Default mode is --dry-run: writes zero files
            dry = _run_cli("repair_model_schema.py", ["--workspace", str(ws), "--json"])
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            dry_out = json.loads(dry.stdout)
            self.assertTrue(dry_out["dry_run"])
            warn_codes = {w["code"] for w in dry_out["warnings"]}
            self.assertTrue(
                {
                    "PLAUSIBILITY_CPI_LOW",
                    "PLAUSIBILITY_PCT_OUTLIER",
                    "PLAUSIBILITY_OIL_OUTLIER",
                    "PLAUSIBILITY_STOCK_RETENTION_MISSING",
                }.issubset(warn_codes),
                warn_codes,
            )
            self.assertEqual(json.loads((ws / "edges.json").read_text(encoding="utf-8")), raw_edges)

            # --in-place repairs signs, ISO/string lags, re-anchors manifest, and removes template propagation stub
            self.assertTrue((ws / "propagation-trace.jsonl").is_file())
            in_place = _run_cli("repair_model_schema.py", ["--workspace", str(ws), "--in-place", "--json"])
            self.assertEqual(in_place.returncode, 0, in_place.stdout + in_place.stderr)
            fixed_edges = json.loads((ws / "edges.json").read_text(encoding="utf-8"))
            self.assertEqual(fixed_edges[0]["sign"], -1)
            self.assertEqual(fixed_edges[0]["lag_ticks"], 30)
            self.assertEqual(fixed_edges[0]["lag_unit"], "days")
            self.assertEqual(fixed_edges[0]["lag_distribution"], {"type": "fixed", "fixed": "P1M"})
            self.assertEqual(fixed_edges[1]["sign"], -1)
            self.assertEqual(fixed_edges[1]["base_strength"], 0.2)
            self.assertEqual(fixed_edges[1]["lag_ticks"], 2)
            fixed_mf = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("propagation_trace", fixed_mf["artifact_paths"])
            self.assertEqual(fixed_mf["artifact_paths"]["execution_trace"], "execution-trace.json")
            self.assertFalse((ws / "propagation-trace.jsonl").exists())

    def test_quality_pulse_non_frozen_narrative_fidelity_and_zero_intervention_drift(self) -> None:
        from aleph.engine import inspect_execution_health

        # 1) Pulse intervention decays back to baseline -> must NOT flag ENGINE_FROZEN
        with tempfile.TemporaryDirectory() as td:
            qs = _run_cli(
                "quickstart_quant.py",
                [
                    "--slug",
                    "pulse-ok",
                    "--change-point",
                    "Pulse shock",
                    "--horizon",
                    "P8M",
                    "--ticks",
                    "8",
                    "--out-dir",
                    td,
                    "--offline-provisional",
                ],
            )
            self.assertEqual(qs.returncode, 0, qs.stdout + qs.stderr)
            ws = Path(json.loads(qs.stdout)["workspace"])
            # Adjust quickstart intervention to pulse at tick 0..1 so downstream level target rises mid-run and returns to baseline
            ivs = json.loads((ws / "interventions.json").read_text(encoding="utf-8"))
            ivs[0]["end_tick"] = 1
            write_json_atomic(ws / "interventions.json", ivs)
            for stale_name in ("execution-trace.json", "simulation-run.json", "replay-report.json", "simulation-model.json"):
                stale_file = ws / stale_name
                if stale_file.exists():
                    stale_file.unlink()
            self.assertEqual(_run_cli("compile_model.py", ["--workspace", str(ws)]).returncode, 0)
            self.assertEqual(_run_cli("run_simulation.py", ["--workspace", str(ws), "--ticks", "6", "--json"]).returncode, 0)
            self.assertEqual(_run_cli("replay_simulation.py", ["--workspace", str(ws), "--json"]).returncode, 0)
            self.assertEqual(_run_cli("run_sensitivity.py", ["--workspace", str(ws), "--json"]).returncode, 0)
            self.assertEqual(_run_cli("render_simulation_report.py", ["--workspace", str(ws), "--out", str(ws / "REPORT.md")]).returncode, 0)
            ev_clean = evaluate(ws)
            self.assertFalse(any("[ENGINE_FROZEN]" in w for w in ev_clean["warnings"]), ev_clean["warnings"])
            self.assertFalse(any("[MODEL_NARRATIVE_FIDELITY]" in w for w in ev_clean["warnings"]), ev_clean["warnings"])

            # 2) Narrative contradiction triggers MODEL_NARRATIVE_FIDELITY
            orig_report = (ws / "REPORT.md").read_text(encoding="utf-8")
            bad_report = orig_report + "\n\n`factor:target_outcome` reached 9999.0 and factor:target_outcome surged.\n"
            (ws / "REPORT.md").write_text(bad_report, encoding="utf-8")
            ev_mismatch = evaluate(ws)
            self.assertTrue(any("[MODEL_NARRATIVE_FIDELITY]" in w for w in ev_mismatch["warnings"]), ev_mismatch["warnings"])

            # 2b) Undisclosed numerical divergence triggers MODEL_NARRATIVE_FIDELITY
            (ws / "REPORT.md").write_text(orig_report, encoding="utf-8")
            run_doc = json.loads((ws / "simulation-run.json").read_text(encoding="utf-8"))
            diverged_run = json.loads(json.dumps(run_doc))
            diverged_run["result"]["payload"]["final_state"]["factor:target_outcome"] = 2e12
            write_json_atomic(ws / "simulation-run.json", diverged_run)
            ev_div = evaluate(ws)
            self.assertTrue(any("[MODEL_NARRATIVE_FIDELITY]" in w for w in ev_div["warnings"]), ev_div["warnings"])

            # 2c) Flat-baseline contradiction triggers MODEL_NARRATIVE_FIDELITY
            flat_run = json.loads(json.dumps(run_doc))
            for k in list(flat_run["result"]["payload"]["final_state"]):
                if k == "factor:target_outcome":
                    flat_run["result"]["payload"]["final_state"][k] = 100.0
                elif k == "factor:shock_driver":
                    flat_run["result"]["payload"]["final_state"][k] = 100.0
            for step in flat_run["result"]["history"]:
                if "factor:target_outcome" in step:
                    step["factor:target_outcome"] = 100.0
                if "factor:shock_driver" in step:
                    step["factor:shock_driver"] = 100.0
            write_json_atomic(ws / "simulation-run.json", flat_run)
            (ws / "REPORT.md").write_text(orig_report + "\n\nThe outcome variable rose by 45% across the simulation horizon.\n", encoding="utf-8")
            ev_flat = evaluate(ws)
            self.assertTrue(any("[MODEL_NARRATIVE_FIDELITY]" in w for w in ev_flat["warnings"]), ev_flat["warnings"])

            # 2d) Trace artifact resolver helper contract
            from aleph.paths import load_trace_rows, resolve_trace_artifact

            rel_path, kind = resolve_trace_artifact({"artifact_paths": {"execution_trace": "execution-trace.json"}}, "2.2.0")
            self.assertEqual((rel_path, kind), ("execution-trace.json", "json"))
            rows = load_trace_rows(ws / "execution-trace.json", kind="json")
            self.assertGreaterEqual(len(rows), 1)

        # 3) Non-empty interventions + uncentered input_mode="level" triggers ZERO_INTERVENTION_DRIFT
        drift_model = compile_model(
            [
                {"id": "factor:src", "role": "exogenous", "scale": "level", "baseline": 100.0},
                {"id": "factor:dst", "role": "endogenous", "scale": "level", "baseline": 10.0},
            ],
            [
                {
                    "id": "causal:level",
                    "from": "factor:src",
                    "to": "factor:dst",
                    "relation": "increases",
                    "sign": 1,
                    "base_strength": 0.5,
                    "lag_ticks": 0,
                    "transform": "linear",
                    "input_mode": "level",
                }
            ],
            [{"id": "iv:1", "target": "factor:src", "op": "add", "value": 5.0, "start_tick": 0, "end_tick": 2}],
            formula_version="2.2.0",
        )
        cfg = EngineConfig(mode="deterministic")
        res = run_deterministic(drift_model, cfg, ticks=4)
        health = inspect_execution_health(drift_model, cfg, ticks=4, result=res)
        self.assertTrue(any(w.get("code") == "ZERO_INTERVENTION_DRIFT" for w in health), health)


class V270VerificationRound2Regressions(unittest.TestCase):
    """Regressions for Round 2 audit findings: schema parity, anti-gaming, elasticity, stock retention, auto_causal, and cross-ref repair."""

    def test_branch_ledger_schema_tail_coverage_is_boolean_and_matches_runtime(self) -> None:
        schema = json.loads((ROOT / "schemas" / "branch-ledger.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["tail_coverage"].get("type"), "boolean")
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "qs"
            res = _run_cli(
                "quickstart_quant.py",
                [
                    "--slug", "schema-tail-check",
                    "--domain", "economics",
                    "--change-point", "Oil spike +10%",
                    "--horizon", "P30D",
                    "--timestep", "P1D",
                    "--out-dir", str(out_dir),
                    "--offline-provisional",
                    "--offline-reason", "schema parity test",
                ],
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            bl = json.loads((out_dir / "schema-tail-check" / "branch-ledger.json").read_text(encoding="utf-8"))
            self.assertIsInstance(bl.get("tail_coverage"), bool)

    def test_offline_provisional_final_anti_gaming_blocks_empty_gaps_and_best_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "qs"
            res = _run_cli(
                "quickstart_quant.py",
                [
                    "--slug", "anti-gaming",
                    "--domain", "economics",
                    "--change-point", "Rate shock",
                    "--horizon", "P30D",
                    "--timestep", "P1D",
                    "--out-dir", str(out_dir),
                    "--offline-provisional",
                    "--offline-reason", "offline eval cutoff",
                ],
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            ws = out_dir / "anti-gaming"
            val_ok = validate_workspace(ws, mode="final", require_report=True, verify_integrity=False)
            self.assertEqual(val_ok["status"], "pass", val_ok)

            # Loophole 1: empty unresolved_critical_gaps in offline_provisional final mode must fail
            manifest_path = ws / "simulation-manifest.json"
            orig = json.loads(manifest_path.read_text(encoding="utf-8"))
            m_empty_gaps = json.loads(json.dumps(orig))
            m_empty_gaps["execution"]["research_control"]["unresolved_critical_gaps"] = []
            write_json_atomic(manifest_path, m_empty_gaps)
            val_gaps = validate_workspace(ws, mode="final", require_report=True, verify_integrity=False)
            self.assertEqual(val_gaps["status"], "fail")
            self.assertIn("EVIDENCE_SATURATION", {i["code"] for i in val_gaps["issues"]})

            # Loophole 2: best-available research_quality in offline_provisional final mode must fail
            m_bad_qual = json.loads(json.dumps(orig))
            m_bad_qual["execution"]["research_quality"] = "best-available"
            write_json_atomic(manifest_path, m_bad_qual)
            val_qual = validate_workspace(ws, mode="final", require_report=True, verify_integrity=False)
            self.assertEqual(val_qual["status"], "fail")
            self.assertIn("RESEARCH_QUALITY", {i["code"] for i in val_qual["issues"]})

    def test_elasticity_220_defaults_to_log_ratio_when_input_mode_omitted(self) -> None:
        m = compile_model(
            [
                {"id": "factor:brent", "role": "exogenous", "scale": "level", "baseline": 80.0},
                {"id": "factor:cpi", "role": "endogenous", "scale": "level", "baseline": 300.0},
            ],
            [
                {
                    "id": "causal:brent-cpi",
                    "from": "factor:brent",
                    "to": "factor:cpi",
                    "relation": "increases",
                    "sign": 1,
                    "base_strength": 0.5,
                    "lag_ticks": 0,
                    "transform": "elasticity",
                }
            ],
            [{"id": "iv:brent-up", "target": "factor:brent", "op": "set", "value": 90.0, "start_tick": 1}],
            formula_version="2.2.0",
        )
        self.assertEqual(m.edges[0].input_mode, "log_ratio")
        res = run_deterministic(m, EngineConfig(mode="deterministic"), ticks=2)
        self.assertTrue(res["ok"], res)
        expected_cpi = 300.0 + math.expm1(0.5 * math.log(90.0 / 80.0))
        self.assertAlmostEqual(res["payload"]["final_state"]["factor:cpi"], expected_cpi, places=6)

        with self.assertRaises(ValueError):
            compile_model(
                [
                    {"id": "factor:brent", "role": "exogenous", "scale": "level", "baseline": 0.0},
                    {"id": "factor:cpi", "role": "endogenous", "scale": "level", "baseline": 300.0},
                ],
                [
                    {
                        "id": "causal:brent-cpi",
                        "from": "factor:brent",
                        "to": "factor:cpi",
                        "relation": "increases",
                        "sign": 1,
                        "base_strength": 0.5,
                        "lag_ticks": 0,
                        "transform": "elasticity",
                    }
                ],
                formula_version="2.2.0",
            )

    def test_stock_coarse_timestep_omitted_retention_unit_warns_in_repair_and_validator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            init_res = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--slug", "stock-warn",
                    "--change-point", "Reserve drawdown",
                    "--time", "2026-01-01",
                    "--horizon", "P6M",
                    "--timestep", "P1M",
                    "--domain", "economics",
                    "--out-dir", td,
                    "--simulation-mode", "deterministic",
                    "--formula-version", "2.2.0",
                    "--offline-provisional",
                    "--offline-reason", "stock retention warning regression",
                ],
            )
            self.assertEqual(init_res.returncode, 0, init_res.stdout + init_res.stderr)
            ws = Path(init_res.stdout.strip().splitlines()[-1]).resolve()
            nodes = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            stock_node = dict(nodes[1])
            stock_node["scale"] = "stock"
            stock_node["retention"] = 0.9
            stock_node.pop("retention_unit", None)
            nodes[1] = stock_node
            write_json_atomic(ws / "nodes.json", nodes)

            rep = _run_cli("repair_model_schema.py", ["--workspace", str(ws), "--dry-run", "--json"])
            self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
            rep_payload = json.loads(rep.stdout)
            self.assertTrue(
                any(w.get("code") == "PLAUSIBILITY_STOCK_RETENTION_UNIT_IMPLICIT" for w in rep_payload.get("warnings", [])),
                rep_payload,
            )

            val = validate_workspace(ws, mode="draft", require_report=False, verify_integrity=False)
            self.assertTrue(
                any(i["code"] == "STOCK_RETENTION_UNIT_IMPLICIT" for i in val["issues"]),
                val["issues"],
            )

            nodes[1]["retention_unit"] = "per_tick"
            write_json_atomic(ws / "nodes.json", nodes)
            rep_clear = json.loads(_run_cli("repair_model_schema.py", ["--workspace", str(ws), "--dry-run", "--json"]).stdout)
            self.assertFalse(any(w.get("code") == "PLAUSIBILITY_STOCK_RETENTION_UNIT_IMPLICIT" for w in rep_clear.get("warnings", [])))
            val_clear = validate_workspace(ws, mode="draft", require_report=False, verify_integrity=False)
            self.assertFalse(any(i["code"] == "STOCK_RETENTION_UNIT_IMPLICIT" for i in val_clear["issues"]))

    def test_auto_causal_apply_into_initialized_validates_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_res = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--slug", "inducer-init",
                    "--change-point", "Oil spike",
                    "--time", "2026-01-01",
                    "--horizon", "P30D",
                    "--domain", "economics",
                    "--out-dir", str(root / "out"),
                    "--simulation-mode", "deterministic",
                    "--formula-version", "2.2.0",
                    "--offline-provisional",
                    "--offline-reason", "auto_causal integration regression",
                ],
            )
            self.assertEqual(init_res.returncode, 0, init_res.stdout + init_res.stderr)
            ws = Path(init_res.stdout.strip().splitlines()[-1]).resolve()
            ev_path = root / "evidence.json"
            prop_path = root / "proposal.json"
            rev_path = root / "review.json"
            write_json_atomic(
                ev_path,
                [
                    {
                        "claim_id": "c1",
                        "source": "oil prices",
                        "target": "cpi",
                        "polarity": "positive",
                        "lag_days": 15,
                        "strength": 0.4,
                        "rationale": "Higher crude oil raises transport and headline consumer prices.",
                        "source_url": "https://example.com/1",
                    }
                ],
            )
            self.assertEqual(_run_cli("auto_causal_inducer.py", ["propose", "--evidence", str(ev_path), "--out", str(prop_path)]).returncode, 0)
            self.assertEqual(_run_cli("auto_causal_inducer.py", ["review", "--proposal", str(prop_path), "--out", str(rev_path)]).returncode, 0)
            app = _run_cli(
                "auto_causal_inducer.py",
                ["apply", "--proposal", str(prop_path), "--review", str(rev_path), "--workspace", str(ws), "--into-initialized"],
            )
            self.assertEqual(app.returncode, 0, app.stdout + app.stderr)
            self.assertEqual(_run_cli("compile_model.py", ["--workspace", str(ws)]).returncode, 0)
            self.assertEqual(_run_cli("run_simulation.py", ["--workspace", str(ws), "--json"]).returncode, 0)
            self.assertEqual(_run_cli("replay_simulation.py", ["--workspace", str(ws), "--json"]).returncode, 0)
            val = json.loads(_run_cli("validate_simulation_artifacts.py", ["--workspace", str(ws), "--mode", "draft", "--json"]).stdout)
            self.assertEqual(val["status"], "pass", val)

    def test_repair_model_schema_propagates_normalized_node_and_edge_ids_across_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "crossref-ws"
            shutil.copytree(FIXTURE, ws)
            # Introduce unprefixed node ID and malformed double-prefixed edge ID across workspace artifacts
            nodes = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            old_nid = nodes[1]["id"]
            bad_nid = "raw driver node"
            nodes[1]["id"] = bad_nid
            write_json_atomic(ws / "nodes.json", nodes)

            edges = json.loads((ws / "edges.json").read_text(encoding="utf-8"))
            old_eid = edges[0]["id"]
            bad_eid = f"causal:edge:{old_eid.split(':', 1)[1]}"
            edges[0]["id"] = bad_eid
            for edge in edges:
                if edge.get("from") == old_nid:
                    edge["from"] = bad_nid
                if edge.get("to") == old_nid:
                    edge["to"] = bad_nid
            write_json_atomic(ws / "edges.json", edges)

            manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            if manifest.get("change_point", {}).get("target") == old_nid:
                manifest["change_point"]["target"] = bad_nid
            manifest["execution"]["research_control"]["offline_provisional"] = True
            manifest["execution"]["research_control"]["offline_reason"] = "crossref test"
            manifest["execution"]["research_control"]["saturation_reached"] = False
            manifest["execution"]["research_control"]["unresolved_critical_gaps"] = ["gap:offline"]
            manifest["execution"]["d_research"] = {"status": "unavailable", "invoked": False}
            manifest["execution"]["research_quality"] = "limited"
            write_json_atomic(ws / "simulation-manifest.json", manifest)

            write_json_atomic(
                ws / "interventions.json",
                [{"id": "iv:renamed", "target": bad_nid, "op": "add", "value": 5.0, "start_tick": 0, "end_tick": 2}],
            )

            branches = json.loads((ws / "branch-ledger.json").read_text(encoding="utf-8"))
            for branch in branches.get("branches", []):
                branch["causal_trace"] = [bad_eid if x == old_eid else x for x in branch.get("causal_trace", [])]
                for ind in branch.get("leading_indicators", []):
                    if isinstance(ind, dict) and ind.get("node") == old_nid:
                        ind["node"] = bad_nid
            write_json_atomic(ws / "branch-ledger.json", branches)

            prop_path = ws / "propagation-trace.jsonl"
            if prop_path.is_file():
                rebuilt_lines: list[str] = []
                prev_hash: str | None = None
                for ln in prop_path.read_text(encoding="utf-8").splitlines():
                    if not ln.strip():
                        continue
                    row = json.loads(ln)
                    if row.get("edge_id") == old_eid:
                        row["edge_id"] = bad_eid
                    for ep in ("from", "to"):
                        if row.get(ep) == old_nid:
                            row[ep] = bad_nid
                    row.pop("hash_chain", None)
                    digest = canonical_hash({"previous_hash": prev_hash, "row": row})
                    row["hash_chain"] = digest
                    prev_hash = digest
                    rebuilt_lines.append(json.dumps(row))
                prop_path.write_text("\n".join(rebuilt_lines) + "\n", encoding="utf-8")

            rep = _run_cli("repair_model_schema.py", ["--workspace", str(ws), "--in-place", "--json"])
            self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
            rep_payload = json.loads(rep.stdout)
            self.assertTrue(any(r.startswith("crossref:branch-ledger.json:") for r in rep_payload.get("repairs", [])), rep_payload)
            self.assertTrue(any(r.startswith("crossref:propagation-trace.jsonl:") for r in rep_payload.get("repairs", [])), rep_payload)

            val = validate_workspace(ws, mode="draft", require_report=False, verify_integrity=False)
            unknown_refs = [i for i in val["issues"] if i.get("code") == "UNKNOWN_REF"]
            self.assertEqual(unknown_refs, [], unknown_refs)
            # Renamed edge ID in causal_trace and node ID in intervention must now resolve
            repaired_ivs = json.loads((ws / "interventions.json").read_text(encoding="utf-8"))
            repaired_nodes = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            node_set = {n["id"] for n in repaired_nodes}
            self.assertIn(repaired_ivs[0]["target"], node_set)
            repaired_branches = json.loads((ws / "branch-ledger.json").read_text(encoding="utf-8"))
            repaired_edges = json.loads((ws / "edges.json").read_text(encoding="utf-8"))
            edge_set = {e["id"] for e in repaired_edges}
            for br in repaired_branches["branches"]:
                for cid in br.get("causal_trace", []):
                    self.assertIn(cid, edge_set)

    def test_repair_model_schema_normalizes_non_canonical_scale_aliases_and_compiles(self):
        import sys
        from pathlib import Path

        scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from aleph.engine import compile_model
        from repair_model_schema import repair_edges, repair_nodes

        repaired, repairs = repair_nodes(
            [
                {"id": "context:shut", "label": "Shutdown", "type": "context", "role": "exogenous", "scale": "binary", "baseline": 1.0},
                {"id": "factor:rate", "label": "Rate", "type": "factor", "role": "endogenous", "scale": "flow_rate", "baseline": 2.0},
            ]
        )
        repaired_edges, edge_repairs = repair_edges(
            [
                {
                    "id": "causal:shut-rate",
                    "from": "context:shut",
                    "to": "factor:rate",
                    "relation": "increases",
                    "sign": 1,
                    "base_strength": 0.4,
                    "lag_ticks": 0,
                    "transform": "step_function",
                }
            ]
        )
        self.assertEqual(repaired[0]["scale"], "level")
        self.assertEqual(repaired[1]["scale"], "flow")
        self.assertEqual(repaired_edges[0]["transform"], "threshold")
        self.assertIn("context:shut:scale_binary_to_level", repairs)
        self.assertIn("factor:rate:scale_flow_rate_to_flow", repairs)
        self.assertIn("causal:shut-rate:transform_step_function_to_threshold", edge_repairs)
        compiled = compile_model(repaired, repaired_edges, [], formula_version="2.2.0")
        self.assertIn("context:shut", compiled.variables)
        self.assertEqual(compiled.variables["context:shut"].scale, "level")
        self.assertEqual(compiled.variables["factor:rate"].scale, "flow")
        self.assertEqual(compiled.edges[0].transform, "threshold")


if __name__ == "__main__":
    unittest.main()
