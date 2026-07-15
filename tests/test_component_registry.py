"""Unit tests for bundled component resolve/verify/discovery security."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from aleph.component_registry import (
    COMPONENT_URI,
    ComponentError,
    discover_d_research,
    resolve_component,
    skill_root_from,
    verify_component_lock,
)

ROOT = Path(__file__).resolve().parents[1]


class ComponentRegistryTests(unittest.TestCase):
    def test_valid_uri_resolves_and_verifies(self) -> None:
        verification = verify_component_lock(skill_root=ROOT)
        self.assertTrue(verification.ok, verification.message)
        self.assertGreater(verification.file_count, 0)
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        self.assertEqual(resolution.component_uri, COMPONENT_URI)
        self.assertEqual(resolution.source_kind, "bundled")
        self.assertTrue(Path(resolution.root).is_dir())
        binding = resolution.binding()
        self.assertNotIn(":", binding["component_lock_sha256"][7:8] and "")  # sanity
        self.assertTrue(str(binding["component_lock_sha256"]).startswith("sha256:"))
        self.assertNotIn(str(ROOT).replace("\\", "/"), json.dumps(binding))

    def test_fake_uri_and_traversal_refused(self) -> None:
        with self.assertRaises(ComponentError) as ctx:
            resolve_component("aleph-component://evil", skill_root=ROOT)
        self.assertEqual(ctx.exception.code, "COMPONENT_OVERRIDE_REFUSED")
        with self.assertRaises(ComponentError):
            resolve_component("../etc/passwd", skill_root=ROOT)
        with self.assertRaises(ComponentError):
            resolve_component(r"C:\Windows\System32", skill_root=ROOT)

    def test_bundled_wins_over_fake_env(self) -> None:
        fake = r"C:\definitely-not-d-research-skill"
        result = discover_d_research(skill_root=ROOT, env={"D_RESEARCH_SKILL": fake})
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["source"], "bundled")
        self.assertEqual(result["path"], COMPONENT_URI)
        self.assertTrue(any("OVERRIDE" in str(item.get("reason", "")) for item in result.get("tried", [])))

    def test_one_byte_tamper_hard_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "aleph"
            # Copy only lock + component subset via real tree clone of needed pieces
            shutil.copytree(ROOT / "components", clone / "components")
            shutil.copy2(ROOT / "component-lock.json", clone / "component-lock.json")
            target = clone / "components" / "d-research" / "package.json"
            data = bytearray(target.read_bytes())
            data[0] = (data[0] + 1) % 256
            target.write_bytes(bytes(data))
            verification = verify_component_lock(skill_root=clone)
            self.assertFalse(verification.ok)
            self.assertEqual(verification.error_code, "COMPONENT_TAMPER")

    def test_missing_and_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "aleph"
            shutil.copytree(ROOT / "components", clone / "components")
            shutil.copy2(ROOT / "component-lock.json", clone / "component-lock.json")
            missing = clone / "components" / "d-research" / "LICENSE"
            missing.unlink()
            verification = verify_component_lock(skill_root=clone)
            self.assertFalse(verification.ok)
            self.assertEqual(verification.error_code, "COMPONENT_FILE_MISSING")

            # restore from ROOT and add extra
            shutil.rmtree(clone)
            shutil.copytree(ROOT / "components", clone / "components")
            shutil.copy2(ROOT / "component-lock.json", clone / "component-lock.json")
            extra = clone / "components" / "d-research" / "EXTRA_TAMPER.txt"
            extra.write_text("nope", encoding="utf-8")
            verification = verify_component_lock(skill_root=clone)
            self.assertFalse(verification.ok)
            self.assertEqual(verification.error_code, "COMPONENT_EXTRA_FILE")

    def test_bytecode_cache_is_an_extra_file_not_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "aleph"
            shutil.copytree(ROOT / "components", clone / "components")
            shutil.copy2(ROOT / "component-lock.json", clone / "component-lock.json")
            cache = clone / "components" / "d-research" / "scripts" / "__pycache__"
            cache.mkdir()
            (cache / "rogue.cpython-313.pyc").write_bytes(b"not an attested resource")
            verification = verify_component_lock(skill_root=clone)
            self.assertFalse(verification.ok)
            self.assertEqual(verification.error_code, "COMPONENT_EXTRA_FILE")

    def test_lock_cannot_claim_an_unapproved_nested_dot_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "aleph"
            shutil.copytree(ROOT / "components", clone / "components")
            lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
            lock["components"]["d-research"]["files"][0]["path"] = (
                "docs/.hidden/evil.md"
            )
            (clone / "component-lock.json").write_text(
                json.dumps(lock), encoding="utf-8", newline="\n"
            )
            verification = verify_component_lock(skill_root=clone)
            self.assertFalse(verification.ok)
            self.assertEqual(verification.error_code, "COMPONENT_LOCK_INVALID")

    def test_crlf_text_snapshot_is_platform_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "aleph"
            shutil.copytree(ROOT / "components", clone / "components")
            shutil.copy2(ROOT / "component-lock.json", clone / "component-lock.json")
            skill = clone / "components" / "d-research" / "SKILL.md"
            raw = skill.read_bytes()
            self.assertIn(b"\n", raw)
            skill.write_bytes(raw.replace(b"\n", b"\r\n", 1))
            verification = verify_component_lock(skill_root=clone)
            self.assertFalse(verification.ok)
            self.assertEqual(verification.error_code, "COMPONENT_DRIFT")

    def test_explicit_incompatible_external_hard_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bad = Path(temporary) / "bad"
            bad.mkdir()
            (bad / "SKILL.md").write_text("---\nname: not-d-research\n---\n", encoding="utf-8")
            (bad / "package.json").write_text(
                json.dumps({"name": "other", "version": "3.0.0"}), encoding="utf-8"
            )
            (bad / "scripts").mkdir()
            (bad / "scripts" / "evidence_ledger.py").write_text("# x\n", encoding="utf-8")
            result = discover_d_research(
                skill_root=ROOT,
                explicit=bad,
                allow_external=True,
                require_bundled=False,
            )
            # When require_bundled False and bundle still exists, component_registry
            # still prefers bundled if require_bundled default path... we force no bundle.
            empty = Path(temporary) / "empty-skill"
            empty.mkdir()
            result = discover_d_research(
                skill_root=empty,
                explicit=bad,
                allow_external=True,
                require_bundled=False,
            )
            self.assertEqual(result["status"], "incompatible")

    def test_skill_root_env(self) -> None:
        root = skill_root_from(env={"ALEPH_SKILL_ROOT": str(ROOT)})
        self.assertEqual(root.resolve(), ROOT.resolve())


if __name__ == "__main__":
    unittest.main()
