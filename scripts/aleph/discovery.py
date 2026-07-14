"""Strict D Research discovery without machine-specific paths."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .io import DEFAULT_MAX_FILE_BYTES, load_json_secure
from .issues import issue

SUPPORTED_MAJORS = frozenset({3})
EXPECTED_SKILL_NAME = "d-research"
EXPECTED_PACKAGE_NAMES = frozenset({"d-research-skill-tools", "d-research"})


def _parse_frontmatter_name(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    match = re.match(r"^---\s*\n(.*?)\n---(?:\s*\n|$)", text, flags=re.DOTALL)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.strip().startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _candidate_report(source: str, path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"source": source, "path": str(path), "exists": path.is_dir()}
    if not path.is_dir():
        entry.update({"ok": False, "compatible": False, "reason": "directory missing"})
        return entry
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        entry.update({"ok": False, "compatible": False, "reason": str(exc)})
        return entry
    skill_md = resolved / "SKILL.md"
    package_json = resolved / "package.json"
    ledger_helper = resolved / "scripts" / "evidence_ledger.py"
    if not skill_md.is_file() or not package_json.is_file() or not ledger_helper.is_file():
        entry.update({"ok": False, "compatible": False, "reason": "missing identity/ledger contract files"})
        return entry
    try:
        if skill_md.stat().st_size > DEFAULT_MAX_FILE_BYTES:
            raise ValueError("SKILL.md exceeds the file-size limit")
        skill_name = _parse_frontmatter_name(skill_md.read_text(encoding="utf-8"))
        package, package_issues = load_json_secure(package_json)
        if package_issues:
            raise ValueError("; ".join(value.legacy_string() for value in package_issues))
        if not isinstance(package, dict):
            raise ValueError("package.json must be an object")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        entry.update({"ok": False, "compatible": False, "reason": str(exc)})
        return entry
    package_name = package.get("name")
    version = package.get("version")
    try:
        major = int(str(version).split(".", 1)[0])
    except (TypeError, ValueError):
        major = None
    identity_ok = skill_name == EXPECTED_SKILL_NAME and package_name in EXPECTED_PACKAGE_NAMES
    compatible = identity_ok and major in SUPPORTED_MAJORS
    entry.update(
        {
            "resolved_path": str(resolved),
            "skill_name": skill_name,
            "package_name": package_name,
            "package_version": version,
            "package_major": major,
            "identity_ok": identity_ok,
            "compatible": compatible,
            "ok": compatible,
        }
    )
    if not identity_ok:
        entry["reason"] = "D Research identity mismatch"
    elif major not in SUPPORTED_MAJORS:
        entry["reason"] = f"unsupported D Research major {major}"
    return entry


def discover_d_research(
    *,
    explicit: str | Path | None = None,
    capability_file: Path | None = None,
    conventional_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Discover an exact, compatible D Research 3.x installation.

    An explicit/env/capability candidate is authoritative: if it exists but is
    incompatible, discovery returns ``incompatible`` instead of silently using
    another installation.  Conventional candidates are searched until a valid
    3.x identity is found.
    """
    candidates: list[tuple[str, Path, bool]] = []
    if explicit is not None:
        candidates.append(("explicit", Path(explicit).expanduser(), True))
    env = os.environ.get("D_RESEARCH_SKILL", "").strip()
    if env:
        candidates.append(("env:D_RESEARCH_SKILL", Path(env).expanduser(), True))
    if capability_file is not None and capability_file.is_file():
        try:
            data, capability_issues = load_json_secure(capability_file)
            if capability_issues or not isinstance(data, dict):
                raise ValueError("invalid capability file")
            configured = data.get("d_research_skill") or (data.get("d_research") or {}).get("path")
            if configured:
                candidates.append(("capability_file", Path(configured).expanduser(), True))
        except (OSError, UnicodeDecodeError, ValueError, AttributeError):
            candidates.append(("capability_file", Path("__invalid_capability_file__"), True))
    roots = conventional_roots or [
        Path.home() / ".codex" / "skills" / "d-research",
        Path.home() / ".agents" / "skills" / "d-research",
        Path.home() / ".claude" / "skills" / "d-research",
        Path.home() / ".config" / "opencode" / "skills" / "d-research",
        Path.home() / ".grok" / "skills" / "d-research",
    ]
    candidates.extend(("conventional", root, False) for root in roots)

    tried: list[dict[str, Any]] = []
    incompatible: dict[str, Any] | None = None
    for source, path, authoritative in candidates:
        entry = _candidate_report(source, path)
        tried.append(entry)
        if entry.get("ok"):
            return {
                "status": "available",
                "path": entry["resolved_path"],
                "source": source,
                "name": entry["skill_name"],
                "package_name": entry["package_name"],
                "package_version": entry["package_version"],
                "package_major": entry["package_major"],
                "supported_majors": [3],
                "compatible": True,
                "identity_verified": True,
                "tried": tried,
            }
        if authoritative:
            return {
                "status": "incompatible",
                "path": entry.get("resolved_path") or str(path),
                "source": source,
                "package_major": entry.get("package_major"),
                "package_version": entry.get("package_version"),
                "compatible": False,
                "identity_verified": bool(entry.get("identity_ok")),
                "supported_majors": [3],
                "tried": tried,
                "assurance_cap": "experimental",
                "issues": [issue("D_RESEARCH", message=entry.get("reason", "configured D Research is unavailable or incompatible")).to_dict()],
            }
        if entry.get("exists") and entry.get("reason") not in {"directory missing"}:
            incompatible = entry

    if incompatible is not None:
        return {
            "status": "incompatible",
            "path": incompatible.get("resolved_path") or incompatible.get("path"),
            "source": incompatible.get("source"),
            "package_major": incompatible.get("package_major"),
            "package_version": incompatible.get("package_version"),
            "compatible": False,
            "identity_verified": bool(incompatible.get("identity_ok")),
            "supported_majors": [3],
            "tried": tried,
            "assurance_cap": "experimental",
            "issues": [issue("D_RESEARCH", message=incompatible.get("reason", "incompatible D Research")).to_dict()],
        }
    return {
        "status": "unavailable",
        "path": None,
        "source": None,
        "compatible": False,
        "identity_verified": False,
        "tried": tried,
        "assurance_cap": "limited",
        "issues": [issue("D_RESEARCH", severity="warning", message="D Research not found; limited mode only").to_dict()],
    }
