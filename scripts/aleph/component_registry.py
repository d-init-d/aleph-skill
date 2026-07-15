"""Immutable bundled-component resolve/verify for Aleph.

Pure helpers separate from subprocess I/O. The sole trusted default research
root is the locked ``aleph-component://d-research`` snapshot under
``components/d-research/``. External installs are opt-in only.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .discovery import _candidate_report, _parse_frontmatter_name
from .io import DEFAULT_MAX_FILE_BYTES, load_json_secure
from .issues import issue
from .paths import is_distribution_path, path_contains_link_or_reparse

COMPONENT_URI = "aleph-component://d-research"
COMPONENT_ID = "d-research"
LOCK_NAME = "component-lock.json"
COMPONENT_REL = Path("components") / "d-research"
SUPPORTED_MAJORS = frozenset({3})
EXPECTED_PACKAGE_NAMES = frozenset({"d-research-skill-tools", "d-research"})
EXPECTED_SKILL_NAME = "d-research"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA_PREFIX = "sha256:"

ERROR_CODES = frozenset(
    {
        "COMPONENT_NOT_FOUND",
        "COMPONENT_LOCK_INVALID",
        "COMPONENT_FILE_MISSING",
        "COMPONENT_EXTRA_FILE",
        "COMPONENT_TAMPER",
        "COMPONENT_DRIFT",
        "COMPONENT_OVERRIDE_REFUSED",
        "COMPONENT_IDENTITY_MISMATCH",
    }
)


class ComponentError(Exception):
    """Hard-fail component identity/integrity error with a stable code."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        if code not in ERROR_CODES:
            code = "COMPONENT_LOCK_INVALID"
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class ComponentResolution:
    component_uri: str
    component_id: str
    root: str
    package_name: str
    package_version: str
    package_major: int
    upstream_tag: str
    upstream_tag_object: str
    upstream_commit: str
    component_lock_sha256: str
    component_tree_sha256: str
    entrypoint: str
    entrypoint_sha256: str
    trust_level: str
    source_kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def binding(self) -> dict[str, Any]:
        """Portable receipt binding — no absolute install paths."""
        return {
            "source_kind": self.source_kind,
            "component_uri": self.component_uri,
            "component_id": self.component_id,
            "package_name": self.package_name,
            "package_version": self.package_version,
            "package_major": self.package_major,
            "upstream_tag": self.upstream_tag,
            "upstream_tag_object": self.upstream_tag_object,
            "upstream_commit": self.upstream_commit,
            "component_lock_sha256": self.component_lock_sha256,
            "component_tree_sha256": self.component_tree_sha256,
            "entrypoint": self.entrypoint,
            "entrypoint_sha256": self.entrypoint_sha256,
        }


@dataclass(frozen=True)
class ComponentVerification:
    ok: bool
    component_id: str
    component_uri: str
    package_name: str
    package_version: str
    package_major: int
    upstream_commit: str
    lock_sha256: str
    tree_sha256: str
    file_count: int
    entrypoints: list[str]
    root: str
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def skill_root_from(module_file: Path | None = None, *, env: dict[str, str] | None = None) -> Path:
    """Resolve Aleph skill root from ALEPH_SKILL_ROOT or this package location."""
    environ = env if env is not None else os.environ
    configured = (environ.get("ALEPH_SKILL_ROOT") or "").strip()
    if configured:
        root = Path(configured).expanduser()
        _assert_safe_existing_dir(root, label="ALEPH_SKILL_ROOT")
        return root.resolve(strict=True)
    # scripts/aleph/component_registry.py -> skill root is parents[2]
    base = Path(module_file or __file__).resolve()
    return base.parents[2]


def _assert_safe_existing_dir(path: Path, *, label: str) -> None:
    raw = str(path)
    if ".." in Path(raw).parts:
        raise ComponentError("COMPONENT_OVERRIDE_REFUSED", f"{label} contains path traversal")
    if path_contains_link_or_reparse(path):
        raise ComponentError("COMPONENT_OVERRIDE_REFUSED", f"{label} contains symlink/reparse")
    if not path.exists() or not path.is_dir():
        raise ComponentError("COMPONENT_NOT_FOUND", f"{label} is not a directory: {path}")


