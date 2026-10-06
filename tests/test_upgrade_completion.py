"""Regression contracts for candidate upgrades; no external services required."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from aleph import auto_causal  # noqa: E402
from aleph.auto_causal import apply_model, propose_model, review_model  # noqa: E402
from aleph.engine import EngineConfig, model_payload  # noqa: E402
from aleph.interactive_roleplay import (  # noqa: E402
    advance_roleplay_round,
    commit_actor_action,
    generate_actor_packets,
    init_roleplay_session,
    replay_roleplay_session,
)
from aleph.io import canonical_hash  # noqa: E402
from compile_model import compile_workspace  # noqa: E402
from test_interactive_roleplay import _create_2actor_spec, _create_test_model  # noqa: E402


def grounded_proposal():
    mechanism = "credit contraction reduces investment spending by restricting borrowing available to businesses"
    rows = [{"record_type": "claim", "claim_id": "claim:test", "confidence": "high",
        "claim": f"Policy Rate reduces Output Gap via {mechanism}.",
        "evidence": "Effect size of 0.4 with a lag of 2 ticks.",
        "node_measurements": {"Policy Rate": {"baseline": 1.0, "unit": "index", "time": "2026-01-01"},
                              "Output Gap": {"baseline": 2.0, "unit": "index", "time": "2026-01-01"}}}]
    return propose_model(rows, created_at="2026-01-01T00:00:00Z")


def manual_review(proposal):
    decisions = {"edge_decisions": {e["id"]: {"decision": "admit", "rationale": "Reviewed measured effect, lag and source mechanism."}
                    for e in proposal["candidate_edges"]},
                 "node_decisions": {n["id"]: {"decision": "admit", "rationale": "Reviewed independent measurement for this node."}
                    for n in proposal["candidate_nodes"]}}
    return review_model(proposal, reviewer="human:test-reviewer", decisions=decisions, reviewed_at="2026-01-02T00:00:00Z")


class ApplyCompletionTests(unittest.TestCase):
    def test_screening_cannot_authorize_execution(self):
        proposal = grounded_proposal()
        self.assertEqual(proposal["gaps"], [])
        screening = review_model(proposal)
        self.assertEqual(screening["overall_status"], "partial")
        with tempfile.TemporaryDirectory() as raw:
            result = apply_model(proposal, screening, Path(raw) / "draft")
            self.assertTrue(result["ok"])
            self.assertFalse(result["runnable"])
            self.assertFalse((Path(result["workspace_dir"]) / "simulation-model.json").exists())

    def test_disk_compile_run_replay_and_no_template_intervention(self):
        proposal = grounded_proposal()
        review = manual_review(proposal)
        self.assertEqual(review["overall_status"], "approved")
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "mô hình có dấu"
            result = apply_model(proposal, review, target)
            self.assertTrue(result["replay_ok"])
            saved = json.loads((target / "simulation-model.json").read_text())
            self.assertEqual(saved, compile_workspace(target))
            self.assertEqual(saved["interventions"], [])
            self.assertEqual(saved["model_hash"], result["model_hash"])
            self.assertTrue((target / "execution-trace.json").is_file())
            self.assertTrue(json.loads((target / "replay-report.json").read_text())["match"])

    def test_publish_failure_preserves_absent_and_empty_targets(self):
        proposal = grounded_proposal()
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as raw:
                target = Path(raw) / "output"
                if existing:
                    target.mkdir()
                real_replace = auto_causal.os.replace
                publications = []
                def fail_publication(source, destination, *, target=target, publications=publications, real_replace=real_replace):
                    if Path(destination) == target:
                        publications.append(source)
                        raise OSError("publish failed")
                    return real_replace(source, destination)
                with patch.object(auto_causal.os, "replace", side_effect=fail_publication):
                    with self.assertRaises(OSError):
                        apply_model(proposal, review_model(proposal), target)
                self.assertEqual(len(publications), 1)
                self.assertEqual(target.exists(), existing)
                self.assertEqual(list(target.iterdir()) if existing else [], [])
                self.assertEqual(list(Path(raw).glob(".aleph-apply-*")), [])

    def test_any_existing_history_or_artifact_is_refused(self):
        proposal = grounded_proposal()
        for name in ("execution-trace.json", "execution-trace.jsonl", "nodes.json", "simulation-manifest.json", "unrelated.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                target = Path(raw)
                content = b'preserve-this-history\n'
                (target / name).write_bytes(content)
                result = apply_model(proposal, manual_review(proposal), target)
                self.assertFalse(result["ok"])
                self.assertEqual([p.name for p in target.iterdir()], [name])
                self.assertEqual((target / name).read_bytes(), content)

    def test_engine_failure_cannot_publish(self):
        proposal = grounded_proposal()
        with tempfile.TemporaryDirectory() as raw, patch.object(auto_causal, "_verify_staged_execution", side_effect=ValueError("engine failed")):
            target = Path(raw) / "model"
            with self.assertRaisesRegex(ValueError, "engine failed"):
                apply_model(proposal, manual_review(proposal), target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(raw).iterdir()), [])

    def test_missing_baselines_and_retention_remain_unknown(self):
        proposal = propose_model([{"claim_id": "claim:debt", "claim": "Debt stock increases Output via lending",
            "evidence": "by 0.4 after 2 ticks. Baseline of 1 observed on 2026-01-01."}])
        self.assertTrue(all(n["baseline"] is None for n in proposal["candidate_nodes"]))
        stock = next(n for n in proposal["candidate_nodes"] if n["scale"] == "stock")
        self.assertIsNone(stock["retention"])
        self.assertIn("missing_retention", {g["gap_type"] for g in proposal["gaps"]})


    def test_reviewed_lag_transform_and_intervention_roundtrip(self):
        proposal = grounded_proposal()
        edge = proposal["candidate_edges"][0]
        edge["transform"] = "threshold"
        edge["transform_parameters"] = {"mode": "hysteresis", "threshold": 1.0, "theta_on": 2.0, "theta_off": 1.0}
        edge["saturation"] = 10.0
        review = manual_review(proposal)
        review["interventions"] = [{"id": "int:reviewed", "target": edge["from"], "op": "add", "value": 0.2, "start_tick": 0, "end_tick": None}]
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "model"
            result = apply_model(proposal, review, target)
            self.assertTrue(result["replay_ok"])
            saved = json.loads((target / "simulation-model.json").read_text())
            self.assertEqual(saved, compile_workspace(target))
            self.assertEqual(saved["interventions"], review["interventions"])
            saved_edge = saved["edges"][0]
            self.assertEqual(saved_edge["lag_ticks"], 2)
            self.assertEqual(saved_edge["lag_unit"], "ticks")
            self.assertEqual(saved_edge["transform"], "threshold")
            self.assertEqual(saved_edge["transform_parameters"], edge["transform_parameters"])

    def test_sidecar_contract_errors_refused_before_writes(self):
        for mutation in (lambda p: p.update(schema_version="bad"),
                         lambda p: p.update(unexpected=True),
                         lambda p: p.update(created_at="2026-02-30T00:00:00Z"),
                         lambda p: p["candidate_nodes"][0].update(baseline=True),
                         lambda p: p["candidate_edges"][0].update(effect_size=10**500)):
            proposal = grounded_proposal()
            review = manual_review(proposal)
            mutation(proposal)
            with tempfile.TemporaryDirectory() as raw:
                target = Path(raw) / "output"
                with self.assertRaisesRegex(ValueError, "SIDECAR_SCHEMA"):
                    apply_model(proposal, review, target)
                self.assertFalse(target.exists())

    def test_review_contract_requires_execution_kind(self):
        proposal = grounded_proposal()
        review = manual_review(proposal)
        del review["review_kind"]
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ValueError, "SIDECAR_SCHEMA"):
                apply_model(proposal, review, Path(raw) / "output")

    def test_rejected_node_cannot_make_approved_graph(self):
        proposal = grounded_proposal()
        review = manual_review(proposal)
        decisions = {"node_decisions": review["node_decisions"], "edge_decisions": review["edge_decisions"]}
        decisions["node_decisions"][proposal["candidate_nodes"][0]["id"]] = {"decision": "reject", "rationale": "Rejected measurement."}
        review = review_model(proposal, reviewer="human:test", decisions=decisions)
        self.assertNotEqual(review["overall_status"], "approved")

    def test_windows_empty_target_rollback_contract(self):
        with tempfile.TemporaryDirectory() as raw:
            target, stage = Path(raw) / "target", Path(raw) / "stage"
            target.mkdir()
            stage.mkdir()
            (stage / "model.json").write_text("{}")
            with patch.object(auto_causal.sys, "platform", "win32"), patch.object(auto_causal.os, "replace", side_effect=OSError("rename failed")):
                with self.assertRaises(OSError):
                    auto_causal._publish_workspace(stage, target)
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])
            self.assertTrue((stage / "model.json").is_file())

    def test_cli_refusal_exits_nonzero_without_traceback(self):
        proposal = grounded_proposal()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "proposal.json").write_text(json.dumps(proposal))
            (root / "review.json").write_text(json.dumps(manual_review(proposal)))
            target = root / "existing"
            target.mkdir()
            (target / "execution-trace.json").write_text("history")
            command = subprocess.run([sys.executable, str(ROOT / "scripts/auto_causal_inducer.py"), "apply", "--proposal", str(root / "proposal.json"), "--review", str(root / "review.json"), "--workspace", str(target)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(command.returncode, 0)
            self.assertFalse(json.loads(command.stdout)["ok"])
            self.assertNotIn("Traceback", command.stderr)
            self.assertEqual((target / "execution-trace.json").read_text(), "history")


class RoleplayCompletionTests(unittest.TestCase):
    def session(self):
        model = _create_test_model()
        session = init_roleplay_session(_create_2actor_spec(), model)
        packets = generate_actor_packets(session, model)
        for actor in session["spec"]["actors"]:
            commit_actor_action(session, actor, "noop", packets[actor]["packet_hash"])
        return session, model

    def test_changed_config_or_spec_cannot_issue_or_execute(self):
        for operation in (generate_actor_packets, advance_roleplay_round):
            session, model = self.session()
            before = copy.deepcopy(session)
            with self.assertRaisesRegex(ValueError, "CONFIG_MISMATCH"):
                operation(session, model, EngineConfig(seed=999))
            self.assertEqual(session, before)
            session["spec"]["payoffs"]["US"]["state_weights"]["inflation"] = 99
            with self.assertRaisesRegex(ValueError, "SPEC_MISMATCH"):
                operation(session, model)

    def test_json_resume_and_rehashed_intervention_tamper(self):
        session, model = self.session()
        session = json.loads(json.dumps(session))
        advance_roleplay_round(session, model)
        self.assertTrue(replay_roleplay_session(session, model)["ok"])
        session["rounds"][0]["interventions"] = [{"id": "malicious", "target": "inflation", "op": "add", "value": 100, "start_tick": 0}]
        record = session["rounds"][0]
        record["round_hash"] = canonical_hash({k: v for k, v in record.items() if k != "round_hash"})
        self.assertIn("INTERVENTION_MISMATCH", replay_roleplay_session(session, model)["error"])

    def test_missing_identity_cannot_replay(self):
        session, model = self.session()
        del session["model_hash"]
        self.assertFalse(replay_roleplay_session(session, model)["ok"])

    def test_formula_change_cannot_execute(self):
        session, model = self.session()
        model.formula_version = "2.0.0"
        with self.assertRaisesRegex(ValueError, "FORMULA_MISMATCH"):
            advance_roleplay_round(session, model)

    def test_cli_resumes_three_rounds_with_persisted_custom_config(self):
        with tempfile.TemporaryDirectory(prefix="roleplay có dấu ") as raw:
            root = Path(raw)
            (root / "simulation-model.json").write_text(json.dumps({**model_payload(_create_test_model()), "formula_version": "2.1"}))
            (root / "spec.json").write_text(json.dumps(_create_2actor_spec()))
            (root / "config.json").write_text(json.dumps({"seed": 999}))
            session_path = root / "session.json"
            cli = ROOT / "scripts/interactive_roleplay.py"
            def invoke(*arguments):
                command = subprocess.run([sys.executable, str(cli), *map(str, arguments)], capture_output=True, text=True, timeout=15)
                self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
                return json.loads(command.stdout)
            invoke("init", "--spec", root / "spec.json", "--workspace", root, "--config", root / "config.json", "--out", session_path)
            for index in range(3):
                packets = invoke("packet", "--session", session_path, "--workspace", root)
                self.assertEqual(packets["round_index"], index)
                for actor, packet_hash in packets["packet_hashes"].items():
                    invoke("commit", "--session", session_path, "--actor", actor, "--action", "noop", "--packet", packet_hash)
                invoke("advance", "--session", session_path, "--workspace", root)
            receipt = invoke("finalize", "--session", session_path)
            self.assertEqual(receipt, invoke("finalize", "--session", session_path))
            replay = invoke("replay", "--session", session_path, "--workspace", root)
            self.assertTrue(replay["match"])
            self.assertEqual(replay["replayed_rounds_count"], 3)

    def test_cli_io_failure_keeps_saved_session(self):
        from argparse import Namespace

        import interactive_roleplay as cli
        session, _ = self.session()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "session.json"
            path.write_text(json.dumps(session))
            before = path.read_bytes()
            with patch.object(cli, "write_json_atomic", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    cli.cmd_finalize(Namespace(session=str(path)))
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
