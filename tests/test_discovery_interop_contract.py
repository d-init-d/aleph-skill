"""Discovery must report the exact D Research interop/ledger contract.

The bundled component >= 3.4.0 ships ``templates/interop-contract.json``.
Discovery has to surface that contract verbatim, report the importer's own
ledger contract next to it, and decide compatibility from declared capability
instead of the legacy major-version heuristic — while candidates without a
contract keep the historical major-3 acceptance (monotonic fallback).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from aleph.discovery import discover_d_research
from aleph.import_ledger import (
    ACCEPTED_FIELD_SETS,
    D_RESEARCH_SIGNATURE_VERSION,
    VALID_RECORD_TYPES,
)

import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = ROOT / "components" / "d-research"


def _external_candidate(base: Path, *, contract: dict | None) -> Path:
    candidate = base / "external-d-research"
    (candidate / "scripts").mkdir(parents=True)
    (candidate / "SKILL.md").write_text("---\nname: d-research\n---\n", encoding="utf-8")
    (candidate / "package.json").write_text(
        json.dumps({"name": "d-research-skill-tools", "version": "3.4.0"}),
        encoding="utf-8",
    )
    (candidate / "scripts" / "evidence_ledger.py").write_text("# stub\n", encoding="utf-8")
    if contract is not None:
        (candidate / "templates").mkdir()
        (candidate / "templates" / "interop-contract.json").write_text(
            json.dumps(contract), encoding="utf-8"
        )
    return candidate


class DiscoveryInteropContractTests(unittest.TestCase):
    def test_bundled_discovery_reports_exact_contract(self) -> None:
        report = discover_d_research(skill_root=ROOT)
        self.assertEqual(report.get("status"), "available")
        self.assertTrue(report.get("compatible"))
        self.assertEqual(report.get("package_version"), "3.4.0")

        interop = report.get("interop_contract")
        self.assertIsInstance(interop, dict)
        self.assertTrue(interop.get("present"))
        self.assertTrue(interop.get("readable"))
        self.assertEqual(interop.get("package_version"), "3.4.0")
        ledger = interop.get("ledger")
        self.assertEqual(ledger.get("header_sizes"), [14, 19, 22, 23, 37])
        self.assertEqual(ledger.get("record_types"), ["blocker", "claim", "lead", "process"])
        self.assertEqual(ledger.get("canonicalization"), "d-research-skill/csv/v1")
        self.assertEqual(ledger.get("signature"), "d-research-skill/hmac-sha256/v1")
        self.assertTrue(interop.get("importer_supports_declared_headers"))
        self.assertTrue(interop.get("importer_supports_declared_record_types"))
        self.assertTrue(interop.get("importer_supports_declared_signature"))
        self.assertTrue(interop.get("routes"))
        self.assertTrue(interop.get("entrypoints"))

    def test_importer_contract_mirrors_import_ledger_constants(self) -> None:
        report = discover_d_research(skill_root=ROOT)
        importer = report.get("importer_ledger_contract")
        self.assertIsInstance(importer, dict)
        self.assertEqual(
            importer.get("header_widths"),
            sorted({len(fields) for fields in ACCEPTED_FIELD_SETS}),
        )
        self.assertEqual(importer.get("header_widths"), [14, 19, 22, 23, 37])
        self.assertEqual(
            importer.get("record_types"),
            sorted(value for value in VALID_RECORD_TYPES if value),
        )
        self.assertEqual(importer.get("signature"), D_RESEARCH_SIGNATURE_VERSION)

    def test_declared_contract_entrypoints_exist_in_snapshot(self) -> None:
        report = discover_d_research(skill_root=ROOT)
        interop = report.get("interop_contract")
        entrypoints = interop.get("entrypoints")
        self.assertIsInstance(entrypoints, list)
        self.assertTrue(entrypoints)
        for entrypoint in entrypoints:
            self.assertTrue(
                (COMPONENT_ROOT / entrypoint).is_file(),
                f"declared entrypoint missing from snapshot: {entrypoint}",
            )

    def test_unsupported_declared_contract_is_incompatible(self) -> None:
        contract = {
            "contract_version": "1.0.0",
            "package_version": "3.4.0",
            "ledger": {
                "header_sizes": [14, 19, 22, 23, 37, 41],
                "record_types": ["blocker", "claim", "lead", "process"],
                "canonicalization": "d-research-skill/csv/v1",
                "signature": "d-research-skill/hmac-sha256/v1",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            candidate = _external_candidate(base, contract=contract)
            empty_root = base / "empty-skill"
            empty_root.mkdir()
            report = discover_d_research(
                skill_root=empty_root,
                explicit=candidate,
                allow_external=True,
                require_bundled=False,
            )
            self.assertEqual(report.get("status"), "incompatible")
            entry = report.get("interop_contract")
            self.assertIsInstance(entry, dict)
            self.assertFalse(entry.get("importer_supports_declared_headers"))
            self.assertTrue(entry.get("importer_supports_declared_record_types"))
            self.assertTrue(entry.get("importer_supports_declared_signature"))

    def test_candidate_without_contract_keeps_major_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            candidate = _external_candidate(base, contract=None)
            empty_root = base / "empty-skill"
            empty_root.mkdir()
            report = discover_d_research(
                skill_root=empty_root,
                explicit=candidate,
                allow_external=True,
                require_bundled=False,
            )
            self.assertEqual(report.get("status"), "available")
            self.assertTrue(report.get("compatible"))
            self.assertNotIn("interop_contract", report)


if __name__ == "__main__":
    unittest.main()
