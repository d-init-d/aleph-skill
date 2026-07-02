from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from _lib import load_csv_rows, load_json, utc_now, write_text


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def table_row(values: list[Any]) -> str:
    return "| " + " | ".join(cell(value) for value in values) + " |"


def render(workspace: Path) -> str:
    manifest = load_json(workspace / "simulation-manifest.json")
    artifacts = manifest.get("artifact_paths", {})
    branches = load_json(workspace / artifacts.get("branch_ledger", "branch-ledger.json")).get("branches", [])
    nodes = load_json(workspace / artifacts.get("nodes", "nodes.json"))
    edges = load_json(workspace / artifacts.get("edges", "edges.json"))
    actors = load_json(workspace / artifacts.get("actors", "actors.json"))
    trace = read_jsonl(workspace / artifacts.get("propagation_trace", "propagation-trace.jsonl"))
    evidence_rows = load_csv_rows(workspace / artifacts.get("evidence_map", "evidence-map.csv"))
    validation_path = workspace / artifacts.get("validation_report", "validation-report.json")
    validation = load_json(validation_path) if validation_path.exists() else {"status": "not-run", "warnings": [], "errors": []}

    change = manifest.get("change_point", {})
    frame = manifest.get("temporal_frame", {})
    scope = manifest.get("scope", {})
    execution = manifest.get("execution", {})
    adaptive = execution.get("adaptive_scope", {})
    control = execution.get("research_control", {})
    direct_statuses = {"opened", "downloaded", "api", "local-file", "user-provided"}
    direct_count = sum(1 for row in evidence_rows if row.get("retrieval_status") in direct_statuses)
    high_quality = sum(
        1
        for row in evidence_rows
        if row.get("retrieval_status") in direct_statuses
        and row.get("source_tier") in {"primary", "authoritative-secondary", "user-provided"}
    )
    tier_counts = Counter(row.get("source_tier", "unknown") for row in evidence_rows)
    contradiction_rows = [row for row in evidence_rows if row.get("contradiction_status") in {"contested", "contradicted"}]
    ranked_branches = sorted(branches, key=lambda item: float(item.get("probability", 0)), reverse=True)
    top_branch = ranked_branches[0] if ranked_branches else None
    report_ready = (
        manifest.get("status") in {"complete", "completed"}
        and control.get("saturation_reached") is True
        and validation.get("mode") == "final"
        and validation.get("status") == "pass"
    )
    effective_validation_status = "final-pass" if report_ready else "draft-not-ready"

    lines: list[str] = [
        "# Aleph Causal Timeline Simulation Report",
        "",
        f"**Generated:** {utc_now()}  ",
        f"**Simulation ID:** `{manifest.get('simulation_id', 'unknown')}`  ",
        f"**Workspace:** `{workspace}`",
        "",
        "## Executive summary",
        "",
        f"This report evaluates the intervention **{change.get('description', '')}** from `{change.get('time', '')}` through `{frame.get('simulation_end', '')}` using `{frame.get('mode', 'unknown')}` temporal framing.",
    ]
    if top_branch:
        lines.append(
            f"The highest-probability branch is **{top_branch.get('name', top_branch.get('id'))}** at `{float(top_branch.get('probability', 0)):.2f}`. This is a conditional estimate, not a prediction of certainty."
        )
    lines.extend(
        [
            f"Research reached evidence saturation: `{control.get('saturation_reached', False)}`. Report readiness: `{effective_validation_status}`.",
            "",
            "## Methodology and scope",
            "",
            f"- Temporal mode: `{frame.get('mode', 'unknown')}`",
            f"- Observation cutoff: `{frame.get('observation_cutoff', 'unknown')}`",
            f"- Simulation window: `{frame.get('simulation_start', 'unknown')}` to `{frame.get('simulation_end', 'unknown')}`",
            f"- Future projection: `{frame.get('future_projection', False)}`",
            f"- Domains: {', '.join(scope.get('domains', []))}",
            f"- Geographies/institutions: {', '.join(scope.get('geographies', []))}",
            f"- Adaptive complexity: `{float(adaptive.get('overall_complexity', 0)):.2f}`",
            f"- Research waves completed: `{adaptive.get('decomposition', {}).get('research_waves_completed', 0)}`",
            f"- Sources examined: `{control.get('sources_examined', 0)}`; retained evidence rows: `{len(evidence_rows)}`",
            f"- Stop reason: {control.get('stop_reason', 'not recorded')}",
            "",
            "### Complexity assessment",
            "",
            table_row(["Dimension", "Score"]),
            table_row(["---", "---:"]),
        ]
    )
    for name, value in adaptive.get("dimensions", {}).items():
        lines.append(table_row([name.replace("_", " ").title(), f"{float(value):.2f}"]))

    lines.extend(
        [
            "",
            "## Baseline and change point",
            "",
            f"- Change type: `{change.get('type', 'unknown')}`",
            f"- Target: `{change.get('target', 'unknown')}`",
            f"- Magnitude: `{change.get('magnitude', 'unknown')}`",
            f"- Location/scope: `{change.get('location', 'unknown')}`",
            f"- Calibration: {frame.get('calibration_strategy', '')}",
            "- Observed facts, analyst inference, simulation output, and counterfactual events remain separately labeled throughout the artifacts.",
            "",
            "## Evidence and source quality",
            "",
            f"- Directly accessed evidence: `{direct_count}/{len(evidence_rows)}`",
            f"- Direct primary/authoritative evidence: `{high_quality}`",
            f"- Contested or contradicted ledger rows: `{len(contradiction_rows)}`",
            "",
            table_row(["Source tier", "Rows"]),
            table_row(["---", "---:"]),
        ]
    )
    for tier, count in sorted(tier_counts.items()):
        lines.append(table_row([tier, count]))

    lines.extend(
        [
            "",
            "## Causal architecture and propagation",
            "",
            f"- Nodes: `{len(nodes)}`; edges: `{len(edges)}`; propagation steps: `{len(trace)}`",
            "",
        ]
    )
    for row in trace[:12]:
        lines.append(
            f"- Step {row.get('step')}: `{row.get('from')}` → `{row.get('to')}`; effect `{row.get('output_effect')}` after `{row.get('lag_applied', 'unspecified')}`. {row.get('mechanism', '')}"
        )

    lines.extend(
        [
            "",
            "## Scenario branches",
            "",
            table_row(["Branch", "Probability", "Confidence", "End time", "End state"]),
            table_row(["---", "---:", "---:", "---", "---"]),
        ]
    )
    for branch in ranked_branches:
        lines.append(
            table_row(
                [
                    branch.get("name", branch.get("id", "")),
                    f"{float(branch.get('probability', 0)):.2f}",
                    f"{float(branch.get('confidence', 0)):.2f}",
                    branch.get("end_state", {}).get("time", ""),
                    branch.get("end_state", {}).get("summary", ""),
                ]
            )
        )

    if frame.get("future_projection"):
        lines.extend(["", "## Future monitoring and probability updates", ""])
        for branch in ranked_branches:
            lines.append(f"### {branch.get('name', branch.get('id', 'Branch'))}")
            lines.append("")
            for indicator in branch.get("leading_indicators", []):
                lines.append(f"- Leading indicator: {indicator}")
            for condition in branch.get("disconfirming_conditions", []):
                lines.append(f"- Disconfirming condition: {condition}")
            lines.append("")

    lines.extend(
        [
            "## Human decision tracks",
            "",
            table_row(["Actor", "Research", "Roleplay", "Execution", "Knowledge cutoff"]),
            table_row(["---", "---", "---", "---", "---"]),
        ]
    )
    for actor in actors:
        research = actor.get("research_track", {})
        roleplay = actor.get("roleplay_track", {})
        lines.append(
            table_row(
                [
                    actor.get("public_role", actor.get("id", "")),
                    research.get("status", "missing"),
                    roleplay.get("status", "missing"),
                    f"{research.get('execution_mode', '?')} / {roleplay.get('execution_mode', '?')}",
                    roleplay.get("knowledge_cutoff", "missing"),
                ]
            )
        )

    sensitive_nodes = [node for node in nodes if node.get("sensitivity", {}).get("level") == "high"]
    lines.extend(["", "## Sensitivity, contradictions, and limitations", ""])
    for node in sensitive_nodes:
        lines.append(f"- High-sensitivity node `{node.get('id')}`: {', '.join(node.get('sensitivity', {}).get('drivers', []))}")
    for row in contradiction_rows:
        lines.append(f"- `{row.get('evidence_id')}` is {row.get('contradiction_status')}: {row.get('claim')}")
    for warning in validation.get("warnings", []):
        lines.append(f"- Validator warning: {warning}")
    if not sensitive_nodes and not contradiction_rows and not validation.get("warnings"):
        lines.append("- No additional high-sensitivity or contradiction warnings were recorded; branch uncertainty still applies.")

    lines.extend(
        [
            "",
            "## Validation and audit",
            "",
            f"- Recorded validation status: `{validation.get('status', 'not-run')}` ({validation.get('mode', 'unknown')})",
            f"- Report readiness: `{effective_validation_status}`",
            f"- Unresolved critical gaps: `{len(control.get('unresolved_critical_gaps', []))}`",
            f"- Repair cycles completed: `{execution.get('repair_cycles_completed', 0)}`",
        ]
    )
    for error in validation.get("errors", []):
        lines.append(f"- Validator error: {error}")

    lines.extend(
        [
            "",
            "## Source appendix",
            "",
            table_row(["Evidence ID", "Tier", "Confidence", "Claim", "Source"]),
            table_row(["---", "---", "---:", "---", "---"]),
        ]
    )
    for row in evidence_rows:
        source = row.get("source", "")
        source_cell = f"[{source}]({source})" if source.startswith(("http://", "https://")) else source
        lines.append(table_row([row.get("evidence_id"), row.get("source_tier"), row.get("confidence"), row.get("claim"), source_cell]))

    lines.extend(
        [
            "",
            "## Warnings and next steps",
            "",
            "- Branch probabilities are conditional estimates and must be updated when monitoring indicators change.",
            "- Re-run research, contradiction checks, validation, and quality scoring after changing evidence or assumptions.",
            "- For future projections, compare observed indicators with each branch's disconfirming conditions before revising probability mass.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a professional Markdown report from Aleph simulation artifacts.")
    parser.add_argument("--workspace", required=True, help="Simulation workspace directory.")
    parser.add_argument("--out", help="Output Markdown path. Defaults to <workspace>/REPORT.md.")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    output = Path(args.out).resolve() if args.out else workspace / "REPORT.md"
    write_text(output, render(workspace))
    print(str(output))


if __name__ == "__main__":
    main()
