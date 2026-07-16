"""Packaging allowlist and single-entrypoint scans for bundled D Research."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lock_bundled_component
from aleph.component_registry import build_component_lock, locked_component_paths
from aleph.installer import collect_distribution_files, scan_secret_like_files
from aleph.paths import is_distribution_path
from lock_bundled_component import normalize_snapshot

ROOT = Path(__file__).resolve().parents[1]


class ComponentPackagingTests(unittest.TestCase):
    def test_lock_and_component_are_distribution_paths(self) -> None:
        self.assertTrue(is_distribution_path("component-lock.json"))
        self.assertTrue(is_distribution_path("THIRD_PARTY_NOTICES.md"))
        self.assertTrue(is_distribution_path("components/d-research/SKILL.md"))
        self.assertTrue(is_distribution_path("components/d-research/.npmignore"))
        self.assertTrue(is_distribution_path("components/d-research/scripts/playwright_probe.mjs"))
        self.assertTrue(is_distribution_path("components/d-research/scripts/evidence_ledger.py"))
        self.assertTrue(is_distribution_path("components/d-research/docs/.archive/UPGRADE-PLAN.md"))
        self.assertFalse(is_distribution_path("components/d-research/docs/.hidden/evil.md"))
        self.assertFalse(is_distribution_path("components/d-research/scripts/__pycache__/helper.pyc"))
        # Global binary looseness still refused
        self.assertFalse(is_distribution_path("scripts/evil.exe"))
        self.assertFalse(is_distribution_path("node_modules/pkg/index.js"))

    def test_collect_includes_component_and_excludes_forbidden(self) -> None:
        files = collect_distribution_files(ROOT)
        rels = {path.relative_to(ROOT).as_posix() for path in files}
        self.assertIn("component-lock.json", rels)
        self.assertTrue(any(r.startswith("components/d-research/") for r in rels))
        self.assertFalse(any("node_modules" in r for r in rels))
        self.assertFalse(any(r.endswith(".pem") for r in rels))
        self.assertIn("components/d-research/docs/.archive/UPGRADE-PLAN.md", rels)
        # Nested skill must exist as resource but only root SKILL is host entry
        self.assertTrue((ROOT / "SKILL.md").is_file())
        self.assertTrue((ROOT / "components" / "d-research" / "SKILL.md").is_file())

    def test_no_secret_like_in_component(self) -> None:
        findings = scan_secret_like_files(ROOT / "components" / "d-research")
        self.assertEqual(findings, [])
        secret_content = [f for f in findings if f.get("reason") == "secret-like content"]
        self.assertEqual(secret_content, [])
        # No real private-key material or env files
        self.assertFalse(any(f.get("reason") == "secret-like filename" for f in findings))

    def test_component_lock_schema(self) -> None:
        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        entry = lock["components"]["d-research"]
        self.assertEqual(entry["uri"], "aleph-component://d-research")
        self.assertEqual(entry["version"], "3.2.1")
        self.assertEqual(entry["source_tag"], "v3.2.1")
        self.assertEqual(entry["file_count"], 201)
        self.assertEqual(entry["file_count"], len(entry["files"]))
        self.assertIn("scripts/evidence_ledger.py", entry["entrypoints"])
        self.assertTrue(entry["tree_sha256"].startswith("sha256:"))
        self.assertEqual(entry["source_archive_format"], "git-archive-tar")
        self.assertEqual(len(entry["upstream_tree"]), 40)
        recipe = entry["snapshot_recipe"]
        self.assertEqual(recipe["text_eol"], "lf")
        self.assertEqual(len(recipe["excluded_paths"]), 529)
        self.assertIn(".github/workflows/release-attest.yml", recipe["excluded_paths"])
        self.assertIn("release-evidence/v3.2.1/promotion.json", recipe["excluded_paths"])
        self.assertNotIn(".npmignore", recipe["excluded_paths"])
        self.assertNotIn("docs/.archive/UPGRADE-PLAN.md", recipe["excluded_paths"])

    def test_component_lock_is_reproducible_and_fully_distributed(self) -> None:
        existing = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(build_component_lock(ROOT), existing)

        distributed = {
            path.relative_to(ROOT).as_posix() for path in collect_distribution_files(ROOT)
        }
        self.assertLessEqual(locked_component_paths(ROOT), distributed)

    def test_ci_verifies_component_against_pinned_upstream_tag(self) -> None:
        lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
        entry = lock["components"]["d-research"]
        workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("component-provenance:", workflow)
        self.assertIn(
            f"UPSTREAM_REPOSITORY: {entry['source_repository']}", workflow
        )
        self.assertIn(f"UPSTREAM_TAG: {entry['source_tag']}", workflow)
        self.assertIn(
            f"UPSTREAM_TAG_OBJECT: {entry['upstream_tag_object']}", workflow
        )
        self.assertIn(f"UPSTREAM_COMMIT: {entry['upstream_commit']}", workflow)
        self.assertIn("git init --bare", workflow)
        self.assertIn("--no-tags --depth=1", workflow)
        self.assertIn("cat-file -t", workflow)
        self.assertIn("--upstream-repo", workflow)
        self.assertIn("upstream_verification", workflow)
        verifier = (ROOT / "scripts" / "lock_bundled_component.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("core.autocrlf=false", verifier)
        self.assertIn("core.eol=lf", verifier)
        self.assertIn("tar.umask=0002", verifier)
        self.assertIn('cat "$provenance"', workflow)

    def test_upstream_archive_ignores_host_line_ending_configuration(self) -> None:
        content = b"hello\n"
        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode="w:") as archive:
            member = tarfile.TarInfo("sample.md")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        archive_bytes = archive_buffer.getvalue()
        tag_object = "1" * 40
        commit = "2" * 40
        tree = "3" * 40
        completed = [
            subprocess.CompletedProcess([], 0, stdout=(tag_object + "\n").encode(), stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=(commit + "\n").encode(), stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=(tree + "\n").encode(), stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=archive_bytes, stderr=b""),
        ]
        rebuilt = {
            "components": {
                "d-research": {
                    "source_tag": "v3.2.0",
                    "upstream_tag_object": tag_object,
                    "upstream_commit": commit,
                    "upstream_tree": tree,
                    "source_archive_sha256": (
                        "sha256:" + hashlib.sha256(archive_bytes).hexdigest()
                    ),
                    "snapshot_recipe": {"excluded_paths": []},
                    "files": [{"path": "sample.md"}],
                }
            }
        }

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            component = root / "components" / "d-research"
            component.mkdir(parents=True)
            (component / "sample.md").write_bytes(content)
            upstream = root / "upstream.git"
            upstream.mkdir()
            with (
                patch.object(lock_bundled_component.shutil, "which", return_value="git"),
                patch.object(
                    lock_bundled_component.subprocess,
                    "run",
                    side_effect=completed,
                ) as run,
            ):
                verification = lock_bundled_component.verify_upstream_snapshot(
                    root,
                    upstream,
                    rebuilt,
                    component_id="d-research",
                )

        self.assertEqual(verification["archive_sha256"], rebuilt["components"]["d-research"]["source_archive_sha256"])
        archive_command = run.call_args_list[3].args[0]
        self.assertEqual(
            archive_command,
            [
                "git",
                "-C",
                str(upstream.resolve()),
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.eol=lf",
                "-c",
                "tar.umask=0002",
                "archive",
                "--format=tar",
                commit,
            ],
        )

    def test_component_snapshot_has_no_cache_or_crlf_text(self) -> None:
        component = ROOT / "components" / "d-research"
        forbidden = {
            path.relative_to(component).as_posix()
            for path in component.rglob("*")
            if path.is_file()
            and (
                "__pycache__" in path.parts
                or
                path.suffix.lower() in {".pyc", ".pyo", ".pyd"}
            )
        }
        self.assertEqual(forbidden, set())
        text_suffixes = {
            ".bib", ".css", ".csv", ".html", ".js", ".json", ".md",
            ".mjs", ".py", ".toml", ".ts", ".txt", ".yaml", ".yml",
        }
        crlf = [
            path.relative_to(component).as_posix()
            for path in component.rglob("*")
            if path.is_file()
            and (path.name in {".npmignore", "LICENSE"} or path.suffix.lower() in text_suffixes)
            and b"\r\n" in path.read_bytes()
        ]
        self.assertEqual(crlf, [])

    def test_snapshot_normalization_is_text_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            component = Path(raw) / "d-research"
            component.mkdir()
            text = component / "sample.md"
            binary = component / "sample.pdf"
            text.write_bytes(b"one\r\ntwo\r\n")
            binary.write_bytes(b"binary\r\nbytes")
            self.assertEqual(normalize_snapshot(component), ["sample.md"])
            self.assertEqual(text.read_bytes(), b"one\ntwo\n")
            self.assertEqual(binary.read_bytes(), b"binary\r\nbytes")
            self.assertEqual(normalize_snapshot(component), [])

    def test_single_host_entrypoint(self) -> None:
        # Only root SKILL.md is the installable skill entry; nested is resource.
        root_skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: aleph-skill", root_skill.split("---", 2)[1])
        nested = (ROOT / "components" / "d-research" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: d-research", nested.split("---", 2)[1])
        # Adapter registry must not list d-research as install target
        registry = json.loads((ROOT / "adapters" / "registry.json").read_text(encoding="utf-8"))
        self.assertNotIn("d-research", registry.get("adapters", {}))


if __name__ == "__main__":
    unittest.main()
