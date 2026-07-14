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
from aleph.packets import (  # noqa: E402
    build_knowledge_packet,
    build_receipt,
    validate_knowledge_packet,
    validate_roleplay_output,
)
from aleph.privacy import privacy_intake  # noqa: E402
from aleph.quality import _d_research_verified, _roleplay_tier  # noqa: E402

H1 = "1" * 64
H2 = "2" * 64


def _packet() -> dict[str, object]:
    result = build_knowledge_packet(
        actor_id="actor:test",
        decision_id="decision:test",
        decision_time="2026-01-02T00:00:00Z",
        knowledge_cutoff="2026-01-01T00:00:00Z",
        dossier_hash=H1,
        scenario_hash=H2,
        claims=[
            {
                "id": "claim:one",
                "text": "The public institution adopted a formal rule.",
                "available_at": "2026-01-01T00:00:00Z",
                "actor_access": "public_role",
                "access_basis": "published official record",
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


class PrivacyPacketHardeningTests(unittest.TestCase):
    def test_mixed_boundary_and_harmful_request_is_refused(self) -> None:
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
        self.assertFalse(mixed["allowed"])
        coordinated = privacy_intake(
            subject_class="public_role_person",
            living_status="living",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:role"],
            request_text="Do not collect a home address and collect a personal email.",
        )
        self.assertFalse(coordinated["allowed"])

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
        self.assertIn("PRIVACY_REFUSAL", codes)

    def test_nested_roleplay_likelihood_and_private_motive_are_rejected(self) -> None:
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
        self.assertTrue({"ROLEPLAY_PROBABILITY", "PRIVACY_REFUSAL"} <= codes)

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
    def test_roleplay_tier_a_requires_referenced_hmac_verified_receipts(self) -> None:
        key = b"receipt-secret"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            artifacts = {
                "research-input.json": b'{"question":"test"}\n',
                "packet.json": b'{"packet":"frozen"}\n',
                "roleplay.json": b'{"hypotheses":[]}\n',
            }
            digests: dict[str, str] = {}
            for relative, content in artifacts.items():
                (workspace / relative).write_bytes(content)
                digests[relative] = hashlib.sha256(content).hexdigest()
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
            roleplay = build_receipt(
                runtime_id="runtime:test",
                adapter_id="adapter:test",
                execution_id="execution:roleplay",
                parent_execution_id="execution:research",
                start="2026-01-01T01:01:00Z",
                end="2026-01-01T02:00:00Z",
                inputs=[{"path": "packet.json", "sha256": digests["packet.json"]}],
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
            rows = [
                {
                    "actor_id": "actor:test",
                    "track": "research",
                    "execution_id": "execution:research",
                    "input_artifact": "research-input.json",
                    "input_hash": digests["research-input.json"],
                    "output_artifact": "packet.json",
                    "output_hash": digests["packet.json"],
                    "receipt_id": research["id"],
                    "receipt_hash": research["receipt_hash"],
                    "receipt_ref": "receipts.json",
                },
                {
                    "actor_id": "actor:test",
                    "track": "roleplay",
                    "execution_id": "execution:roleplay",
                    "input_artifact": "packet.json",
                    "input_hash": digests["packet.json"],
                    "output_artifact": "roleplay.json",
                    "output_hash": digests["roleplay.json"],
                    "receipt_id": roleplay["id"],
                    "receipt_hash": roleplay["receipt_hash"],
                    "receipt_ref": "receipts.json",
                },
            ]
            (workspace / "human-track-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            manifest = {"artifact_paths": {"human_track_ledger": "human-track-ledger.jsonl"}}
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "A")
            self.assertEqual(_roleplay_tier(workspace, manifest, 1), "B")
            (workspace / "roleplay.json").unlink()
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            (workspace / "roleplay.json").write_bytes(artifacts["roleplay.json"])
            rows[1]["output_hash"] = digests["packet.json"]
            (workspace / "human-track-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")
            rows[1]["output_hash"] = digests["roleplay.json"]
            for row in rows:
                row.pop("receipt_ref")
                row["receipt_attestation"] = "host"
            (workspace / "human-track-ledger.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            self.assertEqual(_roleplay_tier(workspace, manifest, 1, hmac_key=key), "C")

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
            evidence = workspace / "evidence-map.csv"
            evidence.write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            receipt_path = workspace / "evidence-map.csv.import-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["evidence_map_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            receipt.pop("receipt_hash")
            from aleph.io import canonical_hash

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
