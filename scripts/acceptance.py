from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from _lib import skill_root
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Adversarial acceptance matrix.")
    parser.add_argument("--adversarial", help="Path to adversarial workspace")
    args = parser.parse_args()
    root = skill_root()
    scripts = root / "scripts"
    results = []

    # Unit tests
    results.append(run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], root))

    # Adversarial must fail
    adv = Path(args.adversarial) if args.adversarial else root.parent / "test-output" / "adversarial-completed"
    if not adv.is_dir():
        adv = root / "tests" / "fixtures" / "adversarial"
    if adv.is_dir():
        r = run(
            [sys.executable, str(scripts / "validate_simulation_artifacts.py"), "--workspace", str(adv), "--mode", "final"],
            root,
        )
        r["expect_nonzero"] = True
        r["pass"] = r["returncode"] != 0
        results.append(r)
        # quality must not be verified/calibrated
        q = run(
            [
                sys.executable,
                str(scripts / "evaluate_simulation_quality.py"),
                "--workspace",
                str(adv),
                "--json",
            ],
            root,
        )
        try:
            data = json.loads(q["stdout_full"])
            q["pass"] = data.get("assurance_tier") not in {"verified", "calibrated"} and data.get("validation_status") != "pass"
        except json.JSONDecodeError:
            q["pass"] = q["returncode"] != 0
        results.append(q)

    # Packs
    results.append(run([sys.executable, str(scripts / "validate_domain_packs.py")], root))
    # Adapters
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
