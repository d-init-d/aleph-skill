"""Dual release artifacts: deterministic DEFLATE, runtime closure, tamper.

The full artifact stays capability-complete; the runtime artifact is an
allowlisted transitive closure carrying the locked D Research component and
its interop contract, self-described by an embedded runtime manifest. Builds
must be reproducible byte-for-byte and extracted contents identical to the
verified distribution manifest — verified outside Git.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aleph import PACKAGE_VERSION  # noqa: E402
from build_release_assets import (  # noqa: E402
    ARCHIVE_ROOT,
    RUNTIME_MANIFEST_NAME,
    build_release_assets,
    verify_runtime_closure,
)

FIXTURES = ROOT / "tests" / "fixtures"


class DualArtifactBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        base = Path(cls._temporary.name)
        cls.first = build_release_assets(ROOT, base / "assets-a")
        cls.second = build_release_assets(ROOT, base / "assets-b")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_builds_are_deterministic(self) -> None:
        self.assertEqual(self.first["archive_sha256"], self.second["archive_sha256"])
        self.assertEqual(
            self.first["runtime_archive_sha256"], self.second["runtime_archive_sha256"]
        )
        self.assertEqual(self.first["compression"], "deflate")

    def test_checksums_cover_all_assets(self) -> None:
        text = Path(self.first["checksums"]).read_text(encoding="utf-8")
        self.assertIn(f"aleph-skill-v{PACKAGE_VERSION}.zip", text)
        self.assertIn(f"aleph-skill-runtime-v{PACKAGE_VERSION}.zip", text)
        self.assertIn(self.first["archive_sha256"], text)
        self.assertIn(self.first["runtime_archive_sha256"], text)

    def test_full_artifact_stays_capability_complete(self) -> None:
        with zipfile.ZipFile(self.first["archive"]) as bundle:
            names = set(bundle.namelist())
        self.assertIn(f"{ARCHIVE_ROOT}/tests/test_validation.py", names)
        self.assertIn(f"{ARCHIVE_ROOT}/distribution-manifest.json", names)
        self.assertIn(
            f"{ARCHIVE_ROOT}/components/d-research/templates/interop-contract.json", names
        )
        self.assertGreater(
            self.first["archive_file_count"], self.first["runtime_file_count"]
        )

    def test_runtime_profile_contents_and_self_description(self) -> None:
        with zipfile.ZipFile(self.first["runtime_archive"]) as bundle:
            names = set(bundle.namelist())
            manifest = json.loads(bundle.read(f"{ARCHIVE_ROOT}/{RUNTIME_MANIFEST_NAME}"))
            self.assertEqual(manifest["profile"], "runtime")
            self.assertEqual(manifest["package_version"], PACKAGE_VERSION)
            self.assertEqual(manifest["file_count"], self.first["runtime_file_count"])
            self.assertEqual(
                manifest["source_distribution_tree_sha256"], self.first["tree_sha256"]
            )
            # Every self-described entry must match the packaged bytes.
            for entry in manifest["files"]:
                payload = bundle.read(f"{ARCHIVE_ROOT}/{entry['path']}")
                self.assertEqual(len(payload), entry["size"], entry["path"])
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(), entry["sha256"], entry["path"]
                )
        self.assertIn(f"{ARCHIVE_ROOT}/SKILL.md", names)
        self.assertIn(f"{ARCHIVE_ROOT}/component-lock.json", names)
        self.assertIn(
            f"{ARCHIVE_ROOT}/components/d-research/templates/interop-contract.json", names
        )
        self.assertFalse([n for n in names if n.startswith(f"{ARCHIVE_ROOT}/tests/")])
        self.assertFalse([n for n in names if n.startswith(f"{ARCHIVE_ROOT}/examples/")])

    def test_runtime_closure_outside_git_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "extracted"
            with zipfile.ZipFile(self.first["runtime_archive"]) as bundle:
                bundle.extractall(extracted)
            runtime_root = extracted / ARCHIVE_ROOT
            closure = verify_runtime_closure(runtime_root)
            self.assertTrue(closure["ok"], closure["problems"])

            target = runtime_root / "components" / "d-research" / "package.json"
            raw = target.read_bytes()
            target.write_bytes(raw + b" ")
            tampered = verify_runtime_closure(runtime_root)
            self.assertFalse(tampered["ok"])
            self.assertTrue(
                any("component lock failed" in problem for problem in tampered["problems"]),
                tampered["problems"],
            )


class WorkspaceFormulaAndPartialTests(unittest.TestCase):
    def _workspace(self, temporary: str) -> Path:
        workspace = Path(temporary) / "ws"
        shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
        return workspace

    def test_validator_reports_workspace_resolved_formula(self) -> None:
        from aleph.io import write_json_atomic
        from aleph.validator import validate_workspace

        for declared, reported in (("2.0.0", "2.0.0"), ("2.1.0", "2.1.0")):
            with tempfile.TemporaryDirectory() as temporary:
                workspace = self._workspace(temporary)
                manifest = json.loads(
                    (workspace / "simulation-manifest.json").read_text(encoding="utf-8")
                )
                manifest["formula_version"] = declared
                write_json_atomic(workspace / "simulation-manifest.json", manifest)
                result = validate_workspace(workspace, mode="draft", require_report=False)
                self.assertEqual(result["formula_version"], reported, declared)

    def test_unsaturated_partial_verified_claim_is_normalized_not_invalid(self) -> None:
        from aleph.io import write_json_atomic
        from aleph.validator import validate_workspace

        with tempfile.TemporaryDirectory() as temporary:
            workspace = self._workspace(temporary)
            manifest = json.loads(
                (workspace / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            control = manifest["execution"]["research_control"]
            control["saturation_reached"] = False
            control["stop_reason"] = "host_limit:test-budget"
            control["next_wave_queue"] = ["follow up on fixture claims"]
            control["unresolved_critical_gaps"] = ["fixture gap"]
            manifest["assurance_tier"] = "verified"
            write_json_atomic(workspace / "simulation-manifest.json", manifest)

            draft = validate_workspace(workspace, mode="draft", require_report=False)
            issues = draft.get("issues") or []
            normalized = [
                item for item in issues if item.get("code") == "PARTIAL_ASSURANCE_NORMALIZED"
            ]
            self.assertEqual(len(normalized), 1, issues)
            self.assertEqual(normalized[0]["severity"], "warning")
            self.assertNotIn("PARTIAL_ASSURANCE_NORMALIZED", draft.get("error_codes") or [])

            final = validate_workspace(workspace, mode="final", require_report=True)
            self.assertEqual(final["status"], "fail")
            self.assertIn("EVIDENCE_SATURATION", final.get("error_codes") or [])


if __name__ == "__main__":
    unittest.main()
