from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _lib import load_json, skill_root
from aleph import EXIT_OK, EXIT_SEMANTIC


def run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)
    return {
        "cmd": cmd,
        "returncode": completed.returncode,
        "stdout_full": completed.stdout,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-2000:],
    }


def append_adversarial_results(
    workspace: Path,
    *,
    root: Path,
    scripts: Path,
    results: list[dict[str, Any]],
) -> None:
    if not workspace.is_dir():
        results.append(
            {
                "cmd": ["fixture", str(workspace)],
                "returncode": EXIT_SEMANTIC,
                "stdout_full": "",
                "stdout": "",
                "stderr": f"adversarial fixture directory not found: {workspace}",
                "stage": "adversarial-fixture",
                "pass": False,
            }
        )
        return

    validation = run(
        [
            sys.executable,
            str(scripts / "validate_simulation_artifacts.py"),
            "--workspace",
            str(workspace),
            "--mode",
            "final",
        ],
        root,
    )
    validation["expect_nonzero"] = True
    try:
        validation_data = json.loads(validation["stdout_full"])
    except json.JSONDecodeError:
        validation_data = None
    validation_codes = (
        validation_data.get("error_codes") if isinstance(validation_data, dict) else None
    )
    validation["pass"] = (
        validation["returncode"] == EXIT_SEMANTIC
        and isinstance(validation_data, dict)
        and validation_data.get("status") == "fail"
        and isinstance(validation_codes, list)
        and "UNKNOWN_FIELD" in validation_codes
    )
    results.append(validation)

    quality = run(
        [
            sys.executable,
            str(scripts / "evaluate_simulation_quality.py"),
            "--workspace",
            str(workspace),
            "--json",
        ],
        root,
    )
    try:
        data = json.loads(quality["stdout_full"])
        quality["pass"] = (
            quality["returncode"] == EXIT_OK
            and isinstance(data, dict)
            and data.get("assurance_tier") not in {"verified", "calibrated"}
            and data.get("validation_status") != "pass"
        )
    except json.JSONDecodeError:
        quality["pass"] = False
    results.append(quality)


