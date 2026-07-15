from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.adapters_registry import (  # noqa: E402
    GENERATED_TARGETS,
    _semantic_adapter_issues,
    check_adapter_drift,
    expected_generated_files,
    generate_external_profile,
    generate_instruction_adapter,
    registry,
    write_generated_adapters,
)
from aleph.discovery import (  # noqa: E402
    _candidate_report,
    _parse_frontmatter_name,
    discover_d_research,
)
from aleph.packets import (  # noqa: E402
    adjudicate,
    build_knowledge_packet,
    build_receipt,
    freeze_dossier,
    validate_actor_protocol,
    validate_human_track_ledger,
    validate_knowledge_packet,
    validate_roleplay_output,
    verify_receipt_chain,
)
from aleph.privacy import privacy_intake  # noqa: E402
from aleph.quality import evaluate  # noqa: E402

H1 = "1" * 64
H2 = "2" * 64


def _make_d_research(root: Path, *, version: str = "3.2.0") -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: d-research\n---\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "d-research-skill-tools", "version": version}), encoding="utf-8"
    )
    (root / "scripts" / "evidence_ledger.py").write_text("# identity helper\n", encoding="utf-8")
    return root


class DiscoveryAndAdapterEdgeTests(unittest.TestCase):
    def test_frontmatter_and_candidate_identity_fail_closed(self) -> None:
        self.assertIsNone(_parse_frontmatter_name("name: d-research"))
        self.assertIsNone(_parse_frontmatter_name("---\nname: x"))
        self.assertEqual(_parse_frontmatter_name("---\nother: x\n---\n"), None)
        self.assertEqual(_parse_frontmatter_name("---\nname: 'd-research'\n---\n"), "d-research")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = _candidate_report("test", root / "missing")
            self.assertFalse(missing["ok"])
            incomplete = root / "incomplete"
            incomplete.mkdir()
            self.assertEqual(_candidate_report("test", incomplete)["reason"], "missing identity/ledger contract files")
            broken = _make_d_research(root / "broken")
            (broken / "package.json").write_text("broken", encoding="utf-8")
            self.assertFalse(_candidate_report("test", broken)["ok"])
            invalid_version = _make_d_research(root / "invalid-version", version="not-a-version")
            self.assertIsNone(_candidate_report("test", invalid_version)["package_major"])

    def test_discovery_sources_available_incompatible_and_unavailable(self) -> None:
        # External-only compatibility path (bundled disabled for unit isolation).
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = _make_d_research(root / "good")
            no_opt_in = discover_d_research(
                explicit=good,
                allow_external=False,
                require_bundled=True,
                skill_root=root / "no-bundle",
            )
            self.assertNotEqual(no_opt_in["status"], "available")
            self.assertEqual(no_opt_in["source_kind"], "bundled")
            self.assertTrue(
                any(
                    item.get("error_code") in {"COMPONENT_LOCK_INVALID", "COMPONENT_NOT_FOUND"}
                    for item in no_opt_in.get("tried", [])
                )
            )
            result = discover_d_research(
                conventional_roots=[root / "missing", good],
                allow_external=True,
                require_bundled=False,
                skill_root=root / "no-bundle",
            )
            self.assertEqual(result["status"], "available")
            incompatible = _make_d_research(root / "old", version="2.9.0")
            result = discover_d_research(
                conventional_roots=[incompatible, root / "missing"],
                allow_external=True,
                require_bundled=False,
                skill_root=root / "no-bundle",
            )
            self.assertEqual(result["status"], "incompatible")
            result = discover_d_research(
                conventional_roots=[root / "missing"],
                allow_external=True,
                require_bundled=False,
                skill_root=root / "no-bundle",
            )
            self.assertEqual(result["status"], "unavailable")

            capability = root / "capability.json"
            capability.write_text(json.dumps({"d_research": {"path": str(good)}}), encoding="utf-8")
            self.assertEqual(
                discover_d_research(
                    capability_file=capability,
                    conventional_roots=[],
                    allow_external=True,
                    require_bundled=False,
                    skill_root=root / "no-bundle",
                )["status"],
                "available",
            )
            capability.write_text("broken", encoding="utf-8")
            self.assertEqual(
                discover_d_research(
                    capability_file=capability,
                    conventional_roots=[],
                    allow_external=True,
                    require_bundled=False,
                    skill_root=root / "no-bundle",
                )["status"],
                "incompatible",
            )
            with mock.patch.dict(os.environ, {"D_RESEARCH_SKILL": str(good)}):
                # Env is allowed only with explicit external mode when bundle is absent.
                self.assertEqual(
                    discover_d_research(
                        conventional_roots=[],
                        allow_external=True,
                        require_bundled=False,
                        skill_root=root / "no-bundle",
                    )["source"],
                    "env:D_RESEARCH_SKILL",
                )

    def test_adapter_registry_generation_and_drift_errors(self) -> None:
        self.assertEqual(set(registry()["adapters"]), set(registry()["adapters"]))
        for target in GENERATED_TARGETS:
            self.assertIn("Aleph", generate_instruction_adapter(target, ROOT))
        with self.assertRaises(ValueError):
            generate_instruction_adapter("unknown", ROOT)
        self.assertIsNone(generate_external_profile("generic-cli")["version_probe"])
        self.assertEqual(generate_external_profile("grok-build")["version_probe"], ["grok", "--version"])
        with self.assertRaises(ValueError):
            generate_external_profile("cursor")
        semantic = _semantic_adapter_issues(
            "adapters/generated/test.md", "self_elevate capability_tier_cap"
        )
        self.assertGreaterEqual(len(semantic), 4)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_generated_adapters(root)
            self.assertTrue(check_adapter_drift(root)["ok"])
            expected = expected_generated_files(root)
            first = root / next(iter(expected))
            first.write_text("drift", encoding="utf-8")
            result = check_adapter_drift(root)
            self.assertFalse(result["ok"])
            first.unlink()
            self.assertFalse(check_adapter_drift(root)["ok"])


