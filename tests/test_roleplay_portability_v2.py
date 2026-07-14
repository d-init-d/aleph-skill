from __future__ import annotations

import csv
import hashlib
import hmac
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

from aleph.adapters_registry import (  # noqa: E402
    check_adapter_drift,
    expected_generated_files,
    generate_instruction_adapter,
)
from aleph.discovery import discover_d_research  # noqa: E402
from aleph.import_ledger import (  # noqa: E402
    D_RESEARCH_SIGNATURE_VERSION,
    FIELDS_LEGACY,
    canonicalise_d_research_csv,
    import_d_research_ledger,
)
from aleph.installer import (  # noqa: E402
    MANIFEST_NAME,
    build_distribution_manifest,
    install,
    install_adapter_file,
    scan_secret_like_files,
    verify_distribution_manifest,
)
from aleph.io import write_json_atomic  # noqa: E402
from aleph.packets import (  # noqa: E402
    build_knowledge_packet,
    build_receipt,
    validate_roleplay_output,
    verify_receipt_chain,
)
from aleph.privacy import privacy_intake  # noqa: E402

H1 = "1" * 64
H2 = "2" * 64


def _valid_packet() -> dict[str, object]:
    result = build_knowledge_packet(
        actor_id="actor:test",
        decision_id="decision:test",
        decision_time="2026-07-01T00:00:00Z",
        knowledge_cutoff="2026-06-30T00:00:00Z",
        dossier_hash=H1,
        scenario_hash=H2,
        claims=[
            {
                "id": "claim:known",
                "text": "Publicly known institutional fact",
                "available_at": "2026-06-01T00:00:00Z",
                "actor_access": "public_role",
                "access_basis": "public record",
            }
        ],
        institutional_constraints=["statutory process"],
        allowed_actions=["approve", "delay"],
        unknowns=["advisor recommendation"],
    )
    assert result["ok"], result
    return result["packet"]


def _descriptor(name: str, digest: str) -> dict[str, str]:
    return {"path": name, "sha256": digest}


class SealedPacketTests(unittest.TestCase):
    def test_invalid_date_and_access_are_excluded_without_content_leak(self) -> None:
        secret_text = "future result known only after cutoff"
        result = build_knowledge_packet(
            actor_id="actor:test",
            decision_id="decision:test",
            decision_time="2026-07-01T00:00:00Z",
            knowledge_cutoff="2026-06-30T00:00:00Z",
            dossier_hash=H1,
            scenario_hash=H2,
            claims=[
                {"id": "claim:bad-date", "text": secret_text, "available_at": "not-a-date", "actor_access": "unknown"},
                {"id": "claim:future", "text": secret_text, "available_at": "2026-07-02T00:00:00Z", "actor_access": "known"},
            ],
            institutional_constraints=["law"],
            allowed_actions=["approve", "delay"],
            unknowns=[],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["packet"]["claims"], [])
        self.assertNotIn("exclusion_ledger", result["packet"])
        self.assertNotIn(secret_text, json.dumps(result["packet"]))
        self.assertNotIn(secret_text, json.dumps(result["exclusion_ledger"]))

    def test_roleplay_rejects_probability_private_motive_and_out_of_graph_action(self) -> None:
        packet = _valid_packet()
        output = {
            "packet_hash": packet["packet_hash"],
            "actor_id": "actor:test",
            "decision_id": "decision:test",
            "execution_id": "execution:roleplay",
            "status": "completed",
            "network_used": False,
            "tools_used": [],
            "browsed": False,
            "hypotheses": [
                {
                    "id": "hypothesis:one",
                    "action": "invent-action",
                    "reasoning": "The actor secretly wants this.",
                    "constraints_applied": [],
                    "known_unknowns": [],
                    "status": "simulation",
                    "evidence_ids": [],
                    "confidence": 0.99,
                },
                {
                    "id": "hypothesis:two",
                    "action": "delay",
                    "reasoning": "The statutory process permits delay.",
                    "constraints_applied": ["statutory process"],
                    "known_unknowns": ["advisor recommendation"],
                    "status": "simulation",
                    "evidence_ids": ["evidence:invented"],
                },
            ],
        }
        result = validate_roleplay_output(output, packet)
        self.assertFalse(result["ok"])
        codes = {value["code"] for value in result["issues"]}
        self.assertTrue({"ROLEPLAY_PROBABILITY", "ROLEPLAY_EVIDENCE", "PRIVACY_REFUSAL", "ENUM"} <= codes)

    def test_valid_offline_roleplay_passes(self) -> None:
        packet = _valid_packet()
        output = {
            "packet_hash": packet["packet_hash"],
            "actor_id": "actor:test",
            "decision_id": "decision:test",
            "execution_id": "execution:roleplay",
            "status": "completed",
            "network_used": False,
            "tools_used": [],
            "browsed": False,
            "hypotheses": [
                {"id": "hypothesis:approve", "action": "approve", "reasoning": "The public process supports approval.", "constraints_applied": ["statutory process"], "known_unknowns": [], "status": "simulation", "evidence_ids": []},
                {"id": "hypothesis:delay", "action": "delay", "reasoning": "The same process permits delay.", "constraints_applied": ["statutory process"], "known_unknowns": ["advisor recommendation"], "status": "simulation", "evidence_ids": []},
            ],
        }
        self.assertTrue(validate_roleplay_output(output, packet)["ok"])


