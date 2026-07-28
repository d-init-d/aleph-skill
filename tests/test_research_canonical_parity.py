"""Canonical ledger dual-run parity: Aleph importer vs bundled D Research helper.

Drives the SHIPPED Aleph ``canonicalise_d_research_csv`` against the pinned
bundled helper's pure ``canonicalise(Path) -> bytes`` from
``components/d-research/scripts/evidence_ledger.py``. Drift is a hard fail
(``D_RESEARCH_CANONICAL_DRIFT``).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
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
from aleph.import_ledger import (
    canonicalise_d_research_csv,
    import_d_research_ledger,
    render_evidence_csv,
)

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
FIELDS_PROTOTYPE = [
    "id", "record_type", "claim", "evidence", "source", "source_type", "source_tier",
    "date", "retrieved_at", "access_method", "retrieval_status", "confidence",
    "contradiction_status", "notes",
]


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

    def test_semantic_acceptance_matches_upstream_for_every_official_width(self) -> None:
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        helper = Path(resolution.root) / "scripts" / "evidence_ledger.py"
        upstream = _load_bundled_evidence_ledger(helper)
        for fields in (FIELDS_14, FIELDS_19, FIELDS_22, FIELDS_23, FIELDS_37):
            with self.subTest(width=len(fields), case="invalid-enums"):
                invalid = _claim_row(source_type="blog", contradiction="maybe")
                path = self._write_temp(_csv_bytes(fields, [invalid]))
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    upstream_status = upstream.validate_ledger(path)
                imported = import_d_research_ledger(path, package_major=3)
                self.assertEqual(upstream_status, 1)
                self.assertFalse(imported.get("ok"), imported)
                pointers = {
                    item.get("pointer") for item in imported.get("issues") or []
                }
                self.assertTrue(any(str(value).endswith("/source_type") for value in pointers))
                self.assertTrue(any(str(value).endswith("/contradiction") for value in pointers))

            with self.subTest(width=len(fields), case="empty-confidence"):
                valid = _claim_row(confidence="")
                path = self._write_temp(_csv_bytes(fields, [valid]))
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    upstream_status = upstream.validate_ledger(path)
                imported = import_d_research_ledger(path, package_major=3)
                self.assertEqual(upstream_status, 0)
                self.assertTrue(imported.get("ok"), imported.get("issues"))
                self.assertEqual(imported["evidence_rows"][0]["confidence"], "0.0")

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

    def test_renderer_and_canonical_parser_fail_closed(self) -> None:
        rendered = render_evidence_csv(
            [
                {
                    "evidence_id": "evidence:c1",
                    "claim": "Rendered claim",
                    "source": "https://example.invalid/source",
                    "source_type": "official",
                    "source_tier": "primary",
                    "date": "2024-01-01",
                    "retrieved_at": "2024-01-02",
                    "access_method": "public_api",
                    "retrieval_status": "api",
                    "quote_or_value": "A, quoted value",
                    "confidence": "0.85",
                    "contradiction_status": "none",
                    "notes": "render fixture",
                }
            ]
        )
        parsed = list(csv.DictReader(io.StringIO(rendered.decode("utf-8"))))
        self.assertEqual(parsed[0]["evidence_id"], "evidence:c1")
        self.assertEqual(parsed[0]["quote_or_value"], "A, quoted value")

        malformed_inputs = {
            "wrong-header": b"id,claim\nc1,test\n",
            "excess-column": _csv_bytes(FIELDS_14, [_claim_row()]).rstrip(b"\n")
            + b",unexpected\n",
            "invalid-utf8": b"\xff\xfe",
        }
        for name, raw in malformed_inputs.items():
            with self.subTest(name=name):
                canonical, _fields, _rows, issues = canonicalise_d_research_csv(raw)
                self.assertIsNone(canonical)
                self.assertIn("LEDGER_MALFORMED", {item.code for item in issues})

    def test_hmac_sidecar_verification_is_strict_and_auto_discovered(self) -> None:
        raw = _csv_bytes(FIELDS_14, [_claim_row()])
        ledger = self._write_temp(raw)
        canonical, _fields, _rows, issues = canonicalise_d_research_csv(raw)
        self.assertFalse(issues)
        assert canonical is not None
        key = b"canonical-parity-test-key"
        signature = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        automatic = ledger.with_suffix(ledger.suffix + ".hmac")
        automatic.write_text(
            f"d-research-skill/hmac-sha256/v1 {signature}\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: automatic.unlink(missing_ok=True))

        verified = import_d_research_ledger(ledger, hmac_key=key, package_major=3)
        self.assertTrue(verified.get("ok"), verified.get("issues"))
        self.assertTrue(verified.get("hmac_verified"))
        self.assertEqual(verified.get("hmac_sidecar"), str(automatic))

        no_key = import_d_research_ledger(ledger, package_major=3)
        self.assertFalse(no_key.get("ok"))
        self.assertIn("HMAC_TAMPER", {item["code"] for item in no_key["issues"]})

        malformed = automatic.with_name(automatic.name + ".malformed")
        malformed.write_text("not-a-supported-signature\n", encoding="utf-8")
        self.addCleanup(lambda: malformed.unlink(missing_ok=True))
        bad_format = import_d_research_ledger(
            ledger,
            hmac_sidecar=malformed,
            hmac_key=key,
            package_major=3,
        )
        self.assertFalse(bad_format.get("ok"))
        self.assertIn("HMAC_TAMPER", {item["code"] for item in bad_format["issues"]})

        mismatch = automatic.with_name(automatic.name + ".mismatch")
        mismatch.write_text(
            "d-research-skill/hmac-sha256/v1 " + "0" * 64 + "\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: mismatch.unlink(missing_ok=True))
        tampered = import_d_research_ledger(
            ledger,
            hmac_sidecar=mismatch,
            hmac_key=key,
            package_major=3,
        )
        self.assertFalse(tampered.get("ok"))
        self.assertEqual(
            {item["code"] for item in tampered["issues"]},
            {"HMAC_TAMPER", "LEDGER_TAMPER"},
        )

        missing_sidecar = import_d_research_ledger(
            ledger,
            hmac_sidecar=automatic.with_name("missing-ledger-sidecar.hmac"),
            hmac_key=key,
            package_major=3,
        )
        self.assertFalse(missing_sidecar.get("ok"))
        self.assertIn("HMAC_TAMPER", {item["code"] for item in missing_sidecar["issues"]})

    def test_source_tier_and_retrieval_status_mapping(self) -> None:
        variants = [
            ("primary", "public_api", "primary", "api"),
            ("paper", "public_file", "authoritative-secondary", "downloaded"),
            ("secondary", "search", "secondary", "search-snippet"),
            ("unknown", "manual_needed", "tertiary", "blocked"),
            ("community", "playwright", "secondary", "opened"),
        ]
        rows = [
            _claim_row(
                claim_id=f"mapping-{index}",
                source_type=source_type,
                access_method=access_method,
            )
            for index, (source_type, access_method, _tier, _status) in enumerate(variants)
        ]
        ledger = self._write_temp(_csv_bytes(FIELDS_14, rows))
        imported = import_d_research_ledger(ledger, package_major=3)
        self.assertTrue(imported.get("ok"), imported.get("issues"))
        mapped = {row["evidence_id"]: row for row in imported["evidence_rows"]}
        for index, (_source_type, _access_method, tier, status) in enumerate(variants):
            with self.subTest(index=index):
                row = mapped[f"evidence:mapping-{index}"]
                self.assertEqual(row["source_tier"], tier)
                self.assertEqual(row["retrieval_status"], status)

    def test_prototype_numeric_confidence_and_import_error_paths(self) -> None:
        prototype = {
            "id": "prototype-1",
            "record_type": "claim",
            "claim": "Prototype claim",
            "evidence": "Prototype evidence",
            "source": "https://example.invalid/prototype",
            "source_type": "official",
            "source_tier": "primary",
            "date": "2024-01-01",
            "retrieved_at": "2024-01-02T00:00:00Z",
            "access_method": "download",
            "retrieval_status": "downloaded",
            "confidence": "0.42",
            "contradiction_status": "none",
            "notes": "prototype fixture",
        }
        ledger = self._write_temp(_csv_bytes(FIELDS_PROTOTYPE, [prototype]))
        imported = import_d_research_ledger(ledger, package_major=3)
        self.assertTrue(imported.get("ok"), imported.get("issues"))
        self.assertEqual(imported["evidence_rows"][0]["confidence"], "0.42")
        self.assertEqual(imported.get("source_contract"), "aleph-prototype-14")

        for confidence in ("1.01", "not-a-number"):
            with self.subTest(confidence=confidence):
                invalid = dict(prototype, id=f"prototype-{confidence}", confidence=confidence)
                path = self._write_temp(_csv_bytes(FIELDS_PROTOTYPE, [invalid]))
                result = import_d_research_ledger(path, package_major=3)
                self.assertFalse(result.get("ok"))
                self.assertIn("/confidence", result["issues"][0]["pointer"])

        unsupported = import_d_research_ledger(ledger, package_major=4)
        self.assertFalse(unsupported.get("ok"))
        self.assertEqual(unsupported["issues"][0]["code"], "LEDGER_MAJOR")
        missing = import_d_research_ledger(
            ledger.with_name("does-not-exist.csv"),
            package_major=3,
        )
        self.assertFalse(missing.get("ok"))
        self.assertEqual(missing["issues"][0]["code"], "LEDGER_MALFORMED")

        invalid_rows = [
            _claim_row(claim_id="", record_type="claim"),
            _claim_row(claim_id="duplicate", record_type="claim"),
            _claim_row(claim_id="duplicate", record_type="claim"),
            _claim_row(claim_id="bad-type", record_type="unknown-record"),
            _claim_row(claim_id="empty-claim", claim="", record_type="claim"),
            _claim_row(claim_id="missing-source", source_url="", record_type="claim"),
        ]
        invalid_path = self._write_temp(_csv_bytes(FIELDS_23, invalid_rows))
        invalid_result = import_d_research_ledger(invalid_path, package_major=3)
        self.assertFalse(invalid_result.get("ok"))
        self.assertTrue(
            {"EMPTY_ID", "LEDGER_DUPLICATE", "LEDGER_MALFORMED"}
            <= {item["code"] for item in invalid_result["issues"]}
        )

        pre_policy_lead = _claim_row(
            claim_id="legacy-lead",
            record_type="lead",
            claim="Lead rows require the policy contract",
        )
        pre_policy_path = self._write_temp(_csv_bytes(FIELDS_23, [pre_policy_lead]))
        pre_policy_result = import_d_research_ledger(pre_policy_path, package_major=3)
        self.assertFalse(pre_policy_result.get("ok"))
        self.assertIn(
            "record_type=lead requires the exact 37-column policy contract",
            pre_policy_result["issues"][0]["message"],
        )

    def test_policy_validation_matrix_covers_security_boundaries(self) -> None:
        scope_hash = "sha256:" + "a" * 64
        cases: list[tuple[str, dict[str, str], str]] = [
            ("required", {"source_access_class": ""}, "missing required policy fields"),
            ("claim", {"claim": ""}, "policy row requires a claim"),
            (
                "lead-source",
                {
                    "record_type": "lead",
                    "source_url": "",
                    "discovery_disposition": "lead_only",
                    "reporting_disposition": "non_official_unverified_leads",
                },
                "lead row requires source_url",
            ),
            (
                "audit-context",
                {
                    "record_type": "process",
                    "source_url": "",
                    "source_title": "",
                    "notes": "",
                    "evidence": "",
                    "snapshot_status": "",
                    "robots_status": "",
                    "verifiability": "",
                    "discovery_disposition": "context_only",
                    "reporting_disposition": "context_only",
                },
                "audit row requires source_url or source_title",
            ),
            ("source-type", {"source_type": "blog"}, "invalid source type"),
            ("confidence", {"confidence": "certain"}, "invalid policy-ledger confidence"),
            ("contradiction", {"contradiction": "maybe"}, "invalid contradiction value"),
            ("verifiability", {"verifiability": "mirror"}, "invalid verifiability value"),
            ("snapshot", {"snapshot_status": "gone"}, "invalid snapshot status"),
            ("robots", {"robots_status": "ignored"}, "invalid robots status"),
            ("license", {"license_spdx": "LicenseRef-"}, "invalid SPDX-style license"),
            ("provenance", {"prov_activity_id": "contains space"}, "invalid provenance"),
            (
                "r0-person",
                {"subject_class": "public_role_person"},
                "R1 person row must use R2 or R3",
            ),
            (
                "r1-sensitive",
                {"data_sensitivity": "sensitive", "redaction_class": "other_pii"},
                "R1 permits public/professional data only",
            ),
            (
                "r2-subject",
                {"policy_tier": "R2", "subject_class": "organization"},
                "R2 requires a person or self subject",
            ),
            (
                "r2-sensitive",
                {
                    "policy_tier": "R2",
                    "subject_class": "self",
                    "data_sensitivity": "personal",
                    "redaction_class": "other_pii",
                },
                "R2 permits public/professional data only",
            ),
            (
                "r3-subject",
                {
                    "policy_tier": "R3",
                    "subject_class": "private_person",
                    "authorization_scope_hash": scope_hash,
                    "retention_until": "2024-01-30T00:00:00Z",
                },
                "R3 requires a self or organization subject",
            ),
            (
                "main-lead",
                {"record_type": "lead", "reporting_disposition": "main_findings"},
                "main_findings requires record_type=claim",
            ),
            (
                "lead-partition",
                {"reporting_disposition": "non_official_unverified_leads"},
                "non_official_unverified_leads requires record_type=lead",
            ),
            (
                "lead-discovery",
                {"discovery_disposition": "lead_only"},
                "lead_only requires record_type=lead",
            ),
            (
                "redaction",
                {"data_sensitivity": "personal", "redaction_class": "none"},
                "personal or sensitive data requires redaction",
            ),
            ("lineage", {"lineage_id": "contains space"}, "invalid lineage identifier"),
            (
                "retention-format",
                {"retention_until": "2024-01-01"},
                "retention timestamp must be RFC 3339",
            ),
            (
                "scope-format",
                {"authorization_scope_hash": "sha256:not-valid"},
                "invalid authorization scope hash",
            ),
            (
                "partial-social",
                {"speaker_identity": "official"},
                "social classification fields must be populated together",
            ),
            (
                "social-promotion",
                {
                    "speaker_identity": "claimed_identity",
                    "speaker_relationship": "secondhand",
                    "content_origin": "original",
                },
                "social main finding lacks verified original evidence",
            ),
            (
                "derivative-social",
                {
                    "speaker_identity": "official",
                    "speaker_relationship": "repost",
                    "content_origin": "quote",
                    "reporting_disposition": "context_only",
                },
                "derivative social evidence requires lineage_id",
            ),
            (
                "prohibited",
                {
                    "policy_tier": "RX",
                    "subject_class": "minor",
                    "data_sensitivity": "minor",
                    "discovery_disposition": "prohibited",
                    "reporting_disposition": "prohibited",
                },
                "prohibited policy row cannot be imported",
            ),
            (
                "secret-fields",
                {
                    "source_access_class": "prohibited_secret",
                    "data_sensitivity": "secret",
                    "reporting_disposition": "prohibited",
                },
                "secret or prohibited metadata retained protected fields",
            ),
            (
                "raw-leak-shape",
                {
                    "source_access_class": "raw_leak_lead_only",
                    "source_url": "",
                    "source_title": "",
                    "evidence": "",
                    "quote_or_anchor": "",
                    "data_sensitivity": "personal",
                    "redaction_class": "other_pii",
                    "discovery_disposition": "lead_only",
                    "reporting_disposition": "non_official_unverified_leads",
                },
                "raw-leak metadata requires lead, process, or blocker",
            ),
            (
                "r3-binding",
                {"policy_tier": "R3", "subject_class": "self"},
                "R3 requires authorization binding",
            ),
            (
                "r3-anchor",
                {
                    "policy_tier": "R3",
                    "subject_class": "self",
                    "authorization_scope_hash": scope_hash,
                    "retention_until": "2024-01-30T00:00:00Z",
                    "date_accessed": "not-a-date",
                },
                "R3 requires a valid retention anchor",
            ),
            (
                "r3-window",
                {
                    "policy_tier": "R3",
                    "subject_class": "self",
                    "authorization_scope_hash": scope_hash,
                    "retention_until": "2024-02-01T00:00:00Z",
                    "date_accessed": "2024-01-01",
                },
                "R3 retention exceeds the 30-day maximum",
            ),
        ]
        for name, overrides, expected in cases:
            with self.subTest(name=name):
                ledger = self._write_temp(_csv_bytes(FIELDS_37, [_claim_row(**overrides)]))
                imported = import_d_research_ledger(ledger, package_major=3)
                messages = "\n".join(item.get("message", "") for item in imported["issues"])
                self.assertFalse(imported.get("ok"), imported)
                self.assertIn(expected, messages)

    def test_valid_policy_authorization_and_license_variants(self) -> None:
        scope_hash = "sha256:" + "b" * 64
        rows = [
            _claim_row(
                claim_id="r3-valid",
                policy_tier="R3",
                subject_class="self",
                purpose_category="self_audit",
                source_access_class="user_provided_private",
                authorization_scope_hash=scope_hash,
                retention_until="2024-01-31T00:00:00Z",
                license_spdx="NOASSERTION",
            ),
            _claim_row(
                claim_id="r4-valid",
                policy_tier="R4",
                subject_class="organization",
                purpose_category="authorized_pentest",
                source_access_class="authorized_provider",
                authorization_scope_hash=scope_hash,
                retention_until="2024-06-01T00:00:00+00:00",
                license_spdx="LicenseRef-Internal",
            ),
            _claim_row(claim_id="empty-license", license_spdx=""),
        ]
        ledger = self._write_temp(_csv_bytes(FIELDS_37, rows))
        imported = import_d_research_ledger(ledger, package_major=3)
        self.assertTrue(imported.get("ok"), imported.get("issues"))
        self.assertEqual(len(imported["evidence_rows"]), 3)

    def _write_temp(self, raw: bytes) -> Path:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
        handle.write(raw)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path


if __name__ == "__main__":
    unittest.main()
