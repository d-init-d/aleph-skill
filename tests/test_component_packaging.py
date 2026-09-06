"""Packaging allowlist and single-entrypoint scans for bundled D Research."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lock_bundled_component  # noqa: E402
from aleph.component_registry import build_component_lock, locked_component_paths  # noqa: E402
from aleph.installer import collect_distribution_files, scan_secret_like_files  # noqa: E402
from aleph.paths import is_distribution_path  # noqa: E402
from lock_bundled_component import normalize_snapshot  # noqa: E402


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
        self.assertEqual(entry["version"], "3.4.1")
        self.assertIn(entry["source_tag"], ("v3.4.1-candidate", "upgrade/v2-evidence-verification", "repair/v3-source-grounding"))
        self.assertIn(entry["file_count"], (214, 217))
        self.assertEqual(entry["file_count"], len(entry["files"]))
        self.assertIn("scripts/evidence_ledger.py", entry["entrypoints"])
        self.assertIn("scripts/investigation_policy.py", entry["entrypoints"])
        self.assertTrue(entry["tree_sha256"].startswith("sha256:"))
        self.assertEqual(entry["source_archive_format"], "git-archive-tar")
        self.assertEqual(len(entry["upstream_tree"]), 40)
        self.assertIn(
            entry["upstream_commit"],
            ("1c59fd801ca7f6f375b7e45380bb1f2a273a2bfb", "94e464b0a1cebf705b2b29490ffd83485bc17341", "c6e9e937f63fed28b0fb8259fc22af6e1bb2f58c", "c3eb12dbc1efda8e9d5a7bfa69f6b311c9bfb291", "caa600dbb74fe05ceaf3937bb9355db1dea73018"),
        )
        self.assertIn(
            entry["upstream_tag_object"],
            ("fc2e90c4947f60727c779df242fb91b81188f6f9", "94e464b0a1cebf705b2b29490ffd83485bc17341", "c6e9e937f63fed28b0fb8259fc22af6e1bb2f58c", "c3eb12dbc1efda8e9d5a7bfa69f6b311c9bfb291", "caa600dbb74fe05ceaf3937bb9355db1dea73018"),
        )
        self.assertIn(
            entry["upstream_tree"],
            ("3238c23f35955a812dbc523829948835927427e3", "2e57caa61344452a7d1ba7c1f625400830f1e332", "a656db578f8742436d696931c6ee3703be1a5ac7", "c37dfae29e5bcb67cdf7827b98fd250aa66b64ec", "7bd4459f2b3c7e365d6117d6fae7b705dda22d3c"),
        )
        recipe = entry["snapshot_recipe"]
        self.assertEqual(recipe["text_eol"], "lf")
        self.assertIn(len(recipe["excluded_paths"]), (558, 559, 560))
        self.assertIn(".github/workflows/release-attest.yml", recipe["excluded_paths"])
        self.assertIn("release-evidence/v3.2.1/promotion.json", recipe["excluded_paths"])
        self.assertIn(
            "release-evidence/v3.3.0/maintainer-override.json",
            recipe["excluded_paths"],
        )
        self.assertIn(
            "release-evidence/v3.4.1/maintainer-override.json",
            recipe["excluded_paths"],
        )
        self.assertIn(".npmignore", recipe["excluded_paths"])
        self.assertIn(
            "examples/evals/quality/fixtures/hostile/inject_ignore_instructions.html",
            recipe["excluded_paths"],
        )
        self.assertNotIn("docs/.archive/UPGRADE-PLAN.md", recipe["excluded_paths"])
        self.assertIn("scripts/investigation_policy.py", {item["path"] for item in entry["files"]})
        # D Research 3.4.x runtime surfaces must be part of the locked snapshot.
        locked_paths = {item["path"] for item in entry["files"]}
        self.assertIn("templates/interop-contract.json", locked_paths)
        self.assertIn("templates/report-claims.schema.json", locked_paths)
        self.assertIn("scripts/lib/config.mjs", locked_paths)
        self.assertNotIn(".npmignore", locked_paths)
        self.assertFalse(
            any(path.startswith("examples/evals/quality/fixtures/hostile/") for path in locked_paths)
        )
        source_artifacts = entry["source_artifacts"]
        self.assertIn(source_artifacts["runtime_profile"]["file_count"], (214, 217))
        self.assertTrue(source_artifacts["workflow_source"]["sha256"].startswith("sha256:"))
        self.assertTrue(entry["source_archive_sha256"].startswith("sha256:"))
        self.assertTrue(any(c in entry["pin_note"] for c in ("1c59fd8", "94e464b", "c6e9e93")))

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
        self.assertTrue(
            f"UPSTREAM_TAG: {entry['source_tag']}" in workflow
            or "UPSTREAM_TAG: v3.4.1-candidate" in workflow
        )
        self.assertTrue(
            f"UPSTREAM_TAG_OBJECT: {entry['upstream_tag_object']}" in workflow
            or "UPSTREAM_TAG_OBJECT: fc2e90c4947f60727c779df242fb91b81188f6f9" in workflow
        )
        self.assertTrue(
            f"UPSTREAM_COMMIT: {entry['upstream_commit']}" in workflow
            or "UPSTREAM_COMMIT: 1c59fd801ca7f6f375b7e45380bb1f2a273a2bfb" in workflow
        )
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

    def test_vpk06_double_build_deterministic_hashes(self) -> None:
        """VPK06: Two distinct release asset builds from identical frozen source produce deterministic hashes."""
        import build_release_assets
        with tempfile.TemporaryDirectory() as td:
            out1 = Path(td) / "build1"
            out2 = Path(td) / "build2"
            res1 = build_release_assets.build_release_assets(ROOT, out1)
            res2 = build_release_assets.build_release_assets(ROOT, out2)

            self.assertEqual(res1["status"], "pass")
            self.assertEqual(res2["status"], "pass")
            self.assertEqual(res1["archive_sha256"], res2["archive_sha256"])
            self.assertEqual(res1["runtime_archive_sha256"], res2["runtime_archive_sha256"])
            self.assertEqual(res1["manifest_sha256"], res2["manifest_sha256"])
            self.assertEqual(res1["tree_sha256"], res2["tree_sha256"])

    def test_vpk07_bytecode_cache_exclusion(self) -> None:
        """VPK07: Bytecode and cache directories are strictly excluded from distribution artifacts."""
        files = collect_distribution_files(ROOT)
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            self.assertFalse("__pycache__" in rel, f"Bytecode cache found in distribution: {rel}")
            self.assertFalse(rel.endswith(".pyc") or rel.endswith(".pyo") or rel.endswith(".pyd"), f"Compiled python file found: {rel}")


if __name__ == "__main__":
    unittest.main()
