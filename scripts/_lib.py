from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure aleph package is importable when scripts run as files
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from aleph.discovery import discover_d_research  # noqa: E402
from aleph.installer import install as safe_install  # noqa: E402
from aleph.io import load_json_secure, stream_csv_rows, write_json_atomic  # noqa: E402

SKILL_NAME = "aleph-skill"


class ArtifactLoadError(ValueError):
    """A bounded artifact read failed strict JSON validation."""


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def load_json(path: Path) -> Any:
    data, problems = load_json_secure(path)
    if problems:
        details = "; ".join(problem.legacy_string() for problem in problems)
        raise ArtifactLoadError(f"invalid JSON artifact {path}: {details}")
    return data


def write_json(path: Path, data: Any) -> None:
    write_json_atomic(path, data)


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    rows, problems = stream_csv_rows(path)
    if problems:
        details = "; ".join(problem.legacy_string() for problem in problems)
        raise ArtifactLoadError(f"invalid CSV artifact {path}: {details}")
    return rows


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 20) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "command": args,
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "command not found", "command": args}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": "command timed out",
            "command": args,
        }


def is_skill_name(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value))


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("SKILL.md frontmatter is not closed")
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip("\r\n")
    data: dict[str, str] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith(" ") and current_key:
            data[current_key] = f"{data[current_key]} {line.strip()}"
            continue
        if ":" not in line:
            raise ValueError(f"Unsupported frontmatter line: {line}")
        key, value = line.split(":", 1)
        current_key = key.strip()
        data[current_key] = value.strip().strip('"').strip("'")
    return data, body


def load_optional_yaml(path: Path) -> Any:
    import importlib.util

    if importlib.util.find_spec("yaml") is None:
        raise RuntimeError("PyYAML is not installed; use JSON or install PyYAML explicitly")
    import yaml  # type: ignore[import-untyped]

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def copy_skill_tree(src: Path, dest: Path, force: bool = False) -> dict[str, Any]:
    """Allowlist-based install; refuses source==dest and nested paths."""
    return safe_install(src, dest, mode="copy", force=force)


def common_d_research_candidates() -> list[Path | None]:
    """Deprecated helper — use discover_d_research(). Kept for compatibility without hardcoded paths."""
    result = discover_d_research()
    if result.get("path"):
        return [Path(result["path"])]
    return []


def first_existing(candidates: list[Path | None]) -> Path | None:
    for candidate in candidates:
        if candidate and str(candidate) and candidate.exists():
            return candidate
    return None


def print_json(data: Any) -> None:
    # Machine-readable on stdout
    print(json.dumps(data, indent=2, ensure_ascii=False))


def exit_from_errors(errors: list[str], code: int = 1) -> None:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(code)


def emit_result(data: Any, *, json_mode: bool = False, exit_code: int = 0) -> None:
    if json_mode:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    if exit_code:
        raise SystemExit(exit_code)
