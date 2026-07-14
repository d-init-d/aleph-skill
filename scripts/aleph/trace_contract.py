"""Shared propagation-trace loading and semantic validation for run/replay CLIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .io import load_workspace_artifact
from .issues import Issue
from .validator import validate_trace


def validate_declared_trace(
    workspace: Path,
    manifest: dict[str, Any],
    nodes: list[Any],
    edges: list[Any],
) -> tuple[Path | None, list[dict[str, Any]], list[Issue]]:
    """Load the manifest-declared trace and apply the canonical validator path."""
    raw_paths = manifest.get("artifact_paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
    trace_relative = str(paths.get("propagation_trace", "propagation-trace.jsonl"))
    evidence_relative = str(paths.get("evidence_map", "evidence-map.csv"))
    trace_path, trace_data, trace_load_issues = load_workspace_artifact(
        workspace,
        trace_relative,
        kind="jsonl",
    )
    _, evidence_data, evidence_load_issues = load_workspace_artifact(
        workspace,
        evidence_relative,
        kind="csv",
    )
    issues = [*trace_load_issues, *evidence_load_issues]
    trace_rows = trace_data if isinstance(trace_data, list) else []
    evidence_rows = evidence_data if isinstance(evidence_data, list) else []
    node_ids = {
        str(value["id"])
        for value in nodes
        if isinstance(value, dict) and value.get("id")
    }
    nodes_by_id = {
        str(value["id"]): value
        for value in nodes
        if isinstance(value, dict) and value.get("id")
    }
    edge_by_id = {
        str(value["id"]): value
        for value in edges
        if isinstance(value, dict) and value.get("id")
    }
    evidence_ids = {
        str(value["evidence_id"])
        for value in evidence_rows
        if isinstance(value, dict) and value.get("evidence_id")
    }
    result = validate_trace(
        trace_rows,
        node_ids,
        edge_by_id,
        evidence_ids,
        manifest,
        nodes_by_id,
    )
    issues.extend(result.issues)
    return trace_path, trace_rows, issues
