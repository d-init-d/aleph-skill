"""Import CLI must persist the audit stream without promoting leads.

A 37-column policy ledger mixing claim/lead/process/blocker rows imports with
only the claim entering the evidence map; leads and process/blocker rows land
in the audit artifact with every source column preserved, and the receipt
binds the audit artifact by hash. Receipts are deterministic: re-importing the
same bytes yields the same receipt hash.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aleph.import_ledger import (
    D_RESEARCH_SIGNATURE_VERSION,
    FIELDS_V3_3,
    canonicalise_d_research_csv,
)
from aleph.io import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

_BASE_POLICY = {
    "source_access_class": "standard_public",
    "subject_class": "organization",
    "purpose_category": "general_research",
    "policy_tier": "R0",
    "speaker_identity": "",
    "speaker_relationship": "",
    "content_origin": "",
    "lineage_id": "",
    "data_sensitivity": "public",
    "redaction_class": "none",
    "retention_until": "",
    "authorization_scope_hash": "",
}


def _row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in FIELDS_V3_3}
    row.update(_BASE_POLICY)
    row.update(overrides)
    return row


def _policy_ledger_bytes() -> bytes:
    rows = [
        _row(
            claim_id="C1",
            claim="Org released its annual report",
            sub_question="What did the org publish?",
            source_title="Org newsroom",
            source_url="https://example.org/report",
            source_type="official",
            date_published="2026-01-01",
            date_accessed="2026-01-02",
            access_method="public_api",
            evidence="Report page states the figure",
            quote_or_anchor="states the figure",
            contradiction="none",
            confidence="high",
            notes="ok",
            record_type="claim",
            discovery_disposition="evidence",
            reporting_disposition="main_findings",
        ),
        _row(
            claim_id="L1",
            claim="A second report may exist",
            source_title="Community forum",
            source_url="https://example.org/hint",
            source_type="community",
            date_accessed="2026-01-02",
            access_method="search",
            confidence="low",
            notes="unverified",
            record_type="lead",
            discovery_disposition="lead_only",
            reporting_disposition="non_official_unverified_leads",
        ),
        _row(
            claim_id="P1",
            claim="Fetched the newsroom index",
            source_title="Org newsroom",
            evidence="crawl pass",
            notes="status=ok",
            record_type="process",
            discovery_disposition="permitted",
            reporting_disposition="context_only",
        ),
        _row(
            claim_id="B1",
            claim="Archive site is paywalled",
            source_title="Archive site",
            notes="status=blocked; reason=paywall",
            record_type="blocker",
            discovery_disposition="blocked",
            reporting_disposition="excluded",
        ),
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS_V3_3, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _run_import(workspace: Path, ledger: Path, key: bytes) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "import_research_ledger.py"),
            "--ledger",
            str(ledger),
            "--hmac-key-env",
            "TEST_D_RESEARCH_KEY",
            "--out",
            str(workspace / "evidence-map.csv"),
            "--workspace",
            str(workspace),
        ],
        cwd=ROOT,
        env={**os.environ, "TEST_D_RESEARCH_KEY": key.decode()},
        capture_output=True,
        text=True,
        check=False,
    )


class ImportAuditStreamTests(unittest.TestCase):
    def _prepare(self, workspace: Path, key: bytes) -> Path:
        ledger = workspace / "source.csv"
        raw = _policy_ledger_bytes()
        ledger.write_bytes(raw)
        canonical, _fields, _rows, issues = canonicalise_d_research_csv(raw)
        self.assertFalse(issues)
        assert canonical is not None
        signature = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        (workspace / "source.csv.hmac").write_text(
            f"{D_RESEARCH_SIGNATURE_VERSION} {signature}\n", encoding="utf-8"
        )
        return ledger

    def test_audit_stream_persists_all_37_columns(self) -> None:
        key = b"audit-stream-test-key"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            ledger = self._prepare(workspace, key)
            completed = _run_import(workspace, ledger, key)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            evidence = (workspace / "evidence-map.csv").read_text(encoding="utf-8")
            evidence_rows = list(csv.DictReader(io.StringIO(evidence)))
            self.assertEqual(len(evidence_rows), 1)
            self.assertEqual(evidence_rows[0]["evidence_id"], "evidence:C1")

            audit_path = workspace / "evidence-map.csv.audit.json"
            self.assertTrue(audit_path.is_file())
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["artifact_type"], "d-research-import-audit")
            self.assertEqual(audit["source_contract"], "d-research-policy-37")
            self.assertEqual(audit["column_count"], 37)
            self.assertEqual(audit["fieldnames"], FIELDS_V3_3)

            self.assertEqual([row["claim_id"] for row in audit["lead_rows"]], ["L1"])
            self.assertEqual(
                [row["claim_id"] for row in audit["audit_rows"]], ["P1", "B1"]
            )
            for row in audit["lead_rows"] + audit["audit_rows"]:
                self.assertEqual(sorted(row), sorted(FIELDS_V3_3))
            lead = audit["lead_rows"][0]
            self.assertEqual(lead["discovery_disposition"], "lead_only")
            self.assertEqual(lead["reporting_disposition"], "non_official_unverified_leads")

            provenance = audit["source_provenance"]
            self.assertEqual(
                [entry["claim_id"] for entry in provenance], ["C1", "L1", "P1", "B1"]
            )
            for entry in provenance:
                self.assertEqual(sorted(entry["raw_row"]), sorted(FIELDS_V3_3))
                self.assertRegex(entry["raw_row_sha256"], r"^[0-9a-f]{64}$")

    def test_receipt_binds_audit_artifact_and_is_deterministic(self) -> None:
        key = b"audit-stream-test-key"
        receipt_hashes: list[str] = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                ledger = self._prepare(workspace, key)
                completed = _run_import(workspace, ledger, key)
                self.assertEqual(
                    completed.returncode, 0, completed.stdout + completed.stderr
                )
                receipt = json.loads(
                    (workspace / "evidence-map.csv.import-receipt.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(receipt["audit_ref"], "evidence-map.csv.audit.json")
                audit_bytes = (workspace / "evidence-map.csv.audit.json").read_bytes()
                self.assertEqual(
                    receipt["audit_sha256"], hashlib.sha256(audit_bytes).hexdigest()
                )
                self.assertEqual(receipt["evidence_count"], 1)
                self.assertEqual(receipt["lead_count"], 1)
                self.assertEqual(receipt["audit_count"], 2)
                body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
                self.assertEqual(receipt["receipt_hash"], canonical_hash(body))
                receipt_hashes.append(receipt["receipt_hash"])
        self.assertEqual(receipt_hashes[0], receipt_hashes[1])


if __name__ == "__main__":
    unittest.main()
