from __future__ import annotations

import argparse
import calendar
import copy
import csv
import datetime as dt
import io
import json
import re
from pathlib import Path

from _lib import load_json, skill_root, utc_now, write_json
from aleph.io import canonical_hash

ISO_DURATION = re.compile(r"^P(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<days>\d+)D)?$")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "simulation-run"


def parse_date(value: str) -> dt.date:
    normalized = value.strip()[:10]
    if re.fullmatch(r"\d{4}", normalized):
        normalized = f"{normalized}-01-01"
    return dt.date.fromisoformat(normalized)


def add_iso_duration(start: dt.date, duration: str) -> dt.date:
    match = ISO_DURATION.fullmatch(duration)
    if not match or not any(match.groupdict().values()):
        raise ValueError("Horizon must be an ISO date duration such as P90D, P24M, or P10Y")
    years = int(match.group("years") or 0)
    months = int(match.group("months") or 0)
    days = int(match.group("days") or 0)
    total_months = start.year * 12 + (start.month - 1) + years * 12 + months
    year, month_index = divmod(total_months, 12)
    month = month_index + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day) + dt.timedelta(days=days)


def infer_timeline_mode(change: dt.date, cutoff: dt.date, simulation_end: dt.date) -> str:
    if simulation_end <= cutoff:
        return "retrospective_counterfactual"
    if change >= cutoff:
        return "prospective_intervention"
    return "hybrid_projection"


