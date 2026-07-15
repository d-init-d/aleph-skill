from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from _lib import ArtifactLoadError, load_json, skill_root
from aleph import PACKAGE_VERSION, SCHEMA_VERSION


def _run(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "name": name,
            "command": command,
            "returncode": None,
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
        }
    reported_status: str | None = None
    json_error: str | None = None
    stripped_stdout = completed.stdout.strip()
    if stripped_stdout.startswith("{"):
        try:
            payload = json.loads(stripped_stdout)
            if isinstance(payload, dict) and isinstance(payload.get("status"), str):
                reported_status = str(payload["status"])
        except json.JSONDecodeError as exc:
            json_error = str(exc)
    result: dict[str, Any] = {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-4000:],
    }
    if reported_status is not None:
        result["reported_status"] = reported_status
    if json_error is not None:
        result["json_error"] = json_error
    return result


def _require_reported_status(
    check: dict[str, Any],
    allowed: frozenset[str],
) -> dict[str, Any]:
    """Fail a successful JSON command when its semantic status is unexpected."""

    reported = check.get("reported_status")
    check["expected_statuses"] = sorted(allowed)
    check["ok"] = bool(check.get("ok")) and reported in allowed
    if reported not in allowed:
        check["status_error"] = (
            f"expected JSON status in {sorted(allowed)}, got {reported!r}"
        )
    return check


def _extract_release_archive(archive: Path, destination: Path) -> Path:
    """Extract the self-built ZIP after refusing unsafe or non-Aleph members."""

    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if not members:
            raise ValueError("release archive is empty")
        for member in members:
            name = member.filename
            if name in seen:
                raise ValueError(f"duplicate release archive member: {name}")
            seen.add(name)
            normalized = name.replace("\\", "/")
            parts = normalized.split("/")
            if (
                name != normalized
                or normalized.startswith("/")
                or any(part in {"", ".", ".."} for part in parts)
                or any(":" in part for part in parts)
                or parts[0] != "aleph-skill"
            ):
                raise ValueError(f"unsafe release archive member: {name}")
            unix_mode = member.external_attr >> 16
            if stat.S_ISLNK(unix_mode) or member.is_dir():
                raise ValueError(f"non-regular release archive member: {name}")
        bundle.extractall(destination)
    extracted = destination / "aleph-skill"
    if not extracted.is_dir() or not (extracted / "SKILL.md").is_file():
        raise ValueError("release archive lacks aleph-skill/SKILL.md")
    return extracted


def _release_artifact_checks(root: Path, python: str) -> list[dict[str, Any]]:
    """Build and validate the exact ZIP as a relocated standalone distribution."""

    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aleph-release-gate-") as temporary:
        temporary_root = Path(temporary)
        assets = temporary_root / "assets"
        build = _run(
            "release-zip-build",
            [python, "-B", "scripts/build_release_assets.py", "--output-dir", str(assets)],
            root,
        )
        checks.append(_require_reported_status(build, frozenset({"pass"})))
        if not build["ok"]:
            return checks

        archive = assets / f"aleph-skill-v{PACKAGE_VERSION}.zip"
        extraction: dict[str, Any] = {
            "name": "release-zip-extract",
            "archive": str(archive),
            "ok": False,
        }
        try:
            extracted = _extract_release_archive(archive, temporary_root / "extracted")
            extraction["root"] = str(extracted)
            extraction["ok"] = True
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            extraction["error"] = str(exc)
            checks.append(extraction)
            return checks
        checks.append(extraction)

        extracted_commands: list[
            tuple[str, list[str], Path, frozenset[str] | None]
        ] = [
            (
                "release-zip-preflight",
                [python, "-B", "scripts/preflight.py", "--json"],
                extracted,
                frozenset({"pass"}),
            ),
            (
                "release-zip-component-lock",
                [python, "-B", "scripts/lock_bundled_component.py"],
                extracted,
                frozenset({"pass"}),
            ),
            (
                "release-zip-component-package",
                ["node", "scripts/package_manifest_check.mjs"],
                extracted / "components" / "d-research",
                None,
            ),
            (
                "release-zip-skill-package",
                [python, "-B", "scripts/validate_skill_package.py", "."],
                extracted,
                None,
            ),
        ]
        for name, command, cwd, statuses in extracted_commands:
            check = _run(name, command, cwd)
            checks.append(
                _require_reported_status(check, statuses)
                if statuses is not None
                else check
            )
    return checks


