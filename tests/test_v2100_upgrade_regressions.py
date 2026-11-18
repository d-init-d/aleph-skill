"""Regression tests for Aleph Skill v2.10.0 isolated-eval fixes and enhancements."""

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

from aleph import PACKAGE_VERSION, VALIDATOR_VERSION  # noqa: E402
from aleph.engine import (  # noqa: E402
    EngineConfig,
    compile_model,
    model_hash,
    run_monte_carlo,
)
from aleph.io import write_json_atomic  # noqa: E402
from aleph.schema import parse_duration_seconds, parse_timestep_seconds  # noqa: E402
from aleph.validator import validate_workspace  # noqa: E402
from compile_model import compile_workspace, load_interventions  # noqa: E402
from repair_model_schema import (  # noqa: E402
    repair_edges,
    repair_interventions,
    repair_nodes,
    repair_stochastic_shocks,
    synthesize_missing_target_nodes,
)


def _run_cli(script: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class TestV2100UpgradeRegressions(unittest.TestCase):
    def test_version_constants_bumped_to_2_10_0(self) -> None:
        self.assertEqual(PACKAGE_VERSION, "2.10.0")
        self.assertEqual(VALIDATOR_VERSION, "2.10.0")

    def test_gated_edge_trace_replay_butterfly_and_final_validation(self) -> None:
        bundle = {
            "temporal_frame": {
                "mode": "prospective_intervention",
                "observation_cutoff": "2026-01-01",
                "simulation_start": "2026-01-01",
                "simulation_end": "2026-06-01",
                "timestep": "P1M",
                "future_projection": True,
            },
            "nodes": [
                {"id": "factor:trigger", "role": "exogenous", "scale": "level", "baseline": 0.5},
                {"id": "factor:gated_out", "role": "endogenous", "scale": "level", "baseline": 10.0},
            ],
            "edges": [
                {
                    "id": "causal:gated-shift",
                    "from": "factor:trigger",
                    "to": "factor:gated_out",
                    "relation": "increases",
                    "sign": 1,
                    "base_strength": 2.0,
                    "lag_ticks": 0,
                    "gate": {"op": "all", "conditions": [{"node": "factor:trigger", "op": "gte", "value": 1.0}]},
                }
            ],
            "interventions": [
                {"id": "int:trip", "target": "factor:trigger", "op": "add", "value": 1.0, "start_tick": 1}
            ],
        }
        with tempfile.TemporaryDirectory(prefix="aleph-v2100-gate-") as td:
            bundle_path = Path(td) / "bundle.json"
            ws = Path(td) / "ws"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            proc = _run_cli(
                "run_pipeline.py",
                [
                    "--workspace",
                    str(ws),
                    "--from-llm-json",
                    str(bundle_path),
                    "--mode",
                    "deterministic",
                    "--forecast-steps",
                    "4",
                    "--finalize",
                ],
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            trace = json.loads((ws / "execution-trace.json").read_text(encoding="utf-8"))
            self.assertTrue(any("gate_multiplier" in step for step in trace["steps"]))

            butterfly = _run_cli("score_butterfly.py", ["--workspace", str(ws)])
            self.assertEqual(butterfly.returncode, 0, butterfly.stdout + butterfly.stderr)
            self.assertEqual(json.loads(butterfly.stdout)["status"], "pass")

            val_final = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(val_final["status"], "pass", val_final)

    def test_regenerate_trace_sync_mode_and_forecast_steps_preserve_tamper_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aleph-v2100-regen-") as td:
            ws = Path(td) / "ws"
            bundle_path = Path(td) / "bundle.json"
            bundle_path.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {"id": "factor:x", "role": "exogenous", "scale": "level", "baseline": 10.0},
                            {"id": "factor:y", "role": "endogenous", "scale": "level", "baseline": 20.0},
                        ],
                        "edges": [
                            {"id": "causal:x-y", "from": "factor:x", "to": "factor:y", "sign": 1, "base_strength": 0.5, "lag_ticks": 0}
                        ],
                        "interventions": [
                            {"id": "int:x", "target": "factor:x", "op": "add", "value": 2.0, "start_tick": 0}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            p1 = _run_cli(
                "run_pipeline.py",
                ["--workspace", str(ws), "--from-llm-json", str(bundle_path), "--mode", "deterministic", "--ticks", "3", "--no-finalize"],
            )
            self.assertEqual(p1.returncode, 0, p1.stdout + p1.stderr)

            # Mutate interventions without --regenerate-trace -> must still fail PARAMETERS_HASH_MISMATCH
            (ws / "interventions.json").write_text(
                json.dumps([{"id": "int:x", "target": "factor:x", "op": "add", "value": 4.0, "start_tick": 0}]),
                encoding="utf-8",
            )
            stale = _run_cli("run_simulation.py", ["--workspace", str(ws), "--mode", "deterministic", "--ticks", "3"])
            self.assertNotEqual(stale.returncode, 0)
            self.assertEqual(json.loads(stale.stdout)["code"], "PARAMETERS_HASH_MISMATCH")

            # Now pass --regenerate-trace --sync-manifest-mode --mode monte_carlo --forecast-steps 5
            regen = _run_cli(
                "run_simulation.py",
                [
                    "--workspace",
                    str(ws),
                    "--mode",
                    "monte_carlo",
                    "--sync-manifest-mode",
                    "--regenerate-trace",
                    "--forecast-steps",
                    "5",
                    "--runs",
                    "20",
                    "--max-runs",
                    "20",
                ],
            )
            self.assertEqual(regen.returncode, 0, regen.stdout + regen.stderr)
            run_doc = json.loads(regen.stdout)
            self.assertEqual(run_doc["mode"], "monte_carlo")
            self.assertEqual(run_doc["ticks"], 6)
            rep = _run_cli("replay_simulation.py", ["--workspace", str(ws)])
            self.assertEqual(rep.returncode, 0, rep.stdout + rep.stderr)
            self.assertTrue(json.loads(rep.stdout)["match"])

    def test_monte_carlo_fan_charts_and_endogenous_hazard_driver_shocks(self) -> None:
        nodes = [
            {"id": "factor:tension", "role": "exogenous", "scale": "level", "baseline": 1.0},
            {"id": "factor:oil", "role": "endogenous", "scale": "level", "baseline": 70.0},
        ]
        edges = [
            {
                "id": "causal:tension-oil",
                "from": "factor:tension",
                "to": "factor:oil",
                "sign": 1,
                "base_strength": 5.0,
                "lag_ticks": 0,
            }
        ]
        # Static shock parity preserved (same model_hash when no gate/hazard_driver)
        static_shocks = [
            {
                "id": "shock:static",
                "target": "factor:oil",
                "op": "add",
                "probability_per_tick": 0.1,
                "magnitude": 10.0,
                "start_tick": 0,
                "end_tick": 4,
            }
        ]
        m_static = compile_model(nodes, edges, formula_version="2.2.0", stochastic_shocks=static_shocks)
        self.assertNotIn("gate", m_static.stochastic_shocks[0])
        self.assertNotIn("hazard_driver", m_static.stochastic_shocks[0])

        # High-tension trajectory vs low-tension gated/hazard trajectory
        endo_shocks = [
            {
                "id": "shock:chokepoint",
                "target": "factor:oil",
                "op": "add",
                "probability_per_tick": 0.0,
                "magnitude": 30.0,
                "start_tick": 1,
                "end_tick": 6,
                "duration_ticks": 2,
                "gate": {"op": "all", "conditions": [{"node": "factor:tension", "op": "gte", "value": 2.0}]},
                "hazard_driver": {"node": "factor:tension", "threshold": 2.0, "slope": 0.5, "max_probability": 0.9},
            }
        ]
        m_low = compile_model(nodes, edges, [], formula_version="2.2.0", stochastic_shocks=endo_shocks)
        m_high = compile_model(
            nodes,
            edges,
            [{"id": "int:spike", "target": "factor:tension", "op": "add", "value": 2.0, "start_tick": 0}],
            formula_version="2.2.0",
            stochastic_shocks=endo_shocks,
        )
        self.assertNotEqual(model_hash(m_low), model_hash(m_high))
        cfg = EngineConfig(seed="42", mode="monte_carlo", min_runs=40, max_runs=40, batch_size=40)
        res_low = run_monte_carlo(m_low, cfg, ticks=6)
        res_high = run_monte_carlo(m_high, cfg, ticks=6)
        self.assertTrue(res_low["ok"] and res_high["ok"])
        summary_high = res_high["summary"]
        for q_key in ("p05", "p10", "p50", "p90", "p95"):
            self.assertIn(q_key, summary_high["quantiles"]["factor:oil"])
        self.assertEqual(len(summary_high["trajectory_quantiles"]), 6)
        self.assertIn("p90", summary_high["trajectory_quantiles"][0]["factor:oil"])
        # Low-tension branch never trips gate; high-tension tail exceeds base
        self.assertFalse(any(b.get("tail") for b in res_low["summary"]["branches"]))
        self.assertTrue(any(b.get("tail") and b.get("tail_class") == "tail_shock" for b in summary_high["branches"]))
        self.assertGreater(summary_high["quantiles"]["factor:oil"]["p95"], res_low["summary"]["quantiles"]["factor:oil"]["p95"])

        # End-to-end finalized workspace with stochastic_shocks passes validate_workspace(mode="final")
        with tempfile.TemporaryDirectory(prefix="aleph-v2100-mc-final-") as td:
            bp = Path(td) / "mc_bundle.json"
            ws = Path(td) / "ws"
            bp.write_text(
                json.dumps(
                    {
                        "nodes": nodes,
                        "edges": edges,
                        "interventions": [{"id": "int:spike", "target": "factor:tension", "op": "add", "value": 2.0, "start_tick": 0}],
                        "stochastic_shocks": endo_shocks,
                    }
                ),
                encoding="utf-8",
            )
            pipe = _run_cli(
                "run_pipeline.py",
                [
                    "--workspace",
                    str(ws),
                    "--from-llm-json",
                    str(bp),
                    "--mode",
                    "monte_carlo",
                    "--forecast-steps",
                    "5",
                    "--runs",
                    "30",
                    "--finalize",
                ],
            )
            self.assertEqual(pipe.returncode, 0, pipe.stdout + pipe.stderr)
            report_text = (ws / "REPORT.md").read_text(encoding="utf-8")
            self.assertIn("### Monte Carlo quantile envelope", report_text)
            val_final = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(val_final["status"], "pass", val_final)

    def test_zero_iso_durations_and_change_point_double_injection_guard(self) -> None:
        self.assertEqual(parse_duration_seconds("P0M"), 0.0)
        self.assertEqual(parse_duration_seconds("P0W"), 0.0)
        self.assertEqual(parse_duration_seconds("P0Y"), 0.0)
        self.assertEqual(parse_duration_seconds("PT0S"), 0.0)
        self.assertIsNone(parse_duration_seconds("P"))
        self.assertIsNone(parse_duration_seconds("PT"))
        self.assertIsNone(parse_timestep_seconds("P0M"))

        with tempfile.TemporaryDirectory(prefix="aleph-v2100-cp-") as td:
            ws = Path(td)
            manifest = {
                "artifact_paths": {"interventions": "interventions.json"},
                "change_point": {"type": "factor_change", "target": "factor:cpi", "magnitude": 1.0},
            }
            (ws / "interventions.json").write_text(
                json.dumps([{"id": "int:tariff", "target": "factor:tariff", "op": "add", "value": 10.0, "start_tick": 0}]),
                encoding="utf-8",
            )
            loaded = load_interventions(ws, manifest)
            self.assertEqual([item["id"] for item in loaded], ["int:tariff"])

    def test_repair_model_schema_covers_all_llm_quirks(self) -> None:
        raw_nodes = [
            {"id": "factor:a", "scale": "level", "baseline": 1.0, "retention": 0.9, "retention_mode": "baseline", "inertia": 1.0},
        ]
        raw_edges = [
            {"id": "edge:ab", "from": "factor:a", "to": "factor:b", "gate": "any", "sign": 1, "base_strength": 0.5},
        ]
        raw_ivs = [
            {"id": "iv:1", "target_node": "missing_exo", "mode": "step", "magnitude": 3.0, "start_tick": 2, "end_tick": 2},
            {"id": "iv:qual", "target": "factor:a", "action_type": "ruling", "start_tick": 1, "end_tick": 3},
        ]
        raw_shocks = [
            {
                "id": "shock:1",
                "variable": "factor:b",
                "probability": 0.2,
                "value": 5.0,
                "mode": "pulse",
                "start_tick": 1,
                "end_tick": 1,
                "hazard_driver": {"node": "factor:a", "threshold": 0.5, "slope": 0.1},
            }
        ]
        repairs: list[str] = []
        nodes, nr = repair_nodes(raw_nodes)
        repairs.extend(nr)
        self.assertNotIn("retention", nodes[0])
        self.assertNotIn("retention_mode", nodes[0])
        self.assertNotIn("inertia", nodes[0])

        edges, er = repair_edges(raw_edges)
        repairs.extend(er)
        self.assertNotIn("gate", edges[0])

        ivs, ir = repair_interventions(raw_ivs)
        repairs.extend(ir)
        self.assertEqual(ivs[0]["target"], "factor:missing_exo")
        self.assertEqual(ivs[0]["op"], "add")
        self.assertEqual(ivs[0]["end_tick"], 3)
        self.assertEqual(ivs[1]["op"], "add")
        self.assertEqual(ivs[1]["value"], 0.0)

        shocks, sr = repair_stochastic_shocks(raw_shocks, repairs=repairs)
        repairs.extend(sr)
        self.assertEqual(shocks[0]["target"], "factor:b")
        self.assertEqual(shocks[0]["op"], "add")
        self.assertEqual(shocks[0]["end_tick"], 2)

        synth = synthesize_missing_target_nodes(nodes, edges, ivs, shocks, repairs)
        synth_ids = {n["id"] for n in synth}
        self.assertEqual(synth_ids, {"factor:b", "factor:missing_exo"})

    def test_run_pipeline_preserves_mixed_case_underscore_workspace_path(self) -> None:
        bundle = {
            "nodes": [
                {"id": "factor:a", "role": "exogenous", "scale": "level", "baseline": 1.0},
                {"id": "factor:b", "role": "endogenous", "scale": "level", "baseline": 2.0},
            ],
            "edges": [
                {"id": "edge:ab", "from": "factor:a", "to": "factor:b", "sign": 1, "base_strength": 0.5, "lag_ticks": 0},
            ],
            "interventions": [
                {"id": "iv:1", "target": "factor:a", "op": "add", "value": 2.0, "start_tick": 0},
            ],
        }
        with tempfile.TemporaryDirectory(prefix="aleph-v2100-ws-") as td:
            root = Path(td)
            bundle_path = root / "bundle.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            ws = root / "Mixed_Case_WS_01"
            res = _run_cli(
                "run_pipeline.py",
                [
                    "--workspace", str(ws),
                    "--from-llm-json", str(bundle_path),
                    "--mode", "deterministic",
                    "--forecast-steps", "4",
                    "--finalize",
                    "--json",
                ],
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertTrue((ws / "simulation-manifest.json").is_file())
            self.assertEqual(validate_workspace(ws, mode="final", require_report=True)["status"], "pass")

    def test_replay_simulation_preserves_legacy_v290_change_point_model_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aleph-v2100-legacy-cp-") as td:
            ws = Path(td) / "legacy_ws"
            init = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--workspace", str(ws),
                    "--change-point", "Legacy dual-injected change point",
                    "--time", "2026-01-01",
                    "--horizon", "P4M",
                    "--timestep", "P1M",
                    "--simulation-mode", "deterministic",
                    "--formula-version", "2.2.0",
                    "--offline-provisional",
                    "--offline-reason", "legacy compatibility test",
                ],
            )
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest["change_point"]["target"] = "factor:oil"
            manifest["change_point"]["magnitude"] = 0.0
            write_json_atomic(ws / "simulation-manifest.json", manifest)
            write_json_atomic(
                ws / "nodes.json",
                [
                    {"id": "factor:oil", "role": "exogenous", "scale": "level", "baseline": 70.0},
                    {"id": "factor:cpi", "role": "endogenous", "scale": "level", "baseline": 300.0},
                ],
            )
            write_json_atomic(
                ws / "edges.json",
                [
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
                ],
            )
            write_json_atomic(
                ws / "interventions.json",
                [
                    {"id": "iv:oil", "target": "factor:oil", "op": "add", "value": 5.0, "start_tick": 1, "end_tick": 4}
                ],
            )
            # Simulate a pre-v2.10.0 compiled model that included the metadata change_point=0.0 entry
            legacy_compiled = compile_workspace(ws, formula_version="2.2.0", include_legacy_change_point=True)
            write_json_atomic(ws / "simulation-model.json", legacy_compiled)
            run_res = _run_cli("run_simulation.py", ["--workspace", str(ws), "--mode", "deterministic", "--ticks", "5", "--json"])
            self.assertEqual(run_res.returncode, 0, run_res.stdout + run_res.stderr)
            replay_res = _run_cli("replay_simulation.py", ["--workspace", str(ws), "--json"])
            self.assertEqual(replay_res.returncode, 0, replay_res.stdout + replay_res.stderr)
            self.assertTrue(json.loads(replay_res.stdout)["model_hash_ok"])


if __name__ == "__main__":
    unittest.main()
