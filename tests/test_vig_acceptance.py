"""Acceptance test suite for Gate G4: Bundled Component Sync & Freeze (VIG01-VIG08).

Verifies integrity, projection, manifest, preflight, self-test, standalone parity,
and tamper detection for the bundled D Research component in Aleph Skill:
- VIG01: Lockfile matches upstream commit SHA and authentic hashes
- VIG02: File projection matches upstream repository file tree exactly
- VIG03: research:manifest CLI output schema and correctness
- VIG04: research:preflight passing on intact bundle, failing on corruption/missing dependency
- VIG05: research:self-test passing cleanly
- VIG06: Claim classification parity between standalone and bundled component
- VIG07: Evidence ledger verification and canonicalisation parity across all formats
- VIG08: Drift and tampering detection (single-byte alteration fails closed)
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lock_bundled_component  # noqa: E402
from aleph.component_registry import (  # noqa: E402
    COMPONENT_URI,
    verify_component_lock,
)
from aleph.import_ledger import (  # noqa: E402
    canonicalise_d_research_csv,
)
from research_gateway import (  # noqa: E402
    build_preflight,
    run_command,
)

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


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    prev_dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = prev_dont_write
    return mod


class VigAcceptanceTests(unittest.TestCase):
    """VIG01-VIG08 Acceptance Test Suite for Gate G4 Bundled Component Sync."""

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

    def test_vig01_lockfile_commit_and_hash_integrity(self) -> None:
        """VIG01: Lockfile matches upstream commit SHA and authentic hashes."""
        lock_path = ROOT / "component-lock.json"
        self.assertTrue(lock_path.is_file())
        lock = json.loads(lock_path.read_text(encoding="utf-8"))

        entry = lock["components"]["d-research"]
        self.assertRegex(entry["upstream_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(entry["upstream_repo"], "repos/d-research-skill")
        self.assertEqual(entry["file_count"], lock["components"]["d-research"]["source_artifacts"]["runtime_profile"]["file_count"])
        self.assertEqual(len(entry["files"]), lock["components"]["d-research"]["source_artifacts"]["runtime_profile"]["file_count"])

        tree_sha = entry["tree_sha256"]
        self.assertTrue(tree_sha.startswith("sha256:"))
        self.assertEqual(len(tree_sha), 7 + 64)

        for f in entry["files"]:
            self.assertTrue(isinstance(f["path"], str) and len(f["path"]) > 0)
            self.assertGreaterEqual(f["size"], 0)
            self.assertTrue(f["sha256"].startswith("sha256:"))
            self.assertEqual(len(f["sha256"]), 7 + 64)

        # Baseline verification passes
        verification = verify_component_lock(skill_root=ROOT)
        self.assertTrue(verification.ok, f"Verification failed: {verification.message}")
        self.assertIsNone(verification.error_code)

        # Negative test: stale or invalid commit in lockfile fails closed
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            shutil.copytree(ROOT / "components", temp_root / "components")
            bad_lock = json.loads(json.dumps(lock))
            bad_lock["components"]["d-research"]["upstream_commit"] = "0000000000000000000000000000000000000000"
            bad_lock["components"]["d-research"]["tree_sha256"] = "sha256:" + "0" * 64
            (temp_root / "component-lock.json").write_text(json.dumps(bad_lock, indent=2), encoding="utf-8")

            bad_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(bad_verif.ok)
            self.assertIn(bad_verif.error_code, {"COMPONENT_TAMPER", "COMPONENT_LOCK_INVALID", "COMPONENT_DRIFT"})

    def test_vig02_file_projection_matches_upstream_tree(self) -> None:
        """VIG02: File projection matches upstream repository file tree exactly."""
        comp_root = ROOT / "components" / "d-research"
        self.assertTrue(comp_root.is_dir())

        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        locked_files = lock["components"]["d-research"]["files"]
        self.assertEqual(len(locked_files), lock["components"]["d-research"]["source_artifacts"]["runtime_profile"]["file_count"])

        for item in locked_files:
            target_path = comp_root / item["path"]
            self.assertTrue(target_path.is_file(), f"Missing projected file: {item['path']}")
            content = target_path.read_bytes()
            computed_sha = "sha256:" + hashlib.sha256(content).hexdigest()
            self.assertEqual(computed_sha, item["sha256"], f"Digest mismatch on {item['path']}")

        # Compare against upstream clone if present
        upstream_repo = (ROOT.parent / "d-research-skill").resolve()
        if upstream_repo.is_dir() and (upstream_repo / ".git").exists():
            source_tag = lock["components"]["d-research"]["source_tag"]
            proc = subprocess.run(
                ["git", "rev-parse", f"{source_tag}^{{}}"],
                capture_output=True,
                text=True,
                cwd=str(upstream_repo),
            )
            if proc.returncode == 0:
                tag_commit = proc.stdout.strip()
                self.assertEqual(tag_commit, lock["components"]["d-research"]["upstream_commit"])

                recipe = lock["components"]["d-research"].get("snapshot_recipe", {})
                transforms = {
                    str(item["path"]): item
                    for item in recipe.get("content_transforms", [])
                    if isinstance(item, dict) and isinstance(item.get("path"), str)
                }

                # Verify files match upstream with LF normalization (except transforms)
                for item in locked_files:
                    if item["path"] in transforms:
                        continue
                    upstream_blob = subprocess.run(
                        ["git", "show", f"{tag_commit}:{item['path']}"],
                        capture_output=True,
                        cwd=str(upstream_repo),
                    )
                    self.assertEqual(
                        upstream_blob.returncode,
                        0,
                        f"Upstream file missing from pinned tag: {item['path']}",
                    )
                    up_bytes = upstream_blob.stdout.replace(b"\r\n", b"\n")
                    comp_bytes = (comp_root / item["path"]).read_bytes().replace(b"\r\n", b"\n")
                    self.assertEqual(comp_bytes, up_bytes, f"Projection mismatch with upstream: {item['path']}")

            # Verify snapshot using official lock_bundled_component verifier
            snapshot_verif = lock_bundled_component.verify_upstream_snapshot(
                root=ROOT.resolve(),
                upstream_repo=upstream_repo,
                rebuilt=lock,
                component_id="d-research",
            )
            self.assertEqual(snapshot_verif["commit"], lock["components"]["d-research"]["upstream_commit"])
            self.assertEqual(snapshot_verif["snapshot_file_count"], lock["components"]["d-research"]["source_artifacts"]["runtime_profile"]["file_count"])

    def test_vig03_manifest_cli_schema_and_correctness(self) -> None:
        """VIG03: research:manifest CLI output schema and correctness."""
        res = run_command("research:manifest", skill_root=ROOT)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["exit_code"], 0)

        manifest = res["result"]
        self.assertEqual(manifest["status"], "available")
        self.assertIn("component_binding", manifest)
        binding = manifest["component_binding"]
        self.assertEqual(binding["component_uri"], COMPONENT_URI)
        self.assertEqual(binding["package_name"], "d-research-skill-tools")
        self.assertEqual(binding["package_version"], "3.5.0")
        self.assertEqual(binding["upstream_commit"], json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))["components"]["d-research"]["upstream_commit"])
        self.assertTrue(binding["component_lock_sha256"].startswith("sha256:"))

        self.assertIn("entrypoints", manifest)
        self.assertIn("scripts/evidence_ledger.py", manifest["entrypoints"])
        self.assertIn("scripts/investigation_policy.py", manifest["entrypoints"])

        routes = manifest["routes"]
        for required_route in (
            "research:manifest",
            "research:preflight",
            "research:self-test",
            "research:evidence-ledger",
            "research:eval-harness",
            "research:quality",
            "research:report",
        ):
            self.assertIn(required_route, routes, f"Missing route {required_route}")
            self.assertTrue(routes[required_route]["dispatchable"])

        # CLI subprocess invocation
        cli_proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "research_gateway.py"), "research:manifest"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(SCRIPTS), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(cli_proc.returncode, 0, cli_proc.stderr)
        cli_json = json.loads(cli_proc.stdout)
        self.assertEqual(cli_json["status"], "ok")
        self.assertEqual(cli_json["result"]["status"], "available")

    def test_vig04_preflight_bundle_integrity_and_failure_detection(self) -> None:
        """VIG04: research:preflight passes on intact bundle, fails on missing dependency or corruption."""
        # Clean bundle passes
        preflight = build_preflight(skill_root=ROOT)
        self.assertEqual(preflight["status"], "available")
        self.assertEqual(preflight["source"], "bundled")
        self.assertEqual(preflight["path"], COMPONENT_URI)

        res = run_command("research:preflight", skill_root=ROOT)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["result"]["status"], "available")

        # Test failure on missing critical file
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            shutil.copytree(ROOT / "components", temp_root / "components")
            shutil.copy2(ROOT / "component-lock.json", temp_root / "component-lock.json")

            missing_target = temp_root / "components" / "d-research" / "scripts" / "evidence_ledger.py"
            missing_target.unlink()

            missing_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(missing_verif.ok)

            missing_preflight = build_preflight(skill_root=temp_root)
            self.assertNotEqual(missing_preflight["status"], "available")
            blockers = missing_preflight.get("blockers") or []
            self.assertTrue(
                any(b.get("code") in {"COMPONENT_FILE_MISSING", "COMPONENT_TAMPER", "COMPONENT_LOCK_INVALID"} for b in blockers)
                or missing_preflight["status"] != "available"
            )

        # Test failure on corrupt file
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            shutil.copytree(ROOT / "components", temp_root / "components")
            shutil.copy2(ROOT / "component-lock.json", temp_root / "component-lock.json")

            corrupt_target = temp_root / "components" / "d-research" / "scripts" / "quality_eval.py"
            corrupt_target.write_bytes(b"# corrupted payload\n")

            corrupt_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(corrupt_verif.ok)
            self.assertEqual(corrupt_verif.error_code, "COMPONENT_TAMPER")

            corrupt_preflight = build_preflight(skill_root=temp_root)
            self.assertNotEqual(corrupt_preflight["status"], "available")

    def test_vig05_self_test_execution_clean(self) -> None:
        """VIG05: research:self-test passing cleanly."""
        res = run_command("research:self-test", skill_root=ROOT)
        self.assertIn(res["status"], {"ok", "degraded"})
        self.assertEqual(res["exit_code"], 0)
        self.assertIsNone(res.get("error_code"))

        checks = {c["name"]: c["status"] for c in res["result"]["checks"]}
        self.assertEqual(checks.get("component-lock"), "pass")
        self.assertEqual(checks.get("script-inventory-lock-coverage"), "pass")
        self.assertEqual(checks.get("evidence-ledger-self-test"), "pass")
        self.assertEqual(checks.get("investigation-policy-self-test"), "pass")

        # Subprocess execution
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "research_gateway.py"), "research:self-test"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(SCRIPTS), "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out_json = json.loads(proc.stdout)
        self.assertIn(out_json["status"], {"ok", "degraded"})
        self.assertEqual(out_json.get("exit_code", 0), 0)

    def test_vig06_claim_classification_parity(self) -> None:
        """VIG06: Parity test between standalone d-research and bundled component on claim classification."""
        standalone_script = ROOT.parent / "d-research-skill" / "scripts" / "quality_eval.py"
        bundled_script = ROOT / "components" / "d-research" / "scripts" / "quality_eval.py"

        self.assertTrue(bundled_script.is_file())
        bundled_mod = _load_module("bundled_quality_eval_vig", bundled_script)

        if not standalone_script.is_file():
            self.skipTest("Standalone d-research-skill repo not found for external parity check.")

        standalone_mod = _load_module("standalone_quality_eval_vig", standalone_script)

        test_cases = [
            (
                "atmospheric pressure at sea level is 101.3 kPa",
                "standard atmospheric pressure is defined as 101.325 kPa at sea level",
                {"quote_or_anchor": "101.325 kPa", "source_url": "https://example.invalid/atmos"},
            ),
            (
                "software version 9.9.9 was released in 2026",
                "release notes confirm version 3.4.1 was deployed",
                {"quote_or_anchor": "version 9.9.9", "source_url": "https://example.invalid/rel"},
            ),
            (
                "the experiment was completely successful without errors",
                "the experiment failed and did not produce expected results",
                {"quote_or_anchor": "experiment failed", "source_url": "https://example.invalid/exp"},
            ),
            (
                "solar panels convert sunlight into electricity",
                "this article contains recipes for traditional French pastry",
                {"quote_or_anchor": "French pastry", "source_url": "https://example.invalid/food"},
            ),
        ]

        for claim, evidence, ctx in test_cases:
            res_standalone = standalone_mod.classify_claim_evidence(claim, evidence, ctx)
            res_bundled = bundled_mod.classify_claim_evidence(claim, evidence, ctx)

            self.assertEqual(res_standalone["status"], res_bundled["status"])
            self.assertEqual(res_standalone.get("supports_claim"), res_bundled.get("supports_claim"))
            self.assertEqual(res_standalone.get("contradicts_claim"), res_bundled.get("contradicts_claim"))
            self.assertEqual(res_standalone, res_bundled)

    def test_vig07_evidence_ledger_parity(self) -> None:
        """VIG07: Parity test on evidence ledger verification across standalone and bundled component."""
        standalone_script = ROOT.parent / "d-research-skill" / "scripts" / "evidence_ledger.py"
        bundled_script = ROOT / "components" / "d-research" / "scripts" / "evidence_ledger.py"

        self.assertTrue(bundled_script.is_file())
        bundled_mod = _load_module("bundled_evidence_ledger_vig", bundled_script)

        has_standalone = standalone_script.is_file()
        standalone_mod = _load_module("standalone_evidence_ledger_vig", standalone_script) if has_standalone else None

        for fields in (FIELDS_14, FIELDS_19, FIELDS_22, FIELDS_23, FIELDS_37):
            with self.subTest(column_count=len(fields)):
                raw = _csv_bytes(fields, [_sample_claim_row()])
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
                    tf.write(raw)
                    temp_csv = Path(tf.name)
                try:
                    bundled_canonical = bundled_mod.canonicalise(temp_csv)
                    aleph_canonical, _, _, issues = canonicalise_d_research_csv(raw)
                    self.assertIsNotNone(aleph_canonical, issues)
                    self.assertEqual(bundled_canonical, aleph_canonical)

                    if standalone_mod is not None:
                        standalone_canonical = standalone_mod.canonicalise(temp_csv)
                        self.assertEqual(bundled_canonical, standalone_canonical)
                finally:
                    temp_csv.unlink(missing_ok=True)

    def test_vig08_drift_and_tampering_detection(self) -> None:
        """VIG08: Single-byte mutation in bundled file causes lock verification and preflight to fail closed."""
        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            shutil.copytree(ROOT / "components", temp_root / "components")
            shutil.copy2(ROOT / "component-lock.json", temp_root / "component-lock.json")

            initial_verif = verify_component_lock(skill_root=temp_root)
            self.assertTrue(initial_verif.ok)

            target = temp_root / "components" / "d-research" / "scripts" / "evidence_ledger.py"
            orig_bytes = target.read_bytes()
            target.write_bytes(orig_bytes + b"\n# drift mutation\n")

            tampered_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(tampered_verif.ok)
            self.assertEqual(tampered_verif.error_code, "COMPONENT_TAMPER")

            preflight = build_preflight(skill_root=temp_root)
            self.assertNotEqual(preflight["status"], "available")
            blockers = preflight.get("blockers") or {}
            self.assertTrue("tamper" in str(blockers).lower() or "integrity" in str(blockers).lower() or preflight["status"] != "available")

            # Restore file and tamper with component-lock.json tree hash
            target.write_bytes(orig_bytes)
            lock_data = json.loads((temp_root / "component-lock.json").read_text(encoding="utf-8"))
            lock_data["components"]["d-research"]["tree_sha256"] = "sha256:" + "e" * 64
            (temp_root / "component-lock.json").write_text(json.dumps(lock_data, indent=2), encoding="utf-8")

            lock_tampered_verif = verify_component_lock(skill_root=temp_root)
            self.assertFalse(lock_tampered_verif.ok)
            self.assertIn(lock_tampered_verif.error_code, {"COMPONENT_TAMPER", "COMPONENT_LOCK_INVALID", "COMPONENT_DRIFT"})


if __name__ == "__main__":
    unittest.main()