def derive_adversarial_fixture(source: Path, destination: Path) -> Path:
    """Create a disposable, deterministic invalid workspace from a valid fixture."""
    if not source.is_dir():
        return destination
    shutil.copytree(source, destination)
    manifest_path = destination / "simulation-manifest.json"
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("valid fixture manifest must be an object")
    manifest["forged_release_claim"] = "verified"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Adversarial acceptance matrix.")
    parser.add_argument("--adversarial", help="Path to adversarial workspace")
    parser.add_argument(
        "--skip-unit-tests",
        action="store_true",
        help="Skip the unit suite when the caller has already run it.",
    )
    parser.add_argument(
        "--skip-component-checks",
        action="store_true",
        help="Skip pack and adapter checks when the caller has already run them.",
    )
    args = parser.parse_args()
    root = skill_root()
    scripts = root / "scripts"
    results: list[dict[str, Any]] = []

    # Unit tests
    if not args.skip_unit_tests:
        results.append(
            run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                root,
            )
        )

    # Adversarial validation must fail without modifying a committed fixture.
    if args.adversarial:
        append_adversarial_results(
            Path(args.adversarial).expanduser().resolve(),
            root=root,
            scripts=scripts,
            results=results,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="aleph-adversarial-") as temporary:
            derived = derive_adversarial_fixture(
                root / "tests" / "fixtures" / "schema-2.0-valid",
                Path(temporary) / "adversarial",
            )
            append_adversarial_results(
                derived,
                root=root,
                scripts=scripts,
                results=results,
            )

    # Packs and adapters
    if not args.skip_component_checks:
        results.append(run([sys.executable, str(scripts / "validate_domain_packs.py")], root))
        results.append(run([sys.executable, str(scripts / "check_adapters.py")], root))

    # Portable init smoke and a complete compile -> run -> replay -> sensitivity
    # -> hindcast -> finalize -> strict revalidation lifecycle.
    with tempfile.TemporaryDirectory(prefix="aleph-acceptance-") as temporary:
        temporary_root = Path(temporary)
        init_result = run(
            [
                sys.executable,
                str(scripts / "init_simulation_workspace.py"),
                "--slug",
                "portable-smoke",
                "--change-point",
                "Acceptance intervention",
                "--time",
                "2026-01-01",
                "--observation-cutoff",
                "2026-06-01",
                "--horizon",
                "P1Y",
                "--out-dir",
                str(temporary_root / "initialized"),
            ],
            root,
        )
        initialized = temporary_root / "initialized" / "portable-smoke"
        init_result["pass"] = init_result["returncode"] == 0 and all(
            (initialized / name).is_file()
            for name in (
                "simulation-manifest.json",
                "nodes.json",
                "edges.json",
                "actors.json",
                "branch-ledger.json",
            )
        )
        init_result["stage"] = "portable-init"
        results.append(init_result)
        init_validation = run(
            [
                sys.executable,
                str(scripts / "validate_simulation_artifacts.py"),
                "--workspace",
                str(initialized),
                "--mode",
                "draft",
            ],
            root,
        )
        init_validation["stage"] = "portable-init-draft-validation"
        init_validation["pass"] = (
            bool(init_result["pass"])
            and init_validation["returncode"] == EXIT_OK
        )
        results.append(init_validation)

        lifecycle = temporary_root / "lifecycle"
        shutil.copytree(root / "tests" / "fixtures" / "schema-2.0-valid", lifecycle)
        sensitivity = {
            "method": "morris",
            "ticks": 1,
            "trajectories": 4,
            "levels": 4,
            "output": {"variable": "factor:output-gap"},
            "parameters": [
                {
                    "id": "parameter:rate-pass-through",
                    "edge_id": "causal:rate-to-gap",
                    "min": 0.1,
                    "max": 0.8,
                    "baseline": 0.4,
                }
            ],
        }
        (lifecycle / "sensitivity-config.json").write_text(
            json.dumps(sensitivity, indent=2) + "\n", encoding="utf-8"
        )
        lifecycle_commands = [
            ("compile", [sys.executable, str(scripts / "compile_model.py"), "--workspace", str(lifecycle)]),
            (
                "deterministic-run",
                [sys.executable, str(scripts / "run_simulation.py"), "--workspace", str(lifecycle), "--ticks", "182"],
            ),
            ("replay", [sys.executable, str(scripts / "replay_simulation.py"), "--workspace", str(lifecycle)]),
            (
                "sensitivity",
                [
                    sys.executable,
                    str(scripts / "run_sensitivity.py"),
                    "--workspace",
                    str(lifecycle),
                    "--method",
                    "morris",
                ],
            ),
            (
                "hindcast",
                [
                    sys.executable,
                    str(scripts / "run_hindcast.py"),
                    "--case",
                    str(root / "packs" / "economics" / "hindcast" / "case-001.json"),
                    "--out",
                    str(lifecycle / "hindcast-report.json"),
                ],
            ),
            ("finalize", [sys.executable, str(scripts / "finalize_simulation.py"), "--workspace", str(lifecycle)]),
            (
                "strict-revalidation",
                [
                    sys.executable,
                    str(scripts / "validate_simulation_artifacts.py"),
                    "--workspace",
                    str(lifecycle),
                    "--mode",
                    "final",
                    "--require-report",
                ],
            ),
        ]
        prerequisite_ok = True
        for stage, command in lifecycle_commands:
            lifecycle_result = run(command, root)
            lifecycle_result["stage"] = stage
            lifecycle_result["pass"] = prerequisite_ok and lifecycle_result["returncode"] == 0
            prerequisite_ok = bool(lifecycle_result["pass"])
            results.append(lifecycle_result)

    ok = all(
        (r.get("pass") if "pass" in r else r.get("returncode") == 0)
        for r in results
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "results": [
                    {
                        "stage": result.get("stage"),
                        "cmd": result["cmd"],
                        "returncode": result["returncode"],
                        "pass": result.get("pass", result["returncode"] == 0),
                    }
                    for result in results
                ],
            },
            indent=2,
        )
    )
    raise SystemExit(EXIT_OK if ok else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
