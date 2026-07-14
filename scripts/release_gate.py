from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from _lib import skill_root
from aleph import PACKAGE_VERSION, SCHEMA_VERSION


def _run(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-4000:],
    }


def _static_contract(root: Path) -> dict[str, Any]:
    issues: list[str] = []
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    lock_path = root / "package-lock.json"

    if package.get("version") != PACKAGE_VERSION:
        issues.append("package.json version differs from aleph.PACKAGE_VERSION")
    if f'version = "{PACKAGE_VERSION}"' not in pyproject:
        issues.append("pyproject.toml version differs from aleph.PACKAGE_VERSION")
    if SCHEMA_VERSION != "2.0.0":
        issues.append(f"unexpected schema version {SCHEMA_VERSION}")
    if not lock_path.is_file():
        issues.append("package-lock.json missing")
    else:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("version") != PACKAGE_VERSION:
            issues.append("package-lock.json version differs from package version")
        root_package = (lock.get("packages") or {}).get("") or {}
        if root_package.get("version") != PACKAGE_VERSION:
            issues.append("package-lock root package version differs from package version")

    scripts = package.get("scripts") or {}
    self_test = str(scripts.get("self-test", ""))
    if "--generate" in self_test:
        issues.append("self-test must not regenerate checked artifacts")
    if scripts.get("release:check") != "python scripts/release_gate.py":
        issues.append("release:check command missing or changed")

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
    checks: list[dict[str, Any]] = [_static_contract(root)]
    commands = [
        ("skill-package", [python, "scripts/validate_skill_package.py", "."]),
        ("adapter-drift", [python, "scripts/check_adapters.py", "--json"]),
        ("domain-packs", [python, "scripts/validate_domain_packs.py", "--json"]),
        ("unit-and-integration", [python, "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py", "-v"]),
        ("preflight", [python, "scripts/preflight.py", "--json"]),
    ]
    if (root / ".git").is_dir() and shutil.which("git"):
        commands.append(("git-diff-check", ["git", "diff", "--check"]))
    if args.with_dev:
        coverage_data = Path(tempfile.gettempdir()) / f"aleph-release-coverage-{uuid.uuid4().hex}"
        commands.extend(
            [
                ("ruff", [python, "-m", "ruff", "check", "scripts", "tests"]),
                ("mypy", [python, "-m", "mypy", "scripts/aleph"]),
                (
                    "coverage-run",
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
                ),
                (
                    "coverage-report",
                    [python, "-m", "coverage", "report", f"--data-file={coverage_data}"],
                ),
            ]
        )

    try:
        for name, command in commands:
            checks.append(_run(name, command, root))
    finally:
        if coverage_data is not None:
            coverage_data.unlink(missing_ok=True)

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