def _normalize_digest(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ComponentError("COMPONENT_LOCK_INVALID", "digest must be a non-empty string")
    text = value.strip().lower()
    if text.startswith(SHA_PREFIX):
        hexpart = text[len(SHA_PREFIX) :]
    else:
        hexpart = text
        text = SHA_PREFIX + hexpart
    if not HEX64.match(hexpart):
        raise ComponentError("COMPONENT_LOCK_INVALID", f"invalid sha256 digest: {value}")
    return text


def _sha256_bytes(data: bytes) -> str:
    return SHA_PREFIX + hashlib.sha256(data).hexdigest()


def _tree_digest(files: list[dict[str, Any]]) -> str:
    rows = [
        f"{entry['path']}\0{entry['size']}\0{entry['sha256']}\n"
        for entry in sorted(files, key=lambda item: str(item["path"]))
    ]
    return _sha256_bytes("".join(rows).encode("utf-8"))


def _is_link_or_reparse(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or os.path.islink(path) or bool(attributes & 0x400)


def _refuse_unsafe_relative(rel: str) -> None:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or rel.startswith("\\"):
        raise ComponentError("COMPONENT_LOCK_INVALID", f"absolute or empty path refused: {rel!r}")
    if re.match(r"^[A-Za-z]:", rel) or rel.startswith("\\\\") or rel.startswith("//"):
        raise ComponentError("COMPONENT_LOCK_INVALID", f"drive/UNC path refused: {rel!r}")
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts):
        raise ComponentError("COMPONENT_LOCK_INVALID", f"path traversal refused: {rel!r}")


def _load_lock(skill_root: Path) -> tuple[dict[str, Any], str]:
    lock_path = skill_root / LOCK_NAME
    if not lock_path.is_file() or _is_link_or_reparse(lock_path):
        raise ComponentError("COMPONENT_LOCK_INVALID", f"missing or unsafe {LOCK_NAME}")
    try:
        raw = lock_path.read_bytes()
        if len(raw) > DEFAULT_MAX_FILE_BYTES:
            raise ComponentError("COMPONENT_LOCK_INVALID", "component lock exceeds size limit")
        data, problems = load_json_secure(lock_path)
    except (OSError, UnicodeDecodeError) as exc:
        raise ComponentError("COMPONENT_LOCK_INVALID", str(exc)) from exc
    if problems or not isinstance(data, dict):
        raise ComponentError("COMPONENT_LOCK_INVALID", "component lock is not a secure JSON object")
    lock_digest = _sha256_bytes(raw)
    return data, lock_digest


def _component_entry(lock: dict[str, Any], component_id: str) -> dict[str, Any]:
    components = lock.get("components")
    if not isinstance(components, dict) or component_id not in components:
        raise ComponentError("COMPONENT_NOT_FOUND", f"component {component_id!r} missing from lock")
    entry = components[component_id]
    if not isinstance(entry, dict):
        raise ComponentError("COMPONENT_LOCK_INVALID", "component entry must be an object")
    return entry


_FORBIDDEN_DIR_NAMES = frozenset(
    {".agents", ".git", ".github", ".venv", "__pycache__", "node_modules", "release-evidence"}
)

# The vendored snapshot is byte-attested and checked out with LF endings.  A
# CRLF in a text resource is therefore drift, not an ignorable platform
# difference: Git's ``.gitattributes`` contract promises the same bytes on
# every release host.
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


def _is_text_snapshot_path(path: Path) -> bool:
    return path.name in _TEXT_NAMES or path.suffix.lower() in _TEXT_SUFFIXES


def _collect_actual_files(component_root: Path) -> dict[str, Path]:
    actual: dict[str, Path] = {}
    for raw_root, directories, names in os.walk(component_root, topdown=True, followlinks=False):
        root = Path(raw_root)
        directories.sort()
        names.sort()
        for directory in directories:
            path = root / directory
            rel = path.relative_to(component_root).as_posix()
            if _is_link_or_reparse(path):
                raise ComponentError(
                    "COMPONENT_TAMPER",
                    f"symlink/reparse directory refused inside component: {rel}",
                )
            if directory in _FORBIDDEN_DIR_NAMES:
                raise ComponentError("COMPONENT_EXTRA_FILE", f"forbidden path present: {rel}")
        for name in names:
            path = root / name
            rel = path.relative_to(component_root).as_posix()
            if _is_link_or_reparse(path) or not path.is_file():
                raise ComponentError(
                    "COMPONENT_TAMPER",
                    f"non-regular or linked file refused inside component: {rel}",
                )
            parts = rel.split("/")
            if any(part in _FORBIDDEN_DIR_NAMES for part in parts):
                raise ComponentError("COMPONENT_EXTRA_FILE", f"forbidden path present: {rel}")
            actual[rel] = path
    return actual


_LOCK_METADATA_FIELDS = (
    "uri",
    "package_name",
    "version",
    "source_repository",
    "source_tag",
    "upstream_tag_object",
    "upstream_commit",
    "upstream_tree",
    "source_archive_format",
    "source_archive_sha256",
    "snapshot_recipe",
)


def build_component_lock(
    skill_root: Path,
    *,
    component_id: str = COMPONENT_ID,
) -> dict[str, Any]:
    """Recompute a component lock from the byte-exact snapshot.

    Provenance fields are copied from the existing lock, while file sizes,
    file digests, count, and tree digest are derived exclusively from the
    current snapshot.  The function is intentionally non-mutating; the
    ``lock_bundled_component.py`` command is the explicit write recipe.
    """
    root = Path(skill_root).resolve(strict=False)
    lock, _lock_digest = _load_lock(root)
    entry = _component_entry(lock, component_id)
    component_root = root / COMPONENT_REL
    if not component_root.is_dir() or _is_link_or_reparse(component_root):
        raise ComponentError(
            "COMPONENT_NOT_FOUND",
            f"bundled component missing at {COMPONENT_REL.as_posix()}",
        )

    metadata: dict[str, Any] = {}
    for field in _LOCK_METADATA_FIELDS:
        if field not in entry:
            raise ComponentError("COMPONENT_LOCK_INVALID", f"lock metadata field missing: {field}")
        metadata[field] = entry[field]

    actual = _collect_actual_files(component_root)
    files: list[dict[str, Any]] = []
    for rel, path in sorted(actual.items()):
        distribution_path = (COMPONENT_REL / rel).as_posix()
        if not is_distribution_path(distribution_path):
            raise ComponentError(
                "COMPONENT_EXTRA_FILE",
                f"snapshot file is outside the distribution allowlist: {rel}",
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ComponentError("COMPONENT_TAMPER", f"unreadable {rel}: {exc}") from exc
        if len(data) > DEFAULT_MAX_FILE_BYTES:
            raise ComponentError("COMPONENT_TAMPER", f"snapshot file exceeds size limit: {rel}")
        if _is_text_snapshot_path(path) and b"\r\n" in data:
            raise ComponentError(
                "COMPONENT_DRIFT",
                f"non-normalized CRLF bytes in {rel}; normalize the snapshot first",
            )
        files.append(
            {
                "path": rel,
                "size": len(data),
                "sha256": _sha256_bytes(data),
            }
        )

    entrypoints = entry.get("entrypoints")
    if not isinstance(entrypoints, list) or not entrypoints:
        raise ComponentError("COMPONENT_LOCK_INVALID", "entrypoints must be a non-empty list")
    file_paths = {str(item["path"]) for item in files}
    if any(not isinstance(path, str) or path not in file_paths for path in entrypoints):
        raise ComponentError("COMPONENT_FILE_MISSING", "an entrypoint is absent from the snapshot")

    rebuilt_entry = dict(metadata)
    rebuilt_entry["tree_sha256"] = _tree_digest(files)
    rebuilt_entry["file_count"] = len(files)
    rebuilt_entry["entrypoints"] = [str(path) for path in entrypoints]
    rebuilt_entry["files"] = files
    # Preserve the documented exclusion/pinning note without copying unknown
    # mutable fields into a release lock.
    for field in ("excluded", "pin_note"):
        if field in entry:
            rebuilt_entry[field] = entry[field]
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "components": {component_id: rebuilt_entry},
    }


def locked_component_paths(
    skill_root: Path,
    *,
    component_id: str = COMPONENT_ID,
) -> set[str]:
    """Return workspace-relative paths claimed by a component lock."""
    lock, _ = _load_lock(Path(skill_root))
    entry = _component_entry(lock, component_id)
    files = entry.get("files")
    if not isinstance(files, list):
        raise ComponentError("COMPONENT_LOCK_INVALID", "lock files[] must be a list")
    result: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ComponentError("COMPONENT_LOCK_INVALID", "lock file entry is malformed")
        result.add((COMPONENT_REL / str(item["path"])).as_posix())
    return result


def verify_component_lock(
    *,
    skill_root: Path,
    component_id: str = COMPONENT_ID,
) -> ComponentVerification:
    """Verify lock metadata and every locked file under the component tree."""
    skill_root = Path(skill_root).resolve(strict=False)
    try:
        if not skill_root.is_dir():
            raise ComponentError("COMPONENT_NOT_FOUND", f"skill root missing: {skill_root}")
        lock, lock_digest = _load_lock(skill_root)
        if lock.get("schema_version") != 1 or lock.get("algorithm") != "sha256":
            raise ComponentError("COMPONENT_LOCK_INVALID", "unsupported lock schema/algorithm")
        entry = _component_entry(lock, component_id)
        uri = entry.get("uri")
        if uri != COMPONENT_URI:
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", f"unexpected component uri: {uri!r}")
        package_name = entry.get("package_name")
        version = entry.get("version")
        if package_name not in EXPECTED_PACKAGE_NAMES:
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", f"unexpected package name: {package_name!r}")
        try:
            major = int(str(version).split(".", 1)[0])
        except (TypeError, ValueError) as exc:
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", f"invalid package version: {version!r}") from exc
        if major not in SUPPORTED_MAJORS:
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", f"unsupported package major: {major}")
        commit = str(entry.get("upstream_commit") or "")
        if not HEX40.match(commit):
            raise ComponentError("COMPONENT_LOCK_INVALID", "upstream_commit must be 40-hex")
        tag_object = str(entry.get("upstream_tag_object") or "")
        if not HEX40.match(tag_object):
            raise ComponentError("COMPONENT_LOCK_INVALID", "upstream_tag_object must be 40-hex")
        upstream_tree = str(entry.get("upstream_tree") or "")
        if not HEX40.match(upstream_tree):
            raise ComponentError("COMPONENT_LOCK_INVALID", "upstream_tree must be 40-hex")
        if entry.get("source_archive_format") != "git-archive-tar":
            raise ComponentError(
                "COMPONENT_LOCK_INVALID",
                "source_archive_format must be git-archive-tar",
            )
        _normalize_digest(entry.get("source_archive_sha256"))
        recipe = entry.get("snapshot_recipe")
        expected_recipe_fields = {
            "version",
            "source",
            "include",
            "excluded_paths",
            "text_eol",
            "forbidden_paths",
        }
        if not isinstance(recipe, dict) or set(recipe) != expected_recipe_fields:
            raise ComponentError("COMPONENT_LOCK_INVALID", "snapshot_recipe fields are invalid")
        if (
            recipe.get("version") != 1
            or recipe.get("source") != "git-tree"
            or recipe.get("include") != "all-tracked-files-except-excluded_paths"
            or recipe.get("text_eol") != "lf"
        ):
            raise ComponentError("COMPONENT_LOCK_INVALID", "snapshot_recipe policy is invalid")
        raw_excluded = recipe.get("excluded_paths")
        raw_forbidden = recipe.get("forbidden_paths")
        if not isinstance(raw_excluded, list) or not raw_excluded:
            raise ComponentError("COMPONENT_LOCK_INVALID", "snapshot_recipe exclusions are required")
        if not isinstance(raw_forbidden, list) or not raw_forbidden:
            raise ComponentError("COMPONENT_LOCK_INVALID", "snapshot_recipe forbidden paths are required")
        if not all(isinstance(value, str) for value in raw_excluded) or raw_excluded != sorted(
            set(raw_excluded)
        ):
            raise ComponentError(
                "COMPONENT_LOCK_INVALID",
                "snapshot_recipe excluded_paths must be sorted unique strings",
            )
        if not all(isinstance(value, str) for value in raw_forbidden) or raw_forbidden != sorted(
            set(raw_forbidden)
        ):
            raise ComponentError(
                "COMPONENT_LOCK_INVALID",
                "snapshot_recipe forbidden_paths must be sorted unique strings",
            )
        for excluded_path in raw_excluded:
            _refuse_unsafe_relative(excluded_path)
        files = entry.get("files")
        if not isinstance(files, list) or not files:
            raise ComponentError("COMPONENT_LOCK_INVALID", "lock files[] must be a non-empty list")
        expected_count = entry.get("file_count")
        if expected_count != len(files):
            raise ComponentError(
                "COMPONENT_LOCK_INVALID",
                f"file_count {expected_count} does not match files[] length {len(files)}",
            )
        normalized: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ComponentError("COMPONENT_LOCK_INVALID", "file entry must be an object")
            rel = item.get("path")
            if not isinstance(rel, str):
                raise ComponentError("COMPONENT_LOCK_INVALID", "file path must be a string")
            _refuse_unsafe_relative(rel)
            distribution_path = (COMPONENT_REL / rel).as_posix()
            if not is_distribution_path(distribution_path):
                raise ComponentError(
                    "COMPONENT_LOCK_INVALID",
                    f"locked path is outside the distribution allowlist: {rel}",
                )
            if rel in seen_paths:
                raise ComponentError("COMPONENT_LOCK_INVALID", f"duplicate lock path: {rel}")
            seen_paths.add(rel)
            size = item.get("size")
            if not isinstance(size, int) or size < 0:
                raise ComponentError("COMPONENT_LOCK_INVALID", f"invalid size for {rel}")
            digest = _normalize_digest(item.get("sha256"))
            normalized.append({"path": rel, "size": size, "sha256": digest})
        overlap = sorted(seen_paths & set(raw_excluded))
        if overlap:
            raise ComponentError(
                "COMPONENT_LOCK_INVALID",
                f"snapshot exclusion is also locked: {overlap[0]}",
            )
        tree_expected = _normalize_digest(entry.get("tree_sha256"))
        tree_actual = _tree_digest(normalized)
        if tree_actual != tree_expected:
            raise ComponentError(
                "COMPONENT_DRIFT",
                "tree digest mismatch against lock",
                details={"expected": tree_expected, "actual": tree_actual},
            )
        component_root = (skill_root / COMPONENT_REL).resolve(strict=False)
        if not component_root.is_dir() or _is_link_or_reparse(skill_root / "components") or _is_link_or_reparse(
            skill_root / COMPONENT_REL
        ):
            raise ComponentError("COMPONENT_NOT_FOUND", f"bundled component missing at {COMPONENT_REL.as_posix()}")
        # Ensure component root is under skill root without traversal.
        try:
            component_root.relative_to(skill_root.resolve(strict=False))
        except ValueError as exc:
            raise ComponentError("COMPONENT_OVERRIDE_REFUSED", "component root escaped skill root") from exc
        actual = _collect_actual_files(component_root)
        missing = sorted(seen_paths - set(actual))
        extra = sorted(set(actual) - seen_paths)
        if missing:
            raise ComponentError(
                "COMPONENT_FILE_MISSING",
                f"missing locked file: {missing[0]}",
                details={"missing": missing[:20], "count": len(missing)},
            )
        if extra:
            raise ComponentError(
                "COMPONENT_EXTRA_FILE",
                f"extra unlocked file: {extra[0]}",
                details={"extra": extra[:20], "count": len(extra)},
            )
        for item in normalized:
            path = actual[item["path"]]
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ComponentError("COMPONENT_TAMPER", f"unreadable {item['path']}: {exc}") from exc
            if _is_text_snapshot_path(path) and b"\r\n" in data:
                raise ComponentError(
                    "COMPONENT_DRIFT",
                    f"non-normalized CRLF bytes in {item['path']}; snapshot requires LF",
                )
            if len(data) != item["size"]:
                raise ComponentError(
                    "COMPONENT_TAMPER",
                    f"size mismatch for {item['path']}",
                    details={"expected": item["size"], "actual": len(data)},
                )
            digest = _sha256_bytes(data)
            if digest != item["sha256"]:
                raise ComponentError(
                    "COMPONENT_TAMPER",
                    f"digest mismatch for {item['path']}",
                    details={"expected": item["sha256"], "actual": digest},
                )
        entrypoints = entry.get("entrypoints")
        if not isinstance(entrypoints, list) or not entrypoints:
            raise ComponentError("COMPONENT_LOCK_INVALID", "entrypoints must be a non-empty list")
        for ep in entrypoints:
            if not isinstance(ep, str) or ep not in seen_paths:
                raise ComponentError("COMPONENT_FILE_MISSING", f"entrypoint missing from lock/tree: {ep!r}")
        helper = component_root / "scripts" / "evidence_ledger.py"
        if not helper.is_file():
            raise ComponentError("COMPONENT_FILE_MISSING", "scripts/evidence_ledger.py missing")
        # Live package identity check
        package_json = component_root / "package.json"
        skill_md = component_root / "SKILL.md"
        package, package_issues = load_json_secure(package_json)
        if package_issues or not isinstance(package, dict):
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", "component package.json invalid")
        if package.get("name") not in EXPECTED_PACKAGE_NAMES:
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", "live package name mismatch")
        if str(package.get("version")) != str(version):
            raise ComponentError(
                "COMPONENT_DRIFT",
                "live package version differs from lock",
                details={"lock": version, "live": package.get("version")},
            )
        if skill_md.stat().st_size > DEFAULT_MAX_FILE_BYTES:
            raise ComponentError("COMPONENT_TAMPER", "SKILL.md exceeds size limit")
        skill_name = _parse_frontmatter_name(skill_md.read_text(encoding="utf-8"))
        if skill_name != EXPECTED_SKILL_NAME:
            raise ComponentError("COMPONENT_IDENTITY_MISMATCH", f"SKILL.md name mismatch: {skill_name!r}")
        return ComponentVerification(
            ok=True,
            component_id=component_id,
            component_uri=COMPONENT_URI,
            package_name=str(package_name),
            package_version=str(version),
            package_major=major,
            upstream_commit=commit,
            lock_sha256=lock_digest,
            tree_sha256=tree_expected,
            file_count=len(normalized),
            entrypoints=[str(ep) for ep in entrypoints],
            root=str(component_root.resolve(strict=True)),
        )
    except ComponentError as exc:
        return ComponentVerification(
            ok=False,
            component_id=component_id,
            component_uri=COMPONENT_URI,
            package_name="",
            package_version="",
            package_major=0,
            upstream_commit="",
            lock_sha256="",
            tree_sha256="",
            file_count=0,
            entrypoints=[],
            root="",
            error_code=exc.code,
            message=exc.message,
        )


def resolve_component(
    component_uri: str,
    *,
    skill_root: Path,
    require_verified: bool = True,
) -> ComponentResolution:
    """Resolve only the portable bundled URI after lock verification."""
    if component_uri != COMPONENT_URI:
        # Refuse absolute/drive/UNC disguised as URI payloads.
        if (
            ".." in component_uri
            or "\\" in component_uri
            or component_uri.startswith("/")
            or re.match(r"^[A-Za-z]:", component_uri)
            or component_uri.startswith("\\\\")
        ):
            raise ComponentError("COMPONENT_OVERRIDE_REFUSED", "malformed component URI")
        raise ComponentError(
            "COMPONENT_OVERRIDE_REFUSED",
            f"only {COMPONENT_URI} is accepted; got {component_uri!r}",
        )
    verification = verify_component_lock(skill_root=skill_root, component_id=COMPONENT_ID)
    if not verification.ok:
        raise ComponentError(
            verification.error_code or "COMPONENT_LOCK_INVALID",
            verification.message or "component verification failed",
        )
    if require_verified and not verification.ok:
        raise ComponentError("COMPONENT_LOCK_INVALID", "component not verified")
    root = Path(verification.root)
    entrypoint = "scripts/evidence_ledger.py"
    helper = root / entrypoint
    entry_digest = _sha256_bytes(helper.read_bytes())
    lock, lock_digest = _load_lock(Path(skill_root))
    entry = _component_entry(lock, COMPONENT_ID)
    return ComponentResolution(
        component_uri=COMPONENT_URI,
        component_id=COMPONENT_ID,
        root=str(root),
        package_name=verification.package_name,
        package_version=verification.package_version,
        package_major=verification.package_major,
        upstream_tag=str(entry.get("source_tag") or ""),
        upstream_tag_object=str(entry.get("upstream_tag_object") or ""),
        upstream_commit=verification.upstream_commit,
        component_lock_sha256=lock_digest,
        component_tree_sha256=verification.tree_sha256,
        entrypoint=entrypoint,
        entrypoint_sha256=entry_digest,
        trust_level="bundled-verified",
        source_kind="bundled",
    )


def locked_script_paths(skill_root: Path, *, component_id: str = COMPONENT_ID) -> set[str]:
    """Return relative script paths allowed by the component lock."""
    lock, _ = _load_lock(skill_root)
    entry = _component_entry(lock, component_id)
    files = entry.get("files") or []
    allowed: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        rel = item.get("path")
        if isinstance(rel, str) and (rel.startswith("scripts/") and (rel.endswith(".py") or rel.endswith(".mjs"))):
            allowed.add(rel)
    return allowed


def discover_d_research(
    *,
    skill_root: Path | None = None,
    explicit: str | Path | None = None,
    allow_external: bool = False,
    require_bundled: bool = True,
    capability_file: Path | None = None,
    conventional_roots: list[Path] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bundled-first D Research discovery.

    Default: verify and return the locked bundled component. ``D_RESEARCH_SKILL``
    never wins over the bundle. External paths require ``allow_external=True``
    as a separate opt-in in addition to the explicit path.
    """
    environ = env if env is not None else os.environ
    root = skill_root if skill_root is not None else skill_root_from(env=dict(environ))
    tried: list[dict[str, Any]] = []

    # 1) Bundled component is always preferred when present.
    verification = verify_component_lock(skill_root=root, component_id=COMPONENT_ID)
    if verification.ok:
        resolution = resolve_component(COMPONENT_URI, skill_root=root, require_verified=True)
        tried.append(
            {
                "source": "bundled",
                "path": resolution.root,
                "ok": True,
                "compatible": True,
                "component_uri": COMPONENT_URI,
            }
        )
        # Silent env override is forbidden: record but do not prefer.
        env_path = (environ.get("D_RESEARCH_SKILL") or "").strip()
        if env_path:
            tried.append(
                {
                    "source": "env:D_RESEARCH_SKILL",
                    "path": env_path,
                    "ok": False,
                    "compatible": False,
                    "reason": "COMPONENT_OVERRIDE_REFUSED: env cannot override bundled component",
                }
            )
        if explicit is not None and not allow_external:
            tried.append(
                {
                    "source": "explicit",
                    "path": str(explicit),
                    "ok": False,
                    "compatible": False,
                    "reason": "COMPONENT_OVERRIDE_REFUSED: use allow_external for external path",
                }
            )
        return {
            "status": "available",
            "path": COMPONENT_URI,
            "resolved_path": resolution.root,
            "source": "bundled",
            "source_kind": "bundled",
            "name": EXPECTED_SKILL_NAME,
            "package_name": resolution.package_name,
            "package_version": resolution.package_version,
            "package_major": resolution.package_major,
            "supported_majors": [3],
            "compatible": True,
            "identity_verified": True,
            "component_uri": COMPONENT_URI,
            "component_binding": resolution.binding(),
            "upstream_commit": resolution.upstream_commit,
            "component_lock_sha256": resolution.component_lock_sha256,
            "component_tree_sha256": resolution.component_tree_sha256,
            "trust_level": resolution.trust_level,
            "tried": tried,
        }

    if require_bundled and not allow_external:
        return {
            "status": "unavailable" if verification.error_code == "COMPONENT_NOT_FOUND" else "incompatible",
            "path": None,
            "source": "bundled",
            "source_kind": "bundled",
            "compatible": False,
            "identity_verified": False,
            "supported_majors": [3],
            "tried": tried
            + [
                {
                    "source": "bundled",
                    "ok": False,
                    "reason": verification.message,
                    "error_code": verification.error_code,
                }
            ],
            "assurance_cap": "limited",
            "error_code": verification.error_code,
            "issues": [
                issue(
                    "D_RESEARCH",
                    message=verification.message or "bundled component unavailable",
                ).to_dict()
            ],
        }

    # 2) Explicit external only when allowed.
    candidates: list[tuple[str, Path, bool]] = []
    if explicit is not None and allow_external:
        candidates.append(("explicit", Path(explicit).expanduser(), True))
    env_value = (environ.get("D_RESEARCH_SKILL") or "").strip()
    # Env is never authoritative for default discovery; only when bundled absent
    # and require_bundled is False and allow_external is True.
    if env_value and allow_external and not require_bundled:
        candidates.append(("env:D_RESEARCH_SKILL", Path(env_value).expanduser(), True))
    if capability_file is not None and capability_file.is_file() and allow_external and not require_bundled:
        try:
            data, capability_issues = load_json_secure(capability_file)
            if capability_issues or not isinstance(data, dict):
                raise ValueError("invalid capability file")
            configured = data.get("d_research_skill") or (data.get("d_research") or {}).get("path")
            if configured:
                candidates.append(("capability_file", Path(str(configured)).expanduser(), True))
        except (OSError, UnicodeDecodeError, ValueError, AttributeError):
            candidates.append(("capability_file", Path("__invalid_capability_file__"), True))
    if conventional_roots is not None and allow_external and not require_bundled:
        candidates.extend(("conventional", path, False) for path in conventional_roots)

    incompatible: dict[str, Any] | None = None
    for source, path, authoritative in candidates:
        report = _candidate_report(source, path)
        tried.append(report)
        if report.get("ok"):
            # Optional lock equivalence: matching helper digest elevates trust.
            helper = Path(str(report["resolved_path"])) / "scripts" / "evidence_ledger.py"
            helper_digest = _sha256_bytes(helper.read_bytes()) if helper.is_file() else None
            return {
                "status": "available",
                "path": report["resolved_path"],
                "resolved_path": report["resolved_path"],
                "source": source,
                "source_kind": "external",
                "name": report.get("skill_name"),
                "package_name": report.get("package_name"),
                "package_version": report.get("package_version"),
                "package_major": report.get("package_major"),
                "supported_majors": [3],
                "compatible": True,
                "identity_verified": True,
                "component_uri": None,
                "assurance_cap": "limited",
                "ledger_helper_sha256": helper_digest,
                "tried": tried,
            }
        if authoritative:
            return {
                "status": "incompatible",
                "path": report.get("resolved_path") or str(path),
                "source": source,
                "source_kind": "external",
                "package_major": report.get("package_major"),
                "package_version": report.get("package_version"),
                "compatible": False,
                "identity_verified": bool(report.get("identity_ok")),
                "supported_majors": [3],
                "tried": tried,
                "assurance_cap": "experimental",
                "error_code": "COMPONENT_IDENTITY_MISMATCH",
                "issues": [
                    issue(
                        "D_RESEARCH",
                        message=report.get("reason", "configured D Research is unavailable or incompatible"),
                    ).to_dict()
                ],
            }
        if report.get("exists") and report.get("reason") not in {"directory missing"}:
            incompatible = report

    if incompatible is not None:
        return {
            "status": "incompatible",
            "path": incompatible.get("resolved_path") or incompatible.get("path"),
            "source": incompatible.get("source"),
            "source_kind": "external",
            "package_major": incompatible.get("package_major"),
            "package_version": incompatible.get("package_version"),
            "compatible": False,
            "identity_verified": bool(incompatible.get("identity_ok")),
            "supported_majors": [3],
            "tried": tried,
            "assurance_cap": "experimental",
            "issues": [
                issue("D_RESEARCH", message=incompatible.get("reason", "incompatible D Research")).to_dict()
            ],
        }
    return {
        "status": "unavailable",
        "path": None,
        "source": None,
        "source_kind": None,
        "compatible": False,
        "identity_verified": False,
        "tried": tried,
        "assurance_cap": "limited",
        "issues": [
            issue("D_RESEARCH", severity="warning", message="D Research not found; limited mode only").to_dict()
        ],
    }
