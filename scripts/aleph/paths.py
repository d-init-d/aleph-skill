"""Workspace-relative path resolution with escape prevention."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .issues import Issue, issue

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class PathEscapeError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(i.legacy_string() for i in issues))


def _is_windows_drive(path_str: str) -> bool:
    # C:\... or C:/... or C:
    if len(path_str) >= 2 and path_str[1] == ":":
        return path_str[0].isalpha()
    return False


def _is_unc(path_str: str) -> bool:
    return path_str.startswith("\\\\") or path_str.startswith("//")


def path_contains_link_or_reparse(path: Path) -> bool:
    """Inspect existing path components without treating Windows 8.3 aliases as links."""
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if not parts:
        return False
    current = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        current /= part
        if not os.path.lexists(current):
            break
        try:
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
        except OSError:
            return True
        linked = current.is_symlink() or os.path.islink(current) or bool(
            attributes & FILE_ATTRIBUTE_REPARSE_POINT
        )
        if linked and index == 1 and os.name != "nt":
            # macOS exposes stable administrator-owned root aliases such as
            # /var -> /private/var. Continue inspecting every lower component.
            continue
        if linked:
            return True
    return False


def validate_relative_artifact_path(raw: str, *, artifact: str = "artifact_path") -> list[Issue]:
    """Validate that a declared path is a safe workspace-relative string (no resolve yet)."""
    problems: list[Issue] = []
    if raw is None or not str(raw).strip():
        problems.append(
            issue("MISSING_FIELD", artifact=artifact, pointer=artifact, message="path must be non-empty")
        )
        return problems
    text = str(raw).strip().replace("\\", "/")
    if "\x00" in text:
        problems.append(issue("PATH_ESCAPE", artifact=artifact, pointer=text, message="NUL byte in path"))
    if text.startswith("/") or text.startswith("~"):
        problems.append(issue("PATH_ABSOLUTE", artifact=artifact, pointer=text, message="absolute path refused"))
    is_windows_drive = _is_windows_drive(text)
    if is_windows_drive:
        problems.append(issue("PATH_DRIVE", artifact=artifact, pointer=text, message="drive-letter path refused"))
    if _is_unc(str(raw)):
        problems.append(issue("PATH_UNC", artifact=artifact, pointer=text, message="UNC path refused"))
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        problems.append(issue("PATH_ESCAPE", artifact=artifact, pointer=text, message="parent traversal '..' refused"))
    if not is_windows_drive and any(":" in part for part in parts):
        problems.append(
            issue(
                "PATH_ESCAPE",
                artifact=artifact,
                pointer=text,
                message="colon path components and Windows alternate data streams are refused",
            )
        )
    return problems


def resolve_in_workspace(
    workspace: Path,
    relative: str,
    *,
    must_exist: bool = False,
    require_file: bool = True,
    follow_symlinks: bool = False,
) -> tuple[Path | None, list[Issue]]:
    """
    Resolve a relative artifact path strictly inside workspace.
    Returns (resolved_path or None, issues).
    """
    problems = validate_relative_artifact_path(str(relative))
    if problems:
        return None, problems

    workspace = workspace.resolve()
    # Normalize separators but do not allow absolute components
    cleaned = str(relative).strip().replace("\\", "/")
    lexical_candidate = workspace / cleaned
    candidate = lexical_candidate.resolve(strict=False)

    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None, [
            issue(
                "PATH_ESCAPE",
                artifact="path",
                pointer=str(relative),
                message=f"resolved path escapes workspace: {candidate}",
            )
        ]

    lexical_real = Path(os.path.realpath(lexical_candidate))
    lexical_absolute = Path(os.path.abspath(lexical_candidate))
    if not follow_symlinks and os.path.normcase(str(lexical_real)) != os.path.normcase(
        str(lexical_absolute)
    ):
        return None, [
            issue(
                "PATH_SYMLINK",
                artifact=str(relative),
                pointer=str(relative),
                message="symlink or reparse-point artifacts are refused",
            )
        ]

    if must_exist and not candidate.exists():
        return None, [
            issue("MISSING_ARTIFACT", artifact=str(relative), pointer=str(relative), message="file does not exist")
        ]

    if candidate.exists():
        if not follow_symlinks and candidate.is_symlink():
            # Also check if any parent is a symlink reparse pointing outside
            return None, [
                issue("PATH_SYMLINK", artifact=str(relative), pointer=str(candidate), message="symlink artifacts refused")
            ]
        # Detect junction/symlink parents that escape
        try:
            real = candidate.resolve(strict=True)
            real.relative_to(workspace.resolve())
            # On Windows, compare with os.path.realpath for reparse points
            real2 = Path(os.path.realpath(candidate))
            try:
                real2.relative_to(Path(os.path.realpath(workspace)))
            except ValueError:
                return None, [
                    issue(
                        "PATH_SYMLINK",
                        artifact=str(relative),
                        pointer=str(relative),
                        message="reparse/symlink escape refused",
                    )
                ]
        except (OSError, ValueError) as exc:
            return None, [
                issue("PATH_ESCAPE", artifact=str(relative), pointer=str(relative), message=str(exc))
            ]
        if require_file and not candidate.is_file():
            return None, [
                issue("PATH_NOT_FILE", artifact=str(relative), pointer=str(candidate), message="not a regular file")
            ]

    return candidate, []


def assert_install_paths_safe(source: Path, destination: Path) -> list[Issue]:
    """Refuse source==dest, nested source/dest relationships."""
    problems: list[Issue] = []
    try:
        src = source.resolve()
        destination_absolute = Path(os.path.abspath(destination))
        dest = Path(os.path.realpath(destination_absolute))
    except OSError as exc:
        return [issue("INSTALL_SOURCE_DEST", message=str(exc))]

    if path_contains_link_or_reparse(destination_absolute):
        problems.append(
            issue(
                "INSTALL_SOURCE_DEST",
                message="destination contains a symlink or reparse point",
                actual=str(destination_absolute),
            )
        )
        return problems

    if src == dest:
        problems.append(
            issue(
                "INSTALL_SOURCE_DEST",
                message="source and destination resolve to the same path",
                expected=str(src),
                actual=str(dest),
            )
        )
        return problems

    try:
        dest.relative_to(src)
        problems.append(
            issue(
                "INSTALL_NESTED",
                message="destination is inside source tree",
                expected="sibling or external dest",
                actual=str(dest),
            )
        )
    except ValueError:
        pass

    try:
        src.relative_to(dest)
        problems.append(
            issue(
                "INSTALL_NESTED",
                message="source is inside destination tree",
                expected="source outside dest",
                actual=str(src),
            )
        )
    except ValueError:
        pass

    return problems


def output_alias_issues(output: Path, inputs: list[Path]) -> list[Issue]:
    """Refuse an output that aliases any input lexically or through a reparse path."""
    output_absolute = os.path.normcase(os.path.abspath(output))
    output_real = os.path.normcase(os.path.realpath(output))
    for input_path in inputs:
        input_absolute = os.path.normcase(os.path.abspath(input_path))
        input_real = os.path.normcase(os.path.realpath(input_path))
        if output_absolute in {input_absolute, input_real} or output_real in {
            input_absolute,
            input_real,
        }:
            return [
                issue(
                    "PATH_ALIAS",
                    pointer=str(output),
                    expected="output path distinct from all inputs",
                    actual=str(input_path),
                    message="refusing to overwrite an input artifact",
                )
            ]
    return []


# Distribution allowlist for installer copy (names / suffixes)
ALLOWLIST_NAMES = frozenset(
    {
        "SKILL.md",
        "AGENTS.md",
        "README.md",
        "README.vi.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "uv.lock",
        "aleph.config.json",
        "component-lock.json",
        "THIRD_PARTY_NOTICES.md",
        "research.config.example.json",
    }
)
ALLOWLIST_DIRS = frozenset(
    {
        "scripts",
        "templates",
        "references",
        "adapters",
        "agents",
        "examples",
        "schemas",
        "packs",
        "tests",
        "components",
        "docs",
        ".github",
    }
)
ALLOWLIST_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".json",
        ".jsonl",
        ".csv",
        ".yaml",
        ".yml",
        ".toml",
        ".txt",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".mjs",
        ".bib",
        ".pdf",
    }
)
# Suffixes allowed only under components/ (never widen global binary allowlist).
COMPONENT_ONLY_SUFFIXES = frozenset({".mjs", ".bib", ".pdf"})
COMPONENT_ALLOWED_DOTFILES = frozenset({".npmignore"})
BLOCKED_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".coverage",
        "coverage.json",
        "coverage.xml",
        ".venv",
        "venv",
        ".DS_Store",
        "Thumbs.db",
    }
)
BLOCKED_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".log", ".key", ".pem", ".p12"})


def is_distribution_path(rel_posix: str) -> bool:
    """Return True if relative path may be copied by the installer."""
    parts = [p for p in rel_posix.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(p in BLOCKED_NAMES for p in parts):
        return False
    if any(p.endswith(".egg-info") for p in parts[:-1]):
        return False
    name = parts[-1]
    if name in BLOCKED_NAMES:
        return False
    if name.startswith(".env"):
        return False
    suffix = Path(name).suffix.lower()
    if suffix in BLOCKED_SUFFIXES:
        return False
    under_component = parts[0] == "components" and len(parts) >= 2
    # Component-only suffixes stay scoped to locked components tree.
    if suffix in COMPONENT_ONLY_SUFFIXES and not under_component and len(parts) > 1:
        # Still allow .mjs under top-level scripts for Aleph gateway if ever added;
        # plan requires .mjs/.bib/.pdf only inside components/ for vendored assets.
        if parts[0] != "scripts":
            return False
    # top-level file
    if len(parts) == 1:
        return name in ALLOWLIST_NAMES or suffix in ALLOWLIST_SUFFIXES
    top = parts[0]
    if top not in ALLOWLIST_DIRS:
        return False
    # skip nested caches
    component_archive = (
        len(parts) >= 5
        and parts[:4] == ["components", "d-research", "docs", ".archive"]
    )
    if any(p.startswith(".") for p in parts[1:-1]) and not component_archive:
        return False
    if name.endswith(".pyc"):
        return False
    if under_component and name in ALLOWLIST_NAMES:
        return True
    if under_component and name in COMPONENT_ALLOWED_DOTFILES:
        return True
    return suffix in ALLOWLIST_SUFFIXES or name in ALLOWLIST_NAMES


def resolve_trace_artifact(
    manifest: dict[str, Any] | None,
    formula_version: str | None = None,
) -> tuple[str, str]:
    """Resolve the active trace relative path and kind ('json' or 'jsonl')."""
    artifact_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    if isinstance(artifact_paths, dict):
        exec_rel = artifact_paths.get("execution_trace")
        if isinstance(exec_rel, str) and exec_rel.strip():
            norm = exec_rel.strip().replace("\\", "/")
            kind = "jsonl" if norm.lower().endswith(".jsonl") else "json"
            return norm, kind
        prop_rel = artifact_paths.get("propagation_trace")
        if isinstance(prop_rel, str) and prop_rel.strip():
            norm = prop_rel.strip().replace("\\", "/")
            kind = "json" if norm.lower().endswith(".json") else "jsonl"
            return norm, kind
    sim_mode = manifest.get("simulation_mode") if isinstance(manifest, dict) else None
    if formula_version in {"2.1.0", "2.2.0"} or sim_mode in {"deterministic", "monte_carlo"}:
        return "execution-trace.json", "json"
    return "propagation-trace.jsonl", "jsonl"


def load_trace_rows(path: Path, kind: str | None = None) -> list[dict[str, Any]]:
    """Load step/propagation rows from either a JSON execution trace or JSONL trace."""
    import json

    resolved_kind = kind or ("jsonl" if path.suffix.lower() == ".jsonl" else "json")
    raw = path.read_text(encoding="utf-8")
    if resolved_kind == "json":
        payload = json.loads(raw)
        if isinstance(payload, dict):
            rows = payload.get("steps", [])
        elif isinstance(payload, list):
            rows = payload
        else:
            raise ValueError(f"trace payload in {path.name} must be an object or array")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"trace rows in {path.name} must be objects")
        return [dict(row) for row in rows]
    rows_out: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError(f"trace row {line_no} in {path.name} must be an object")
        rows_out.append(item)
    return rows_out