class ReceiptTests(unittest.TestCase):
    def _chain(self) -> tuple[bytes, list[dict[str, object]]]:
        key = b"receipt-test-key"
        research = build_receipt(
            runtime_id="runtime:test",
            adapter_id="adapter:test",
            execution_id="execution:research",
            parent_execution_id=None,
            start="2026-07-01T00:00:00Z",
            end="2026-07-01T00:01:00Z",
            inputs=[_descriptor("evidence.csv", H1)],
            outputs=[_descriptor("dossier.json", H2)],
            network_policy="allow-public-readonly",
            tool_policy="research-only",
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
            start="2026-07-01T00:01:01Z",
            end="2026-07-01T00:02:00Z",
            inputs=[_descriptor("dossier.json", H2)],
            outputs=[_descriptor("hypotheses.json", H1)],
            network_policy="deny",
            tool_policy="deny",
            observed_tools=[],
            capability_snapshot_hash=H2,
            previous_receipt_hash=research["receipt_hash"],
            hmac_key=key,
        )
        return key, [research, roleplay]

    def test_valid_chain_passes(self) -> None:
        key, receipts = self._chain()
        result = verify_receipt_chain(receipts, research_id="execution:research", roleplay_id="execution:roleplay", hmac_key=key)
        self.assertTrue(result["ok"], result)

    def test_empty_tampered_hmac_and_order_fail(self) -> None:
        key, receipts = self._chain()
        self.assertFalse(verify_receipt_chain([], research_id="execution:research", roleplay_id="execution:roleplay", hmac_key=key)["ok"])
        tampered = json.loads(json.dumps(receipts))
        tampered[0]["runtime_id"] = "runtime:tampered"
        self.assertFalse(verify_receipt_chain(tampered, research_id="execution:research", roleplay_id="execution:roleplay", hmac_key=key)["ok"])
        wrong_key = verify_receipt_chain(receipts, research_id="execution:research", roleplay_id="execution:roleplay", hmac_key=b"wrong")
        self.assertFalse(wrong_key["ok"])
        reversed_chain = verify_receipt_chain(list(reversed(receipts)), research_id="execution:research", roleplay_id="execution:roleplay", hmac_key=key)
        self.assertFalse(reversed_chain["ok"])


class PrivacyTests(unittest.TestCase):
    def test_nested_sensitive_data_fails_before_network(self) -> None:
        result = privacy_intake(
            subject_class="public_role_person",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:role"],
            payload={"safe": {"deeper": {"home_address": "1 Private Lane"}}},
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["stop_before"], ["network", "roleplay"])


class DResearchInteropTests(unittest.TestCase):
    def _ledger(self, directory: Path) -> tuple[Path, bytes]:
        path = directory / "evidence.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS_LEGACY)
            writer.writeheader()
            writer.writerow(
                {
                    "claim_id": "C001",
                    "claim": "A public source states the atomic claim.",
                    "sub_question": "baseline",
                    "source_title": "Official source",
                    "source_url": "https://example.org/source",
                    "source_type": "official",
                    "date_published": "2026-01-01",
                    "date_accessed": "2026-07-01",
                    "access_method": "fetch",
                    "evidence": "Quoted evidence",
                    "quote_or_anchor": "section 1",
                    "contradiction": "none",
                    "confidence": "high",
                    "notes": "checked",
                }
            )
        canonical, _, _, issues = canonicalise_d_research_csv(path.read_bytes())
        self.assertEqual(issues, [])
        assert canonical is not None
        return path, canonical

    def test_canonical_sidecar_and_provenance_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, canonical = self._ledger(Path(tmp))
            key = b"d-research-key"
            digest = hmac.new(key, canonical, hashlib.sha256).hexdigest()
            sidecar = path.with_suffix(".csv.hmac")
            sidecar.write_text(f"{D_RESEARCH_SIGNATURE_VERSION} {digest}\n", encoding="utf-8")
            result = import_d_research_ledger(path, hmac_sidecar=sidecar, hmac_key=key, package_major=3)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["hmac_verified"])
            row = result["evidence_rows"][0]
            self.assertEqual(row["claim"], "A public source states the atomic claim.")
            self.assertEqual(row["source"], "https://example.org/source")
            self.assertEqual(row["date"], "2026-01-01")
            self.assertIn("d_research_confidence=high", row["notes"])
            self.assertEqual(result["source_provenance"][0]["raw_row"]["quote_or_anchor"], "section 1")

    def test_tamper_and_wrong_major_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, canonical = self._ledger(Path(tmp))
            key = b"d-research-key"
            sidecar = path.with_suffix(".csv.hmac")
            sidecar.write_text(f"{D_RESEARCH_SIGNATURE_VERSION} {hmac.new(key, canonical, hashlib.sha256).hexdigest()}\n", encoding="utf-8")
            path.write_text(path.read_text(encoding="utf-8").replace("atomic claim", "changed claim"), encoding="utf-8")
            self.assertFalse(import_d_research_ledger(path, hmac_sidecar=sidecar, hmac_key=key, package_major=3)["ok"])
            self.assertFalse(import_d_research_ledger(path, package_major=4)["ok"])

    def test_discovery_refuses_fake_identity_and_incompatible_major(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "evidence_ledger.py").write_text("# fixture\n", encoding="utf-8")
            (root / "SKILL.md").write_text("---\nname: not-d-research\n---\n", encoding="utf-8")
            (root / "package.json").write_text('{"name":"d-research-skill-tools","version":"3.2.0"}\n', encoding="utf-8")
            self.assertEqual(discover_d_research(explicit=root)["status"], "incompatible")
            (root / "SKILL.md").write_text("---\nname: d-research\n---\n", encoding="utf-8")
            (root / "package.json").write_text('{"name":"d-research-skill-tools","version":"4.0.0"}\n', encoding="utf-8")
            self.assertEqual(discover_d_research(explicit=root)["status"], "incompatible")
            self.assertEqual(discover_d_research(explicit=root / "missing")["status"], "incompatible")


