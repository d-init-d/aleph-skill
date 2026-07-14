"""Build deterministic, manifest-exact Aleph release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aleph import PACKAGE_VERSION
from aleph.installer import MANIFEST_NAME, verify_distribution_manifest
from aleph.io import ResourceLimitError
from aleph.paths import is_distribution_path

ARCHIVE_ROOT = "aleph-skill"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o100644 << 16
    return info


def _verified_files(root: Path) -> tuple[list[str], dict[str, tuple[int, str]], dict[str, Any]]:
    verification = verify_distribution_manifest(root, require=True)
    if not verification.get("ok"):
        issues = verification.get("issues", [])
        raise ValueError(f"distribution manifest verification failed: {issues}")
    files = verification.get("files")
    if not isinstance(files, list) or MANIFEST_NAME not in files:
        raise ValueError("verified distribution file list is incomplete")
    entries = verification.get("file_entries")
    if not isinstance(entries, list):
        raise ValueError("verified distribution entries are unavailable")
    expected: dict[str, tuple[int, str]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("verified distribution entry is malformed")
        relative = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(size, int) or not isinstance(digest, str):
            raise ValueError("verified distribution entry is malformed")
        expected[relative] = (size, digest)
    manifest = root / MANIFEST_NAME
    manifest_digest = verification.get("manifest_sha256")
    if not isinstance(manifest_digest, str):
        raise ValueError("verified manifest digest is unavailable")
    expected[MANIFEST_NAME] = (manifest.stat().st_size, manifest_digest)
    relative_files = sorted(str(item) for item in files)
    if set(relative_files) != set(expected):
        raise ValueError("verified distribution entries do not match the file list")
    return relative_files, expected, verification


def _write_archive(
    root: Path,
    archive: Path,
    relative_files: list[str],
    expected: Mapping[str, tuple[int, str]],
) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, mode="w", allowZip64=True) as bundle:
            for relative in relative_files:
                source = root / relative
                if not source.is_file():
                    raise ValueError(f"manifest file disappeared while packaging: {relative}")
                expected_size, expected_digest = expected[relative]
                digest = hashlib.sha256()
                observed_size = 0
                with source.open("rb") as source_handle, bundle.open(
                    _zip_info(f"{ARCHIVE_ROOT}/{relative}"), mode="w"
                ) as destination_handle:
                    while True:
                        chunk = source_handle.read(1024 * 1024)
                        if not chunk:
                            break
                        observed_size += len(chunk)
                        if observed_size > expected_size:
                            raise ValueError(f"manifest file changed while packaging: {relative}")
                        digest.update(chunk)
                        destination_handle.write(chunk)
                if observed_size != expected_size or digest.hexdigest() != expected_digest:
                    raise ValueError(f"manifest file changed while packaging: {relative}")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)


def build_release_assets(root: Path, output_dir: Path) -> dict[str, Any]:
    """Build a reproducible ZIP, manifest copy, and checksum list."""
    root = root.resolve()
    output_dir = output_dir.resolve()
    try:
        relative_output = output_dir.relative_to(root)
    except ValueError:
        relative_output = None
    if relative_output is not None:
        if not relative_output.parts:
            raise ValueError("output directory must not be the distribution root")
        output_candidates = (
            relative_output / f"aleph-skill-v{PACKAGE_VERSION}.zip",
            relative_output / MANIFEST_NAME,
            relative_output / "SHA256SUMS.txt",
        )
        if any(is_distribution_path(path.as_posix()) for path in output_candidates):
            raise ValueError("output directory would modify the attested distribution tree")

    relative_files, expected, verification = _verified_files(root)

    archive = output_dir / f"aleph-skill-v{PACKAGE_VERSION}.zip"
    manifest_asset = output_dir / MANIFEST_NAME
    checksums = output_dir / "SHA256SUMS.txt"
    output_dir.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix=".aleph-release-", dir=output_dir))
    staged_archive = staging / archive.name
    staged_manifest = staging / manifest_asset.name
    staged_checksums = staging / checksums.name
    try:
        _write_archive(root, staged_archive, relative_files, expected)
        shutil.copyfile(root / MANIFEST_NAME, staged_manifest)
        expected_manifest_size, expected_manifest_digest = expected[MANIFEST_NAME]
        if (
            staged_manifest.stat().st_size != expected_manifest_size
            or _sha256(staged_manifest) != expected_manifest_digest
        ):
            raise ValueError("distribution manifest changed while copying the release asset")
        after_files, after_expected, after_verification = _verified_files(root)
        if (
            after_files != relative_files
            or after_expected != expected
            or after_verification.get("tree_sha256") != verification.get("tree_sha256")
        ):
            raise ValueError("distribution changed while packaging")

        archive_digest = _sha256(staged_archive)
        manifest_digest = _sha256(staged_manifest)
        checksum_text = (
            f"{archive_digest}  {archive.name}\n"
            f"{manifest_digest}  {manifest_asset.name}\n"
        )
        staged_checksums.write_text(checksum_text, encoding="utf-8", newline="\n")
        os.replace(staged_archive, archive)
        os.replace(staged_manifest, manifest_asset)
        os.replace(staged_checksums, checksums)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "status": "pass",
        "package_version": PACKAGE_VERSION,
        "archive": str(archive),
        "archive_sha256": archive_digest,
        "manifest": str(manifest_asset),
        "manifest_sha256": manifest_digest,
        "checksums": str(checksums),
        "archive_file_count": len(relative_files),
        "distribution_file_count": verification.get("file_count"),
        "tree_sha256": verification.get("tree_sha256"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build deterministic release assets from the verified distribution manifest."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        result = build_release_assets(args.root, args.output_dir)
    except (OSError, ResourceLimitError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2))
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
