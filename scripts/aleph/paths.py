"""Workspace-relative path resolution with escape prevention."""

from __future__ import annotations

import os
from pathlib import Path

from .issues import Issue, issue


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
    if _is_windows_drive(text):
        problems.append(issue("PATH_DRIVE", artifact=artifact, pointer=text, message="drive-letter path refused"))
    if _is_unc(str(raw)):
        problems.append(issue("PATH_UNC", artifact=artifact, pointer=text, message="UNC path refused"))
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        problems.append(issue("PATH_ESCAPE", artifact=artifact, pointer=text, message="parent traversal '..' refused"))
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

    if os.path.normcase(str(dest)) != os.path.normcase(str(destination_absolute)):
        problems.append(
            issue(
                "INSTALL_SOURCE_DEST",
                message="destination contains a symlink or reparse point",
                expected=str(destination_absolute),
                actual=str(dest),
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
        "LICENSE",
        "package.json",
        "pyproject.toml",
        "aleph.config.json",
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
    }
)
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
    # top-level file
    if len(parts) == 1:
        return name in ALLOWLIST_NAMES or suffix in ALLOWLIST_SUFFIXES
    top = parts[0]
    if top not in ALLOWLIST_DIRS:
        return False
    # skip nested caches
    if any(p.startswith(".") for p in parts[1:-1]):
        return False
    if name.endswith(".pyc"):
        return False
    return suffix in ALLOWLIST_SUFFIXES or name in ALLOWLIST_NAMES
