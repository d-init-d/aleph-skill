"""Secure artifact loader with size limits and streaming."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .issues import Issue, issue
from .paths import resolve_in_workspace

DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_MAX_JSONL_ROW = 1 * 1024 * 1024  # 1 MiB
DEFAULT_MAX_WORKSPACE_BYTES = 250 * 1024 * 1024  # 250 MiB
DEFAULT_JSON_DEPTH = 64
DEFAULT_MAX_COLLECTION_ITEMS = 1_000_000


class ResourceLimitError(Exception):
    def __init__(self, iss: Issue):
        self.issue = iss
        super().__init__(iss.legacy_string())


def sha256_file(path: Path, *, max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> str:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ResourceLimitError(
                    issue("RESOURCE_LIMIT", artifact=str(path), message=f"file exceeds {max_bytes} bytes")
                )
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _check_json_value(
    value: Any,
    *,
    max_depth: int,
    depth: int = 0,
    pointer: str = "",
    item_budget: list[int] | None = None,
) -> list[Issue]:
    """Reject resource bombs and JSON extensions such as NaN/Infinity."""
    budget = item_budget if item_budget is not None else [DEFAULT_MAX_COLLECTION_ITEMS]
    if depth > max_depth:
        return [issue("RESOURCE_LIMIT", pointer=pointer or "/", message=f"JSON depth exceeds {max_depth}")]
    budget[0] -= 1
    if budget[0] < 0:
        return [issue("RESOURCE_LIMIT", pointer=pointer or "/", message="JSON collection item limit exceeded")]
    if isinstance(value, float) and not math.isfinite(value):
        return [issue("NON_FINITE", pointer=pointer or "/", message="NaN/Infinity refused", actual=str(value))]
    if isinstance(value, str) and _contains_lone_surrogate(value):
        return [
            issue(
                "INVALID_ARTIFACT",
                pointer=pointer or "/",
                message="lone UTF-16 surrogate refused",
            )
        ]
    problems: list[Issue] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if _contains_lone_surrogate(str(key)):
                problems.append(
                    issue(
                        "INVALID_ARTIFACT",
                        pointer=pointer or "/",
                        message="JSON object key contains a lone UTF-16 surrogate",
                        actual=ascii(key),
                    )
                )
                continue
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            problems.extend(
                _check_json_value(
                    child,
                    max_depth=max_depth,
                    depth=depth + 1,
                    pointer=f"{pointer}/{escaped}",
                    item_budget=budget,
                )
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            problems.extend(
                _check_json_value(
                    child,
                    max_depth=max_depth,
                    depth=depth + 1,
                    pointer=f"{pointer}/{index}",
                    item_budget=budget,
                )
            )
    return problems


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant refused: {value}")


def _contains_lone_surrogate(value: str) -> bool:
    """Return whether a decoded JSON string contains an invalid UTF-16 surrogate."""
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _reject_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key refused: {key}")
        value[key] = child
    return value


def load_json_secure_with_digest(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_depth: int = DEFAULT_JSON_DEPTH,
) -> tuple[Any | None, str | None, list[Issue]]:
    """Read, digest, and parse the same bounded bytes as strict JSON."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None, None, [
                issue(
                    "RESOURCE_LIMIT",
                    artifact=str(path),
                    message=f"file size exceeds {max_bytes}",
                )
            ]
        text = raw.decode("utf-8")
        data = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_object,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        return None, None, [issue("INVALID_ARTIFACT", artifact=str(path), message=str(exc))]
    depth_issues = _check_json_value(data, max_depth=max_depth)
    if depth_issues:
        return None, None, depth_issues
    return data, sha256_bytes(raw), []


def load_json_secure(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_depth: int = DEFAULT_JSON_DEPTH,
) -> tuple[Any | None, list[Issue]]:
    data, _digest, problems = load_json_secure_with_digest(
        path,
        max_bytes=max_bytes,
        max_depth=max_depth,
    )
    return data, problems