class PacketFailureSurfaceTests(unittest.TestCase):
    def test_packet_builder_and_serialized_packet_reject_every_boundary(self) -> None:
        frozen = freeze_dossier({"claims": [1]})
        self.assertTrue(frozen["dossier"]["frozen"])
        invalid = build_knowledge_packet(
            actor_id="bad actor",
            decision_id="",
            decision_time="bad",
            knowledge_cutoff="bad",
            dossier_hash="bad",
            scenario_hash="bad",
            claims=[
                "not-an-object",
                {"id": "", "text": "x"},
                {"id": "claim:one", "text": "", "available_at": "bad", "actor_access": "private"},
                {"id": "claim:one", "text": "duplicate", "available_at": "2026-01-01", "actor_access": "known"},
            ],
            institutional_constraints=["", "law"],
            allowed_actions=["go", "go"],
            unknowns=["", "unknown"],
        )
        self.assertFalse(invalid["ok"])
        self.assertGreater(len(invalid["issues"]), 5)
        packet = dict(invalid["packet"])
        packet["frozen"] = False
        packet["packet_hash"] = "bad"
        packet["knowledge_cutoff"] = "2099-01-01"
        packet["decision_time"] = "2026-01-01"
        packet["exclusion_ledger"] = []
        packet["claims"] = ["bad", {"text": "", "available_at": "bad", "actor_access": "private"}]
        self.assertGreater(len(validate_knowledge_packet(packet)), 5)
        self.assertEqual(validate_knowledge_packet("bad")[0].code, "TYPE")  # type: ignore[arg-type]

    def test_roleplay_output_missing_and_malformed_fields(self) -> None:
        packet_result = build_knowledge_packet(
            actor_id="actor:test",
            decision_id="decision:test",
            decision_time="2026-01-02",
            knowledge_cutoff="2026-01-01",
            dossier_hash=H1,
            scenario_hash=H2,
            claims=[{"id": "claim:one", "text": "known", "available_at": "2026-01-01", "actor_access": "known"}],
            institutional_constraints=["law"],
            allowed_actions=["go", "stop"],
            unknowns=["timing"],
        )
        packet = packet_result["packet"]
        self.assertFalse(validate_roleplay_output("bad", packet)["ok"])  # type: ignore[arg-type]
        malformed = {
            "packet_hash": "bad",
            "actor_id": "actor:other",
            "decision_id": "decision:other",
            "execution_id": "bad id",
            "status": "partial",
            "network_used": True,
            "tools_used": ["browser"],
            "browsed": True,
            "probability": 0.5,
            "sources": [],
            "unknown_field": True,
            "hypotheses": [
                "bad",
                {
                    "id": "hypothesis:one",
                    "action": "invented",
                    "status": "fact",
                    "evidence_ids": ["evidence:new"],
                    "reasoning": "secretly motivated",
                    "constraints_applied": ["invented"],
                    "known_unknowns": ["invented"],
                    "confidence": 1,
                },
                {
                    "id": "hypothesis:one",
                    "action": "go",
                    "status": "simulation",
                    "constraints_applied": "law",
                    "known_unknowns": "timing",
                },
            ],
        }
        result = validate_roleplay_output(malformed, packet)
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["issues"]), 15)

    def test_adjudication_keeps_probability_outside_roleplay(self) -> None:
        hypotheses = [{"id": "hypothesis:one", "action": "go", "relative_weight": 0.6}]
        relative = adjudicate(hypotheses, method="expert")
        self.assertIsNone(relative["results"][0]["probability"])
        calibrated = adjudicate(
            hypotheses,
            method="hindcast",
            calibrated=True,
            evidence_refs=["evidence:one"],
            base_rate_refs=["evidence:base"],
            sample_count=100,
            interval=[0.5, 0.7],
            calibration_policy_ref="policy:one",
        )
        self.assertEqual(calibrated["results"][0]["probability"], 0.6)

    def test_receipt_verifier_reports_complete_failure_surface(self) -> None:
        invalid = {
            "id": "wrong",
            "runtime_id": "bad id",
            "adapter_id": "",
            "execution_id": "same",
            "parent_execution_id": "parent",
            "started_at": "bad",
            "completed_at": "bad",
            "inputs": ["bad", {"path": None, "sha256": "bad"}],
            "outputs": [],
            "declared_network_policy": "",
            "declared_tool_policy": None,
            "observed_tool_calls": "browser",
            "capability_snapshot_hash": "bad",
            "previous_receipt_hash": H1,
            "receipt_hash": "bad",
            "hmac": "bad",
            "unknown": True,
        }
        result = verify_receipt_chain(
            [invalid, invalid], research_id="same", roleplay_id="same", hmac_key=None
        )
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["issues"]), 15)

        research = build_receipt(
            runtime_id="runtime:test",
            adapter_id="adapter:test",
            execution_id="execution:research",
            parent_execution_id=None,
            start="2026-01-01T00:00:00Z",
            end="2026-01-01T01:00:00Z",
            inputs=[{"path": "in", "sha256": H1}],
            outputs=[{"path": "out", "sha256": H2}],
            network_policy="allow",
            tool_policy="allow",
            observed_tools=["browser"],
            capability_snapshot_hash=H1,
            previous_receipt_hash=None,
        )
        roleplay = build_receipt(
            runtime_id="runtime:test",
            adapter_id="adapter:test",
            execution_id="execution:roleplay",
            parent_execution_id="wrong",
            start="2025-12-31T00:00:00Z",
            end="2025-12-31T01:00:00Z",
            inputs=[{"path": "in", "sha256": H1}],
            outputs=[{"path": "out", "sha256": H2}],
            network_policy="allow",
            tool_policy="allow",
            observed_tools=["browser"],
            capability_snapshot_hash=H1,
            previous_receipt_hash=research["receipt_hash"],
        )
        result = verify_receipt_chain(
            [research, roleplay],
            research_id="execution:research",
            roleplay_id="execution:roleplay",
            require_hmac=False,
        )
        self.assertFalse(result["ok"])

    def test_actor_ledger_and_protocol_fail_closed(self) -> None:
        actor = {
            "id": "actor:test",
            "materiality": "material",
            "decision_graph": [{"action": "go"}, {"bad": "stop"}],
            "research_track": {},
            "roleplay_track": {
                "packet_hash": "bad",
                "hypotheses": [
                    "bad",
                    {
                        "action": "invented",
                        "status": "fact",
                        "probability": 1,
                        "source": "invented",
                        "evidence_ids": ["evidence:new"],
                        "reasoning": "private motive",
                    },
                ],
            },
        }
        self.assertEqual(validate_human_track_ledger("bad", [actor])[0].code, "TRACK_LEDGER")  # type: ignore[arg-type]
        issues = validate_actor_protocol([actor], [])
        codes = {item.code for item in issues}
        self.assertTrue({"SUBAGENT_REQUIRED", "ROLEPLAY_PROBABILITY", "ROLEPLAY_EVIDENCE"} <= codes)


