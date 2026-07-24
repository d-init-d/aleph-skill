from __future__ import annotations

import csv
import hashlib
import hmac
import io
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

from aleph.import_ledger import (  # noqa: E402
    D_RESEARCH_SIGNATURE_VERSION,
    FIELDS_V3_1,
    canonicalise_d_research_csv,
)
from aleph.io import canonical_hash  # noqa: E402
from aleph.packets import (  # noqa: E402
    build_knowledge_packet,
    build_receipt,
    dossier_contract_hash,
    scenario_contract_hash,
    validate_actor_protocol,
    validate_knowledge_packet,
    validate_roleplay_output,
)
from aleph.privacy import privacy_intake  # noqa: E402
from aleph.quality import _d_research_verified, _roleplay_tier  # noqa: E402

H1 = "1" * 64
H2 = "2" * 64


def _packet(*, dossier_hash: str = H1, scenario_hash: str = H2) -> dict[str, object]:
    result = build_knowledge_packet(
        actor_id="actor:test",
        decision_id="decision:test",
        decision_time="2026-01-02T00:00:00Z",
        knowledge_cutoff="2026-01-01T00:00:00Z",
        dossier_hash=dossier_hash,
        scenario_hash=scenario_hash,
        claims=[
            {
                "id": "claim:one",
                "text": "The public institution adopted a formal rule.",
                "available_at": "2026-01-01T00:00:00Z",
                "actor_access": "public_role",
                "access_basis": "public_role",
            }
        ],
        institutional_constraints=["law"],
        allowed_actions=["approve", "delay"],
        unknowns=["timing"],
    )
    assert result["ok"]
    packet = result["packet"]
    assert isinstance(packet, dict)
    return packet


def _roleplay_output(packet: dict[str, object]) -> dict[str, object]:
    return {
        "packet_hash": packet["packet_hash"],
        "actor_id": packet["actor_id"],
        "decision_id": packet["decision_id"],
        "execution_id": "execution:roleplay",
        "status": "completed",
        "network_used": False,
        "tools_used": [],
        "browsed": False,
        "hypotheses": [
            {
                "id": "hypothesis:approve",
                "action": "approve",
                "reasoning": "The institutional process permits approval.",
                "constraints_applied": ["law"],
                "known_unknowns": ["timing"],
                "status": "simulation",
                "evidence_ids": [],
            },
            {
                "id": "hypothesis:delay",
                "action": "delay",
                "reasoning": "The institutional process also permits delay.",
                "constraints_applied": ["law"],
                "known_unknowns": ["timing"],
                "status": "simulation",
                "evidence_ids": [],
            },
        ],
    }


