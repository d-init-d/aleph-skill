from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph import PACKAGE_VERSION  # noqa: E402
from aleph.installer import (  # noqa: E402
    MANIFEST_NAME,
    build_distribution_manifest,
    plan_install,
    verify_distribution_manifest,
)
from aleph.io import write_json_atomic  # noqa: E402
from build_release_assets import ARCHIVE_ROOT, build_release_assets  # noqa: E402
from release_gate import _static_contract  # noqa: E402


class ReleasePackagingV201Tests(unittest.TestCase):
    def _source(self, parent: Path) -> Path:
        source = parent / "source"
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text("---\nname: aleph-skill\n---\n", encoding="utf-8")
        (source / "scripts" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
        (source / ".gitignore").write_text("dist/\n", encoding="utf-8")
        write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
        return source

    def test_archive_is_reproducible_and_manifest_exact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            source = self._source(temporary)
            first = build_release_assets(source, temporary / "first")
            second = build_release_assets(source, temporary / "second")

            self.assertEqual(first["archive_sha256"], second["archive_sha256"])
            manifest = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
            expected = {
                f"{ARCHIVE_ROOT}/{entry['path']}" for entry in manifest["files"]
            }
            expected.add(f"{ARCHIVE_ROOT}/{MANIFEST_NAME}")
            archive = Path(str(first["archive"]))
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(set(bundle.namelist()), expected)
                self.assertTrue(all(item.compress_type == zipfile.ZIP_STORED for item in bundle.infolist()))
                bundle.extractall(temporary / "extracted")

            extracted = temporary / "extracted" / ARCHIVE_ROOT
            self.assertTrue(verify_distribution_manifest(extracted)["ok"])
            plan = plan_install(extracted, temporary / "installed", "symlink")
            self.assertTrue(plan["ok"], plan["issues"])
            self.assertNotIn(f"{ARCHIVE_ROOT}/.gitignore", expected)

    def test_repository_distribution_contains_every_self_test_input(self) -> None:
        self.assertTrue(_static_contract(ROOT)["ok"])
        manifest = build_distribution_manifest(ROOT)
        distributed = {entry["path"] for entry in manifest["files"]}
        required = {
            "uv.lock",
            ".github/workflows/ci.yml",
            ".github/workflows/verify.yml",
            ".github/workflows/release.yml",
            ".github/release-notes/v2.0.1.md",
        }
        self.assertFalse(required - distributed, sorted(required - distributed))

    def test_tampered_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            source = self._source(temporary)
            (source / "SKILL.md").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest verification failed"):
                build_release_assets(source, temporary / "output")

    def test_manifest_metadata_order_and_unknown_fields_are_fail_closed(self) -> None:
        mutations = (
            lambda value: value.update({"package_version": "0.0.0"}),
            lambda value: value.update({"unexpected": True}),
            lambda value: value.update({"file_count": 999}),
            lambda value: value.update({"files": list(reversed(value["files"]))}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index), tempfile.TemporaryDirectory() as raw:
                temporary = Path(raw)
                source = self._source(temporary)
                manifest_path = source / MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest)
                write_json_atomic(manifest_path, manifest)

                verification = verify_distribution_manifest(source)
                self.assertFalse(verification["ok"], verification)
                self.assertIn(
                    "STALE_ARTIFACT",
                    {item["code"] for item in verification["issues"]},
                )
                with self.assertRaisesRegex(ValueError, "manifest verification failed"):
                    build_release_assets(source, temporary / "output")

    def test_manifest_duplicate_json_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            source = self._source(temporary)
            manifest_path = source / MANIFEST_NAME
            original = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                original.replace("{", '{"package_version":"0.0.0",', 1),
                encoding="utf-8",
            )

            verification = verify_distribution_manifest(source)
            self.assertFalse(verification["ok"], verification)
            self.assertEqual(verification["status"], "invalid")

    def test_output_must_not_enter_the_attested_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            source = self._source(temporary)
            with self.assertRaisesRegex(ValueError, "attested distribution tree"):
                build_release_assets(source, source / "scripts" / "release-output")

    def test_manifest_asset_copy_race_is_refused_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            source = self._source(temporary)
            output = temporary / "output"

            def inject_manifest(_source: Path, destination: Path) -> None:
                Path(destination).write_text('{"injected": true}\n', encoding="utf-8")

            with (
                mock.patch(
                    "build_release_assets.shutil.copyfile",
                    side_effect=inject_manifest,
                ),
                self.assertRaisesRegex(ValueError, "manifest changed while copying"),
            ):
                build_release_assets(source, output)

            self.assertFalse((output / MANIFEST_NAME).exists())
            self.assertFalse(list(output.glob("*.zip")))
            self.assertFalse((output / "SHA256SUMS.txt").exists())

    def test_tag_release_is_gated_pinned_and_attested(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        uses_lines = [
            line.strip()
            for line in workflow.splitlines()
            if "uses:" in line and "./.github/workflows/" not in line
        ]
        self.assertTrue(uses_lines)
        for line in uses_lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"uses: [^@]+@[0-9a-f]{40}(?:\s+#.*)?$")
        for required in (
            "uses: ./.github/workflows/verify.yml",
            "needs: verify",
            "cancel-in-progress: false",
            "uv sync --locked --python 3.13",
            "scripts/build_release_assets.py --output-dir dist-repro",
            "actions/attest-build-provenance@",
            "--verify-tag",
            "git cat-file -t",
            "refs/aleph-release-tags/",
            "git rev-parse HEAD",
            "git merge-base --is-ancestor",
            "refs/remotes/origin/main",
            "already exists; refusing to modify it",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn(
            'git cat-file -t "refs/tags/$GITHUB_REF_NAME"', workflow
        )
        self.assertNotIn("gh release edit", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertTrue(
            (ROOT / ".github" / "release-notes" / f"v{PACKAGE_VERSION}.md").is_file()
        )
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            package["scripts"]["release:build"], "python scripts/build_release_assets.py"
        )
        self.assertIsNone(re.search(r"uses:\s+[^\n]+@v\d", workflow))

        notes = (
            ROOT / ".github" / "release-notes" / f"v{PACKAGE_VERSION}.md"
        ).read_text(encoding="utf-8")
        self.assertIn("sha256sum -c SHA256SUMS.txt", notes)
        self.assertIn("gh attestation verify aleph-skill-v2.0.1.zip", notes)
        self.assertIn("--repo d-init-d/aleph-skill", notes)
        self.assertIn(
            "--signer-workflow d-init-d/aleph-skill/.github/workflows/release.yml",
            notes,
        )
        self.assertIn("--source-ref refs/tags/v2.0.1", notes)
        self.assertIn("GitHub's attestation service", notes)


if __name__ == "__main__":
    unittest.main()
