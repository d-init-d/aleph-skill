from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SKILL_NAME = "aleph-timeline-simulator"


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
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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
    import yaml  # type: ignore[import-not-found]

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def copy_skill_tree(src: Path, dest: Path, force: bool = False) -> None:
    if dest.exists():
        if not force:
            raise FileExistsError(f"Destination already exists: {dest}")
        shutil.rmtree(dest)

    def ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
        return {name for name in names if name in ignored}

    shutil.copytree(src, dest, ignore=ignore)


def common_aleph_candidates() -> list[Path]:
    return [
        Path(os.environ.get("ALEPH_REPO", "")) if os.environ.get("ALEPH_REPO") else None,
        skill_root().parent / "Aleph",
        skill_root().parent.parent / "Aleph",
        Path(r"D:\Downloads\aleph-qweb 3.7\Aleph"),
    ]  # type: ignore[return-value]


def common_d_research_candidates() -> list[Path]:
    return [
        Path(os.environ.get("D_RESEARCH_SKILL", "")) if os.environ.get("D_RESEARCH_SKILL") else None,
        Path.home() / ".codex" / "skills" / "d-research",
        Path.home() / ".agents" / "skills" / "d-research",
        Path(r"D:\Downloads\aleph-qweb 3.7\d-research-skill"),
    ]  # type: ignore[return-value]


def first_existing(candidates: list[Path | None]) -> Path | None:
    for candidate in candidates:
        if candidate and str(candidate) and candidate.exists():
            return candidate
    return None


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def exit_from_errors(errors: list[str]) -> None:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
