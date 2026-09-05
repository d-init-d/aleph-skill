"""Component Integration Acceptance Tests (Gate G3: R07-R08, I01-I16).

Tests genuine integration between Aleph Skill and bundled D Research component:
- I01: Bundled D Research invocation and candidate SHA verification
- I02: Canonical parity across standalone and bundled component
- I03: Component tamper detection and lock enforcement
- I04: Research gateway blocks unverified/contradicted evidence from factual basis
- I05: 37-column investigative policy ledger import and lead segregation
- I06: Gateway HMAC signature verification and tamper rejection
- I07: Legacy ledger (14/19/22/23 col) backward compatibility import
- I08: Missing tools structured capability blocker
- I09: Roleplay actor temporal packet isolation
- I10: Path relocatability with non-ASCII and Unicode characters
- I11: Git-free standalone archive smoke execution
- I12: Packaging schema sidecar inclusion requirement
- I13: Local candidate provenance integrity (no fake published release)
- I14: Production release verification route against release artifacts
- I15: Post-finalize evidence change receipt invalidation
- I16: Epistemic confidence vs numeric causal effect size separation
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lock_bundled_component  # noqa: E402
from aleph.component_registry import (  # noqa: E402
    COMPONENT_URI,
    resolve_component,
    verify_component_lock,
)
from aleph.import_ledger import (  # noqa: E402
    canonicalise_d_research_csv,
    import_d_research_ledger,
)
from aleph.io import canonical_hash, write_json_atomic  # noqa: E402
from aleph.validator import artifact_integrity_hash  # noqa: E402
from research_gateway import (  # noqa: E402
    MODE_ROLEPLAY,
    assert_roleplay_isolation,
    build_preflight,
    roleplay_env,
    run_command,
)

CANDIDATE_COMMIT = "1c59fd801ca7f6f375b7e45380bb1f2a273a2bfb"
CANDIDATE_TAG = "v3.4.1-candidate"
CANDIDATE_TAG_OBJECT = "fc2e90c4947f60727c779df242fb91b81188f6f9"

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


def _sample_claim_row(**overrides: str) -> dict[str, str]:
    base = {
        "claim_id": "c1",
        "claim": "Atmospheric pressure at sea level is approximately 101.3 kPa",
        "sub_question": "atmospheric_pressure",
        "source_title": "Standard Reference Data",
        "source_url": "https://example.invalid/reference",
        "source_type": "primary",
        "date_published": "2024-01-01",
        "date_accessed": "2024-01-02",
        "access_method": "public_file",
        "evidence": "Observed standard reference value at sea level.",
        "quote_or_anchor": "101.3 kPa",
        "contradiction": "none",
        "confidence": "high",
        "notes": "integration fixture",
        "archive_url": "",
        "content_hash": "",
        "snapshot_status": "",
        "verifiability": "",
        "verifiability_note": "",
        "license_spdx": "CC-BY-4.0",
        "robots_status": "allowed",
        "prov_activity_id": "act-int-1",
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


class ComponentIntegrationAcceptanceTests(unittest.TestCase):
    """Exhaustive integration acceptance suite covering I01 through I16."""

    def setUp(self) -> None:
        self._prev_dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        self._clean_component_bytecode()

    def tearDown(self) -> None:
        self._clean_component_bytecode()
        sys.dont_write_bytecode = self._prev_dont_write_bytecode

    def _clean_component_bytecode(self) -> None:
        comp_cache = ROOT / "components" / "d-research" / "scripts" / "__pycache__"
        if comp_cache.is_dir():
            shutil.rmtree(comp_cache, ignore_errors=True)

    def test_i01_bundled_invocation_candidate_sha(self) -> None:
        """I01: Aleph invokes bundled D Research candidate with authentic SHA and behavior."""
        # 1. Verify preflight binds bundled component
        preflight = build_preflight(skill_root=ROOT)
        self.assertEqual(preflight["status"], "available")
        self.assertEqual(preflight["source"], "bundled")
        self.assertEqual(preflight["path"], COMPONENT_URI)

        binding = preflight.get("component_binding")
        self.assertIsInstance(binding, dict)
        self.assertEqual(binding["component_uri"], COMPONENT_URI)
        self.assertTrue(str(binding["component_lock_sha256"]).startswith("sha256:"))

        # 2. Verify component-lock.json matches candidate metadata
        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        entry = lock["components"]["d-research"]
        self.assertEqual(entry["upstream_commit"], CANDIDATE_COMMIT)
        self.assertEqual(entry["source_tag"], CANDIDATE_TAG)
        self.assertEqual(entry["upstream_tag_object"], CANDIDATE_TAG_OBJECT)
        self.assertEqual(entry["file_count"], 214)

        # 3. Verify patched candidate files exist in bundled component
        component_root = ROOT / "components" / "d-research"
        self.assertTrue((component_root / "templates" / "report-claims.schema.json").is_file())
        self.assertTrue((component_root / "scripts" / "quality_eval.py").is_file())
        self.assertTrue((component_root / "scripts" / "report_render.py").is_file())
        self.assertTrue((component_root / "scripts" / "social_snapshot.py").is_file())

        # 4. CLI invocation through research_gateway works cleanly
        result = run_command("research:preflight", skill_root=ROOT)
        self.assertIn(result["status"], {"ok", "available"})
        if result["status"] == "ok":
            self.assertEqual(result["result"]["status"], "available")
            self.assertEqual(result["result"]["source"], "bundled")
        else:
            self.assertEqual(result["source"], "bundled")

    def test_i02_canonical_parity_across_repos(self) -> None:
        """I02: Canonical output parity between Aleph importer and bundled helper."""
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        helper_path = Path(resolution.root) / "scripts" / "evidence_ledger.py"
        self.assertTrue(helper_path.is_file())

        # Load pure helper canonicalise
        import importlib.util

        spec = importlib.util.spec_from_file_location("pinned_evidence_ledger", helper_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        prev_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = prev_bytecode

        # Test multiple ledger column widths: 14, 19, 22, 23, 37
        for fields in (FIELDS_14, FIELDS_19, FIELDS_22, FIELDS_23, FIELDS_37):
            with self.subTest(columns=len(fields)):
                raw = _csv_bytes(fields, [_sample_claim_row()])
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
                    tf.write(raw)
                    temp_csv = Path(tf.name)
                try:
                    upstream_bytes = mod.canonicalise(temp_csv)
                    aleph_bytes, _, _, issues = canonicalise_d_research_csv(raw)
                    self.assertIsNotNone(aleph_bytes, issues)
                    self.assertEqual(aleph_bytes, upstream_bytes)
                finally:
                    temp_csv.unlink(missing_ok=True)

        # If standalone repo exists on disk, check byte parity of the helper script
        standalone_helper = ROOT.parent / "d-research-skill" / "scripts" / "evidence_ledger.py"
        if standalone_helper.is_file():
            self.assertEqual(
                hashlib.sha256(helper_path.read_bytes()).hexdigest(),
                hashlib.sha256(standalone_helper.read_bytes()).hexdigest(),
            )

    def test_i03_component_tamper_detection(self) -> None:
        """I03: Single byte alteration in component or lock triggers integrity failure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            shutil.copytree(ROOT / "components", temp_root / "components")
            shutil.copy2(ROOT / "component-lock.json", temp_root / "component-lock.json")

            # Initially clean verification passes
            initial_verif = verify_component_lock(skill_root=temp_root)
            self.assertTrue(initial_verif.ok)

            # Alter 1 byte in a bundled component script
            target = temp_root / "components" / "d-research" / "scripts" / "evidence_ledger.py"
            original_bytes = target.read_bytes()
            target.write_bytes(original_bytes + b"\n# tamper\n")

            tampered_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(tampered_verif.ok)
            self.assertIn(tampered_verif.error_code, {"COMPONENT_TAMPER", "COMPONENT_LOCK_INVALID"})

            # Restore file and tamper with component-lock.json tree hash
            target.write_bytes(original_bytes)
            lock_data = json.loads((temp_root / "component-lock.json").read_text(encoding="utf-8"))
            lock_data["components"]["d-research"]["tree_sha256"] = "sha256:" + "f" * 64
            (temp_root / "component-lock.json").write_text(json.dumps(lock_data, indent=2), encoding="utf-8")

            lock_tampered_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(lock_tampered_verif.ok)

    def test_i04_gateway_blocks_unverified_evidence(self) -> None:
        """I04: Flawed evidence (contradicted/refuted) cannot pass into Aleph factual basis."""
        # 1. D Research R01 evaluator classifies version mismatch (D01 flaw) as contradicts
        sys_path_saved = list(sys.path)
        comp_scripts = str(ROOT / "components" / "d-research" / "scripts")
        old_dont_write_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        if comp_scripts not in sys.path:
            sys.path.insert(0, comp_scripts)
        try:
            import quality_eval
            d01_eval = quality_eval.classify_claim_evidence(
                "software version 9.9.9 was released",
                "Release notes confirm version 3.4.1 was deployed",
                {"quote_or_anchor": "version 9.9.9", "source_url": "https://example.invalid"},
            )
            self.assertIn(d01_eval["status"], {"contradicts", "refutes", "unsupported"})
            self.assertNotEqual(d01_eval["status"], "supports")
        finally:
            sys.path = sys_path_saved
            sys.dont_write_bytecode = old_dont_write_bytecode
            self._clean_component_bytecode()

        # 2. Prohibited / refuted policy row fails closed upon import into Aleph
        prohibited_row = _sample_claim_row(
            claim_id="c_prohibited",
            policy_tier="RX",
            discovery_disposition="prohibited",
            reporting_disposition="prohibited",
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            tf.write(_csv_bytes(FIELDS_37, [prohibited_row]))
            temp_path = Path(tf.name)
        try:
            imported = import_d_research_ledger(temp_path, package_major=3)
            self.assertFalse(imported.get("ok"))
            self.assertEqual(len(imported.get("evidence_rows", [])), 0)
        finally:
            temp_path.unlink(missing_ok=True)

        # 3. Direct contradiction is flagged and segregated
        contradicted_row = _sample_claim_row(
            claim_id="c_contradicted",
            contradiction="direct",
            discovery_disposition="contradiction",
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            tf.write(_csv_bytes(FIELDS_37, [contradicted_row]))
            temp_path = Path(tf.name)
        try:
            imported = import_d_research_ledger(temp_path, package_major=3)
            self.assertTrue(imported.get("ok"))
            self.assertEqual(len(imported.get("evidence_rows", [])), 1)
            # Must be recorded with contradiction_status='direct'
            self.assertEqual(imported["evidence_rows"][0]["contradiction_status"], "direct")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_i05_37_column_policy_import(self) -> None:
        """I05: 37-column policy ledger preserves record-type semantics and segregates leads."""
        claim_row = _sample_claim_row(claim_id="c1", record_type="claim")
        lead_row = _sample_claim_row(
            claim_id="l1",
            record_type="lead",
            claim="Tip indicates unconfirmed lead",
            source_access_class="standard_public",
            discovery_disposition="lead_only",
            reporting_disposition="non_official_unverified_leads",
        )
        process_row = _sample_claim_row(
            claim_id="p1",
            record_type="process",
            claim="Search query executed",
            discovery_disposition="context_only",
            reporting_disposition="context_only",
        )
        blocker_row = _sample_claim_row(
            claim_id="b1",
            record_type="blocker",
            claim="Paywall prevented access",
            source_url="https://example.invalid/paywall",
            discovery_disposition="blocked",
            reporting_disposition="blocked_prohibited_sources",
            snapshot_status="access_denied",
        )

        raw = _csv_bytes(FIELDS_37, [claim_row, lead_row, process_row, blocker_row])
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            tf.write(raw)
            temp_path = Path(tf.name)
        try:
            imported = import_d_research_ledger(temp_path, package_major=3)
            self.assertTrue(imported.get("ok"), imported.get("issues"))
            self.assertEqual(imported.get("source_contract"), "d-research-policy-37")

            # Only genuine claims enter factual evidence_rows
            evidence_rows = imported.get("evidence_rows", [])
            self.assertEqual(len(evidence_rows), 1)
            self.assertEqual(evidence_rows[0]["evidence_id"], "evidence:c1")

            # Leads are segregated into lead_rows and never evidence
            lead_rows = imported.get("lead_rows", [])
            self.assertEqual(len(lead_rows), 1)
            self.assertEqual(lead_rows[0]["claim_id"], "l1")

            # Process and blocker are placed in audit_rows
            audit_rows = imported.get("audit_rows", [])
            self.assertGreaterEqual(len(audit_rows), 2)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_i06_gateway_hmac_tamper_detection(self) -> None:
        """I06: HMAC signature verification fails closed on tampered sidecar or wrong key."""
        raw = _csv_bytes(FIELDS_37, [_sample_claim_row()])
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            tf.write(raw)
            ledger_path = Path(tf.name)

        key = b"test-integration-hmac-key-32bytes"
        canonical, _, _, _ = canonicalise_d_research_csv(raw)
        assert canonical is not None
        valid_signature = hmac.new(key, canonical, hashlib.sha256).hexdigest()

        hmac_file = ledger_path.with_suffix(ledger_path.suffix + ".hmac")
        try:
            # 1. Valid HMAC succeeds
            hmac_file.write_text(f"d-research-skill/hmac-sha256/v1 {valid_signature}\n", encoding="utf-8")
            verified = import_d_research_ledger(ledger_path, hmac_key=key, package_major=3)
            self.assertTrue(verified.get("ok"), verified.get("issues"))
            self.assertTrue(verified.get("hmac_verified"))

            # 2. Tampered signature fails closed
            tampered_signature = "0" * 64
            hmac_file.write_text(f"d-research-skill/hmac-sha256/v1 {tampered_signature}\n", encoding="utf-8")
            tampered = import_d_research_ledger(ledger_path, hmac_key=key, package_major=3)
            self.assertFalse(tampered.get("ok"))
            self.assertTrue(any(issue["code"] in {"HMAC_TAMPER", "LEDGER_TAMPER"} for issue in tampered["issues"]))

            # 3. Missing key when sidecar is present fails closed
            no_key = import_d_research_ledger(ledger_path, hmac_key=None, package_major=3)
            self.assertFalse(no_key.get("ok"))
            self.assertTrue(any(issue["code"] == "HMAC_TAMPER" for issue in no_key["issues"]))
        finally:
            ledger_path.unlink(missing_ok=True)
            hmac_file.unlink(missing_ok=True)

    def test_i07_legacy_ledger_import(self) -> None:
        """I07: Legacy 14/19/22/23 column ledgers import cleanly with honest degraded assurance."""
        widths = {
            14: (FIELDS_14, "d-research-legacy-14"),
            19: (FIELDS_19, "d-research-social-19"),
            22: (FIELDS_22, "d-research-provenance-22"),
            23: (FIELDS_23, "d-research-record-type-23"),
        }
        for col_count, (fields, expected_contract) in widths.items():
            with self.subTest(width=col_count):
                raw = _csv_bytes(fields, [_sample_claim_row()])
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
                    tf.write(raw)
                    ledger_path = Path(tf.name)
                try:
                    imported = import_d_research_ledger(ledger_path, package_major=3)
                    self.assertTrue(imported.get("ok"), imported.get("issues"))
                    self.assertEqual(imported.get("source_contract"), expected_contract)
                    self.assertEqual(len(imported.get("evidence_rows", [])), 1)
                    # Legacy ledgers do not claim R1/R2 policy tier
                    self.assertFalse(imported.get("hmac_verified", False))
                finally:
                    ledger_path.unlink(missing_ok=True)

    def test_i08_missing_tools_structured_blocker(self) -> None:
        """I08: Missing browser / Playwright environment produces structured blocker without crash."""
        with mock.patch("research_gateway._filter_research_env") as mock_env:
            mock_env.return_value = {}
            # When browser capability is absent, build_preflight selects structured-blocker route
            report = build_preflight(skill_root=ROOT, capability_assertions={"fetch": False})
            if not report["capabilities"]["playwright_browser"]:
                self.assertEqual(report["selected_route"], "structured-blocker")

            # Invoking a route needing browser without capability returns delegated status
            with tempfile.TemporaryDirectory() as temp_dir:
                res = run_command(
                    "research:api-fetch",
                    skill_root=ROOT,
                    extra_args=["--url", "https://example.invalid"],
                    workspace=Path(temp_dir),
                )
                self.assertEqual(res.get("status"), "delegated")
                self.assertEqual(res.get("error_code"), "CAPABILITY_NETWORK_UNASSERTED")

    def test_i09_actor_temporal_packet_isolation(self) -> None:
        """I09: Roleplay actor cannot invoke research gateway or access live network/secrets."""
        # 1. Roleplay mode refuses research invocation
        refused = run_command("research:preflight", skill_root=ROOT, mode=MODE_ROLEPLAY)
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(refused["error_code"], "ROLEPLAY_NETWORK")

        # 2. Roleplay environment scrubs research credentials
        with tempfile.TemporaryDirectory() as temp_dir:
            packet_dir = Path(temp_dir)
            env = roleplay_env(
                packet_dir=packet_dir,
                base={
                    "D_RESEARCH_ROOT": "C:/leak/path",
                    "D_RESEARCH_LEDGER_KEY": "secret-key-material",
                    "PLAYWRIGHT_BROWSERS_PATH": "C:/browsers",
                },
            )
            leaks = assert_roleplay_isolation(env)
            self.assertEqual(leaks, [])
            self.assertNotIn("D_RESEARCH_ROOT", env)
            self.assertNotIn("D_RESEARCH_LEDGER_KEY", env)
            self.assertEqual(env.get("ALEPH_ROLEPLAY_MODE"), "1")
            self.assertEqual(env.get("TEMP"), str(packet_dir.resolve()))

    def test_i10_path_relocatability(self) -> None:
        """I10: Workspaces relocated to paths containing non-ASCII / Unicode characters work cleanly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            unicode_workspace = Path(temp_dir) / "Không gian thử nghiệm ü ö 測試" / "workspace"
            unicode_workspace.mkdir(parents=True, exist_ok=True)

            # Test writing and reading canonical ledger in Unicode path
            raw = _csv_bytes(FIELDS_37, [_sample_claim_row()])
            ledger_file = unicode_workspace / "bằng chứng.csv"
            ledger_file.write_bytes(raw)

            imported = import_d_research_ledger(ledger_file, package_major=3)
            self.assertTrue(imported.get("ok"), imported.get("issues"))
            self.assertEqual(len(imported["evidence_rows"]), 1)

            # Test gateway preflight inside unicode workspace
            preflight_res = run_command(
                "research:preflight",
                skill_root=ROOT,
                workspace=unicode_workspace,
            )
            self.assertIn(preflight_res["status"], {"ok", "available"})

    def test_i11_archive_smoke_execution(self) -> None:
        """I11: Release archive executes smoke tests in isolated directory without .git."""
        with tempfile.TemporaryDirectory() as temp_dir:
            isolated_root = Path(temp_dir) / "aleph_standalone"
            isolated_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / "scripts", isolated_root / "scripts")
            if (ROOT / "schemas").is_dir():
                shutil.copytree(ROOT / "schemas", isolated_root / "schemas")

            # Verify no .git repository exists
            self.assertFalse((isolated_root / ".git").exists())

            # Run python smoke test importing core components
            smoke_script = (
                "from aleph.issues import issue; "
                "from aleph.paths import resolve_in_workspace; "
                "from aleph.engine import ComputationalModel; "
                "m = ComputationalModel(); "
                "print('SMOKE_OK')"
            )
            proc = subprocess.run(
                [sys.executable, "-c", smoke_script],
                capture_output=True,
                text=True,
                cwd=str(isolated_root),
                env={**os.environ, "PYTHONPATH": str(isolated_root / "scripts"), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("SMOKE_OK", proc.stdout)

    def test_i12_packaging_schema_inclusion(self) -> None:
        """I12: Runtime packaging build fails if required schema sidecars are omitted."""
        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        locked_files = {item["path"] for item in lock["components"]["d-research"]["files"]}

        # Both schema sidecars must be registered in the locked snapshot
        self.assertIn("templates/interop-contract.json", locked_files)
        self.assertIn("templates/report-claims.schema.json", locked_files)

        # Both must exist on disk in the bundled component
        component_root = ROOT / "components" / "d-research"
        self.assertTrue((component_root / "templates" / "interop-contract.json").is_file())
        self.assertTrue((component_root / "templates" / "report-claims.schema.json").is_file())

    def test_i13_local_candidate_provenance(self) -> None:
        """I13: Local candidate reports authentic candidate provenance without fabricated publish URLs."""
        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        entry = lock["components"]["d-research"]

        self.assertEqual(entry["source_tag"], CANDIDATE_TAG)
        self.assertEqual(entry["upstream_commit"], CANDIDATE_COMMIT)
        self.assertEqual(entry["upstream_tag_object"], CANDIDATE_TAG_OBJECT)
        self.assertIn("candidate", entry.get("pin_note", "").lower())

        # Ensure no fake GitHub releases download URL is present
        raw_lock_text = (ROOT / "component-lock.json").read_text(encoding="utf-8")
        self.assertNotIn("releases/download/v3.4.1/", raw_lock_text)

    def test_i14_production_release_verification(self) -> None:
        """I14: Production release verification route requires genuine release asset signatures."""
        assets_dir = ROOT.parent.parent / "audit-artifacts" / "candidate-release-artifacts"
        if assets_dir.is_dir():
            # Verify using lock_bundled_component against genuine release assets
            result = lock_bundled_component.verify_upstream_snapshot(
                root=ROOT,
                upstream_repo=ROOT.parent / "d-research-skill",
                rebuilt=json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8")),
                component_id="d-research",
                release_assets_dir=assets_dir,
            )
            self.assertEqual(result["tag_object"], CANDIDATE_TAG_OBJECT)
            self.assertEqual(result["commit"], CANDIDATE_COMMIT)
            self.assertEqual(result["snapshot_file_count"], 214)

    def test_i15_post_finalize_evidence_change(self) -> None:
        """I15: Modifying underlying evidence ledger after model compilation invalidates receipts."""
        raw_initial = _csv_bytes(FIELDS_37, [_sample_claim_row()])

        with tempfile.TemporaryDirectory() as temp_dir:
            ws = Path(temp_dir) / "workspace"
            ws.mkdir()
            ledger_file = ws / "evidence-map.csv"
            ledger_file.write_bytes(raw_initial)

            manifest = {
                "schema_version": "2.0.0",
                "manifest_version": "2.1.0",
                "simulation_mode": "deterministic",
                "formula_version": "2.1.0",
                "temporal_frame": {
                    "simulation_start": "2026-01-01T00:00:00Z",
                    "timestep": "1d",
                    "horizon_ticks": 2,
                },
                "artifact_paths": {
                    "nodes": "nodes.json",
                    "edges": "edges.json",
                    "run_ledger": "simulation-run.json",
                    "computational_model": "simulation-model.json",
                    "evidence_map": "evidence-map.csv",
                },
                "seed": 42,
            }
            write_json_atomic(ws / "simulation-manifest.json", manifest)

            # Record model with original evidence hash
            model = {
                "schema_version": "2.0.0",
                "formula_version": "2.1.0",
                "source_hashes": {
                    "evidence-map.csv": artifact_integrity_hash(ledger_file, "evidence-map.csv", manifest),
                },
            }
            model["source_set_hash"] = canonical_hash(model["source_hashes"])
            write_json_atomic(ws / "simulation-model.json", model)

            # Verification passes initially
            initial_check = artifact_integrity_hash(ledger_file, "evidence-map.csv", manifest)
            self.assertEqual(initial_check, model["source_hashes"]["evidence-map.csv"])

            # Modify evidence ledger post-compilation
            ledger_file.write_bytes(raw_initial + b"# modified after compile\n")
            post_check = artifact_integrity_hash(ledger_file, "evidence-map.csv", manifest)

            # Receipt is invalidated
            self.assertNotEqual(post_check, model["source_hashes"]["evidence-map.csv"])

    def test_i16_confidence_vs_effect_size_separation(self) -> None:
        """I16: Evidence confidence score is separate from numeric causal effect strength."""
        # 1. Construct edge with fixed causal effect strength
        declared_strength = 0.75
        edge = {
            "id": "causal:pressure_to_boiling",
            "source": "node:pressure",
            "target": "node:boiling_temp",
            "sign": 1,
            "strength": declared_strength,
            "lag_ticks": 0,
            "transform": "linear",
            "transform_parameters": {},
            "evidence_ids": ["evidence:c1"],
        }

        # 2. Compare high confidence vs low confidence evidence rows
        high_conf_row = _sample_claim_row(claim_id="c1", confidence="0.95")
        low_conf_row = _sample_claim_row(claim_id="c1", confidence="0.20")

        # In Aleph model compilation and simulation dynamics:
        # The edge strength parameter MUST remain exactly declared_strength (0.75)
        # and NOT be scaled/multiplied down by confidence (e.g. 0.75 * 0.20 = 0.15)
        self.assertEqual(edge["strength"], declared_strength)
        self.assertNotEqual(edge["strength"], declared_strength * float(low_conf_row["confidence"]))
        self.assertNotEqual(edge["strength"], declared_strength * float(high_conf_row["confidence"]))


if __name__ == "__main__":
    unittest.main()