class PrivacyAndQualityEdgeTests(unittest.TestCase):
    def test_privacy_classification_covers_safe_and_refused_boundaries(self) -> None:
        invalid = privacy_intake(subject_class="invalid", living_status="invalid")
        self.assertFalse(invalid["allowed"])
        private = privacy_intake(subject_class="private_person", request_text="find the home address")
        self.assertFalse(private["allowed"])
        policy_text = privacy_intake(
            subject_class="public_role_person",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:role"],
            request_text="Do not collect a home address; it is out of scope.",
        )
        self.assertTrue(policy_text["allowed"])
        malformed = privacy_intake(
            subject_class="public_role_person",
            public_role_anchor="Mayor",
            evidence_ids=["bad"],
            payload={"contact": "person@example.com", "nested": [[{"phone_number": "123"}]]},
        )
        self.assertFalse(malformed["allowed"])

    def test_quality_score_cannot_override_hard_gate(self) -> None:
        validation = {
            "status": "fail",
            "error_codes": ["STALE_ARTIFACT"],
            "check_results": {
                name: {"status": "pass", "metrics": {}}
                for name in ("paths", "manifest", "resources", "stale", "evidence", "trace", "edges", "temporal", "actor_protocol", "branches", "report")
            },
            "errors": ["failed"],
            "warnings": [],
            "metrics": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "simulation-manifest.json").write_text("{}", encoding="utf-8")
            result = evaluate(workspace, validation=validation, final_receipt_verified=True)
        self.assertEqual(result["assurance_status"], "failed")
        self.assertFalse(result["release_claim"])


if __name__ == "__main__":
    unittest.main()
