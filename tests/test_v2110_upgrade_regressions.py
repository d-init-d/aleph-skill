"""Regression tests for Aleph Skill v2.11.0 audit fixes and enhancements."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph import PACKAGE_VERSION, VALIDATOR_VERSION  # noqa: E402
from aleph.auto_causal import apply_model, propose_model, review_model  # noqa: E402
from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    compile_model,
    model_hash,
    model_payload,
    run_monte_carlo,
)
from aleph.execution_binding import (  # noqa: E402
    build_trace_execution_binding,
    generate_workspace_execution_trace,
    select_monte_carlo_trace_run_id,
)
from aleph.io import write_json_atomic  # noqa: E402
from aleph.packets import (  # noqa: E402
    build_knowledge_packet,
    build_receipt,
    dossier_contract_hash,
    scenario_contract_hash,
    verify_assumption_roleplay_receipt,
)
from aleph.privacy import privacy_intake  # noqa: E402
from aleph.quality import _roleplay_tier, evaluate  # noqa: E402
from aleph.trace_contract import validate_execution_trace_data  # noqa: E402
from aleph.validator import validate_report  # noqa: E402
from repair_model_schema import (  # noqa: E402
    _collect_plausibility_warnings,
    repair_edges,
    repair_interventions,
    repair_nodes,
    repair_stochastic_shocks,
    synthesize_missing_target_nodes,
)


def _run_cli(script: str, args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = dict(os.environ)
    merged_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *args],
        cwd=ROOT,
        env=merged_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_cycle_model(*, strength: float, existence_prob: float) -> ComputationalModel:
    nodes = {
        "factor:a": Variable(
            id="factor:a",
            role="factor",
            baseline=10.0,
            scale="level",
            unit="index",
        ),
        "factor:b": Variable(
            id="factor:b",
            role="factor",
            baseline=10.0,
            scale="level",
            unit="index",
        ),
    }
    edges = [
        ModelEdge(
            id="causal:a-b",
            source="factor:a",
            target="factor:b",
            sign=1,
            transform="linear",
            strength=strength,
            lag_ticks=0,
            existence_prob=existence_prob,
        ),
        ModelEdge(
            id="causal:b-a",
            source="factor:b",
            target="factor:a",
            sign=1,
            transform="linear",
            strength=strength,
            lag_ticks=0,
        ),
    ]
    return ComputationalModel(
        variables=nodes,
        edges=edges,
        interventions=[{"id": "int:kick", "target": "factor:a", "op": "add", "value": 2.0, "start_tick": 0, "end_tick": 1}],
        formula_version="2.2.0",
    )


class TestV2110UpgradeRegressions(unittest.TestCase):
    def test_version_constants_bumped_to_2_11_0(self) -> None:
        self.assertEqual(PACKAGE_VERSION, "2.11.0")
        self.assertEqual(VALIDATOR_VERSION, "2.11.0")

    def test_mc_selects_nonzero_valid_run_id_and_handles_all_unresolved(self) -> None:
        # Construct an instantaneous cycle where run 0 fails convergence when ab exists, while runs without ab converge.
        model = _make_cycle_model(strength=2.2, existence_prob=0.5)
        picked_seed = None
        picked_result = None
        picked_cfg = None
        for seed_idx in range(1, 50):
            cfg = EngineConfig(
                mode="monte_carlo",
                seed=f"seed-{seed_idx}",
                min_runs=20,
                max_runs=20,
                batch_size=20,
                max_invalid_fraction=0.99,
                jacobi_max_iter=8,
                scc_solver="jacobi",
                timestep=30.0,
            )
            res = run_monte_carlo(model, cfg, ticks=3)
            summary = res.get("summary") or {}
            branches = summary.get("branches") or []
            reps = [b.get("representative_run") for b in branches]
            if summary.get("invalid_runs", 0) > 0 and "run:0" not in reps and len(branches) > 0:
                picked_seed = seed_idx
                picked_result = res
                picked_cfg = cfg
                break
        self.assertIsNotNone(picked_seed, "expected a seed where run:0 failed while later runs converged")
        assert picked_cfg is not None and picked_result is not None

        chosen = select_monte_carlo_trace_run_id(model, picked_cfg, ticks=3, result=picked_result)
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertGreater(chosen, 0)

        trace = generate_workspace_execution_trace(
            model,
            picked_cfg,
            ticks=3,
            result=picked_result,
            formula_version="2.2.0",
        )
        self.assertGreaterEqual(int(trace["total_samples"]), chosen + 1)
        node_ids = set(model.variables.keys())
        edge_by_id = {e.id: e for e in model.edges}
        trace_issues = [item for item in validate_execution_trace_data(trace, node_ids=node_ids, edge_by_id=edge_by_id) if item.severity == "error"]
        self.assertEqual(trace_issues, [])
        bound, bind_issues = build_trace_execution_binding(
            trace,
            model,
            picked_cfg,
            ticks=3,
            result=picked_result,
            manifest={"formula_version": "2.2.0", "simulation_mode": "monte_carlo"},
        )
        self.assertIsNotNone(bound)
        self.assertEqual([item for item in bind_issues if item.severity == "error"], [])

        # All-unresolved case (100% invalid mass) produces a schema-valid unresolved trace.
        broken_model = _make_cycle_model(strength=2.2, existence_prob=1.0)
        broken_cfg = EngineConfig(
            mode="monte_carlo",
            seed="all-fail",
            min_runs=5,
            max_runs=5,
            batch_size=5,
            max_invalid_fraction=1.0,
            jacobi_max_iter=8,
            scc_solver="jacobi",
            timestep=30.0,
        )
        broken_res = run_monte_carlo(broken_model, broken_cfg, ticks=3)
        self.assertEqual(broken_res["summary"]["invalid_runs"], 5)
        unresolved_trace = generate_workspace_execution_trace(
            broken_model,
            broken_cfg,
            ticks=3,
            result=broken_res,
            formula_version="2.2.0",
        )
        self.assertEqual(unresolved_trace["invalid_mass"], 1.0)
        self.assertEqual(unresolved_trace["steps"], [])
        self.assertEqual(unresolved_trace["total_samples"], 5)
        unresolved_issues = [
            item
            for item in validate_execution_trace_data(
                unresolved_trace,
                node_ids=set(broken_model.variables.keys()),
                edge_by_id={e.id: e for e in broken_model.edges},
            )
            if item.severity == "error"
        ]
        self.assertEqual(unresolved_issues, [])

    def test_auto_causal_into_initialized_refuses_existing_simulation_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposal = propose_model(
                [
                    {
                        "id": "evidence:c1",
                        "claim": "Oil price shocks increase inflation within one month.",
                        "source_url": "https://example.org/oil",
                        "source_tier": "primary",
                        "published_at": "2026-01-01",
                        "retrieved_at": "2026-01-02",
                        "confidence": 0.8,
                    }
                ]
            )
            review = review_model(proposal)

            for protected_name, content in (
                ("execution-trace.json", "{}\n"),
                ("simulation-model.json", "{}\n"),
                ("simulation-run.json", "{}\n"),
                ("replay-report.json", "{}\n"),
                ("propagation-trace.jsonl", json.dumps({"edge_id": "causal:real-edge"}) + "\n"),
            ):
                ws = root / f"ws-{protected_name.replace('.', '_')}"
                init_res = _run_cli(
                    "init_simulation_workspace.py",
                    [
                        "--workspace",
                        str(ws),
                        "--slug",
                        "guard-test",
                        "--change-point",
                        "Oil shock",
                        "--time",
                        "2026-01-01",
                        "--horizon",
                        "P3M",
                        "--observation-cutoff",
                        "2026-01-01",
                        "--offline-provisional",
                    ],
                )
                self.assertEqual(init_res.returncode, 0, init_res.stderr)
                (ws / protected_name).write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "REFUSED_EXISTING_SIMULATION_DATA"):
                    apply_model(proposal, review, ws, into_initialized=True)

    def test_run_sensitivity_includes_stochastic_shocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws-shock-sens"
            init_res = _run_cli(
                "init_simulation_workspace.py",
                [
                    "--workspace",
                    str(ws),
                    "--slug",
                    "shock-sens",
                    "--change-point",
                    "Oil shock",
                    "--time",
                    "2026-01-01",
                    "--horizon",
                    "P3M",
                    "--observation-cutoff",
                    "2026-01-01",
                    "--offline-provisional",
                ],
            )
            self.assertEqual(init_res.returncode, 0, init_res.stderr)

            nodes = [
                {
                    "id": "factor:oil",
                    "type": "factor",
                    "name": "Oil",
                    "description": "Oil index",
                    "status": "assumption",
                    "scale": "level",
                    "unit": "index",
                    "baseline": 80.0,
                    "bounds": [40.0, 200.0],
                    "evidence_ids": [],
                    "time": "2026-01-01",
                },
                {
                    "id": "factor:cpi",
                    "type": "factor",
                    "name": "CPI",
                    "description": "CPI index",
                    "status": "assumption",
                    "scale": "level",
                    "unit": "index",
                    "baseline": 100.0,
                    "bounds": [80.0, 150.0],
                    "evidence_ids": [],
                    "time": "2026-01-01",
                },
            ]
            edges = [
                {
                    "id": "causal:oil-cpi",
                    "from": "factor:oil",
                    "to": "factor:cpi",
                    "relation": "increases",
                    "sign": 1,
                    "mechanism": "Energy cost pass-through to consumer inflation.",
                    "transform": "linear",
                    "base_strength": 0.25,
                    "lag_ticks": 0,
                    "evidence_confidence": 0.7,
                    "status": "admitted",
                    "evidence_ids": [],
                    "assumption": "Pass-through premise",
                }
            ]
            shocks = [
                {
                    "id": "shock:supply_disruption",
                    "target": "factor:oil",
                    "op": "add",
                    "magnitude": 15.0,
                    "probability_per_tick": 0.2,
                    "start_tick": 1,
                    "duration_ticks": 2,
                }
            ]
            manifest = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            manifest["simulation_mode"] = "monte_carlo"
            manifest["stochastic_shocks"] = shocks
            write_json_atomic(ws / "simulation-manifest.json", manifest)
            write_json_atomic(ws / "nodes.json", nodes)
            write_json_atomic(ws / "edges.json", edges)
            write_json_atomic(
                ws / "simulation-config.json",
                {
                    "mode": "monte_carlo",
                    "seed": "7",
                    "n_runs": 10,
                    "timestep": "P1M",
                    "formula_version": "2.2.0",
                    "stochastic_shocks": shocks,
                },
            )
            write_json_atomic(
                ws / "sensitivity-config.json",
                {
                    "method": "oat",
                    "ticks": 4,
                    "output": {"variable": "factor:cpi"},
                    "parameters": [
                        {
                            "id": "param:oil_cpi",
                            "edge_id": "causal:oil-cpi",
                            "min": 0.1,
                            "max": 0.4,
                            "baseline": 0.25,
                        }
                    ],
                },
            )
            comp = _run_cli("compile_model.py", ["--workspace", str(ws)])
            self.assertEqual(comp.returncode, 0, comp.stdout + comp.stderr)
            sens = _run_cli("run_sensitivity.py", ["--workspace", str(ws)])
            self.assertEqual(sens.returncode, 0, sens.stdout + sens.stderr)
            sens_report = json.loads((ws / "sensitivity-report.json").read_text(encoding="utf-8"))
            compiled_model = json.loads((ws / "simulation-model.json").read_text(encoding="utf-8"))
            self.assertEqual(sens_report["model_hash"], compiled_model["model_hash"])

    def test_assumption_roleplay_receipt_and_env_key_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            key_text = "unit-test-aleph-receipt-secret"
            key_bytes = key_text.encode("utf-8")
            manifest = {
                "schema_version": "2.0.0",
                "simulation_id": "sim:assumption-tier-test",
                "change_point": {"type": "decision", "target": "actor:fictional"},
                "temporal_frame": {
                    "mode": "prospective_intervention",
                    "observation_cutoff": "2026-01-01T00:00:00Z",
                },
                "scope": {"horizon": "P1D", "domains": ["fiction"], "geographies": ["test"]},
                "assumptions": [{"id": "assumption:test", "statement": "Premise holds."}],
                "active_contexts": ["context:test"],
                "artifact_paths": {
                    "actors": "actors.json",
                    "human_track_ledger": "human-track-ledger.jsonl",
                },
            }
            actor: dict[str, object] = {
                "id": "actor:fictional",
                "person_node": "entity:fictional",
                "public_role": "",
                "scope_note": "Creative assumption-only simulation",
                "materiality": "material",
                "subject_class": "fictional_person",
                "actor_basis": "assumption",
                "living_status": "fictional",
                "evidence_ids": [],
                "assumptions": ["Actor prefers stability."],
                "decision_graph": {"allowed_actions": ["hold", "pivot"]},
                "institutional_constraints": ["declared premise"],
                "uncertainty_factors": [],
            }
            pkt_res = build_knowledge_packet(
                actor_id="actor:fictional",
                decision_id="decision:test",
                decision_time="2026-01-02T00:00:00Z",
                knowledge_cutoff="2026-01-01T00:00:00Z",
                dossier_hash=dossier_contract_hash(actor),
                scenario_hash=scenario_contract_hash(manifest),
                claims=[],
                institutional_constraints=["declared premise"],
                allowed_actions=["hold", "pivot"],
                unknowns=[],
                assumptions=["Actor prefers stability."],
            )
            self.assertTrue(pkt_res["ok"], pkt_res)
            packet = pkt_res["packet"]
            assert isinstance(packet, dict)
            hypotheses = [
                {
                    "id": "hypothesis:hold",
                    "action": "hold",
                    "reasoning": "Consistent with stability premise.",
                    "constraints_applied": ["declared premise"],
                    "known_unknowns": [],
                    "status": "simulation",
                    "evidence_ids": [],
                },
                {
                    "id": "hypothesis:pivot",
                    "action": "pivot",
                    "reasoning": "Alternative creative option.",
                    "constraints_applied": ["declared premise"],
                    "known_unknowns": [],
                    "status": "simulation",
                    "evidence_ids": [],
                },
            ]
            output = {
                "packet_hash": packet["packet_hash"],
                "actor_id": "actor:fictional",
                "decision_id": "decision:test",
                "execution_id": "execution:roleplay-assumption",
                "status": "completed",
                "network_used": False,
                "tools_used": [],
                "browsed": False,
                "hypotheses": hypotheses,
            }
            packet_bytes = (json.dumps(packet) + "\n").encode("utf-8")
            output_bytes = (json.dumps(output) + "\n").encode("utf-8")
            (ws / "assumption-packet.json").write_bytes(packet_bytes)
            (ws / "assumption-roleplay.json").write_bytes(output_bytes)
            pkt_sha = hashlib.sha256(packet_bytes).hexdigest()
            out_sha = hashlib.sha256(output_bytes).hexdigest()

            receipt = build_receipt(
                runtime_id="runtime:test",
                adapter_id="adapter:test",
                execution_id="execution:roleplay-assumption",
                parent_execution_id=None,
                start="2026-01-01T01:00:00Z",
                end="2026-01-01T02:00:00Z",
                inputs=[{"path": "assumption-packet.json", "sha256": pkt_sha}],
                outputs=[{"path": "assumption-roleplay.json", "sha256": out_sha}],
                network_policy="deny",
                tool_policy="deny",
                observed_tools=[],
                capability_snapshot_hash="a" * 64,
                previous_receipt_hash=None,
                hmac_key=key_bytes,
            )
            verified_standalone = verify_assumption_roleplay_receipt(
                receipt,
                roleplay_id="execution:roleplay-assumption",
                hmac_key=key_bytes,
                require_hmac=True,
            )
            self.assertTrue(verified_standalone["ok"], verified_standalone)

            (ws / "receipts.json").write_text(json.dumps([receipt]), encoding="utf-8")
            actor["roleplay_track"] = {
                "status": "completed",
                "execution_mode": "isolated-pass",
                "agent_ref": "agent:roleplay-assumption",
                "execution_id": "execution:roleplay-assumption",
                "started_at": "2026-01-01T01:00:00Z",
                "completed_at": "2026-01-01T02:00:00Z",
                "artifact": "assumption-roleplay.json",
                "knowledge_cutoff": packet["knowledge_cutoff"],
                "packet_hash": packet["packet_hash"],
                "hypotheses": hypotheses,
            }
            (ws / "actors.json").write_text(json.dumps([actor]), encoding="utf-8")
            row = {
                "actor_id": "actor:fictional",
                "track": "roleplay",
                "execution_mode": "isolated-pass",
                "agent_ref": "agent:roleplay-assumption",
                "execution_id": "execution:roleplay-assumption",
                "started_at": "2026-01-01T01:00:00Z",
                "completed_at": "2026-01-01T02:00:00Z",
                "input_artifact": "assumption-packet.json",
                "input_hash": pkt_sha,
                "output_artifact": "assumption-roleplay.json",
                "output_hash": out_sha,
                "receipt_id": receipt["id"],
                "receipt_hash": receipt["receipt_hash"],
                "previous_receipt_hash": None,
                "receipt_ref": "receipts.json",
                "receipt_attestation": "host",
                "status": "completed",
            }
            (ws / "human-track-ledger.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            write_json_atomic(ws / "simulation-manifest.json", manifest)

            self.assertEqual(_roleplay_tier(ws, manifest, 1, hmac_key=key_bytes), "A")
            # Also verify evaluate() resolves ALEPH_RECEIPT_KEY automatically from environment.
            fake_validation = {
                "status": "pass",
                "error_codes": [],
                "check_results": {"actor_protocol": {"status": "pass", "metrics": {"material_actors": 1}}},
            }
            with mock.patch.dict(os.environ, {"ALEPH_RECEIPT_KEY": key_text}):
                report = evaluate(ws, validation=fake_validation, final_receipt_verified=True)
            self.assertEqual(report["roleplay_tier"], "A")

    def test_interactive_roleplay_iso_timestep_and_normalized_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = ComputationalModel(
                variables={
                    "factor:gdp": Variable(id="factor:gdp", role="factor", baseline=100.0, scale="level", bounds=(50.0, 200.0), unit="index"),
                },
                edges=[],
                formula_version="2.2.0",
            )
            compiled_payload = {
                "schema_version": "2.0.0",
                "formula_version": "2.2.0",
                "model_hash": model_hash(model),
                **model_payload(model),
            }
            model_path = root / "simulation-model.json"
            write_json_atomic(model_path, compiled_payload)
            cfg_path = root / "engine-config.json"
            write_json_atomic(cfg_path, {"mode": "deterministic", "seed": "42", "timestep": "P1M", "formula_version": "2.2.0"})
            spec_path = root / "roleplay-spec.json"
            write_json_atomic(
                spec_path,
                {
                    "schema_version": "1.0.0",
                    "session_id": "session:iso-test",
                    "simulation_id": "sim:iso-test",
                    "actors": ["actor:cb", "actor:mkt"],
                    "decision_ticks": [1],
                    "allowed_actions": {
                        "actor:cb": [
                            {"id": "hold", "name": "Hold", "cost": 0.0, "noop": True},
                            {"id": "stimulate", "name": "Stimulate", "cost": 1.0, "noop": False},
                        ],
                        "actor:mkt": [
                            {"id": "hold", "name": "Hold", "cost": 0.0, "noop": True},
                            {"id": "hedge", "name": "Hedge", "cost": 0.5, "noop": False},
                        ],
                    },
                    "action_effects": {
                        "actor:cb:stimulate": [{"target": "factor:gdp", "magnitude": 5.0, "lag": 0, "op": "add"}],
                        "actor:mkt:hedge": [{"target": "factor:gdp", "magnitude": -1.0, "lag": 0, "op": "add"}],
                    },
                    "payoffs": {
                        "actor:cb": {
                            "objectives": {"factor:gdp": 1.0},
                            "attribution": "Mandate assumption",
                        },
                        "actor:mkt": {
                            "objectives": {"factor:gdp": 0.5},
                            "attribution": "Market assumption",
                        },
                    },
                },
            )
            session_path = root / "session.json"
            init_res = _run_cli(
                "interactive_roleplay.py",
                ["init", "--spec", str(spec_path), "--model", str(model_path), "--config", str(cfg_path), "--out", str(session_path)],
            )
            self.assertEqual(init_res.returncode, 0, init_res.stdout + init_res.stderr)
            pkt_res = _run_cli(
                "interactive_roleplay.py",
                ["packet", "--session", str(session_path), "--model", str(model_path)],
            )
            self.assertEqual(pkt_res.returncode, 0, pkt_res.stdout + pkt_res.stderr)
            pkt_hashes = json.loads(pkt_res.stdout)["packet_hashes"]
            for actor_id, action_id in (("actor:cb", "stimulate"), ("actor:mkt", "hold")):
                commit_res = _run_cli(
                    "interactive_roleplay.py",
                    ["commit", "--session", str(session_path), "--actor", actor_id, "--action", action_id, "--packet-hash", pkt_hashes[actor_id]],
                )
                self.assertEqual(commit_res.returncode, 0, commit_res.stdout + commit_res.stderr)
            adv_res = _run_cli(
                "interactive_roleplay.py",
                ["advance", "--session", str(session_path), "--model", str(model_path), "--spec", str(spec_path)],
            )
            self.assertEqual(adv_res.returncode, 0, adv_res.stdout + adv_res.stderr)
            adv_data = json.loads(adv_res.stdout)
            self.assertTrue(adv_data["ok"])

    def test_repair_model_schema_v2110_cases(self) -> None:
        raw_nodes = [
            {
                "id": "factor:gdp",
                "type": "factor",
                "name": "GDP",
                "status": "assumption",
                "scale": "level",
                "unit": "index",
                "baseline": 100.0,
                "bounds": [150.0, 50.0],
                "time": "2026-01-01",
            }
        ]
        node_id_map: dict[str, str] = {}
        fixed_nodes, node_repairs = repair_nodes(raw_nodes, id_map_out=node_id_map)
        self.assertEqual(fixed_nodes[0]["bounds"], [50.0, 150.0])
        self.assertTrue(any("swapped_reversed_bounds" in r for r in node_repairs))

        raw_edges = [
            {
                "id": "causal:shock-to-gdp",
                "from": "oil_shock",
                "to": "factor:gdp",
                "relation": "decreases",
                "sign": -1,
                "transform": "elasticity",
                "input_mode": "deviation",
                "base_strength": 0.2,
                "lag_ticks": 0,
                "gate": {
                    "op": "all",
                    "conditions": [{"node": "oil_shock", "op": "gt", "value": 1.0}],
                },
            }
        ]
        fixed_edges, _edge_repairs = repair_edges(raw_edges, node_id_map=node_id_map)
        self.assertEqual(fixed_edges[0]["gate"]["conditions"][0]["node"], "factor:oil_shock")
        synth_repairs: list[str] = []
        all_nodes = synthesize_missing_target_nodes(fixed_nodes, fixed_edges, [], [], synth_repairs)
        self.assertIn("factor:oil_shock", {n["id"] for n in all_nodes})
        self.assertTrue(synth_repairs)

        fixed_ivs, _ = repair_interventions(
            [{"target": "factor:gdp", "op": "add", "value": 5.0, "start_tick": "-2", "end_tick": "-1"}],
            node_id_map={"gdp": "factor:gdp", "factor:gdp": "factor:gdp"},
        )
        self.assertEqual(fixed_ivs[0]["start_tick"], 0)
        self.assertEqual(fixed_ivs[0]["end_tick"], 1)

        fixed_shocks, _ = repair_stochastic_shocks(
            [{"target": "factor:gdp", "op": "add", "magnitude": 10.0, "probability_per_tick": 0.1, "start_tick": "-1", "duration_ticks": 2}],
            node_id_map={"gdp": "factor:gdp", "factor:gdp": "factor:gdp"},
        )
        self.assertEqual(fixed_shocks[0]["start_tick"], 0)

        # Elasticity warnings
        zero_base_node = {
            "id": "factor:zero_src",
            "type": "factor",
            "name": "Zero Src",
            "status": "assumption",
            "scale": "level",
            "unit": "index",
            "baseline": 0.0,
            "time": "2026-01-01",
        }
        el_edge_log = {
            "id": "causal:zero-src-gdp",
            "from": "factor:zero_src",
            "to": "factor:gdp",
            "relation": "increases",
            "sign": 1,
            "transform": "elasticity",
            "input_mode": "log_ratio",
            "base_strength": 0.2,
            "lag_ticks": 0,
        }
        warnings = _collect_plausibility_warnings([*all_nodes, zero_base_node], edges=[fixed_edges[0], el_edge_log])
        codes = {w["code"] for w in warnings}
        self.assertIn("ELASTICITY_DEVIATION_INPUT", codes)
        self.assertIn("ELASTICITY_NONPOSITIVE_BASELINE", codes)

    def test_validator_negated_calibrated_probability_and_privacy_severity(self) -> None:
        manifest = {"likelihood_mode": "relative_weight"}
        negated_report = (
            "## Executive Summary\n"
            "This scenario uses relative weights only and assigns no calibrated probability.\n"
            "It is reported without calibrated probability claims.\n"
        )
        neg_res = validate_report(negated_report, manifest, required=False, calibration_verified=False)
        self.assertNotIn("VERBAL_PROBABILITY_CONTRADICTION", {item.code for item in neg_res.issues})

        affirmative_report = (
            "## Executive Summary\n"
            "This scenario yields a calibrated probability of 65%.\n"
        )
        aff_res = validate_report(affirmative_report, manifest, required=False, calibration_verified=False)
        self.assertIn("VERBAL_PROBABILITY_CONTRADICTION", {item.code for item in aff_res.issues})

        priv = privacy_intake(subject_class="invalid", living_status="invalid")
        self.assertTrue(priv["allowed"])
        self.assertFalse(any(item["severity"] == "error" for item in priv["issues"]))
        self.assertTrue(any(item["code"] in {"SUBJECT_CLASS", "ENUM"} and item["severity"] == "warning" for item in priv["issues"]))


if __name__ == "__main__":
    unittest.main()