def build_workspace(args: argparse.Namespace) -> Path:
    root = skill_root()
    templates = root / "templates"
    slug = slugify(args.slug or args.change_point or "simulation-run")
    out_base = Path(args.out_dir).resolve() if args.out_dir else (Path.cwd() / "simulation-output").resolve()
    resolved_root = root.resolve()
    if out_base == resolved_root or resolved_root in out_base.parents:
        raise ValueError("Simulation output cannot be created inside the installed skill. Pass --out-dir pointing to the user workspace.")
    workspace = out_base / slug
    workspace.mkdir(parents=True, exist_ok=args.force)

    change_date = parse_date(args.time)
    cutoff_date = parse_date(args.observation_cutoff or dt.date.today().isoformat())
    simulation_end = parse_date(args.simulation_end) if args.simulation_end else add_iso_duration(change_date, args.horizon)
    if simulation_end < change_date:
        raise ValueError("Simulation end cannot be earlier than the change point")
    timeline_mode = infer_timeline_mode(change_date, cutoff_date, simulation_end)

    manifest = load_json(templates / "simulation-manifest.json")
    manifest["schema_version"] = "2.0.0"
    manifest["simulation_id"] = f"sim-{slug}"
    manifest["created_at"] = utc_now()
    manifest["status"] = "draft"
    manifest["likelihood_mode"] = "relative_weight"
    manifest["simulation_mode"] = "qualitative"
    manifest["assurance_tier"] = None
    manifest["change_point"]["description"] = args.change_point
    manifest["change_point"]["time"] = change_date.isoformat()
    manifest["change_point"]["location"] = args.geography
    manifest["temporal_frame"] = {
        "mode": timeline_mode,
        "observation_cutoff": cutoff_date.isoformat(),
        "simulation_start": change_date.isoformat(),
        "simulation_end": simulation_end.isoformat(),
        "future_projection": simulation_end > cutoff_date,
        "calibration_strategy": "pending adaptive scope assessment",
        "monitoring_indicators": [],
    }
    manifest["scope"] = {
        "horizon": args.horizon,
        "domains": [item.strip() for item in args.domain.split(",") if item.strip()],
        "geographies": [item.strip() for item in args.geography.split(",") if item.strip()],
    }
    manifest["execution"]["adaptive_scope"] = {
        "assessed": False,
        "overall_complexity": 0.0,
        "dimensions": {
            "temporal_span": 0.0,
            "domain_breadth": 0.0,
            "geographic_breadth": 0.0,
            "actor_density": 0.0,
            "causal_depth": 0.0,
            "evidence_uncertainty": 0.0,
            "stakes": 0.0,
        },
        "rationale": "pending assessment",
        "decomposition": {"subquestions": [], "critical_paths": [], "research_waves_completed": 0},
    }
    manifest["execution"]["research_quality"] = "unknown"
    manifest["execution"]["research_control"] = {
        "policy": "evidence-saturation",
        "sources_examined": 0,
        "saturation_reached": False,
        "consecutive_no_new_material_claims": 0,
        "stop_reason": "",
        "unresolved_critical_gaps": [],
        "next_wave_queue": [],
    }
    manifest["execution"]["d_research"] = {"status": "unknown", "invoked": False}
    manifest["execution"]["subagents"] = {
        "status": "unknown",
        "tool": None,
        "detection_method": "pending runtime capability check",
        "fallback_reason": "",
    }
    manifest["execution"]["repair_cycles_completed"] = 0
    manifest["execution"]["checkpoints"] = {
        "initialized": True,
        "scope_assessed": False,
        "baseline_researched": False,
        "human_tracks_completed": False,
        "graph_built": False,
        "propagated": False,
        "branched": False,
        "validated": False,
    }

    node = load_json(templates / "timeline-node.json")
    node["time"] = change_date.isoformat()
    entity = copy.deepcopy(node)
    entity.update(
        {
            "id": "entity:example",
            "type": "entity",
            "name": "Example Public-Role Actor",
            "status": "fact",
            "timeline": "observed_baseline",
            "probability": None,
            "confidence": 0.5,
            "time": cutoff_date.isoformat(),
            "description": "A public-role entity linked coherently to the initialized draft actor dossier.",
            "state_before": {"summary": "Public role exists at the observation cutoff."},
            "trigger": {"kind": "context", "description": "Institutional baseline."},
            "mechanism": "The entity participates only through its declared public institutional role.",
            "state_after": {"summary": "Public-role participation remains available to the scenario."},
            "lag": "P0D",
            "role": "endogenous",
            "baseline": 0.0,
        }
    )
    entity.pop("assumption_ref", None)
    target = copy.deepcopy(node)
    target.update(
        {
            "id": "factor:target",
            "name": "Example Target Factor",
            "description": "A coherent target node for the initialized example causal edge.",
            "state_before": {"summary": "Target factor at its declared baseline.", "value": 0.0},
            "trigger": {"kind": "factor_change", "description": "Transmission from factor:example."},
            "mechanism": "The source perturbation changes the target through a declared transmission channel whose direction, magnitude, timing, evidence, and contextual multiplier are recorded in the causal edge.",
            "state_after": {"summary": "Target factor changes in the simulated branch.", "value": 0.16},
            "baseline": 0.0,
        }
    )
    context = copy.deepcopy(entity)
    context.update(
        {
            "id": "context:baseline",
            "type": "context",
            "name": "Baseline Context",
            "description": "Neutral context modifier for the initialized causal edge.",
            "state_before": {"summary": "Baseline context is active."},
            "trigger": {"kind": "context", "description": "Scenario baseline."},
            "mechanism": "The neutral baseline context leaves the example edge multiplier at one.",
            "state_after": {"summary": "Baseline context remains active."},
            "role": "exogenous",
            "baseline": 1.0,
        }
    )
    edge = load_json(templates / "causal-edge.json")
    actor = {
        "id": "actor:example",
        "person_node": "entity:example",
        "public_role": "public decision maker",
        "scope_note": "Draft non-material actor; promote to material only after sealed research and roleplay tracks exist.",
        "materiality": "non_material",
        "subject_class": "public_role_person",
        "evidence_ids": ["evidence:example"],
    }
    branches = load_json(templates / "branch-ledger.json")
    for branch in branches["branches"]:
        branch["end_state"]["time"] = simulation_end.isoformat()

    write_json(workspace / "simulation-manifest.json", manifest)
    write_json(workspace / "nodes.json", [entity, node, target, context])
    write_json(workspace / "edges.json", [edge])
    write_json(workspace / "actors.json", [actor])
    evidence_text = (templates / "evidence-map.csv").read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(evidence_text))
    evidence_rows = list(reader)
    for evidence_row in evidence_rows:
        evidence_row["date"] = cutoff_date.isoformat()
        evidence_row["retrieved_at"] = f"{cutoff_date.isoformat()}T00:00:00Z"
    evidence_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(evidence_buffer, fieldnames=reader.fieldnames or [])
    writer.writeheader()
    writer.writerows(evidence_rows)
    (workspace / "evidence-map.csv").write_text(
        evidence_buffer.getvalue(), encoding="utf-8", newline="\n"
    )
    trace = load_json(templates / "propagation-trace.jsonl")
    trace["time"] = change_date.isoformat()
    trace.pop("hash_chain", None)
    trace["hash_chain"] = canonical_hash({"previous_hash": None, "row": trace})
    (workspace / "propagation-trace.jsonl").write_text(json.dumps(trace, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (workspace / "human-track-ledger.jsonl").write_text("", encoding="utf-8", newline="\n")
    write_json(workspace / "branch-ledger.json", branches)
    validation_report = load_json(templates / "validation-report.json")
    validation_report.update(
        {
            "validated_at": utc_now(),
            "mode": "draft",
            "status": "not-run",
            "checks": {},
            "metrics": {},
            "warnings": [],
            "errors": [],
        }
    )
    write_json(workspace / "validation-report.json", validation_report)
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an adaptive Aleph simulation workspace from templates.")
    parser.add_argument("--slug", help="Workspace slug.")
    parser.add_argument("--change-point", default="Example change point", help="Change point description.")
    parser.add_argument("--time", default=dt.date.today().isoformat(), help="Change point date.")
    parser.add_argument("--horizon", default="P24M", help="ISO simulation horizon from the change point.")
    parser.add_argument("--simulation-end", help="Optional absolute simulation end date; overrides the derived horizon end.")
    parser.add_argument("--observation-cutoff", help="Last date treated as observed reality. Defaults to today.")
    parser.add_argument("--domain", default="mixed", help="Comma-separated domains.")
    parser.add_argument("--geography", default="global", help="Comma-separated geographic or institutional scopes.")
    parser.add_argument("--out-dir", help="Output base directory outside the installed skill. Defaults to ./simulation-output.")
    parser.add_argument("--force", action="store_true", help="Allow using an existing workspace directory.")
    args = parser.parse_args()
    workspace = build_workspace(args)
    print(str(workspace))


if __name__ == "__main__":
    main()