def _static_contract(root: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        package = load_json(root / "package.json")
    except ArtifactLoadError as exc:
        package = {}
        issues.append(str(exc))
    if not isinstance(package, dict):
        package = {}
        issues.append("package.json must be an object")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    lock_path = root / "package-lock.json"
    uv_lock_path = root / "uv.lock"

    if package.get("version") != PACKAGE_VERSION:
        issues.append("package.json version differs from aleph.PACKAGE_VERSION")
    if f'version = "{PACKAGE_VERSION}"' not in pyproject:
        issues.append("pyproject.toml version differs from aleph.PACKAGE_VERSION")
    if SCHEMA_VERSION != "2.0.0":
        issues.append(f"unexpected schema version {SCHEMA_VERSION}")
    if not lock_path.is_file():
        issues.append("package-lock.json missing")
    else:
        try:
            lock = load_json(lock_path)
        except ArtifactLoadError as exc:
            lock = {}
            issues.append(str(exc))
        if not isinstance(lock, dict):
            lock = {}
            issues.append("package-lock.json must be an object")
        if lock.get("version") != PACKAGE_VERSION:
            issues.append("package-lock.json version differs from package version")
        root_package = (lock.get("packages") or {}).get("") or {}
        if root_package.get("version") != PACKAGE_VERSION:
            issues.append("package-lock root package version differs from package version")
    if not uv_lock_path.is_file():
        issues.append("uv.lock missing")
    else:
        uv_lock = uv_lock_path.read_text(encoding="utf-8")
        locked_project = re.search(
            r'^\[\[package\]\]\s*\nname = "aleph-skill"\s*\nversion = "([^"]+)"',
            uv_lock,
            re.MULTILINE,
        )
        if locked_project is None or locked_project.group(1) != PACKAGE_VERSION:
            issues.append("uv.lock project version differs from package version")

    scripts = package.get("scripts") or {}
    self_test = str(scripts.get("self-test", ""))
    if "--generate" in self_test:
        issues.append("self-test must not regenerate checked artifacts")
    if scripts.get("release:check") != "python scripts/release_gate.py":
        issues.append("release:check command missing or changed")
    if scripts.get("release:build") != "python scripts/build_release_assets.py":
        issues.append("release:build command missing or changed")

    syntax_errors: list[str] = []
    for path in sorted((root / "scripts").rglob("*.py")) + sorted((root / "tests").rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            syntax_errors.append(f"{path.relative_to(root).as_posix()}: {exc}")
    issues.extend(syntax_errors)
    return {"name": "static-contract", "ok": not issues, "issues": issues}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run non-mutating Aleph release gates.")
    parser.add_argument("--json", action="store_true", help="Emit the complete machine-readable report.")
    parser.add_argument(
        "--with-dev",
        action="store_true",
        help="Also run Ruff, mypy, and coverage; requires the dev extra.",
    )
    args = parser.parse_args()

    root = skill_root()
    python = sys.executable
    coverage_data: Path | None = None
    dev_cache: tempfile.TemporaryDirectory[str] | None = None
    checks: list[dict[str, Any]] = [_static_contract(root)]
    commands: list[tuple[str, list[str], frozenset[str] | None]] = [
        ("skill-package", [python, "scripts/validate_skill_package.py", "."], None),
        ("adapter-drift", [python, "scripts/check_adapters.py", "--json"], None),
        ("domain-packs", [python, "scripts/validate_domain_packs.py", "--json"], None),
        (
            "component-lock",
            [python, "-B", "scripts/lock_bundled_component.py"],
            frozenset({"pass"}),
        ),
        (
            "research-self-test",
            [python, "-B", "scripts/research_gateway.py", "research:self-test", "--json"],
            frozenset({"ok", "degraded"}),
        ),
        (
            "research-package-check",
            ["node", "scripts/package_manifest_check.mjs"],
            None,
        ),
        (
            "research-acceptance",
            [python, "-B", "scripts/research_gateway.py", "research:acceptance", "--json"],
            frozenset({"ok", "degraded"}),
        ),
    ]
    if args.with_dev:
        dev_cache = tempfile.TemporaryDirectory(prefix="aleph-release-dev-")
        mypy_cache = Path(dev_cache.name) / "mypy"
        coverage_data = Path(tempfile.gettempdir()) / f"aleph-release-coverage-{uuid.uuid4().hex}"
        commands.extend(
            [
                (
                    "ruff",
                    [python, "-m", "ruff", "check", "--no-cache", "scripts", "tests"],
                    None,
                ),
                (
                    "mypy-strict",
                    [
                        python,
                        "-m",
                        "mypy",
                        "--strict",
                        f"--cache-dir={mypy_cache}",
                        "scripts",
                    ],
                    None,
                ),
                (
                    "unit-and-integration-with-coverage",
                    [
                        python,
                        "-m",
                        "coverage",
                        "run",
                        f"--data-file={coverage_data}",
                        "--branch",
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-p",
                        "test*.py",
                    ],
                    None,
                ),
            ]
        )
    else:
        commands.append(
            (
                "unit-and-integration",
                [
                    python,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test*.py",
                    "-v",
                ],
                None,
            )
        )

    commands.extend(
        [
            (
                "preflight",
                [python, "scripts/preflight.py", "--json"],
                frozenset({"pass"}),
            ),
            (
                "lifecycle-acceptance",
                [
                    python,
                    "scripts/acceptance.py",
                    "--skip-unit-tests",
                ],
                None,
            ),
        ]
    )
    if coverage_data is not None:
        commands.append(
            (
                "coverage-report",
                [python, "-m", "coverage", "report", f"--data-file={coverage_data}"],
                None,
            )
        )
    if (root / ".git").exists() and shutil.which("git"):
        commands.append(("git-diff-check", ["git", "diff", "--check"], None))

    try:
        for name, command, statuses in commands:
            cwd = root / "components" / "d-research" if name == "research-package-check" else root
            check = _run(name, command, cwd)
            checks.append(
                _require_reported_status(check, statuses)
                if statuses is not None
                else check
            )
        checks.extend(_release_artifact_checks(root, python))
    finally:
        if coverage_data is not None:
            coverage_data.unlink(missing_ok=True)
        if dev_cache is not None:
            dev_cache.cleanup()

    ok = all(check.get("ok") is True for check in checks)
    report = {
        "ok": ok,
        "package_version": PACKAGE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "checks": checks,
    }
    if args.json or not ok:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for check in checks:
            print(f"{check['name']}: {'pass' if check.get('ok') else 'fail'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
