"""Shared propagation-trace loading and semantic validation for run/replay CLIs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .io import canonical_hash, canonical_json_bytes, load_workspace_artifact
from .issues import Issue, issue
from .validator import validate_trace

RUN_ID_RE = re.compile(r"^run:[0-9a-zA-Z_-]+$")
EDGE_ID_RE = re.compile(r"^(causal|edge):[0-9a-zA-Z_-]+$")
PARAMS_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_execution_trace_data(
    trace: dict[str, Any],
    node_ids: set[str] | None = None,
    edge_by_id: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> list[Issue]:
    """Validate an execution-trace.json object against the authoritative schema and integrity rules."""
    issues: list[Issue] = []
    if not isinstance(trace, dict):
        return [issue("TYPE", pointer="/", message="execution trace must be an object")]

    required_fields = [
        "schema_version",
        "run_id",
        "model_id",
        "formula_version",
        "generated_by",
        "generation_mode",
        "execution_timestamp",
        "seed",
        "parameters_hash",
        "total_ticks",
        "total_samples",
        "invalid_mass",
        "steps",
        "replay_hash",
    ]
    for field in required_fields:
        if field not in trace:
            issues.append(issue("MISSING_FIELD", pointer=f"/{field}", message=f"missing required field {field}"))

    # Check generation_mode
    mode = trace.get("generation_mode")
    if mode not in {"engine_derived", "analyst_authored_legacy", "replay_verification"}:
        issues.append(issue("ENUM", pointer="/generation_mode", actual=mode, message="invalid generation_mode"))

    # Check formula_version
    formula = trace.get("formula_version")
    if formula not in {"2.0.0", "2.1.0"}:
        issues.append(issue("ENUM", pointer="/formula_version", actual=formula, message="invalid formula_version"))

    # Check run_id
    run_id_val = str(trace.get("run_id", ""))
    if not RUN_ID_RE.match(run_id_val):
        issues.append(issue("FORMAT", pointer="/run_id", actual=run_id_val, message="must match ^run:[0-9a-zA-Z_-]+$"))

    # Check parameters_hash
    phash = str(trace.get("parameters_hash", ""))
    if not PARAMS_HASH_RE.match(phash):
        issues.append(issue("FORMAT", pointer="/parameters_hash", actual=phash, message="must match ^sha256:[0-9a-f]{64}$"))
    elif config is not None:
        expected_phash = None
        if isinstance(config, str) and config.startswith("sha256:"):
            expected_phash = config
        elif isinstance(config, dict):
            if "parameters_hash" in config:
                expected_phash = str(config["parameters_hash"])
            elif "model" in config and "config" in config:
                expected_phash = f"sha256:{canonical_hash(config)}"
        if expected_phash is not None and phash != expected_phash:
            issues.append(
                issue(
                    "PARAMETERS_HASH_MISMATCH",
                    pointer="/parameters_hash",
                    expected=expected_phash,
                    actual=phash,
                    message="trace parameters_hash does not match configuration hash",
                )
            )

    # Check replay_hash
    rhash = str(trace.get("replay_hash", ""))
    if not HEX64_RE.match(rhash):
        issues.append(issue("FORMAT", pointer="/replay_hash", actual=rhash, message="must match ^[0-9a-f]{64}$"))

    # Check invalid_mass
    imass = trace.get("invalid_mass")
    if not isinstance(imass, (int, float)) or isinstance(imass, bool) or not (0.0 <= float(imass) <= 1.0):
        issues.append(issue("RANGE", pointer="/invalid_mass", actual=imass, message="must be number between 0.0 and 1.0"))

    steps = trace.get("steps")
    if not isinstance(steps, list):
        issues.append(issue("TYPE", pointer="/steps", message="steps must be an array"))
        return issues

    total_samples = trace.get("total_samples", 1)
    prev_hash = "0" * 64
    for idx, step in enumerate(steps):
        p = f"/steps/{idx}"
        if not isinstance(step, dict):
            issues.append(issue("TYPE", pointer=p, message="step must be an object"))
            continue
        step_required = [
            "step",
            "sample_id",
            "tick",
            "emission_tick",
            "delivery_tick",
            "node_id",
            "edge_id",
            "drawn_strength",
            "sampled_parameters",
            "state_transitions",
            "hash_chain",
        ]
        for sf in step_required:
            if sf not in step:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{sf}", message=f"step missing required field {sf}"))

        # Check sample_id bounds
        sample_id = step.get("sample_id")
        if isinstance(sample_id, int) and not isinstance(sample_id, bool):
            if isinstance(total_samples, int) and (sample_id < 0 or sample_id >= total_samples):
                issues.append(issue("OUT_OF_BOUNDS", pointer=f"{p}/sample_id", actual=sample_id, message="sample_id out of range"))

        # Check edge_id format and existence
        edge_id = str(step.get("edge_id", ""))
        if not EDGE_ID_RE.match(edge_id):
            issues.append(issue("FORMAT", pointer=f"{p}/edge_id", actual=edge_id, message="must match ^(causal|edge):[0-9a-zA-Z_-]+$"))
        if edge_by_id is not None:
            raw_id = edge_id.split(":", 1)[1] if ":" in edge_id else edge_id
            if edge_id not in edge_by_id and raw_id not in edge_by_id:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/edge_id", actual=edge_id))

        # Check node_id existence
        node_id = step.get("node_id")
        if node_ids is not None and node_id not in node_ids:
            issues.append(issue("UNKNOWN_REF", pointer=f"{p}/node_id", actual=node_id))

        # Check hash_chain
        hchain = step.get("hash_chain")
        if not isinstance(hchain, str) or not HEX64_RE.match(hchain):
            issues.append(issue("FORMAT", pointer=f"{p}/hash_chain", actual=hchain, message="hash_chain must be 64-char hex"))
        else:
            step_copy = {k: v for k, v in step.items() if k != "hash_chain"}
            step_bytes = canonical_json_bytes(step_copy)
            hasher = hashlib.sha256()
            hasher.update(prev_hash.encode("utf-8") + b":" + step_bytes)
            expected_hash = hasher.hexdigest()
            if hchain != expected_hash:
                issues.append(issue("HASH_MISMATCH", pointer=f"{p}/hash_chain", expected=expected_hash, actual=hchain, message="hash_chain divergence"))
            prev_hash = hchain

    if steps and prev_hash != rhash:
        issues.append(issue("HASH_MISMATCH", pointer="/replay_hash", expected=prev_hash, actual=rhash, message="replay_hash does not match final step hash_chain"))

    return issues


def validate_declared_trace(
    workspace: Path,
    manifest: dict[str, Any],
    nodes: list[Any],
    edges: list[Any],
    config: dict[str, Any] | None = None,
) -> tuple[Path | None, list[dict[str, Any]], list[Issue]]:
    """Load the manifest-declared trace (or auto-detected trace) and validate its contract."""
    raw_paths = manifest.get("artifact_paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}

    # Prefer execution_trace if declared, then propagation_trace, then check existence
    if "execution_trace" in paths:
        trace_relative = str(paths["execution_trace"])
    elif "propagation_trace" in paths:
        trace_relative = str(paths["propagation_trace"])
    else:
        # Default auto-detect: check execution-trace.json first, then propagation-trace.jsonl
        candidate_json = workspace / "execution-trace.json"
        candidate_jsonl = workspace / "propagation-trace.jsonl"
        if candidate_json.is_file():
            trace_relative = "execution-trace.json"
        elif candidate_jsonl.is_file():
            trace_relative = "propagation-trace.jsonl"
        else:
            trace_relative = "execution-trace.json"

    evidence_relative = str(paths.get("evidence_map", "evidence-map.csv"))
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

    # If it's a JSON file (Execution Trace Schema)
    if trace_relative.endswith(".json"):
        trace_path, trace_data, trace_load_issues = load_workspace_artifact(
            workspace,
            trace_relative,
            kind="json",
        )
        if trace_load_issues or trace_path is None or not isinstance(trace_data, dict):
            return trace_path, [], trace_load_issues

        semantic_issues = validate_execution_trace_data(
            trace_data,
            node_ids=node_ids,
            edge_by_id=edge_by_id,
            manifest=manifest,
            config=config,
        )
        steps = trace_data.get("steps", [])
        return trace_path, steps if isinstance(steps, list) else [], semantic_issues

    # Otherwise treat as JSONL (legacy propagation trace)
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