def stream_jsonl(
    path: Path,
    *,
    max_row_bytes: int = DEFAULT_MAX_JSONL_ROW,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> Iterator[tuple[int, dict[str, Any] | None, list[Issue]]]:
    size = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_no, line in enumerate(handle, start=1):
            size += len(line.encode("utf-8"))
            if size > max_file_bytes:
                yield line_no, None, [
                    issue("RESOURCE_LIMIT", artifact=str(path), pointer=f"/line/{line_no}", message="workspace file too large")
                ]
                return
            if not line.strip():
                continue
            raw = line.encode("utf-8")
            if len(raw) > max_row_bytes:
                yield line_no, None, [
                    issue("RESOURCE_LIMIT", artifact=str(path), pointer=f"/line/{line_no}", message="JSONL row too large")
                ]
                continue
            try:
                value = json.loads(
                    line,
                    parse_constant=_reject_json_constant,
                    object_pairs_hook=_reject_duplicate_json_object,
                )
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                yield line_no, None, [
                    issue("INVALID_JSONL", artifact=str(path), pointer=f"/line/{line_no}", message=str(exc))
                ]
                continue
            if not isinstance(value, dict):
                yield line_no, None, [
                    issue("TYPE", artifact=str(path), pointer=f"/line/{line_no}", message="JSONL row must be object")
                ]
                continue
            value_issues = _check_json_value(value, max_depth=DEFAULT_JSON_DEPTH)
            if value_issues:
                for problem in value_issues:
                    problem.artifact = str(path)
                    problem.pointer = f"/line/{line_no}{problem.pointer}"
                yield line_no, None, value_issues
                continue
            yield line_no, value, []


def load_jsonl_secure(path: Path, **kwargs: Any) -> tuple[list[dict[str, Any]], list[Issue]]:
    rows: list[dict[str, Any]] = []
    problems: list[Issue] = []
    for _line_no, value, issues in stream_jsonl(path, **kwargs):
        problems.extend(issues)
        if value is not None:
            rows.append(value)
    return rows, problems


def stream_csv_rows(path: Path, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> tuple[list[dict[str, str]], list[Issue]]:
    try:
        if path.stat().st_size > max_file_bytes:
            return [], [issue("RESOURCE_LIMIT", artifact=str(path), message="CSV exceeds size limit")]
    except OSError as exc:
        return [], [issue("INVALID_ARTIFACT", artifact=str(path), message=str(exc))]
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            normalized_headers = [header.strip() for header in headers]
            if not headers or any(not header for header in normalized_headers):
                return [], [
                    issue(
                        "INVALID_ARTIFACT",
                        artifact=str(path),
                        message="CSV header names must be non-empty",
                    )
                ]
            seen: set[str] = set()
            duplicates: set[str] = set()
            for header in normalized_headers:
                if header in seen:
                    duplicates.add(header)
                seen.add(header)
            if duplicates:
                return [], [
                    issue(
                        "INVALID_ARTIFACT",
                        artifact=str(path),
                        message=f"duplicate CSV headers refused: {sorted(duplicates)}",
                    )
                ]
            rows = list(reader)
            if any(None in row for row in rows):
                return [], [
                    issue(
                        "INVALID_ARTIFACT",
                        artifact=str(path),
                        message="CSV row has more values than declared headers",
                    )
                ]
            return rows, []
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return [], [issue("INVALID_ARTIFACT", artifact=str(path), message=str(exc))]


def _write_atomic_bytes(path: Path, data: bytes) -> None:
    """Write through an exclusive random sibling, fsync, then atomically replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, data: Any) -> None:
    """Atomic JSON finalize via an exclusive random sibling."""
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False, allow_nan=False) + "\n"
    _write_atomic_bytes(path, text.encode("utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    _write_atomic_bytes(path, text.replace("\r\n", "\n").encode("utf-8"))


def write_bytes_atomic(path: Path, data: bytes) -> None:
    _write_atomic_bytes(path, data)


def load_workspace_artifact(
    workspace: Path,
    relative: str,
    *,
    kind: str = "json",
) -> tuple[Path | None, Any | None, list[Issue]]:
    resolved, path_issues = resolve_in_workspace(workspace, relative, must_exist=True, require_file=True)
    if path_issues:
        return None, None, path_issues
    assert resolved is not None
    if kind == "json":
        data, issues = load_json_secure(resolved)
        return resolved, data, issues
    if kind == "jsonl":
        data, issues = load_jsonl_secure(resolved)
        return resolved, data, issues
    if kind == "csv":
        data, issues = stream_csv_rows(resolved)
        return resolved, data, issues
    if kind == "text":
        try:
            size = resolved.stat().st_size
            if size > DEFAULT_MAX_FILE_BYTES:
                return resolved, None, [
                    issue("RESOURCE_LIMIT", artifact=str(resolved), message=f"file size {size} exceeds {DEFAULT_MAX_FILE_BYTES}")
                ]
            return resolved, resolved.read_text(encoding="utf-8"), []
        except (OSError, UnicodeDecodeError) as exc:
            return resolved, None, [issue("INVALID_ARTIFACT", artifact=str(resolved), message=str(exc))]
    return resolved, None, [issue("TYPE", message=f"unknown kind {kind}")]


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def canonical_hash(data: Any) -> str:
    return sha256_bytes(canonical_json_bytes(data))


def validate_workspace_budget(
    workspace: Path,
    *,
    max_bytes: int = DEFAULT_MAX_WORKSPACE_BYTES,
) -> tuple[int, list[Issue]]:
    """Bound total regular-file bytes without following directory symlinks."""
    total = 0
    problems: list[Issue] = []
    try:
        for root, dirs, files in os.walk(workspace, followlinks=False):
            root_path = Path(root)
            dirs[:] = [name for name in dirs if not (root_path / name).is_symlink()]
            for name in files:
                path = root_path / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    total += path.stat().st_size
                except OSError as exc:
                    problems.append(issue("INVALID_ARTIFACT", artifact=str(path), message=str(exc)))
                    continue
                if total > max_bytes:
                    problems.append(
                        issue(
                            "RESOURCE_LIMIT",
                            artifact=str(workspace),
                            message=f"workspace size {total} exceeds {max_bytes}",
                        )
                    )
                    return total, problems
    except OSError as exc:
        problems.append(issue("INVALID_ARTIFACT", artifact=str(workspace), message=str(exc)))
    return total, problems
