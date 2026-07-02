from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _lib import load_json, utc_now, write_json
from validate_simulation_artifacts import validate_workspace


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def evaluate(workspace: Path) -> dict[str, Any]:
    validation = validate_workspace(workspace, mode="final", require_report=True)
    manifest = load_json(workspace / "simulation-manifest.json")
    execution = manifest.get("execution", {})
    metrics = validation.get("metrics", {})
    checks = validation.get("checks", {})

    structural = max(0.0, 20.0 - 1.5 * len(validation.get("errors", [])))
    direct_ratio = float(metrics.get("direct_source_ratio", 0.0))
    high_quality_target = int(metrics.get("required_high_quality_sources", 1))
    high_quality = int(metrics.get("high_quality_direct_sources", 0))
    evidence = 10.0 * clamp(direct_ratio) + 10.0 * clamp(high_quality / high_quality_target)
    causality = 20.0 if checks.get("graph") == "pass" and checks.get("trace") == "pass" and checks.get("temporal") == "pass" else 5.0

    if checks.get("human_tracks") != "pass":
        human = 0.0
    elif execution.get("subagents", {}).get("status") == "available":
        human = 15.0
    else:
        human = 12.5
    branching = 10.0 if checks.get("branches") == "pass" else 0.0

    report_path = workspace / str(manifest.get("artifact_paths", {}).get("final_report", "REPORT.md"))
    completion = 10.0 if report_path.exists() and validation.get("status") == "pass" else 3.0 if report_path.exists() else 0.0

    adaptive = execution.get("adaptive_scope", {})
    control = execution.get("research_control", {})
    adaptive_ready = (
        adaptive.get("assessed") is True
        and control.get("saturation_reached") is True
        and not control.get("unresolved_critical_gaps")
    )
    adaptive_coverage = 5.0 if adaptive_ready else 0.0

    sections = {
        "structural_integrity": round(structural, 2),
        "evidence_quality": round(evidence, 2),
        "causal_traceability": round(causality, 2),
        "human_track_separation": round(human, 2),
        "branch_uncertainty": round(branching, 2),
        "completion_reporting": round(completion, 2),
        "adaptive_coverage": round(adaptive_coverage, 2),
    }
    score = round(min(100.0, sum(sections.values())), 2)
    quality_gates = {
        "validation_passed": validation.get("status") == "pass",
        "evidence_floor_passed": evidence >= 15.0,
        "score_threshold_90_passed": score >= 90.0,
        "evidence_saturation_passed": control.get("saturation_reached") is True,
        "temporal_integrity_passed": checks.get("temporal") == "pass",
    }
    all_gates = all(quality_gates.values())
    grade = (
        "excellent"
        if score >= 90 and all_gates
        else "good"
        if score >= 80 and validation.get("status") == "pass" and evidence >= 12
        else "partial"
        if score >= 60
        else "fail"
    )
    return {
        "schema_version": "1.2.0",
        "evaluated_at": utc_now(),
        "workspace": str(workspace),
        "score": score,
        "grade": grade,
        "quality_gates": quality_gates,
        "sections": sections,
        "validation_status": validation.get("status"),
        "errors": validation.get("errors", []),
        "warnings": validation.get("warnings", []),
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score an Aleph simulation workspace on a 100-point quality rubric.")
    parser.add_argument("--workspace", required=True, help="Simulation workspace directory.")
    parser.add_argument("--out", help="Optional JSON output path.")
    parser.add_argument("--threshold", type=float, default=90.0, help="Required score when --enforce is used.")
    parser.add_argument("--enforce", action="store_true", help="Exit non-zero below threshold or on validation failure.")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    result = evaluate(workspace)
    if args.out:
        write_json(Path(args.out).resolve(), result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.enforce and (
        result["score"] < args.threshold
        or result["validation_status"] != "pass"
        or not all(result["quality_gates"].values())
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
