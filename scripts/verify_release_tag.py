"""Verify an annotated release tag against checked-out source and main."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
REMOTE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STATE_SCHEMA_VERSION = "1.0"
MAX_STATE_BYTES = 4096


class VerificationError(ValueError):
    """A release-tag verification failure with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _absolute_without_symlink_resolution(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise VerificationError("GIT_UNAVAILABLE", f"cannot execute git: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise VerificationError("GIT_FAILED", f"git {' '.join(args)} failed: {detail}")
    return result


def _validate_inputs(repository: Path, tag: str, remote: str, main_branch: str) -> None:
    if not repository.is_dir():
        raise VerificationError("REPOSITORY_INVALID", "repository path is not a directory")
    if TAG_PATTERN.fullmatch(tag) is None:
        raise VerificationError(
            "TAG_FORMAT", "release tag must use the exact vMAJOR.MINOR.PATCH form"
        )
    if REMOTE_PATTERN.fullmatch(remote) is None:
        raise VerificationError("REMOTE_INVALID", "remote name contains unsafe characters")
    branch_check = _git(
        repository,
        "check-ref-format",
        f"refs/heads/{main_branch}",
        check=False,
    )
    if branch_check.returncode != 0:
        raise VerificationError("MAIN_BRANCH_INVALID", "main branch name is not a valid Git ref")
    inside = _git(repository, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise VerificationError("REPOSITORY_INVALID", "path is not a Git work tree")


def _load_state(path: Path) -> dict[str, str]:
    try:
        if path.is_symlink() or not path.is_file():
            raise VerificationError("STATE_INVALID", "expected state is not a regular file")
        if path.stat().st_size > MAX_STATE_BYTES:
            raise VerificationError("STATE_INVALID", "expected state exceeds the size limit")

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise VerificationError("STATE_INVALID", f"duplicate state field: {key}")
                value[key] = item
            return value

        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except VerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("STATE_INVALID", f"cannot read expected state: {exc}") from exc
    required = {"schema_version", "tag", "tag_object", "tag_commit"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise VerificationError("STATE_INVALID", "expected state fields are invalid")
    if not all(isinstance(raw[key], str) for key in required):
        raise VerificationError("STATE_INVALID", "expected state values must be strings")
    return {key: raw[key] for key in required}


def _write_state(path: Path, state: dict[str, str]) -> None:
    payload = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise VerificationError("STATE_WRITE_FAILED", f"cannot create state file: {exc}") from exc


def verify_release_tag(
    repository: Path,
    *,
    tag: str,
    remote: str = "origin",
    main_branch: str = "main",
    expected_state: Path | None = None,
    state_out: Path | None = None,
) -> dict[str, str]:
    """Fetch and verify a remote annotated tag, optionally binding two checks."""
    repository = repository.resolve()
    _validate_inputs(repository, tag, remote, main_branch)
    if expected_state is not None and state_out is not None:
        raise VerificationError("STATE_MODE", "expected-state and state-out are mutually exclusive")

    release_ref = f"refs/aleph-release-tags/{tag}"
    main_ref = f"refs/remotes/{remote}/{main_branch}"
    fetch = _git(
        repository,
        "fetch",
        "--force",
        "--no-tags",
        remote,
        f"+refs/tags/{tag}:{release_ref}",
        f"+refs/heads/{main_branch}:{main_ref}",
        check=False,
    )
    if fetch.returncode != 0:
        detail = fetch.stderr.strip() or fetch.stdout.strip() or f"exit {fetch.returncode}"
        raise VerificationError("FETCH_FAILED", f"cannot fetch release refs: {detail}")

    tag_type = _git(repository, "cat-file", "-t", release_ref, check=False)
    if tag_type.returncode != 0 or tag_type.stdout.strip() != "tag":
        raise VerificationError("TAG_NOT_ANNOTATED", "remote release ref is not an annotated tag")
    tag_object = _git(repository, "rev-parse", "--verify", release_ref).stdout.strip()
    tag_commit = _git(
        repository, "rev-parse", "--verify", f"{release_ref}^{{commit}}"
    ).stdout.strip()

    if expected_state is not None:
        expected = _load_state(_absolute_without_symlink_resolution(expected_state))
        if expected["schema_version"] != STATE_SCHEMA_VERSION or expected["tag"] != tag:
            raise VerificationError("STATE_MISMATCH", "expected state does not describe this tag")
        if expected["tag_object"] != tag_object or expected["tag_commit"] != tag_commit:
            raise VerificationError("TAG_MOVED", "remote tag changed after the initial verification")

    head = _git(repository, "rev-parse", "--verify", "HEAD").stdout.strip()
    if head != tag_commit:
        raise VerificationError("HEAD_MISMATCH", "checked-out source does not match the tag commit")
    ancestry = _git(repository, "merge-base", "--is-ancestor", tag_commit, main_ref, check=False)
    if ancestry.returncode == 1:
        raise VerificationError("NOT_ON_MAIN", "release tag commit is not contained in main")
    if ancestry.returncode != 0:
        detail = ancestry.stderr.strip() or ancestry.stdout.strip() or f"exit {ancestry.returncode}"
        raise VerificationError("GIT_FAILED", f"cannot verify main ancestry: {detail}")

    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "tag": tag,
        "tag_object": tag_object,
        "tag_commit": tag_commit,
    }
    if state_out is not None:
        _write_state(_absolute_without_symlink_resolution(state_out), state)
    return {"status": "pass", **state, "head": head, "main_ref": main_ref}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--tag", required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--main-branch", default="main")
    state_group = parser.add_mutually_exclusive_group()
    state_group.add_argument("--expected-state", type=Path)
    state_group.add_argument("--state-out", type=Path)
    args = parser.parse_args()
    try:
        result = verify_release_tag(
            args.repository,
            tag=args.tag,
            remote=args.remote,
            main_branch=args.main_branch,
            expected_state=args.expected_state,
            state_out=args.state_out,
        )
    except VerificationError as exc:
        print(json.dumps({"status": "fail", "code": exc.code, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
