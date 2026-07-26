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
from aleph.component_registry import (
    COMPONENT_REL,
    LOCK_NAME,
    locked_component_paths,
    verify_component_lock,
)
from aleph.installer import MANIFEST_NAME, verify_distribution_manifest
from aleph.io import ResourceLimitError
from aleph.paths import is_distribution_path

ARCHIVE_ROOT = "aleph-skill"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"

# The runtime profile is an allowlist over the verified distribution list.
# The full artifact stays capability-complete; runtime drops development-only
# surfaces (tests, examples, changelog, packaging locks) while keeping the
# complete routed closure: entry docs, scripts, templates, references,
# adapters, domain packs, schemas, agents metadata, and the entire locked
# D Research component with its interop contract.
RUNTIME_TOP_FILES = frozenset(
    {
        "SKILL.md",
        "AGENTS.md",
        "README.md",
        "README.vi.md",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "package.json",
        "component-lock.json",
    }
)
RUNTIME_DIRS = (
    "scripts/",
    "templates/",
    "references/",
    "adapters/",
    "packs/",
    "schemas/",
    "agents/",
    "components/",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(name: str, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = compress_type
    info.external_attr = 0o100644 << 16
    return info


def _verify_component_distribution_coverage(root: Path, distributed: set[str]) -> None:
    """Require every locked component byte to be present in the release manifest."""
    lock_present = (root / LOCK_NAME).is_file()
    component_present = (root / COMPONENT_REL).is_dir()
    if not lock_present and not component_present:
        return
    if not lock_present or not component_present:
        raise ValueError("bundled component and component-lock.json must be packaged together")

    component = verify_component_lock(skill_root=root)
    if not component.ok:
        code = component.error_code or "COMPONENT_LOCK_INVALID"
        message = component.message or "component verification failed"
        raise ValueError(f"bundled component verification failed: {code}: {message}")
    locked = locked_component_paths(root)
    prefix = COMPONENT_REL.as_posix() + "/"
    distributed_component = {path for path in distributed if path.startswith(prefix)}
    missing = sorted(locked - distributed_component)
    if missing:
        raise ValueError(
            "component lock contains files absent from the distribution manifest: "
            + ", ".join(missing[:10])
        )
    extra = sorted(distributed_component - locked)
    if extra:
        raise ValueError(
            "distribution manifest contains unlocked component files: "
            + ", ".join(extra[:10])
        )


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
    _verify_component_distribution_coverage(root, set(relative_files))
    return relative_files, expected, verification


def _write_archive(
    root: Path,
    archive: Path,
    relative_files: list[str],
    expected: Mapping[str, tuple[int, str]],
    *,
    compress_type: int = zipfile.ZIP_DEFLATED,
    extra_entries: Mapping[str, bytes] | None = None,
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
                    _zip_info(f"{ARCHIVE_ROOT}/{relative}", compress_type), mode="w"
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
            for name in sorted(extra_entries or {}):
                with bundle.open(
                    _zip_info(f"{ARCHIVE_ROOT}/{name}", compress_type), mode="w"
                ) as destination_handle:
                    destination_handle.write((extra_entries or {})[name])
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_archive_contents(
    archive: Path,
    relative_files: list[str],
    expected: Mapping[str, tuple[int, str]],
    extra_entries: Mapping[str, bytes] | None = None,
) -> None:
    """Extracted contents must be byte-identical to the verified manifest."""
    extras = dict(extra_entries or {})
    expected_names = {f"{ARCHIVE_ROOT}/{relative}" for relative in relative_files}
    expected_names.update(f"{ARCHIVE_ROOT}/{name}" for name in extras)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if sorted(names) != sorted(expected_names):
            raise ValueError("archive member list diverges from the verified manifest")
        for relative in relative_files:
            expected_size, expected_digest = expected[relative]
            digest = hashlib.sha256()
            observed = 0
            with bundle.open(f"{ARCHIVE_ROOT}/{relative}") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    observed += len(chunk)
            if observed != expected_size or digest.hexdigest() != expected_digest:
                raise ValueError(f"archive member diverges from the manifest: {relative}")
        for name, payload in extras.items():
            if bundle.read(f"{ARCHIVE_ROOT}/{name}") != payload:
                raise ValueError(f"archive member diverges from the manifest: {name}")


def _runtime_files(relative_files: list[str]) -> list[str]:
    selected = [
        relative
        for relative in relative_files
        if relative in RUNTIME_TOP_FILES or relative.startswith(RUNTIME_DIRS)
    ]
    if not selected:
        raise ValueError("runtime allowlist selected no files")
    return selected


def _runtime_manifest_bytes(
    runtime_files: list[str],
    expected: Mapping[str, tuple[int, str]],
    verification: Mapping[str, Any],
) -> bytes:
    entries = [
        {
            "path": relative,
            "size": expected[relative][0],
            "sha256": expected[relative][1],
        }
        for relative in runtime_files
    ]
    ordered = hashlib.sha256(
        "\n".join(f"{item['sha256']}  {item['path']}" for item in entries).encode("utf-8")
    ).hexdigest()
    manifest = {
        "profile": "runtime",
        "package_version": PACKAGE_VERSION,
        "file_count": len(entries),
        "tree_sha256": ordered,
        "source_distribution_tree_sha256": verification.get("tree_sha256"),
        "files": entries,
    }
    return (json.dumps(manifest, indent=2, sort_keys=False) + "\n").encode("utf-8")


def verify_runtime_closure(
    extracted_root: Path,
    *,
    require_component: bool = True,
    require_gateway: bool = True,
    require_packs: bool = True,
) -> dict[str, Any]:
    """Fail closed unless the runtime tree carries its full routed closure.

    Checks: the locked D Research component verifies byte-exactly outside
    Git, its interop contract ships, every script named by the gateway
    inventory and every routed script exists, every reference doc linked
    from SKILL.md exists, and all domain packs validate. The ``require_*``
    switches mirror the source tree's capabilities so a minimal synthetic
    distribution (as used by packaging tests) is not forced to carry
    surfaces its source never had.
    """
    import importlib.util
    import sys

    extracted_root = extracted_root.resolve()
    problems: list[str] = []

    if require_component:
        component = verify_component_lock(skill_root=extracted_root)
        if not component.ok:
            problems.append(
                f"component lock failed: {component.error_code}: {component.message}"
            )
        interop = extracted_root / COMPONENT_REL / "templates" / "interop-contract.json"
        if not interop.is_file():
            problems.append("component interop contract missing")

    gateway_path = extracted_root / "scripts" / "research_gateway.py"
    if not gateway_path.is_file():
        if require_gateway:
            problems.append("scripts/research_gateway.py missing")
    else:
        specification = importlib.util.spec_from_file_location(
            "aleph_runtime_gateway_check", gateway_path
        )
        if specification is None or specification.loader is None:
            problems.append("cannot load research gateway for closure check")
        else:
            module = importlib.util.module_from_spec(specification)
            inserted = str(extracted_root / "scripts")
            sys.path.insert(0, inserted)
            try:
                specification.loader.exec_module(module)
                component_root = extracted_root / COMPONENT_REL
                for relative in module.SCRIPT_INVENTORY:
                    if not (component_root / relative).is_file():
                        problems.append(f"inventory script missing: {relative}")
                for command, route in module.COMMAND_ROUTES.items():
                    script = route.get("script")
                    if script is not None and not (component_root / script).is_file():
                        problems.append(f"routed dependency missing: {command} -> {script}")
            except Exception as exc:  # noqa: BLE001 - closure must fail closed
                problems.append(f"gateway import failed: {exc}")
            finally:
                if inserted in sys.path:
                    sys.path.remove(inserted)
                sys.modules.pop("aleph_runtime_gateway_check", None)

    skill_md = extracted_root / "SKILL.md"
    if not skill_md.is_file():
        problems.append("SKILL.md missing")
    else:
        import re

        text = skill_md.read_text(encoding="utf-8")
        for match in sorted(set(re.findall(r"`(references/[A-Za-z0-9_.-]+\.md)`", text))):
            if not (extracted_root / match).is_file():
                problems.append(f"referenced doc missing: {match}")

    if require_packs:
        try:
            from aleph.packs import validate_all_packs

            packs = validate_all_packs(extracted_root)
            if not packs.get("ok"):
                problems.append("domain packs failed validation in the runtime tree")
        except Exception as exc:  # noqa: BLE001 - closure must fail closed
            problems.append(f"domain pack validation failed: {exc}")

    return {"ok": not problems, "problems": problems}


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
            relative_output / f"aleph-skill-runtime-v{PACKAGE_VERSION}.zip",
            relative_output / MANIFEST_NAME,
            relative_output / "SHA256SUMS.txt",
        )
        if any(is_distribution_path(path.as_posix()) for path in output_candidates):
            raise ValueError("output directory would modify the attested distribution tree")

    relative_files, expected, verification = _verified_files(root)
    runtime_files = _runtime_files(relative_files)
    runtime_manifest = _runtime_manifest_bytes(runtime_files, expected, verification)
    runtime_extras = {RUNTIME_MANIFEST_NAME: runtime_manifest}

    archive = output_dir / f"aleph-skill-v{PACKAGE_VERSION}.zip"
    runtime_archive = output_dir / f"aleph-skill-runtime-v{PACKAGE_VERSION}.zip"
    manifest_asset = output_dir / MANIFEST_NAME
    checksums = output_dir / "SHA256SUMS.txt"
    output_dir.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix=".aleph-release-", dir=output_dir))
    staged_archive = staging / archive.name
    staged_runtime = staging / runtime_archive.name
    staged_manifest = staging / manifest_asset.name
    staged_checksums = staging / checksums.name
    try:
        # Deterministic DEFLATE: an identical rebuild must be byte-identical
        # before the compressed artifact is adopted; otherwise fall back to
        # the historical STORED layout (still deterministic, never smaller
        # capability). Extracted contents are verified against the manifest
        # either way.
        compression = "deflate"
        rebuild = staging / f".determinism-{archive.name}"
        _write_archive(root, staged_archive, relative_files, expected)
        _write_archive(root, rebuild, relative_files, expected)
        if _sha256(staged_archive) != _sha256(rebuild):
            compression = "stored"
            _write_archive(
                root, staged_archive, relative_files, expected, compress_type=zipfile.ZIP_STORED
            )
            _write_archive(
                root, rebuild, relative_files, expected, compress_type=zipfile.ZIP_STORED
            )
            if _sha256(staged_archive) != _sha256(rebuild):
                raise ValueError("release archive build is not reproducible")
        rebuild.unlink()
        _verify_archive_contents(staged_archive, relative_files, expected)

        runtime_compress = (
            zipfile.ZIP_DEFLATED if compression == "deflate" else zipfile.ZIP_STORED
        )
        runtime_rebuild = staging / f".determinism-{runtime_archive.name}"
        _write_archive(
            root,
            staged_runtime,
            runtime_files,
            expected,
            compress_type=runtime_compress,
            extra_entries=runtime_extras,
        )
        _write_archive(
            root,
            runtime_rebuild,
            runtime_files,
            expected,
            compress_type=runtime_compress,
            extra_entries=runtime_extras,
        )
        if _sha256(staged_runtime) != _sha256(runtime_rebuild):
            raise ValueError("runtime archive build is not reproducible")
        runtime_rebuild.unlink()
        _verify_archive_contents(staged_runtime, runtime_files, expected, runtime_extras)

        # The runtime profile must carry its complete routed closure. The
        # required surfaces mirror what the verified source actually ships.
        closure_root = staging / "runtime-closure"
        with zipfile.ZipFile(staged_runtime) as bundle:
            bundle.extractall(closure_root)
        closure = verify_runtime_closure(
            closure_root / ARCHIVE_ROOT,
            require_component=(root / LOCK_NAME).is_file() or (root / COMPONENT_REL).is_dir(),
            require_gateway=(root / "scripts" / "research_gateway.py").is_file(),
            require_packs=(root / "packs").is_dir(),
        )
        if not closure.get("ok"):
            raise ValueError(
                "runtime closure verification failed: " + "; ".join(closure["problems"])
            )
        shutil.rmtree(closure_root, ignore_errors=True)

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
        runtime_digest = _sha256(staged_runtime)
        manifest_digest = _sha256(staged_manifest)
        checksum_text = (
            f"{archive_digest}  {archive.name}\n"
            f"{runtime_digest}  {runtime_archive.name}\n"
            f"{manifest_digest}  {manifest_asset.name}\n"
        )
        staged_checksums.write_text(checksum_text, encoding="utf-8", newline="\n")
        os.replace(staged_archive, archive)
        os.replace(staged_runtime, runtime_archive)
        os.replace(staged_manifest, manifest_asset)
        os.replace(staged_checksums, checksums)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "status": "pass",
        "package_version": PACKAGE_VERSION,
        "archive": str(archive),
        "archive_sha256": archive_digest,
        "compression": compression,
        "runtime_archive": str(runtime_archive),
        "runtime_archive_sha256": runtime_digest,
        "runtime_file_count": len(runtime_files),
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