class InstallerAdapterTests(unittest.TestCase):
    def test_manifest_copy_digest_and_env_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            destination = base / "destination"
            (source / "scripts").mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: aleph-skill\n---\n", encoding="utf-8")
            (source / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
            (source / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
            write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
            self.assertTrue(verify_distribution_manifest(source)["ok"])
            result = install(source, destination, mode="copy", force=True)
            self.assertEqual(result["status"], "copied", result)
            self.assertFalse((destination / ".env").exists())
            self.assertEqual(result["destination_tree_sha256"], result["manifest"]["tree_sha256"])

    def test_stale_manifest_refuses_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            destination = base / "destination"
            source.mkdir()
            (source / "SKILL.md").write_text("one\n", encoding="utf-8")
            write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
            (source / "SKILL.md").write_text("tampered\n", encoding="utf-8")
            result = install(source, destination, mode="copy")
            self.assertEqual(result["status"], "refused")

    def test_generated_adapter_installs_as_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source"
            source = source_root / "adapters" / "generated" / "cursor.md"
            source.parent.mkdir(parents=True)
            (source_root / "SKILL.md").write_text(
                "---\nname: aleph-skill\n---\n", encoding="utf-8"
            )
            source.write_text(generate_instruction_adapter("cursor", source_root), encoding="utf-8")
            write_json_atomic(source_root / MANIFEST_NAME, build_distribution_manifest(source_root))
            destination = root / ".cursor" / "rules" / "aleph.mdc"
            result = install_adapter_file(
                source,
                destination,
                mode="copy",
                source_root=source_root,
            )
            self.assertEqual(result["status"], "copied", result)
            self.assertTrue(destination.is_file())

    def test_adapter_check_is_non_mutating_and_host_specific(self) -> None:
        paths = [ROOT / relative for relative in expected_generated_files(ROOT)]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        result = check_adapter_drift(ROOT)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertTrue(result["ok"], result)
        self.assertEqual(before, after)
        self.assertNotEqual(generate_instruction_adapter("cursor", ROOT), generate_instruction_adapter("windsurf", ROOT))

    def test_secret_scanner_finds_nested_hidden_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "nested" / ".env.production").write_text("API_KEY=abcdefghijklmnop\n", encoding="utf-8")
            findings = scan_secret_like_files(root)
            self.assertEqual(findings[0]["path"], "nested/.env.production")

    def test_actor_packet_refuses_output_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            actors = [
                {
                    "id": "actor:test",
                    "subject_class": "public_role_person",
                    "living_status": "living",
                    "public_role": "Mayor",
                    "scope_note": "public role only",
                    "evidence_ids": ["evidence:role"],
                    "decision_graph": {"allowed_actions": ["approve", "delay"]},
                    "institutional_constraints": ["law"],
                    "uncertainty_factors": ["unknown"],
                    "research_track": {
                        "claims": [
                            {"id": "claim:known", "claim": "Known claim", "available_at": "2026-06-01T00:00:00Z", "access_basis": "public_role"}
                        ]
                    },
                }
            ]
            (workspace / "actors.json").write_text(json.dumps(actors), encoding="utf-8")
            (workspace / "simulation-manifest.json").write_text('{"schema_version":"2.0.0"}\n', encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "actor_packet.py"),
                    "--workspace", str(workspace),
                    "--actor-id", "actor:test",
                    "--decision-time", "2026-07-01T00:00:00Z",
                    "--cutoff", "2026-06-30T00:00:00Z",
                    "--out", "../escaped.json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse((workspace.parent / "escaped.json").exists())


if __name__ == "__main__":
    unittest.main()