def _write_receipted_roleplay_workspace(
    workspace: Path,
    *,
    stored_packet: object | None = None,
    duplicate_packet_key: bool = False,
    extra_roleplay_input: bool = False,
    key: bytes = b"receipt-secret",
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, bytes]]:
    manifest: dict[str, object] = {
        "schema_version": "2.0.0",
        "simulation_id": "sim:roleplay-test",
        "change_point": {"type": "decision", "target": "actor:test"},
        "temporal_frame": {
            "mode": "prospective_intervention",
            "observation_cutoff": "2026-01-01T00:00:00Z",
        },
        "scope": {"horizon": "P1D", "domains": ["policy"], "geographies": ["test"]},
        "assumptions": [{"id": "assumption:test", "statement": "Test assumption"}],
        "active_contexts": ["context:test"],
        "artifact_paths": {
            "actors": "actors.json",
            "human_track_ledger": "human-track-ledger.jsonl",
        },
    }
    actor: dict[str, object] = {
        "id": "actor:test",
        "person_node": "entity:test",
        "public_role": "Public official",
        "scope_note": "Public-role decisions only",
        "materiality": "material",
        "subject_class": "public_role_person",
        "living_status": "living",
        "evidence_ids": ["evidence:test"],
        "decision_graph": {"allowed_actions": ["approve", "delay"]},
        "institutional_constraints": ["law"],
        "uncertainty_factors": ["timing"],
        "research_track": {
            "status": "completed",
            "agent_ref": "agent:research",
            "execution_id": "execution:research",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T01:00:00Z",
            "artifact": "packet.json",
            "claims": [
                {
                    "id": "claim:one",
                    "claim": "The public institution adopted a formal rule.",
                    "evidence_ids": ["evidence:test"],
                    "confidence": 0.9,
                    "available_at": "2026-01-01T00:00:00Z",
                    "access_basis": "public_role",
                }
            ],
        },
    }
    packet = _packet(
        dossier_hash=dossier_contract_hash(actor),
        scenario_hash=scenario_contract_hash(manifest),
    )
    output = _roleplay_output(packet)
    packet_text = json.dumps(packet if stored_packet is None else stored_packet)
    if duplicate_packet_key:
        packet_text = '{"allowed_actions":["foreign-action"],' + packet_text[1:]
    packet_bytes = (packet_text + "\n").encode()
    output_bytes = (json.dumps(output) + "\n").encode()
    artifacts = {
        "research-input.json": b'{"question":"test"}\n',
        "packet.json": packet_bytes,
        "roleplay.json": output_bytes,
    }
    if extra_roleplay_input:
        artifacts["raw-evidence.json"] = b'{"forbidden":"roleplay input"}\n'
    digests: dict[str, str] = {}
    for relative, content in artifacts.items():
        (workspace / relative).write_bytes(content)
        digests[relative] = hashlib.sha256(content).hexdigest()

    hypotheses = output["hypotheses"]
    assert isinstance(hypotheses, list)
    actor["roleplay_track"] = {
        "status": "completed",
        "agent_ref": "agent:roleplay",
        "execution_id": "execution:roleplay",
        "started_at": "2026-01-01T01:01:00Z",
        "completed_at": "2026-01-01T02:00:00Z",
        "artifact": "roleplay.json",
        "knowledge_cutoff": packet["knowledge_cutoff"],
        "packet_hash": packet["packet_hash"],
        "hypotheses": hypotheses,
    }
    actors = [actor]
    (workspace / "actors.json").write_text(json.dumps(actors), encoding="utf-8")

    research = build_receipt(
        runtime_id="runtime:test",
        adapter_id="adapter:test",
        execution_id="execution:research",
        parent_execution_id=None,
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T01:00:00Z",
        inputs=[{"path": "research-input.json", "sha256": digests["research-input.json"]}],
        outputs=[{"path": "packet.json", "sha256": digests["packet.json"]}],
        network_policy="allow",
        tool_policy="allow",
        observed_tools=["browser"],
        capability_snapshot_hash=H1,
        previous_receipt_hash=None,
        hmac_key=key,
    )
    roleplay_inputs = [{"path": "packet.json", "sha256": digests["packet.json"]}]
    if extra_roleplay_input:
        roleplay_inputs.append(
            {"path": "raw-evidence.json", "sha256": digests["raw-evidence.json"]}
        )
    roleplay = build_receipt(
        runtime_id="runtime:test",
        adapter_id="adapter:test",
        execution_id="execution:roleplay",
        parent_execution_id="execution:research",
        start="2026-01-01T01:01:00Z",
        end="2026-01-01T02:00:00Z",
        inputs=roleplay_inputs,
        outputs=[{"path": "roleplay.json", "sha256": digests["roleplay.json"]}],
        network_policy="deny",
        tool_policy="deny",
        observed_tools=[],
        capability_snapshot_hash=H1,
        previous_receipt_hash=research["receipt_hash"],
        hmac_key=key,
    )
    (workspace / "receipts.json").write_text(
        json.dumps([research, roleplay]), encoding="utf-8"
    )
    rows: list[dict[str, object]] = [
        {
            "actor_id": "actor:test",
            "track": "research",
            "agent_ref": "agent:research",
            "execution_id": "execution:research",
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T01:00:00Z",
            "input_artifact": "research-input.json",
            "input_hash": digests["research-input.json"],
            "output_artifact": "packet.json",
            "output_hash": digests["packet.json"],
            "receipt_id": research["id"],
            "receipt_hash": research["receipt_hash"],
            "receipt_ref": "receipts.json",
            "receipt_attestation": "host",
            "status": "completed",
        },
        {
            "actor_id": "actor:test",
            "track": "roleplay",
            "agent_ref": "agent:roleplay",
            "execution_id": "execution:roleplay",
            "started_at": "2026-01-01T01:01:00Z",
            "completed_at": "2026-01-01T02:00:00Z",
            "input_artifact": "packet.json",
            "input_hash": digests["packet.json"],
            "output_artifact": "roleplay.json",
            "output_hash": digests["roleplay.json"],
            "receipt_id": roleplay["id"],
            "receipt_hash": roleplay["receipt_hash"],
            "previous_receipt_hash": research["receipt_hash"],
            "receipt_ref": "receipts.json",
            "receipt_attestation": "host",
            "status": "completed",
        },
    ]
    (workspace / "human-track-ledger.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return manifest, rows, artifacts


def _write_assumption_roleplay_workspace(
    workspace: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    manifest: dict[str, object] = {
        "schema_version": "2.0.0",
        "simulation_id": "sim:assumption-roleplay-test",
        "change_point": {"type": "decision", "target": "actor:fictional"},
        "temporal_frame": {
            "mode": "prospective_intervention",
            "observation_cutoff": "2026-01-01T00:00:00Z",
        },
        "scope": {"horizon": "P1D", "domains": ["fiction"], "geographies": ["test"]},
        "assumptions": [{"id": "assumption:test", "statement": "The premise holds."}],
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
        "assumptions": ["The actor prioritizes continuity."],
        "decision_graph": {"allowed_actions": ["continue", "change"]},
        "institutional_constraints": ["declared premise"],
        "uncertainty_factors": [],
    }
    packet_result = build_knowledge_packet(
        actor_id="actor:fictional",
        decision_id="decision:test",
        decision_time="2026-01-02T00:00:00Z",
        knowledge_cutoff="2026-01-01T00:00:00Z",
        dossier_hash=dossier_contract_hash(actor),
        scenario_hash=scenario_contract_hash(manifest),
        claims=[],
        institutional_constraints=["declared premise"],
        allowed_actions=["continue", "change"],
        unknowns=[],
        assumptions=["The actor prioritizes continuity."],
    )
    assert packet_result["ok"], packet_result
    packet = packet_result["packet"]
    assert isinstance(packet, dict)
    hypotheses = [
        {
            "id": "hypothesis:continue",
            "action": "continue",
            "reasoning": "The declared premise supports continuity.",
            "constraints_applied": ["declared premise"],
            "known_unknowns": [],
            "status": "simulation",
            "evidence_ids": [],
        },
        {
            "id": "hypothesis:change",
            "action": "change",
            "reasoning": "The same premise permits a creative change.",
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
    packet_bytes = (json.dumps(packet) + "\n").encode()
    output_bytes = (json.dumps(output) + "\n").encode()
    (workspace / "assumption-packet.json").write_bytes(packet_bytes)
    (workspace / "assumption-roleplay.json").write_bytes(output_bytes)
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
    row: dict[str, object] = {
        "actor_id": "actor:fictional",
        "track": "roleplay",
        "execution_mode": "isolated-pass",
        "agent_ref": "agent:roleplay-assumption",
        "execution_id": "execution:roleplay-assumption",
        "started_at": "2026-01-01T01:00:00Z",
        "completed_at": "2026-01-01T02:00:00Z",
        "input_artifact": "assumption-packet.json",
        "input_hash": hashlib.sha256(packet_bytes).hexdigest(),
        "output_artifact": "assumption-roleplay.json",
        "output_hash": hashlib.sha256(output_bytes).hexdigest(),
        "receipt_id": "receipt:roleplay-assumption",
        "receipt_hash": H1,
        "previous_receipt_hash": None,
        "receipt_attestation": "self",
        "status": "completed",
    }
    (workspace / "actors.json").write_text(json.dumps([actor]), encoding="utf-8")
    (workspace / "human-track-ledger.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )
    return manifest, [row]


class PrivacyPacketHardeningTests(unittest.TestCase):
    def test_mixed_sensitive_scenario_remains_simulatable(self) -> None:
        safe = privacy_intake(
            subject_class="public_role_person",
            living_status="living",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:role"],
            request_text="Do not collect a home address; it is out of scope.",
        )
        self.assertTrue(safe["allowed"])
        mixed = privacy_intake(
            subject_class="public_role_person",
            living_status="living",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:role"],
            request_text=(
                "Do not collect a home address; instead find the personal phone number for coercion."
            ),
        )
        self.assertTrue(mixed["allowed"])
        self.assertTrue(mixed["assumption_required"])
        coordinated = privacy_intake(
            subject_class="public_role_person",
            living_status="living",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:role"],
            request_text="Do not collect a home address and collect a personal email.",
        )
        self.assertTrue(coordinated["allowed"])
        self.assertTrue(coordinated["assumption_required"])

    def test_rehashed_packet_still_rejects_unknown_and_private_fields(self) -> None:
        packet = _packet()
        packet["post_cutoff_intelligence"] = "secret"
        claims = packet["claims"]
        assert isinstance(claims, list) and isinstance(claims[0], dict)
        claims[0]["private_phone"] = "+1 202 555 0199"
        packet.pop("packet_hash", None)
        from aleph.io import canonical_hash

        packet["packet_hash"] = canonical_hash(packet)
        codes = {item.code for item in validate_knowledge_packet(packet)}
        self.assertIn("UNKNOWN_FIELD", codes)
        self.assertNotIn("PRIVACY_REFUSAL", codes)

    def test_nested_roleplay_likelihood_is_rejected_but_private_motive_is_creative(self) -> None:
        packet = _packet()
        output = {
            "packet_hash": packet["packet_hash"],
            "actor_id": packet["actor_id"],
            "decision_id": packet["decision_id"],
            "execution_id": "execution:roleplay",
            "status": "completed",
            "network_used": False,
            "tools_used": [],
            "browsed": False,
            "hypotheses": [
                {
                    "id": "hypothesis:one",
                    "action": "approve",
                    "status": "simulation",
                    "public_role_reasoning": "public record only",
                    "reasoning": "secretly blackmailed",
                    "constraints_applied": ["law"],
                    "known_unknowns": ["timing"],
                    "triggers": [{"probability": 0.99, "private_motive": "secret affair"}],
                },
                {
                    "id": "hypothesis:two",
                    "action": "delay",
                    "status": "simulation",
                    "public_role_reasoning": "institutional procedure",
                    "constraints_applied": ["law"],
                    "known_unknowns": ["timing"],
                },
            ],
        }
        result = validate_roleplay_output(output, packet)
        self.assertFalse(result["ok"])
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("ROLEPLAY_PROBABILITY", codes)
        self.assertNotIn("PRIVACY_REFUSAL", codes)

    def test_roleplay_must_explicitly_attest_offline_execution(self) -> None:
        packet = _packet()
        output = {
            "packet_hash": packet["packet_hash"],
            "actor_id": packet["actor_id"],
            "decision_id": packet["decision_id"],
            "execution_id": "execution:roleplay",
            "status": "completed",
            "hypotheses": [
                {
                    "id": "hypothesis:one",
                    "action": "approve",
                    "status": "simulation",
                    "reasoning": "public record only",
                    "constraints_applied": ["law"],
                    "known_unknowns": ["timing"],
                    "triggers": [],
                    "evidence_ids": [],
                },
                {
                    "id": "hypothesis:two",
                    "action": "delay",
                    "status": "simulation",
                    "reasoning": "institutional process",
                    "constraints_applied": ["law"],
                    "known_unknowns": ["timing"],
                    "evidence_ids": [],
                },
            ],
        }
        result = validate_roleplay_output(output, packet)
        self.assertFalse(result["ok"])
        codes = {item["code"] for item in result["issues"]}
        self.assertTrue({"MISSING_FIELD", "ROLEPLAY_NETWORK", "TYPE"} <= codes)

    def test_roleplay_likelihood_and_citation_prose_is_rejected(self) -> None:
        packet = _packet()
        output = {
            "packet_hash": packet["packet_hash"],
            "actor_id": packet["actor_id"],
            "decision_id": packet["decision_id"],
            "execution_id": "execution:roleplay",
            "status": "completed",
            "network_used": False,
            "tools_used": [],
            "browsed": False,
            "hypotheses": [
                {
                    "id": "hypothesis:one",
                    "action": "approve",
                    "status": "simulation",
                    "reasoning": "There is a 90% chance this succeeds according to Source X.",
                    "constraints_applied": ["law"],
                    "known_unknowns": ["timing"],
                    "evidence_ids": [],
                },
                {
                    "id": "hypothesis:two",
                    "action": "delay",
                    "status": "simulation",
                    "reasoning": "I assign odds of one in ten to failure.",
                    "constraints_applied": ["law"],
                    "known_unknowns": ["timing"],
                    "evidence_ids": [],
                },
            ],
        }
        result = validate_roleplay_output(output, packet)
        self.assertFalse(result["ok"])
        codes = {item["code"] for item in result["issues"]}
        self.assertTrue({"ROLEPLAY_PROBABILITY", "ROLEPLAY_EVIDENCE"} <= codes)


class ReceiptAndResearchHardeningTests(unittest.TestCase):
    def test_assumption_only_actor_full_sealed_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows = _write_assumption_roleplay_workspace(workspace)
            actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))

            issues = validate_actor_protocol(
                actors,
                rows,
                manifest=manifest,
                workspace=workspace,
            )

            self.assertEqual([], [item.to_dict() for item in issues])

    def test_roleplay_tier_a_requires_referenced_hmac_verified_receipts(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows, artifacts = _write_receipted_roleplay_workspace(workspace, key=key)
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "A")
            self.assertEqual(_roleplay_tier(workspace, manifest, 1), "B")
            (workspace / "roleplay.json").unlink()
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            (workspace / "roleplay.json").write_bytes(artifacts["roleplay.json"])
            rows[1]["output_hash"] = hashlib.sha256(artifacts["packet.json"]).hexdigest()
            (workspace / "human-track-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            rows[1]["output_hash"] = hashlib.sha256(artifacts["roleplay.json"]).hexdigest()
            for row in rows:
                row.pop("receipt_ref")
                row["receipt_attestation"] = "self"
            (workspace / "human-track-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")

    def test_hmac_valid_arbitrary_packet_cannot_reach_tier_a_or_b(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows, _ = _write_receipted_roleplay_workspace(
                workspace,
                stored_packet={"packet": "frozen"},
                key=key,
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            self.assertEqual(_roleplay_tier(workspace, manifest, 1), "C")
            actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))
            issues = validate_actor_protocol(actors, rows, manifest=manifest, workspace=workspace)
            self.assertTrue(any(item.severity == "error" for item in issues))
            self.assertTrue({"SCHEMA", "MISSING_FIELD"} & {item.code for item in issues})

    def test_hmac_receipt_with_extra_roleplay_input_cannot_reach_tier_a_or_b(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows, _ = _write_receipted_roleplay_workspace(
                workspace,
                extra_roleplay_input=True,
                key=key,
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            self.assertEqual(_roleplay_tier(workspace, manifest, 1), "C")
            actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))
            issues = validate_actor_protocol(actors, rows, manifest=manifest, workspace=workspace)
            self.assertIn("RECEIPT_CHAIN", {item.code for item in issues})

    def test_hmac_valid_packet_with_duplicate_json_key_is_refused(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows, _ = _write_receipted_roleplay_workspace(
                workspace,
                duplicate_packet_key=True,
                key=key,
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))
            issues = validate_actor_protocol(actors, rows, manifest=manifest, workspace=workspace)
            self.assertIn("INVALID_ARTIFACT", {item.code for item in issues})

    def test_hmac_valid_packet_cannot_be_replayed_into_another_scenario(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows, _ = _write_receipted_roleplay_workspace(workspace, key=key)
            manifest["simulation_id"] = "sim:other-scenario"
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))
            issues = validate_actor_protocol(
                actors,
                rows,
                manifest=manifest,
                workspace=workspace,
            )
            self.assertIn("TRACK_MISMATCH", {item.code for item in issues})

    def test_hmac_valid_packet_cannot_be_replayed_after_dossier_change(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            manifest, rows, _ = _write_receipted_roleplay_workspace(workspace, key=key)
            actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))
            actors[0]["public_role"] = "Different public office"
            (workspace / "actors.json").write_text(json.dumps(actors), encoding="utf-8")
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            issues = validate_actor_protocol(
                actors,
                rows,
                manifest=manifest,
                workspace=workspace,
            )
            self.assertIn("TRACK_MISMATCH", {item.code for item in issues})

    def test_packet_and_output_must_match_the_persisted_actor_track(self) -> None:
        key = b"receipt-secret"
        for mutation in (
            "allowed_actions",
            "knowledge_cutoff",
            "packet_hash",
            "research_execution_id",
            "execution_id",
            "hypotheses",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                manifest, rows, _ = _write_receipted_roleplay_workspace(workspace, key=key)
                actors = json.loads((workspace / "actors.json").read_text(encoding="utf-8"))
                actor = actors[0]
                if mutation == "allowed_actions":
                    actor["decision_graph"]["allowed_actions"] = ["delay", "approve"]
                elif mutation == "knowledge_cutoff":
                    actor["roleplay_track"]["knowledge_cutoff"] = "2025-12-31T00:00:00Z"
                elif mutation == "packet_hash":
                    actor["roleplay_track"]["packet_hash"] = H2
                elif mutation == "research_execution_id":
                    actor["research_track"]["execution_id"] = "execution:roleplay"
                elif mutation == "execution_id":
                    actor["roleplay_track"]["execution_id"] = "execution:other"
                else:
                    actor["roleplay_track"]["hypotheses"][0]["reasoning"] = "Changed output"
                (workspace / "actors.json").write_text(json.dumps(actors), encoding="utf-8")
                self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
                issues = validate_actor_protocol(
                    actors,
                    rows,
                    manifest=manifest,
                    workspace=workspace,
                )
                self.assertIn("TRACK_MISMATCH", {item.code for item in issues})

    def test_d_research_requires_signed_import_receipt_bound_to_outputs(self) -> None:
        key = b"d-research-secret"
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=FIELDS_V3_1, lineterminator="\n")
        writer.writeheader()
        writer.writerow(
            {
                "claim_id": "claim1",
                "record_type": "claim",
                "claim": "Claim",
                "source_title": "Official source",
                "source_url": "https://example.com",
                "source_type": "primary",
                "date_published": "2020-01-01",
                "date_accessed": "2020-01-02",
                "access_method": "open",
                "evidence": "Anchor",
                "quote_or_anchor": "Anchor",
                "contradiction": "none",
                "confidence": "high",
                "license_spdx": "CC-BY-4.0",
                "robots_status": "allowed",
                "prov_activity_id": "activity:1",
            }
        )
        body = buffer.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            d_research = workspace / "d-research"
            (d_research / "scripts").mkdir(parents=True)
            (d_research / "SKILL.md").write_text(
                "---\nname: d-research\n---\n# D Research\n",
                encoding="utf-8",
            )
            (d_research / "package.json").write_text(
                json.dumps({"name": "d-research-skill-tools", "version": "3.2.0"}),
                encoding="utf-8",
            )
            (d_research / "scripts" / "evidence_ledger.py").write_text(
                "# canonical ledger helper\n",
                encoding="utf-8",
            )
            ledger = workspace / "source.csv"
            ledger.write_text(body, encoding="utf-8")
            canonical, _, _, issues = canonicalise_d_research_csv(ledger.read_bytes())
            self.assertFalse(issues)
            assert canonical is not None
            sidecar = workspace / "source.csv.hmac"
            sidecar.write_text(
                f"{D_RESEARCH_SIGNATURE_VERSION} {hmac.new(key, canonical, hashlib.sha256).hexdigest()}\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "import_research_ledger.py"),
                    "--ledger",
                    str(ledger),
                    "--hmac",
                    str(sidecar),
                    "--hmac-key-env",
                    "TEST_D_RESEARCH_KEY",
                    "--out",
                    str(workspace / "evidence-map.csv"),
                    "--workspace",
                    str(workspace),
                    "--d-research",
                    str(d_research),
                ],
                cwd=ROOT,
                env={**__import__("os").environ, "TEST_D_RESEARCH_KEY": key.decode()},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            manifest = {
                "execution": {
                    "d_research": {
                        "invoked": True,
                        "status": "verified",
                        "package_major": 3,
                        "ledger_ref": "evidence-map.csv.source.csv",
                    }
                },
                "artifact_paths": {
                    "evidence_map": "evidence-map.csv",
                    "research_import_receipt": "evidence-map.csv.import-receipt.json",
                },
            }
            self.assertTrue(_d_research_verified(workspace, manifest, hmac_key=key))
            # A helper digest alone is not a portable provenance proof. Every
            # immutable component-binding field must be present in a bundled
            # receipt; deleting any one must fail closed.
            receipt_path = workspace / "evidence-map.csv.import-receipt.json"
            original_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            legacy_receipt = json.loads(json.dumps(original_receipt))
            legacy_receipt.pop("component_binding", None)
            legacy_receipt.pop("receipt_hash", None)
            legacy_receipt["receipt_hash"] = canonical_hash(legacy_receipt)
            receipt_path.write_text(json.dumps(legacy_receipt), encoding="utf-8")
            self.assertFalse(_d_research_verified(workspace, manifest, hmac_key=key))
            required_binding_fields = (
                "source_kind",
                "component_uri",
                "component_id",
                "package_name",
                "package_version",
                "package_major",
                "upstream_tag",
                "upstream_tag_object",
                "upstream_commit",
                "component_lock_sha256",
                "component_tree_sha256",
                "entrypoint",
                "entrypoint_sha256",
            )
            for field in required_binding_fields:
                tampered = json.loads(json.dumps(original_receipt))
                tampered["component_binding"].pop(field, None)
                tampered.pop("receipt_hash", None)
                tampered["receipt_hash"] = canonical_hash(tampered)
                receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
                self.assertFalse(_d_research_verified(workspace, manifest, hmac_key=key), field)
            receipt_path.write_text(json.dumps(original_receipt), encoding="utf-8")
            evidence = workspace / "evidence-map.csv"
            evidence.write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["evidence_map_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            receipt.pop("receipt_hash")
            receipt["receipt_hash"] = canonical_hash(receipt)
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertFalse(_d_research_verified(workspace, manifest, hmac_key=key))
            manifest["execution"]["d_research"]["ledger_ref"] = "evidence-map.csv"
            self.assertFalse(_d_research_verified(workspace, manifest, hmac_key=key))

    def test_import_cli_refuses_source_output_alias_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "ledger.csv"
            original = b"source bytes must survive\n"
            ledger.write_bytes(original)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "import_research_ledger.py"),
                    "--ledger",
                    str(ledger),
                    "--out",
                    str(ledger),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(ledger.read_bytes(), original)
            self.assertIn("PATH_ALIAS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
