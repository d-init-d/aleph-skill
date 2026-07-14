"""Transactional installer backed by a deterministic distribution manifest."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from . import PACKAGE_VERSION
from .io import canonical_json_bytes, sha256_file, write_json_atomic
from .issues import Issue, issue
from .paths import (
    assert_install_paths_safe,
    is_distribution_path,
    path_contains_link_or_reparse,
)

INSTALL_MODES = frozenset({"dry-run", "copy", "symlink"})
MANIFEST_NAME = "distribution-manifest.json"
PRUNED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
SECRET_NAMES = frozenset({".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"})
SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx"})
SECRET_CONTENT = re.compile(
    rb"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"(?:^|\n)[ \t]*(?:api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,})",
    re.IGNORECASE,
)
SECRET_SCAN_CHUNK_BYTES = 1024 * 1024
SECRET_SCAN_OVERLAP_BYTES = 512
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
MANIFEST_MAX_BYTES = 16 * 1024 * 1024


def _is_pruned_directory_name(name: str) -> bool:
    return name in PRUNED_DIRS or name.endswith(".egg-info")


def _is_link_or_reparse(path: Path) -> bool:
    """Detect POSIX links and Windows reparse points without following them."""
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or os.path.islink(path) or bool(
        attributes & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _path_contains_link_or_reparse(path: Path) -> bool:
    """Detect a linked/reparse component in an existing path prefix."""
    return path_contains_link_or_reparse(path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tree_digest(entries: list[dict[str, Any]]) -> str:
    rows = [f"{entry['path']}\0{entry['sha256']}\0{entry['size']}\n" for entry in sorted(entries, key=lambda item: item["path"])]
    return _sha256_bytes("".join(rows).encode("utf-8"))


def collect_distribution_files(source: Path) -> list[Path]:
    """Collect allowlisted regular files; never follow directory symlinks."""
    files: list[Path] = []
    source = source.resolve()
    for root, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not _is_pruned_directory_name(name) and not _is_link_or_reparse(Path(root) / name)
        ]
        root_path = Path(root)
        rel_root = root_path.relative_to(source)
        for name in names:
            candidate = root_path / name
            if _is_link_or_reparse(candidate) or not candidate.is_file():
                continue
            relative = (rel_root / name).as_posix() if str(rel_root) != "." else name
            if relative == MANIFEST_NAME or is_distribution_path(relative):
                files.append(candidate)
    return sorted(files, key=lambda path: path.relative_to(source).as_posix())


def _contains_secret_content(path: Path) -> bool:
    """Stream the complete file so size cannot bypass the secret gate."""
    tail = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(SECRET_SCAN_CHUNK_BYTES)
            if not chunk:
                return False
            window = tail + chunk
            if SECRET_CONTENT.search(window):
                return True
            tail = window[-SECRET_SCAN_OVERLAP_BYTES:]


def scan_secret_like_files(source: Path) -> list[dict[str, str]]:
    """Find secret-like files/content without following symlinks or scanning .git."""
    findings: list[dict[str, str]] = []
    source = source.resolve()
    for root, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not _is_pruned_directory_name(name) and not _is_link_or_reparse(Path(root) / name)
        ]
        root_path = Path(root)
        for name in names:
            path = root_path / name
            relative = path.relative_to(source).as_posix()
            lowered = name.lower()
            if name.startswith(".") and relative not in {".gitattributes", ".gitignore"}:
                findings.append({"path": relative, "reason": "hidden file is not distributable"})
                continue
            if lowered in SECRET_NAMES or lowered.startswith(".env.") or path.suffix.lower() in SECRET_SUFFIXES:
                findings.append({"path": relative, "reason": "secret-like filename"})
                continue
            if _is_link_or_reparse(path) or not path.is_file():
                continue
            try:
                if _contains_secret_content(path):
                    findings.append({"path": relative, "reason": "secret-like content"})
            except OSError:
                findings.append({"path": relative, "reason": "unreadable during security scan"})
    return findings


def symlink_exposure_issues(source: Path, manifest_files: list[str]) -> list[Issue]:
    """Refuse symlink installs unless the source contains only attested files."""
    allowed = set(manifest_files)
    exposed: list[str] = []
    for root, dirs, names in os.walk(source, followlinks=False):
        root_path = Path(root)
        retained_dirs: list[str] = []
        for name in dirs:
            path = root_path / name
            relative = path.relative_to(source).as_posix()
            if _is_link_or_reparse(path) or _is_pruned_directory_name(name):
                exposed.append(relative)
            else:
                retained_dirs.append(name)
        dirs[:] = retained_dirs
        for name in names:
            path = root_path / name
            relative = path.relative_to(source).as_posix()
            if _is_link_or_reparse(path) or relative not in allowed:
                exposed.append(relative)
        if len(exposed) >= 20:
            break
    if not exposed:
        return []
    return [
        issue(
            "INSTALL_NOT_ALLOWLISTED",
            pointer=exposed[0],
            message=(
                "symlink install would expose unattested source entries; "
                f"first={exposed[0]}, observed={len(exposed)}"
            ),
        )
    ]


def source_symlink_issues(source: Path) -> list[Issue]:
    problems: list[Issue] = []
    for root, dirs, names in os.walk(source, followlinks=False):
        retained: list[str] = []
        for name in dirs:
            path = Path(root) / name
            if _is_link_or_reparse(path):
                relative = path.relative_to(source).as_posix()
                problems.append(
                    issue(
                        "INSTALL_NOT_ALLOWLISTED",
                        pointer=relative,
                        message="distribution symlinks and reparse points are refused",
                    )
                )
            elif not _is_pruned_directory_name(name):
                retained.append(name)
        dirs[:] = retained
        for name in names:
            path = Path(root) / name
            if _is_link_or_reparse(path):
                relative = path.relative_to(source).as_posix()
                problems.append(
                    issue(
                        "INSTALL_NOT_ALLOWLISTED",
                        pointer=relative,
                        message="distribution symlinks and reparse points are refused",
                    )
                )
    return problems


def build_distribution_manifest(source: Path) -> dict[str, Any]:
    source = source.resolve()
    entries: list[dict[str, Any]] = []
    for path in collect_distribution_files(source):
        relative = path.relative_to(source).as_posix()
        if relative == MANIFEST_NAME:
            continue
        entries.append({"path": relative, "sha256": sha256_file(path), "size": path.stat().st_size})
    return {
        "schema_version": "2.0.0",
        "package_version": PACKAGE_VERSION,
        "algorithm": "sha256",
        "self_excluded": MANIFEST_NAME,
        "file_count": len(entries),
        "tree_sha256": _tree_digest(entries),
        "files": entries,
    }


def verify_distribution_manifest(source: Path, *, require: bool = True) -> dict[str, Any]:
    source = source.resolve()
    path = source / MANIFEST_NAME
    if not path.is_file():
        return {
            "ok": not require,
            "status": "absent",
            "issues": [] if not require else [issue("INSTALL_NOT_ALLOWLISTED", message="distribution manifest missing").to_dict()],
        }
    try:
        with path.open("rb") as handle:
            manifest_bytes = handle.read(MANIFEST_MAX_BYTES + 1)
        if len(manifest_bytes) > MANIFEST_MAX_BYTES:
            raise ValueError(
                f"distribution manifest exceeds {MANIFEST_MAX_BYTES} bytes"
            )
        stored = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "invalid", "issues": [issue("INSTALL_NOT_ALLOWLISTED", message=str(exc)).to_dict()]}
    except ValueError as exc:
        return {"ok": False, "status": "invalid", "issues": [issue("RESOURCE_LIMIT", message=str(exc)).to_dict()]}
    expected = build_distribution_manifest(source)
    problems: list[Issue] = source_symlink_issues(source)
    if not isinstance(stored, dict):
        problems.append(
            issue(
                "INSTALL_NOT_ALLOWLISTED",
                artifact=MANIFEST_NAME,
                message="distribution manifest root must be an object",
                actual=type(stored).__name__,
            )
        )
        stored = {}
    if stored != expected:
        stored_files = stored.get("files", []) if isinstance(stored, dict) else []
        stored_by_path: dict[str, dict[str, Any]] = {}
        for entry in stored_files if isinstance(stored_files, list) else []:
            if not isinstance(entry, dict):
                continue
            relative = entry.get("path")
            if isinstance(relative, str):
                stored_by_path[relative] = entry
        expected_by_path = {entry["path"]: entry for entry in expected["files"]}
        for relative in sorted(set(stored_by_path) | set(expected_by_path)):
            if stored_by_path.get(relative) != expected_by_path.get(relative):
                problems.append(issue("STALE_ARTIFACT", pointer=relative, message="distribution manifest entry mismatch"))
        if stored.get("tree_sha256") != expected.get("tree_sha256"):
            problems.append(issue("STALE_ARTIFACT", pointer="tree_sha256", expected=expected["tree_sha256"], actual=stored.get("tree_sha256")))
    return {
        "ok": not problems,
        "status": "verified" if not problems else "stale",
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "tree_sha256": expected["tree_sha256"],
        "file_count": expected["file_count"],
        "files": [entry["path"] for entry in expected["files"]] + [MANIFEST_NAME],
        "file_entries": expected["files"],
        "issues": [item.to_dict() for item in problems],
    }


def plan_install(source: Path, destination: Path, mode: str) -> dict[str, Any]:
    if mode not in INSTALL_MODES:
        raise ValueError(f"mode must be one of {sorted(INSTALL_MODES)}")
    source = source.resolve()
    destination = Path(os.path.abspath(destination))
    problems = assert_install_paths_safe(source, destination)
    manifest = verify_distribution_manifest(source, require=False)
    if manifest.get("status") not in {"absent", "verified"}:
        problems.extend(
            Issue(**{key: value for key, value in raw.items() if key in {"code", "severity", "artifact", "pointer", "message", "expected", "actual"}})
            for raw in manifest.get("issues", [])
        )
    if mode != "dry-run" and manifest.get("status") != "verified":
        problems.append(
            issue(
                "INSTALL_NOT_ALLOWLISTED",
                message="copy and symlink installs require a verified distribution manifest",
            )
        )
    files = manifest.get("files", []) if manifest.get("status") == "verified" else [
        path.relative_to(source).as_posix() for path in collect_distribution_files(source)
    ]
    secret_findings = scan_secret_like_files(source)
    copied_secrets = [finding for finding in secret_findings if finding["path"] in set(files)]
    for finding in copied_secrets:
        problems.append(issue("INSTALL_NOT_ALLOWLISTED", pointer=finding["path"], message=finding["reason"]))
    if mode == "symlink":
        if manifest.get("status") != "verified":
            problems.append(
                issue("INSTALL_NOT_ALLOWLISTED", message="symlink install requires a verified distribution manifest")
            )
        else:
            problems.extend(symlink_exposure_issues(source, files))
        for finding in secret_findings:
            if finding not in copied_secrets:
                problems.append(
                    issue("INSTALL_NOT_ALLOWLISTED", pointer=finding["path"], message=finding["reason"])
                )
    return {
        "mode": mode,
        "source": str(source),
        "destination": str(destination),
        "file_count": len(files),
        "files": files,
        "manifest": {key: manifest.get(key) for key in ("status", "manifest_sha256", "tree_sha256", "file_count")},
        "assurance_cap": (
            "limited"
            if mode == "symlink" or manifest.get("status") != "verified"
            else "verified"
        ),
        "excluded_security_findings": secret_findings,
        "issues": [item.to_dict() for item in problems],
        "ok": not problems,
    }


def _destination_digest(destination: Path, files: list[str]) -> str:
    entries = []
    for relative in files:
        if relative == MANIFEST_NAME:
            continue
        path = destination / relative
        entries.append({"path": relative, "sha256": sha256_file(path), "size": path.stat().st_size})
    return _tree_digest(entries)


def _same_or_descendant(path: Path, root: Path) -> bool:
    path_text = os.path.normcase(os.path.abspath(path))
    root_text = os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([path_text, root_text]) == root_text
    except ValueError:
        return False


def receipt_path_issues(
    receipt_path: Path | None,
    *,
    source: Path,
    destination: Path,
    destination_is_directory: bool,
) -> list[Issue]:
    """Require a receipt to be disjoint from attested and installed content."""
    if receipt_path is None:
        return []
    receipt = Path(os.path.abspath(receipt_path))
    problems: list[Issue] = []
    if _path_contains_link_or_reparse(receipt.parent):
        problems.append(
            issue(
                "INSTALL_SOURCE_DEST",
                pointer=str(receipt),
                message="receipt parent contains a symlink or reparse point",
            )
        )
    if _same_or_descendant(receipt, source):
        problems.append(
            issue(
                "INSTALL_SOURCE_DEST",
                pointer=str(receipt),
                message="receipt must be outside the attested source tree",
            )
        )
    if (
        destination_is_directory
        and _same_or_descendant(receipt, destination)
        or not destination_is_directory
        and os.path.normcase(os.path.abspath(receipt))
        == os.path.normcase(os.path.abspath(destination))
    ):
        problems.append(
            issue(
                "INSTALL_SOURCE_DEST",
                pointer=str(receipt),
                message="receipt must not overlap installed content",
            )
        )
    return problems


def _remove_committed_path(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if _is_link_or_reparse(path):
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _rollback_committed_path(destination: Path, backup: Path | None) -> str:
    if _path_contains_link_or_reparse(destination.parent):
        return "rollback-failed: destination parent contains a symlink or reparse point"
    try:
        _remove_committed_path(destination)
        if backup is not None and backup.exists():
            os.replace(backup, destination)
            return "restored"
        return "removed-new-install"
    except OSError as exc:
        return f"rollback-failed: {exc}"


def rollback_install_result(result: dict[str, Any], destination: Path) -> str:
    """Safely roll back a committed install using its retained backup."""
    backup_raw = result.get("backup")
    backup = Path(str(backup_raw)) if backup_raw else None
    return _rollback_committed_path(Path(os.path.abspath(destination)), backup)


def discard_install_backup(result: dict[str, Any]) -> str:
    """Discard a post-commit rollback backup after the enclosing receipt is durable."""
    backup_raw = result.get("backup")
    if not backup_raw:
        result["rollback_status"] = "not-needed"
        return "not-needed"
    backup = Path(str(backup_raw))
    if _path_contains_link_or_reparse(backup.parent):
        status = "backup-discard-failed: backup parent contains a symlink or reparse point"
        result["rollback_status"] = status
        return status
    try:
        if os.path.lexists(backup):
            _remove_committed_path(backup)
        result["backup"] = None
        result["rollback_status"] = "backup-discarded"
        return "backup-discarded"
    except OSError as exc:
        status = f"backup-discard-failed: {exc}"
        result["rollback_status"] = status
        return status


def install(
    source: Path,
    destination: Path,
    *,
    mode: str = "copy",
    force: bool = False,
    receipt_path: Path | None = None,
    retain_backup: bool = False,
) -> dict[str, Any]:
    source = source.resolve()
    destination = Path(os.path.abspath(destination))
    plan = plan_install(source, destination, mode)
    receipt_issues = receipt_path_issues(
        receipt_path,
        source=source,
        destination=destination,
        destination_is_directory=True,
    )
    if receipt_issues:
        plan["ok"] = False
        plan["status"] = "refused"
        plan["issues"].extend(value.to_dict() for value in receipt_issues)
        return plan
    if not plan["ok"]:
        plan["status"] = "refused"
        _write_receipt(plan, receipt_path, source, destination)
        return plan
    if mode == "dry-run":
        plan["status"] = "dry-run"
        _write_receipt(plan, receipt_path, source, destination)
        return plan

    if mode == "symlink":
        if plan["manifest"]["status"] != "verified":
            plan["status"] = "refused"
            plan["issues"].append(issue("INSTALL_NOT_ALLOWLISTED", message="symlink install requires a verified distribution manifest").to_dict())
            _write_receipt(plan, receipt_path, source, destination)
            return plan
        commit_path_issues = assert_install_paths_safe(source, destination)
        if commit_path_issues:
            plan["ok"] = False
            plan["status"] = "refused"
            plan["issues"].extend(value.to_dict() for value in commit_path_issues)
            _write_receipt(plan, receipt_path, source, destination)
            return plan
        destination.parent.mkdir(parents=True, exist_ok=True)
        commit_path_issues = assert_install_paths_safe(source, destination)
        if commit_path_issues:
            plan["ok"] = False
            plan["status"] = "refused"
            plan["issues"].extend(value.to_dict() for value in commit_path_issues)
            _write_receipt(plan, receipt_path, source, destination)
            return plan
        symlink_backup: Path | None = None
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                plan["status"] = "refused"
                plan["issues"].append(issue("INSTALL_SOURCE_DEST", message="refusing to remove a real directory for symlink install").to_dict())
                _write_receipt(plan, receipt_path, source, destination)
                return plan
            if not force:
                plan["status"] = "refused"
                plan["issues"].append(issue("INSTALL_SOURCE_DEST", message="destination exists; use force").to_dict())
                _write_receipt(plan, receipt_path, source, destination)
                return plan
            symlink_backup = destination.with_name(f".{destination.name}.aleph-backup-{int(time.time_ns())}")
            os.replace(destination, symlink_backup)
        try:
            if assert_install_paths_safe(source, destination):
                raise OSError("destination path changed after install preflight")
            os.symlink(source, destination, target_is_directory=True)
        except OSError as exc:
            if symlink_backup is not None and symlink_backup.exists() and not os.path.lexists(destination):
                os.replace(symlink_backup, destination)
            plan["status"] = "failed"
            plan["rollback_status"] = "restored" if symlink_backup is not None else "not-needed"
            plan["issues"].append(issue("INSTALL_SOURCE_DEST", message=f"symlink failed: {exc}").to_dict())
            _write_receipt(plan, receipt_path, source, destination)
            return plan
        plan["status"] = "symlinked"
        plan["assurance_cap"] = "limited"
        plan["backup"] = str(symlink_backup) if symlink_backup is not None else None
        if not _write_receipt(plan, receipt_path, source, destination):
            plan["rollback_status"] = _rollback_committed_path(destination, symlink_backup)
        elif not retain_backup:
            discard_install_backup(plan)
        return plan

    commit_path_issues = assert_install_paths_safe(source, destination)
    if commit_path_issues:
        plan["ok"] = False
        plan["status"] = "refused"
        plan["issues"].extend(value.to_dict() for value in commit_path_issues)
        _write_receipt(plan, receipt_path, source, destination)
        return plan
    destination.parent.mkdir(parents=True, exist_ok=True)
    commit_path_issues = assert_install_paths_safe(source, destination)
    if commit_path_issues:
        plan["ok"] = False
        plan["status"] = "refused"
        plan["issues"].extend(value.to_dict() for value in commit_path_issues)
        _write_receipt(plan, receipt_path, source, destination)
        return plan
    staging = Path(tempfile.mkdtemp(prefix=".aleph-install-", dir=str(destination.parent)))
    if assert_install_paths_safe(source, destination) or _path_contains_link_or_reparse(
        staging.parent
    ):
        shutil.rmtree(staging, ignore_errors=True)
        plan["ok"] = False
        plan["status"] = "refused"
        plan["issues"].append(
            issue(
                "INSTALL_SOURCE_DEST",
                message="destination path changed while creating install staging",
            ).to_dict()
        )
        _write_receipt(plan, receipt_path, source, destination)
        return plan
    backup: Path | None = None
    committed = False
    try:
        for relative in plan["files"]:
            if assert_install_paths_safe(source, destination) or _path_contains_link_or_reparse(
                staging.parent
            ):
                raise RuntimeError("destination path changed while staging install files")
            source_file = source / relative
            destination_file = staging / relative
            if _is_link_or_reparse(source_file) or not source_file.is_file():
                raise RuntimeError(f"manifest source is not a regular file: {relative}")
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
        expected_digest = plan["manifest"].get("tree_sha256")
        staged_digest = _destination_digest(staging, plan["files"])
        if expected_digest and staged_digest != expected_digest:
            raise RuntimeError("staged tree digest does not match distribution manifest")
        expected_manifest_digest = plan["manifest"].get("manifest_sha256")
        staged_manifest = staging / MANIFEST_NAME
        if (
            expected_manifest_digest
            and (
                not staged_manifest.is_file()
                or sha256_file(staged_manifest) != expected_manifest_digest
            )
        ):
            raise RuntimeError("staged distribution manifest digest mismatch")
        commit_path_issues = assert_install_paths_safe(source, destination)
        if commit_path_issues or _path_contains_link_or_reparse(staging.parent):
            raise RuntimeError("destination path changed after install preflight")
        if destination.exists() or destination.is_symlink():
            if not force:
                plan["status"] = "refused"
                plan["issues"].append(issue("INSTALL_SOURCE_DEST", message="destination exists").to_dict())
                shutil.rmtree(staging, ignore_errors=True)
                _write_receipt(plan, receipt_path, source, destination)
                return plan
            backup = destination.with_name(f".{destination.name}.aleph-backup-{int(time.time_ns())}")
            os.replace(destination, backup)
        try:
            if assert_install_paths_safe(source, destination) or _path_contains_link_or_reparse(
                staging.parent
            ):
                raise RuntimeError("destination path changed before install commit")
            os.replace(staging, destination)
            committed = True
        except Exception:
            if backup is not None and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if assert_install_paths_safe(source, destination):
            raise RuntimeError("destination path changed after install commit")
        destination_digest = _destination_digest(destination, plan["files"])
        if expected_digest and destination_digest != expected_digest:
            raise RuntimeError("installed tree digest mismatch; rollback completed")
        installed_manifest = destination / MANIFEST_NAME
        if (
            expected_manifest_digest
            and (
                not installed_manifest.is_file()
                or sha256_file(installed_manifest) != expected_manifest_digest
            )
        ):
            raise RuntimeError("installed distribution manifest digest mismatch")
        plan.update(
            {
                "status": "copied",
                "destination_tree_sha256": destination_digest,
                "backup": str(backup) if backup is not None else None,
                "rollback_status": "backup-retained" if backup is not None else "not-needed",
            }
        )
        if not _write_receipt(plan, receipt_path, source, destination):
            plan["rollback_status"] = _rollback_committed_path(destination, backup)
        elif not retain_backup:
            discard_install_backup(plan)
        return plan
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if committed:
            rollback_status = _rollback_committed_path(destination, backup)
        elif backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
            rollback_status = "restored"
        else:
            rollback_status = "not-needed"
        plan["status"] = "failed"
        plan["rollback_status"] = rollback_status
        plan["issues"].append(issue("INSTALL_SOURCE_DEST", message=str(exc)).to_dict())
        _write_receipt(plan, receipt_path, source, destination)
        return plan


def _finish_adapter_result(result: dict[str, Any], receipt_path: Path | None) -> dict[str, Any]:
    if receipt_path is None:
        return result
    if _path_contains_link_or_reparse(receipt_path.parent):
        result.update(
            {
                "ok": False,
                "status": "failed",
                "issues": [
                    *list(result.get("issues") or []),
                    issue(
                        "INSTALL_SOURCE_DEST",
                        message="receipt parent changed to a symlink or reparse point",
                    ).to_dict(),
                ],
            }
        )
        return result
    receipt = {"schema_version": "2.0.0", **result}
    receipt["receipt_hash"] = _sha256_bytes(canonical_json_bytes(receipt))
    try:
        write_json_atomic(receipt_path, receipt)
    except OSError as exc:
        result.update(
            {
                "ok": False,
                "status": "failed",
                "issues": [
                    *list(result.get("issues") or []),
                    issue("INSTALL_SOURCE_DEST", message=f"receipt write failed: {exc}").to_dict(),
                ],
            }
        )
        return result
    result["receipt"] = str(receipt_path)
    result["receipt_hash"] = receipt["receipt_hash"]
    return result


def install_adapter_file(
    source_file: Path,
    destination_file: Path,
    *,
    mode: str = "dry-run",
    force: bool = False,
    receipt_path: Path | None = None,
    source_root: Path | None = None,
    retain_backup: bool = False,
) -> dict[str, Any]:
    """Install one attested generated rule/profile as a file.

    The file must be a regular, non-symlink entry in a verified Aleph
    distribution manifest. This keeps thin host adapters under the same
    supply-chain and secret gates as full-directory installs.
    """
    declared_source = source_file.absolute()
    destination_file = Path(os.path.abspath(destination_file))
    if source_root is None:
        for candidate in (declared_source.parent, *declared_source.parents):
            if (candidate / MANIFEST_NAME).is_file():
                source_root = candidate
                break
    receipt_source = source_root.resolve() if source_root is not None else declared_source.parent
    receipt_issues = receipt_path_issues(
        receipt_path,
        source=receipt_source,
        destination=destination_file,
        destination_is_directory=False,
    )
    if receipt_issues:
        return {
            "ok": False,
            "status": "refused",
            "issues": [value.to_dict() for value in receipt_issues],
        }
    if mode not in {"dry-run", "copy"}:
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [
                    issue(
                        "INSTALL_SOURCE_DEST",
                        message="instruction/profile targets support dry-run or copy only",
                    ).to_dict()
                ],
            },
            receipt_path,
        )
    if source_root is None:
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [
                    issue(
                        "INSTALL_NOT_ALLOWLISTED",
                        message="adapter install requires a verified distribution root",
                    ).to_dict()
                ],
            },
            receipt_path,
        )
    source_root = source_root.resolve()
    path_problems = assert_install_paths_safe(source_root, destination_file)
    if path_problems:
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [value.to_dict() for value in path_problems],
            },
            receipt_path,
        )
    try:
        relative = declared_source.relative_to(source_root).as_posix()
    except ValueError:
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [
                    issue(
                        "INSTALL_NOT_ALLOWLISTED",
                        pointer=str(declared_source),
                        message="adapter source is outside the declared distribution root",
                    ).to_dict()
                ],
            },
            receipt_path,
        )
    manifest = verify_distribution_manifest(source_root, require=True)
    manifest_files = set(manifest.get("files") or [])
    if manifest.get("status") != "verified" or relative not in manifest_files:
        issues = list(manifest.get("issues") or [])
        if relative not in manifest_files:
            issues.append(
                issue(
                    "INSTALL_NOT_ALLOWLISTED",
                    pointer=relative,
                    message="adapter source is not attested by the distribution manifest",
                ).to_dict()
            )
        return _finish_adapter_result(
            {"ok": False, "status": "refused", "issues": issues}, receipt_path
        )
    if _is_link_or_reparse(declared_source) or not declared_source.is_file():
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [
                    issue(
                        "INSTALL_NOT_ALLOWLISTED",
                        pointer=relative,
                        message="adapter source must be a regular non-symlink file",
                    ).to_dict()
                ],
            },
            receipt_path,
        )
    secret_findings = [
        finding for finding in scan_secret_like_files(source_root) if finding["path"] == relative
    ]
    if secret_findings:
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [
                    issue(
                        "INSTALL_NOT_ALLOWLISTED",
                        pointer=finding["path"],
                        message=finding["reason"],
                    ).to_dict()
                    for finding in secret_findings
                ],
            },
            receipt_path,
        )
    source_file = declared_source.resolve(strict=True)
    source_digest = sha256_file(source_file)
    manifest_entry = next(
        (
            entry
            for entry in manifest.get("file_entries") or []
            if isinstance(entry, dict) and entry.get("path") == relative
        ),
        None,
    )
    if (
        manifest_entry is None
        or manifest_entry.get("sha256") != source_digest
        or manifest_entry.get("size") != source_file.stat().st_size
    ):
        return _finish_adapter_result(
            {
                "ok": False,
                "status": "refused",
                "issues": [
                    issue(
                        "STALE_ARTIFACT",
                        pointer=relative,
                        message="adapter bytes differ from the attested manifest entry",
                    ).to_dict()
                ],
            },
            receipt_path,
        )
    result: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "source": str(source_file),
        "destination": str(destination_file),
        "file_count": 1,
        "files": [destination_file.name],
        "source_digest": source_digest,
        "manifest": {
            key: manifest.get(key)
            for key in ("status", "manifest_sha256", "tree_sha256", "file_count")
        },
    }
    destination_occupied = os.path.lexists(destination_file)
    if destination_occupied and destination_file.is_dir():
        result.update({"ok": False, "status": "refused", "issues": [issue("INSTALL_SOURCE_DEST", message="target must be a file, not a directory").to_dict()]})
        return _finish_adapter_result(result, receipt_path)
    if mode == "dry-run":
        result["status"] = "dry-run"
        return _finish_adapter_result(result, receipt_path)
    if destination_occupied and not force:
        result.update({"ok": False, "status": "refused", "issues": [issue("INSTALL_SOURCE_DEST", message="destination exists; use force").to_dict()]})
        return _finish_adapter_result(result, receipt_path)
    commit_path_problems = assert_install_paths_safe(source_root, destination_file)
    if commit_path_problems:
        result.update(
            {
                "ok": False,
                "status": "refused",
                "issues": [value.to_dict() for value in commit_path_problems],
            }
        )
        return _finish_adapter_result(result, receipt_path)
    destination_file.parent.mkdir(parents=True, exist_ok=True)
    commit_path_problems = assert_install_paths_safe(source_root, destination_file)
    if commit_path_problems:
        result.update(
            {
                "ok": False,
                "status": "refused",
                "issues": [value.to_dict() for value in commit_path_problems],
            }
        )
        return _finish_adapter_result(result, receipt_path)
    backup = None
    temporary: Path | None = None
    committed = False
    try:
        with source_file.open("rb") as source_handle, tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination_file.name}.aleph-tmp-",
            dir=destination_file.parent,
            delete=False,
        ) as temporary_handle:
            temporary = Path(temporary_handle.name)
            if assert_install_paths_safe(source_root, destination_file) or _path_contains_link_or_reparse(
                temporary.parent
            ):
                raise RuntimeError("adapter destination path changed while creating staging")
            shutil.copyfileobj(source_handle, temporary_handle, length=1024 * 1024)
            temporary_handle.flush()
            os.fsync(temporary_handle.fileno())
        if sha256_file(temporary) != source_digest:
            raise RuntimeError("adapter source changed after manifest verification")
        if assert_install_paths_safe(source_root, destination_file):
            raise RuntimeError("adapter destination path changed after preflight")
        if os.path.lexists(destination_file):
            backup = destination_file.with_name(f".{destination_file.name}.aleph-backup-{int(time.time_ns())}")
            os.replace(destination_file, backup)
        if assert_install_paths_safe(source_root, destination_file) or _path_contains_link_or_reparse(
            temporary.parent
        ):
            raise RuntimeError("adapter destination path changed before commit")
        os.replace(temporary, destination_file)
        committed = True
        if assert_install_paths_safe(source_root, destination_file):
            raise RuntimeError("adapter destination path changed after commit")
        if sha256_file(destination_file) != result["source_digest"]:
            raise RuntimeError("installed file digest mismatch")
    except Exception as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if committed:
            rollback_status = _rollback_committed_path(destination_file, backup)
        elif backup is not None and backup.exists() and not os.path.lexists(destination_file):
            os.replace(backup, destination_file)
            rollback_status = "restored"
        else:
            rollback_status = "not-needed"
        result.update({"ok": False, "status": "failed", "issues": [issue("INSTALL_SOURCE_DEST", message=str(exc)).to_dict()]})
        result["rollback_status"] = rollback_status
        return _finish_adapter_result(result, receipt_path)
    result.update({"status": "copied", "destination_digest": sha256_file(destination_file), "backup": str(backup) if backup else None})
    finished = _finish_adapter_result(result, receipt_path)
    if finished.get("status") == "failed":
        finished["rollback_status"] = _rollback_committed_path(destination_file, backup)
    elif not retain_backup:
        discard_install_backup(finished)
    return finished


def _write_receipt(
    plan: dict[str, Any],
    receipt_path: Path | None,
    source: Path,
    destination: Path,
) -> bool:
    if receipt_path is None:
        return True
    current_receipt_issues = receipt_path_issues(
        receipt_path,
        source=source,
        destination=destination,
        destination_is_directory=True,
    )
    if current_receipt_issues:
        plan["ok"] = False
        plan["status"] = "failed"
        plan["issues"].extend(value.to_dict() for value in current_receipt_issues)
        return False
    receipt = {
        "schema_version": "2.0.0",
        "source": str(source),
        "destination": str(destination),
        "mode": plan.get("mode"),
        "status": plan.get("status"),
        "file_count": plan.get("file_count"),
        "files": plan.get("files"),
        "manifest": plan.get("manifest"),
        "destination_tree_sha256": plan.get("destination_tree_sha256"),
        "rollback_status": plan.get("rollback_status"),
        "ok": plan.get("ok"),
        "issues": plan.get("issues", []),
        "backup": plan.get("backup"),
    }
    receipt["receipt_hash"] = _sha256_bytes(canonical_json_bytes(receipt))
    try:
        write_json_atomic(receipt_path, receipt)
    except OSError as exc:
        plan["ok"] = False
        plan["status"] = "failed"
        plan["issues"].append(
            issue("INSTALL_SOURCE_DEST", message=f"receipt write failed: {exc}").to_dict()
        )
        return False
    plan["receipt"] = str(receipt_path)
    plan["receipt_hash"] = receipt["receipt_hash"]
    return True
