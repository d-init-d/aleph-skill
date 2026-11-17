#!/usr/bin/env python3
"""Turn-key Aleph causal simulation pipeline orchestrator.

Chains workspace initialization (if absent), research ledger import,
auto-causal induction & deterministic review/apply, model compilation,
simulation execution, draft validation, and Markdown report rendering.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aleph import EXIT_OK, EXIT_USAGE  # noqa: E402


def _run_stage(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "stage": name,
        "command": command,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _seed_offline_minimum_model(workspace: Path, *, mode: str) -> None:
    """Ensure initialized workspace has valid baseline nodes/edges when run without a ledger."""
    nodes_path = workspace / "nodes.json"
    edges_path = workspace / "edges.json"
    manifest_path = workspace / "simulation-manifest.json"
    if not manifest_path.is_file():
        return
    if nodes_path.is_file() and edges_path.is_file():
        nodes_data = json.loads(nodes_path.read_text(encoding="utf-8"))
        edges_data = json.loads(edges_path.read_text(encoding="utf-8"))
        if isinstance(nodes_data, list) and len(nodes_data) >= 2 and isinstance(edges_data, list) and edges_data:
            return
    nodes_path.write_text(
        json.dumps(
            [
                {
                    "id": "factor:driver",
                    "type": "factor",
                    "name": "Primary Driver",
                    "description": "Exogenous driver variable",
                    "time": "2026-01-01",
                    "state_before": {"summary": "Baseline equilibrium", "value": 10.0, "unit": "index"},
                    "trigger": {"kind": "baseline", "description": "Initial baseline"},
                    "mechanism": "Direct causal channel",
                    "state_after": {"summary": "Simulated state", "value": 10.0, "unit": "index"},
                    "lag": "P0D",
                    "evidence_ids": ["evidence:e1"],
                    "status": "assumption",
                    "timeline": "shared_baseline",
                    "confidence": 0.5,
                    "role": "exogenous",
                    "datatype": "continuous",
                    "unit": "index",
                    "scale": "level",
                    "baseline": 10.0,
                },
                {
                    "id": "factor:outcome",
                    "type": "factor",
                    "name": "Target Outcome",
                    "description": "Endogenous outcome variable",
                    "time": "2026-01-01",
                    "state_before": {"summary": "Baseline equilibrium", "value": 5.0, "unit": "index"},
                    "trigger": {"kind": "transmission", "description": "Causal inflow"},
                    "mechanism": "Linear transmission",
                    "state_after": {"summary": "Simulated state", "value": 5.0, "unit": "index"},
                    "lag": "P0D",
                    "evidence_ids": ["evidence:e1"],
                    "status": "inference",
                    "timeline": "simulated_branch" if mode != "qualitative" else "shared_baseline",
                    "confidence": 0.5,
                    "role": "endogenous",
                    "datatype": "continuous",
                    "unit": "index",
                    "scale": "level",
                    "baseline": 5.0,
                },
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_pipeline(
    *,
    workspace: Path,
    ledger: Path | None = None,
    mode: str = "deterministic",
    formula_version: str = "2.2.0",
    seed: str = "42",
    ticks: int = 6,
    runs: int | None = None,
    change_point: str = "Baseline intervention scenario",
    horizon: str = "P6M",
    timestep: str = "P1M",
    offline_provisional: bool = False,
) -> dict[str, Any]:
    ws = workspace.resolve()
    ws.mkdir(parents=True, exist_ok=True)
    stages: list[dict[str, Any]] = []
    skill_root = SCRIPTS_DIR.parent

    if not (ws / "simulation-manifest.json").is_file():
        init_cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "init_simulation_workspace.py"),
            "--slug",
            ws.name,
            "--out-dir",
            str(ws.parent),
            "--force",
            "--change-point",
            change_point,
            "--horizon",
            horizon,
            "--timestep",
            timestep,
            "--simulation-mode",
            mode,
            "--formula-version",
            formula_version,
        ]
        if offline_provisional or ledger is None:
            init_cmd.extend(
                [
                    "--offline-provisional",
                    "--offline-reason",
                    "Turn-key pipeline execution in offline/provisional mode",
                ]
            )
        st = _run_stage("init", init_cmd, skill_root)
        stages.append(st)
        if not st["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "init", "stages": stages}

    if ledger is not None:
        resolved_ledger = ledger.resolve()
        if resolved_ledger.suffix.lower() == ".csv":
            with resolved_ledger.open("r", encoding="utf-8-sig", newline="") as handle:
                header = set(next(csv.reader(handle), []))
            if "claim_id" in header and "record_type" in header:
                imp_cmd = [
                    sys.executable,
                    str(SCRIPTS_DIR / "import_research_ledger.py"),
                    "--ledger",
                    str(resolved_ledger),
                    "--workspace",
                    str(ws),
                    "--out",
                    str(ws / "evidence-map.csv"),
                    "--receipt-out",
                    str(ws / "research-import-receipt.json"),
                ]
                st_imp = _run_stage("import-research", imp_cmd, ws)
                stages.append(st_imp)
                if not st_imp["ok"]:
                    return {"ok": False, "workspace": str(ws), "failed_stage": "import-research", "stages": stages}

        prop_path = ws / "causal-proposal.json"
        rev_path = ws / "causal-review.json"
        st_prop = _run_stage(
            "propose-model",
            [
                sys.executable,
                str(SCRIPTS_DIR / "auto_causal_inducer.py"),
                "propose-model",
                "--ledger" if resolved_ledger.suffix.lower() == ".csv" else "--evidence",
                str(resolved_ledger),
                "--out",
                str(prop_path),
            ],
            ws,
        )
        stages.append(st_prop)
        if not st_prop["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "propose-model", "stages": stages}

        st_rev = _run_stage(
            "review-model",
            [
                sys.executable,
                str(SCRIPTS_DIR / "auto_causal_inducer.py"),
                "review-model",
                "--proposal",
                str(prop_path),
                "--out",
                str(rev_path),
            ],
            ws,
        )
        stages.append(st_rev)
        if not st_rev["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "review-model", "stages": stages}

        st_apply = _run_stage(
            "apply-model",
            [
                sys.executable,
                str(SCRIPTS_DIR / "auto_causal_inducer.py"),
                "apply-model",
                "--proposal",
                str(prop_path),
                "--review",
                str(rev_path),
                "--workspace",
                str(ws),
                "--into-initialized",
            ],
            ws,
        )
        stages.append(st_apply)
        if not st_apply["ok"]:
            return {"ok": False, "workspace": str(ws), "failed_stage": "apply-model", "stages": stages}
    else:
        _seed_offline_minimum_model(ws, mode=mode)

    st_comp = _run_stage(
        "compile",
        [sys.executable, str(SCRIPTS_DIR / "compile_model.py"), "--workspace", str(ws)],
        ws,
    )
    stages.append(st_comp)
    if not st_comp["ok"]:
        return {"ok": False, "workspace": str(ws), "failed_stage": "compile", "stages": stages}

    sim_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "run_simulation.py"),
        "--workspace",
        str(ws),
        "--mode",
        mode,
        "--seed",
        str(seed),
        "--ticks",
        str(ticks),
        "--json",
    ]
    if runs is not None and mode == "monte_carlo":
        sim_cmd.extend(["--runs", str(runs), "--max-runs", str(runs)])
    st_sim = _run_stage("simulate", sim_cmd, ws)
    stages.append(st_sim)
    if not st_sim["ok"]:
        return {"ok": False, "workspace": str(ws), "failed_stage": "simulate", "stages": stages}

    st_replay = _run_stage(
        "replay",
        [sys.executable, str(SCRIPTS_DIR / "replay_simulation.py"), "--workspace", str(ws)],
        ws,
    )
    stages.append(st_replay)
    if not st_replay["ok"]:
        return {"ok": False, "workspace": str(ws), "failed_stage": "replay", "stages": stages}

    st_val = _run_stage(
        "validate",
        [
            sys.executable,
            str(SCRIPTS_DIR / "validate_simulation_artifacts.py"),
            "--workspace",
            str(ws),
            "--mode",
            "draft",
            "--write-report",
            "--json",
        ],
        ws,
    )
    stages.append(st_val)

    st_rep = _run_stage(
        "render-report",
        [sys.executable, str(SCRIPTS_DIR / "render_simulation_report.py"), "--workspace", str(ws)],
        ws,
    )
    stages.append(st_rep)

    artifacts_present = sorted(
        name
        for name in (
            "simulation-manifest.json",
            "nodes.json",
            "edges.json",
            "simulation-model.json",
            "simulation-run.json",
            "execution-trace.json",
            "branch-ledger.json",
            "validation-report.json",
            "REPORT.md",
        )
        if (ws / name).is_file()
    )
    if (ws / "replay-report.json").is_file() and "replay-report.json" not in artifacts_present:
        artifacts_present = sorted([*artifacts_present, "replay-report.json"])
    return {
        "ok": all(item["ok"] for item in stages),
        "workspace": str(ws),
        "mode": mode,
        "formula_version": formula_version,
        "stages": [{"stage": s["stage"], "returncode": s["returncode"], "ok": s["ok"]} for s in stages],
        "artifacts": artifacts_present,
    }


def _self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="aleph-pipeline-selftest-") as td:
        ws = Path(td) / "demo-ws"
        ledger_path = Path(td) / "claims.json"
        ledger_path.write_text(
            json.dumps(
                [
                    {
                        "claim_id": "claim:rate-gap",
                        "record_type": "claim",
                        "claim": "Policy Rate decreases Output Gap via tightening financial conditions",
                        "evidence": "A rate increase lowers output gap by 0.25 with lag 1 tick (2025-01-01, unit %, baseline 4.5, unit %).",
                        "source_factor": "Policy Rate",
                        "target_factor": "Output Gap",
                        "sign": -1,
                        "effect_size": 0.25,
                        "lag_ticks": 1,
                        "mechanism": "tightening financial conditions",
                        "observation_time": "2025-01-01",
                        "node_measurements": {
                            "factor:policy-rate": {"baseline": 4.5, "time": "2025-01-01", "unit": "%"},
                            "factor:output-gap": {"baseline": 0.5, "time": "2025-01-01", "unit": "%"},
                        },
                        "confidence": "high",
                    }
                ]
            ),
            encoding="utf-8",
        )
        res = run_pipeline(
            workspace=ws,
            ledger=ledger_path,
            mode="deterministic",
            formula_version="2.2.0",
            seed="42",
            ticks=4,
            offline_provisional=True,
        )
        if not res["ok"]:
            print(json.dumps(res, indent=2), file=sys.stderr)
            return 1
        print(json.dumps({"self_test": "ok", "stages": len(res["stages"]), "artifacts": res["artifacts"]}, indent=2))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end Aleph causal simulation pipeline.")
    parser.add_argument("--workspace", help="Target Aleph simulation workspace directory")
    parser.add_argument("--ledger", help="Optional D-Research CSV or JSON evidence ledger")
    parser.add_argument("--mode", choices=["deterministic", "monte_carlo"], default="deterministic")
    parser.add_argument("--formula-version", choices=["2.0.0", "2.1.0", "2.2.0"], default="2.2.0")
    parser.add_argument("--seed", default="42")
    parser.add_argument("--ticks", type=int, default=6)
    parser.add_argument("--runs", type=int, help="Monte Carlo sample count")
    parser.add_argument("--change-point", default="Baseline intervention scenario")
    parser.add_argument("--horizon", default="P6M")
    parser.add_argument("--timestep", default="P1M")
    parser.add_argument("--offline-provisional", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if not args.workspace:
        print("ERROR: --workspace is required unless --self-test is passed", file=sys.stderr)
        return EXIT_USAGE
    res = run_pipeline(
        workspace=Path(args.workspace),
        ledger=Path(args.ledger) if args.ledger else None,
        mode=args.mode,
        formula_version=args.formula_version,
        seed=args.seed,
        ticks=args.ticks,
        runs=args.runs,
        change_point=args.change_point,
        horizon=args.horizon,
        timestep=args.timestep,
        offline_provisional=args.offline_provisional,
    )
    print(json.dumps(res, indent=2))
    return EXIT_OK if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
