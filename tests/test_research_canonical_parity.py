"""Canonical ledger dual-run parity: Aleph importer vs bundled D Research helper.

Drives the SHIPPED Aleph ``canonicalise_d_research_csv`` against the pinned
bundled helper's pure ``canonicalise(Path) -> bytes`` from
``components/d-research/scripts/evidence_ledger.py``. Drift is a hard fail
(``D_RESEARCH_CANONICAL_DRIFT``).
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType

from aleph.component_registry import COMPONENT_URI, resolve_component
from aleph.import_ledger import canonicalise_d_research_csv, import_d_research_ledger

ROOT = Path(__file__).resolve().parents[1]

FIELDS_14 = [
    "claim_id", "claim", "sub_question", "source_title", "source_url", "source_type",
    "date_published", "date_accessed", "access_method", "evidence", "quote_or_anchor",
    "contradiction", "confidence", "notes",
]
FIELDS_19 = FIELDS_14 + [
    "archive_url", "content_hash", "snapshot_status", "verifiability", "verifiability_note",
]
FIELDS_22 = FIELDS_19 + ["license_spdx", "robots_status", "prov_activity_id"]
FIELDS_23 = FIELDS_22 + ["record_type"]
FIELDS_POLICY = [
    "source_access_class", "subject_class", "purpose_category", "policy_tier",
    "speaker_identity", "speaker_relationship", "content_origin", "lineage_id",
    "data_sensitivity", "discovery_disposition", "reporting_disposition",
    "redaction_class", "retention_until", "authorization_scope_hash",
]
FIELDS_37 = FIELDS_23 + FIELDS_POLICY


def _csv_bytes(fields: list[str], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields})
    return buffer.getvalue().encode("utf-8")


def _claim_row(**overrides: str) -> dict[str, str]:
    base = {
        "claim_id": "c1",
        "claim": "Water boils at 100C at 1 atm",
        "sub_question": "boiling",
        "source_title": "NIST",
        "source_url": "https://example.invalid/nist",
        "source_type": "primary",
        "date_published": "2020-01-01",
        "date_accessed": "2024-01-01",
        "access_method": "public_file",
        "evidence": "standard value",
        "quote_or_anchor": "100 C",
        "contradiction": "none",
        "confidence": "high",
        "notes": "fixture",
        "archive_url": "",
        "content_hash": "",
        "snapshot_status": "",
        "verifiability": "",
        "verifiability_note": "",
        "license_spdx": "CC0-1.0",
        "robots_status": "allowed",
        "prov_activity_id": "act1",
        "record_type": "claim",
        "source_access_class": "standard_public",
        "subject_class": "organization",
        "purpose_category": "general_research",
        "policy_tier": "R1",
        "speaker_identity": "",
        "speaker_relationship": "",
        "content_origin": "",
        "lineage_id": "",
        "data_sensitivity": "public",
        "discovery_disposition": "evidence",
        "reporting_disposition": "main_findings",
        "redaction_class": "none",
        "retention_until": "",
        "authorization_scope_hash": "",
    }
    base.update(overrides)
    return base


def _load_bundled_evidence_ledger(helper: Path) -> ModuleType:
    """Import the pinned component helper module from its absolute path."""
    spec = importlib.util.spec_from_file_location(
        "d_research_evidence_ledger_pinned",
        helper,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load evidence_ledger from {helper}")
    module = importlib.util.module_from_spec(spec)
    # Ensure sibling imports inside the helper resolve under scripts/.
    scripts_dir = str(helper.parent)
    if scripts_dir not in __import__("sys").path:
        __import__("sys").path.insert(0, scripts_dir)
    # The component tree is immutable and its verifier hard-fails any extra
    # bytecode.  Import the pinned helper without materializing ``__pycache__``.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    if not hasattr(module, "canonicalise"):
        raise AttributeError(f"{helper} has no pure function canonicalise(Path) -> bytes")
    return module


def _upstream_canonicalise(module: ModuleType, ledger_path: Path) -> bytes:
    """Call the real pure helper — not a reimplementation."""
    result = module.canonicalise(ledger_path)
    if not isinstance(result, (bytes, bytearray)):
        raise TypeError(f"canonicalise returned {type(result)!r}, expected bytes")
    return bytes(result)


class CanonicalParityTests(unittest.TestCase):
    def test_pinned_helper_module_is_component_file(self) -> None:
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        helper = Path(resolution.root) / "scripts" / "evidence_ledger.py"
        module = _load_bundled_evidence_ledger(helper)
        self.assertEqual(Path(module.__file__).resolve(), helper.resolve())
        self.assertTrue(callable(module.canonicalise))

    def test_byte_drift_is_hard_fail(self) -> None:
        """If Aleph canonical bytes diverge from the pinned helper, fail closed."""
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        helper = Path(resolution.root) / "scripts" / "evidence_ledger.py"
        module = _load_bundled_evidence_ledger(helper)
        raw = _csv_bytes(FIELDS_14, [_claim_row()])
        ledger = self._write_temp(raw)
        upstream = _upstream_canonicalise(module, ledger)
        aleph, _, _, issues = canonicalise_d_research_csv(raw)
        self.assertIsNotNone(aleph, issues)
        assert aleph is not None
        self.assertEqual(aleph, upstream)
        broken = aleph[:-1] + (b"X" if not aleph.endswith(b"X") else b"Y")
        self.assertNotEqual(broken, upstream)
        with self.assertRaises(AssertionError) as ctx:
            if broken != upstream:
                self.fail(
                    "D_RESEARCH_CANONICAL_DRIFT "
                    f"aleph={hashlib.sha256(broken).hexdigest()} "
                    f"upstream={hashlib.sha256(upstream).hexdigest()}"
                )
        self.assertIn("D_RESEARCH_CANONICAL_DRIFT", str(ctx.exception))

    def test_column_contracts_and_record_types(self) -> None:
        fixtures = {
            14: (FIELDS_14, [_claim_row()]),
            19: (FIELDS_19, [_claim_row()]),
            22: (FIELDS_22, [_claim_row()]),
            23: (
                FIELDS_23,
                [
                    _claim_row(claim_id="c1", record_type="claim"),
                    _claim_row(
                        claim_id="p1",
                        record_type="process",
                        claim="searched",
                        discovery_disposition="context_only",
                        reporting_disposition="context_only",
                        notes="result=completed",
                    ),
                    _claim_row(
                        claim_id="b1",
                        record_type="blocker",
                        claim="paywall",
                        source_url="https://example.invalid/x",
                        discovery_disposition="blocked",
                        reporting_disposition="blocked_prohibited_sources",
                        snapshot_status="access_denied",
                    ),
                ],
            ),
            37: (
                FIELDS_37,
                [
                    _claim_row(claim_id="c1", record_type="claim"),
                    _claim_row(
                        claim_id="l1",
                        record_type="lead",
                        claim="Community report suggests a checkable lead",
                        discovery_disposition="lead_only",
                        reporting_disposition="non_official_unverified_leads",
                    ),
                    _claim_row(
                        claim_id="p1",
                        record_type="process",
                        claim="searched",
                        discovery_disposition="context_only",
                        reporting_disposition="context_only",
                        notes="result=completed",
                    ),
                    _claim_row(
                        claim_id="b1",
                        record_type="blocker",
                        claim="paywall",
                        source_url="https://example.invalid/x",
                        discovery_disposition="blocked",
                        reporting_disposition="blocked_prohibited_sources",
                        snapshot_status="access_denied",
                    ),
                ],
            ),
        }
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        helper = Path(resolution.root) / "scripts" / "evidence_ledger.py"
        self.assertTrue(helper.is_file(), f"missing pinned helper at {helper}")
        upstream_mod = _load_bundled_evidence_ledger(helper)

        dual_run: dict[str, object] = {
            "helper": "components/d-research/scripts/evidence_ledger.py",
            "helper_sha256": "sha256:" + hashlib.sha256(helper.read_bytes()).hexdigest(),
            "component_tree_sha256": resolution.component_tree_sha256,
            "component_lock_sha256": resolution.component_lock_sha256,
            "widths": {},
        }

        for columns, (fields, rows) in fixtures.items():
            raw = _csv_bytes(fields, rows)
            aleph_canonical, _, _parsed, issues = canonicalise_d_research_csv(raw)
            self.assertIsNotNone(aleph_canonical, issues)
            assert aleph_canonical is not None

            ledger_path = self._write_temp(raw)
            upstream_canonical = _upstream_canonicalise(upstream_mod, ledger_path)
            if columns == 37:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(upstream_mod.validate_ledger(ledger_path), 0)

            aleph_sha = hashlib.sha256(aleph_canonical).hexdigest()
            upstream_sha = hashlib.sha256(upstream_canonical).hexdigest()
            match = aleph_canonical == upstream_canonical
            dual_run["widths"][str(columns)] = {
                "aleph_sha": aleph_sha,
                "upstream_sha": upstream_sha,
                "match": match,
                "bytes": len(aleph_canonical),
            }
            if not match:
                self.fail(
                    "D_RESEARCH_CANONICAL_DRIFT "
                    f"columns={columns}: aleph={aleph_sha} upstream={upstream_sha}"
                )

            imported = import_d_research_ledger(ledger_path, package_major=3)
            self.assertTrue(imported.get("ok"), imported.get("issues"))
            self.assertEqual(imported.get("canonical_sha256"), aleph_sha)
            # Only claims become evidence rows; process/blocker stay in audit.
            if columns == 23:
                self.assertEqual(len(imported["evidence_rows"]), 1)
                self.assertGreaterEqual(len(imported.get("audit_rows") or []), 2)
                dual_run["widths"]["23"]["evidence_rows"] = len(imported["evidence_rows"])  # type: ignore[index]
                dual_run["widths"]["23"]["audit_rows"] = len(imported.get("audit_rows") or [])  # type: ignore[index]
            if columns == 37:
                self.assertEqual(imported.get("source_contract"), "d-research-policy-37")
                self.assertEqual(len(imported["evidence_rows"]), 1)
                self.assertEqual(len(imported.get("lead_rows") or []), 1)
                self.assertGreaterEqual(len(imported.get("audit_rows") or []), 2)
                dual_run["widths"]["37"]["evidence_rows"] = len(imported["evidence_rows"])  # type: ignore[index]
                dual_run["widths"]["37"]["lead_rows"] = len(imported.get("lead_rows") or [])  # type: ignore[index]
                dual_run["widths"]["37"]["audit_rows"] = len(imported.get("audit_rows") or [])  # type: ignore[index]

        # Every supported upstream width must have matched.
        widths = dual_run["widths"]
        assert isinstance(widths, dict)
        self.assertEqual(set(widths), {"14", "19", "22", "23", "37"})
        for width, payload in widths.items():
            assert isinstance(payload, dict)
            self.assertTrue(payload["match"], f"width {width} did not match")

        # Optional durable evidence file when SCRATCH is provided by the harness.
        scratch = os.environ.get("ALEPH_CANONICAL_PARITY_OUT") or os.environ.get("SCRATCH")
        if scratch:
            out_path = Path(scratch) / "canonical-parity.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(dual_run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_raw_leak_leads_are_metadata_only_and_never_evidence(self) -> None:
        lead = _claim_row(
            claim_id="l1",
            record_type="lead",
            claim="A redacted public report identifies a follow-up lead",
            source_title="Redacted lead metadata",
            source_url="",
            evidence="",
            quote_or_anchor="",
            archive_url="",
            content_hash="",
            source_access_class="raw_leak_lead_only",
            data_sensitivity="personal",
            discovery_disposition="lead_only",
            reporting_disposition="non_official_unverified_leads",
            redaction_class="other_pii",
        )
        valid_path = self._write_temp(_csv_bytes(FIELDS_37, [lead]))
        imported = import_d_research_ledger(valid_path, package_major=3)
        self.assertTrue(imported.get("ok"), imported.get("issues"))
        self.assertEqual(imported.get("evidence_rows"), [])
        self.assertEqual(len(imported.get("lead_rows") or []), 1)

        unsafe = dict(lead)
        unsafe["evidence"] = "raw secret material"
        unsafe_path = self._write_temp(_csv_bytes(FIELDS_37, [unsafe]))
        rejected = import_d_research_ledger(unsafe_path, package_major=3)
        self.assertFalse(rejected.get("ok"))
        self.assertEqual(rejected.get("evidence_rows"), [])

    def test_policy_rows_fail_closed_without_valid_scope_or_social_promotion(self) -> None:
        invalid_rows = [
            _claim_row(policy_tier="banana"),
            _claim_row(
                policy_tier="R4",
                source_access_class="authorized_provider",
                purpose_category="authorized_pentest",
                data_sensitivity="professional",
                authorization_scope_hash="",
                retention_until="",
            ),
            _claim_row(
                speaker_identity="claimed_identity",
                speaker_relationship="secondhand",
                content_origin="original",
                reporting_disposition="main_findings",
            ),
        ]
        for row in invalid_rows:
            with self.subTest(row=row):
                ledger = self._write_temp(_csv_bytes(FIELDS_37, [row]))
                imported = import_d_research_ledger(ledger, package_major=3)
                self.assertFalse(imported.get("ok"), imported)
                self.assertEqual(imported.get("evidence_rows"), [])

    def _write_temp(self, raw: bytes) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        handle.write(raw)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path


if __name__ == "__main__":
    unittest.main()
