"""Recompute or verify the byte-exact lock for a bundled Aleph component."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from aleph.component_registry import (
    COMPONENT_ID,
    LOCK_NAME,
    ComponentError,
    build_component_lock,
)
from aleph.io import write_json_atomic

_TEXT_SUFFIXES = frozenset(
    {
        ".bib",
        ".css",
        ".csv",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".toml",
        ".ts",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_TEXT_NAMES = frozenset({".npmignore", "LICENSE", "NOTICE", "NOTICE.txt"})


def _is_text(path: Path) -> bool:
    return path.name in _TEXT_NAMES or path.suffix.lower() in _TEXT_SUFFIXES


def _is_link_or_reparse(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or os.path.islink(path) or bool(attributes & 0x400)


def normalize_snapshot(component_root: Path) -> list[str]:
    """Normalize CRLF to LF in known text resources, returning changed paths."""
    if not component_root.is_dir() or _is_link_or_reparse(component_root):
        raise ValueError(f"component root must be a regular directory: {component_root}")
    changed: list[str] = []
    for raw_root, directories, names in os.walk(component_root, topdown=True, followlinks=False):
        root = Path(raw_root)
        directories.sort()
        names.sort()
        for directory in directories:
            path = root / directory
            if _is_link_or_reparse(path):
                raise ValueError(f"linked directory refused during normalization: {path}")
        for name in names:
            path = root / name
            if _is_link_or_reparse(path) or not path.is_file():
                raise ValueError(f"non-regular file refused during normalization: {path}")
            if not _is_text(path):
                continue
            raw = path.read_bytes()
            normalized = raw.replace(b"\r\n", b"\n")
            if normalized != raw:
                path.write_bytes(normalized)
                changed.append(path.relative_to(component_root).as_posix())
    return changed


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def verify_upstream_snapshot(
    root: Path,
    upstream_repo: Path,
    rebuilt: dict[str, Any],
    *,
    component_id: str,
) -> dict[str, Any]:
    """Verify tag, tree, archive, recipe, and bytes against a local upstream clone."""

    git = shutil.which("git")
    if git is None:
        raise ValueError("git is required for --upstream-repo verification")
    repo = upstream_repo.resolve(strict=True)
    entry = rebuilt["components"][component_id]

    def git_text(*arguments: str) -> str:
        completed = subprocess.run(
            [git, "-C", str(repo), *arguments],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"upstream git command failed: {message}")
        return completed.stdout.decode("utf-8", errors="strict").strip()

    tag = str(entry["source_tag"])
    tag_object = git_text("rev-parse", tag)
    commit = git_text("rev-parse", f"{tag}^{{}}")
    tree = git_text("rev-parse", f"{commit}^{{tree}}")
    if tag_object != entry["upstream_tag_object"]:
        raise ValueError("upstream annotated tag object differs from component lock")
    if commit != entry["upstream_commit"]:
        raise ValueError("upstream tag target differs from component lock")
    if tree != entry["upstream_tree"]:
        raise ValueError("upstream Git tree differs from component lock")

    archive_process = subprocess.run(
        [
            git,
            "-C",
            str(repo),
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
        capture_output=True,
        check=False,
    )
    if archive_process.returncode != 0:
        message = archive_process.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot build upstream git archive: {message}")
    archive = archive_process.stdout
    archive_sha256 = "sha256:" + hashlib.sha256(archive).hexdigest()
    if archive_sha256 != entry["source_archive_sha256"]:
        raise ValueError("reproducible upstream git-archive digest differs from component lock")

    archived_files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or member.name in archived_files:
                raise ValueError(f"unsafe or duplicate upstream archive member: {member.name}")
            handle = bundle.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read upstream archive member: {member.name}")
            archived_files[member.name] = handle.read()

    recipe = entry["snapshot_recipe"]
    excluded = set(recipe["excluded_paths"])
    missing_exclusions = sorted(excluded - set(archived_files))
    if missing_exclusions:
        raise ValueError(f"snapshot recipe excludes an untracked path: {missing_exclusions[0]}")
    selected = set(archived_files) - excluded
    locked = {str(item["path"]) for item in entry["files"]}
    if selected != locked:
        missing = sorted(locked - selected)
        extra = sorted(selected - locked)
        raise ValueError(f"snapshot recipe/lock path drift: missing={missing[:3]} extra={extra[:3]}")

    component_root = root / "components" / component_id
    for relative in sorted(locked):
        upstream_bytes = archived_files[relative]
        if _is_text(Path(relative)):
            upstream_bytes = upstream_bytes.replace(b"\r\n", b"\n")
        if (component_root / relative).read_bytes() != upstream_bytes:
            raise ValueError(f"snapshot byte differs from upstream Git object: {relative}")
    return {
        "tag_object": tag_object,
        "commit": commit,
        "tree": tree,
        "archive_sha256": archive_sha256,
        "tracked_file_count": len(archived_files),
        "excluded_file_count": len(excluded),
        "snapshot_file_count": len(locked),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute or verify the deterministic bundled-component lock."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--component", default=COMPONENT_ID)
    parser.add_argument("--write", action="store_true", help="write the rebuilt lock atomically")
    parser.add_argument(
        "--normalize-lf",
        action="store_true",
        help="normalize CRLF text resources before rebuilding (requires --write)",
    )
    parser.add_argument(
        "--upstream-repo",
        type=Path,
        help="Local upstream Git clone used to verify tag/tree/archive/snapshot provenance.",
    )
    args = parser.parse_args()
    if args.normalize_lf and not args.write:
        parser.error("--normalize-lf requires --write because it mutates the snapshot")

    root = args.root.resolve()
    lock_path = root / LOCK_NAME
    try:
        changed: list[str] = []
        if args.normalize_lf:
            changed = normalize_snapshot(root / "components" / args.component)
        rebuilt = build_component_lock(root, component_id=args.component)
        upstream = (
            verify_upstream_snapshot(
                root,
                args.upstream_repo,
                rebuilt,
                component_id=args.component,
            )
            if args.upstream_repo is not None
            else None
        )
        existing = _read_json(lock_path)
        matches = existing == rebuilt
        if args.write and not matches:
            write_json_atomic(lock_path, rebuilt)
            matches = True
        result = {
            "status": "pass" if matches else "stale",
            "component": args.component,
            "lock": str(lock_path),
            "normalized_files": changed,
            "written": bool(args.write and not existing == rebuilt),
            "file_count": rebuilt["components"][args.component]["file_count"],
            "tree_sha256": rebuilt["components"][args.component]["tree_sha256"],
            "upstream_verification": upstream,
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not matches:
            raise SystemExit(1)
    except (OSError, ValueError, json.JSONDecodeError, ComponentError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
