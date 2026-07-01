from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _lib import load_csv_rows, load_json, utc_now, write_text


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def table_row(values: list[str]) -> str:
    return "| " + " | ".join(values) + " |"


def render(workspace: Path) -> str:
    manifest = load_json(workspace / "simulation-manifest.json")
    artifacts = manifest.get("artifact_paths", {})
    branches = load_json(workspace / artifacts.get("branch_ledger", "branch-ledger.json")).get("branches", [])
    validation_path = workspace / artifacts.get("validation_report", "validation-report.json")
    validation = load_json(validation_path) if validation_path.exists() else {"status": "not-run", "warnings": [], "errors": []}
    trace = read_jsonl(workspace / artifacts.get("propagation_trace", "propagation-trace.jsonl"))
    evidence_rows = load_csv_rows(workspace / artifacts.get("evidence_map", "evidence-map.csv"))

    lines: list[str] = []
    lines.append("# Aleph Timeline Simulation Report")
    lines.append("")
    lines.append(f"Generated: {utc_now()}")
    lines.append(f"Workspace: `{workspace}`")
    lines.append("")
    lines.append("## Change point and assumptions")
    lines.append("")
    change = manifest.get("change_point", {})
    lines.append(f"- Type: `{change.get('type', 'unknown')}`")
    lines.append(f"- Target: `{change.get('target', 'unknown')}`")
    lines.append(f"- Description: {change.get('description', '')}")
    lines.append(f"- Time: {change.get('time', '')}")
    lines.append(f"- Scope: {manifest.get('scope', {})}")
    lines.append("")
    lines.append("## Evidence summary")
    lines.append("")
    lines.append(f"- Evidence rows: {len(evidence_rows)}")
    lines.append("- Treat all generated branches as simulation unless explicitly sourced as observed facts.")
    lines.append("")
    lines.append("## Propagation highlights")
    lines.append("")
    if trace:
        for row in trace[:10]:
            lines.append(
                f"- Step {row.get('step')}: `{row.get('from')}` -> `{row.get('to')}` "
                f"effect `{row.get('output_effect')}` via {row.get('mechanism')}"
            )
    else:
        lines.append("- No propagation trace rows found.")
    lines.append("")
    lines.append("## Timeline branch distribution")
    lines.append("")
    lines.append(table_row(["Branch", "Probability", "Summary", "Confidence"]))
    lines.append(table_row(["---", "---:", "---", "---:"]))
    for branch in branches:
        lines.append(
            table_row(
                [
                    str(branch.get("name", branch.get("id", ""))),
                    f"{float(branch.get('probability', 0)):.2f}",
                    str(branch.get("summary", "")),
                    f"{float(branch.get('confidence', 0)):.2f}",
                ]
            )
        )
    lines.append("")
    lines.append("## Validation and audit")
    lines.append("")
    lines.append(f"- Validation status: `{validation.get('status')}`")
    for warning in validation.get("warnings", []):
        lines.append(f"- Warning: {warning}")
    for error in validation.get("errors", []):
        lines.append(f"- Error: {error}")
    lines.append("")
    lines.append("## Warnings and next steps")
    lines.append("")
    lines.append("- Replace example evidence rows with real D Research ledger entries before using this report for analysis.")
    lines.append("- Run Aleph validation and backtests when local Aleph artifacts are available.")
    lines.append("- Re-run branch probability normalization after adding or pruning branches.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a Markdown report from simulation artifacts.")
    parser.add_argument("--workspace", required=True, help="Simulation workspace directory.")
    parser.add_argument("--out", help="Output Markdown path. Defaults to <workspace>/REPORT.md.")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    output = Path(args.out).resolve() if args.out else workspace / "REPORT.md"
    text = render(workspace)
    write_text(output, text)
    print(str(output))


if __name__ == "__main__":
    main()
