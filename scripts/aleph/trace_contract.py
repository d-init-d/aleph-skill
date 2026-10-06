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
SCHEMA_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
EDGE_ID_RE = re.compile(r"^(causal|edge):[0-9a-zA-Z_-]+$")
PARAMS_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_REF_RE = re.compile(r"^evidence:[0-9a-zA-Z_-]+$")

ALLOWED_TOP_LEVEL_KEYS = frozenset({
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
    "final_state_vector",
    "replay_hash",
})

REQUIRED_TOP_LEVEL_KEYS = [
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

ALLOWED_STEP_KEYS = frozenset({
    "step",
    "sample_id",
    "tick",
    "emission_tick",
    "delivery_tick",
    "source_node_id",
    "node_id",
    "edge_id",
    "drawn_strength",
    "sampled_parameters",
    "source_state",
    "target_state_before",
    "target_state_after",
    "state_transitions",
    "stock_flow_integrations",
    "transform_applied",
    "evidence_refs",
    "hash_chain",
})

REQUIRED_STEP_KEYS = [
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


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    import math
    try:
        f = float(value)
        return math.isfinite(f)
    except (OverflowError, ValueError):
        return False


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_valid_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    import datetime
    s = value
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
        return dt.tzinfo is not None
    except Exception:
        return False


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

    # Check for unknown top-level fields (additionalProperties: false)
    for field in sorted(trace.keys()):
        if field not in ALLOWED_TOP_LEVEL_KEYS:
            issues.append(issue("UNKNOWN_FIELD", pointer=f"/{field}", actual=field, message=f"unknown field '{field}' not allowed in execution trace"))

    for field in REQUIRED_TOP_LEVEL_KEYS:
        if field not in trace:
            issues.append(issue("MISSING_FIELD", pointer=f"/{field}", message=f"missing required field {field}"))

    # Validate schema_version
    if "schema_version" in trace:
        sv = trace["schema_version"]
        if not isinstance(sv, str):
            issues.append(issue("TYPE", pointer="/schema_version", actual=sv, message="schema_version must be a string"))
        elif not SCHEMA_VERSION_RE.match(sv):
            issues.append(issue("FORMAT", pointer="/schema_version", actual=sv, message="must match ^[0-9]+\\.[0-9]+\\.[0-9]+$"))

    # Validate model_id
    if "model_id" in trace:
        mid = trace["model_id"]
        if not isinstance(mid, str):
            issues.append(issue("TYPE", pointer="/model_id", actual=mid, message="model_id must be a string"))
        elif len(mid) < 1:
            issues.append(issue("EMPTY_ID", pointer="/model_id", actual=mid, message="model_id must be a non-empty string"))

    # Validate formula_version
    if "formula_version" in trace:
        formula = trace["formula_version"]
        if not isinstance(formula, str):
            issues.append(issue("TYPE", pointer="/formula_version", actual=formula, message="formula_version must be a string"))
        elif formula not in {"2.0.0", "2.1.0"}:
            issues.append(issue("ENUM", pointer="/formula_version", actual=formula, message="invalid formula_version, must be 2.0.0 or 2.1.0"))

    # Validate generated_by
    if "generated_by" in trace:
        gen_by = trace["generated_by"]
        if not isinstance(gen_by, str):
            issues.append(issue("TYPE", pointer="/generated_by", actual=gen_by, message="generated_by must be a string"))
        elif len(gen_by) < 1:
            issues.append(issue("RANGE", pointer="/generated_by", actual=gen_by, message="generated_by must be a non-empty string"))

    # Validate generation_mode
    if "generation_mode" in trace:
        mode = trace["generation_mode"]
        if not isinstance(mode, str):
            issues.append(issue("TYPE", pointer="/generation_mode", actual=mode, message="generation_mode must be a string"))
        elif mode not in {"engine_derived", "analyst_authored_legacy", "replay_verification"}:
            issues.append(issue("ENUM", pointer="/generation_mode", actual=mode, message="invalid generation_mode"))

    # Validate execution_timestamp
    if "execution_timestamp" in trace:
        ts = trace["execution_timestamp"]
        if not isinstance(ts, str) or not _is_valid_iso_datetime(ts):
            issues.append(issue("FORMAT", pointer="/execution_timestamp", actual=ts, message="execution_timestamp must be ISO 8601 date-time with timezone"))

    # Validate seed
    if "seed" in trace:
        sd = trace["seed"]
        if not _is_integer(sd):
            issues.append(issue("TYPE", pointer="/seed", actual=sd, message="seed must be an integer"))

    # Validate run_id
    if "run_id" in trace:
        run_id_val = trace["run_id"]
        if not isinstance(run_id_val, str):
            issues.append(issue("TYPE", pointer="/run_id", actual=run_id_val, message="run_id must be a string"))
        elif not RUN_ID_RE.match(run_id_val):
            issues.append(issue("FORMAT", pointer="/run_id", actual=run_id_val, message="must match ^run:[0-9a-zA-Z_-]+$"))

    # Validate parameters_hash
    if "parameters_hash" in trace:
        phash = trace["parameters_hash"]
        if not isinstance(phash, str):
            issues.append(issue("TYPE", pointer="/parameters_hash", actual=phash, message="parameters_hash must be a string"))
        elif not PARAMS_HASH_RE.match(phash):
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

    # Validate total_ticks
    if "total_ticks" in trace:
        tt = trace["total_ticks"]
        if not _is_integer(tt):
            issues.append(issue("TYPE", pointer="/total_ticks", actual=tt, message="total_ticks must be an integer"))
        elif tt < 1:
            issues.append(issue("RANGE", pointer="/total_ticks", actual=tt, message="total_ticks must be >= 1"))

    # Validate total_samples
    total_samples = trace.get("total_samples", 1)
    if "total_samples" in trace:
        tsamp = trace["total_samples"]
        if not _is_integer(tsamp):
            issues.append(issue("TYPE", pointer="/total_samples", actual=tsamp, message="total_samples must be an integer"))
        elif tsamp < 1:
            issues.append(issue("RANGE", pointer="/total_samples", actual=tsamp, message="total_samples must be >= 1"))

    # Validate invalid_mass
    if "invalid_mass" in trace:
        imass = trace["invalid_mass"]
        if not _is_finite_number(imass):
            issues.append(issue("TYPE", pointer="/invalid_mass", actual=imass, message="invalid_mass must be a finite number"))
        elif not (0.0 <= float(imass) <= 1.0):
            issues.append(issue("RANGE", pointer="/invalid_mass", actual=imass, message="must be number between 0.0 and 1.0"))

    # Validate replay_hash
    rhash = None
    if "replay_hash" in trace:
        raw_rhash = trace["replay_hash"]
        if not isinstance(raw_rhash, str):
            issues.append(issue("TYPE", pointer="/replay_hash", actual=raw_rhash, message="replay_hash must be a string"))
        elif not HEX64_RE.match(raw_rhash):
            issues.append(issue("FORMAT", pointer="/replay_hash", actual=raw_rhash, message="must match ^[0-9a-f]{64}$"))
        else:
            rhash = raw_rhash

    # Validate final_state_vector (optional)
    if "final_state_vector" in trace:
        fsv = trace["final_state_vector"]
        if not isinstance(fsv, dict):
            issues.append(issue("TYPE", pointer="/final_state_vector", actual=fsv, message="final_state_vector must be an object"))
        else:
            for vk, vv in fsv.items():
                if not _is_finite_number(vv):
                    issues.append(issue("TYPE", pointer=f"/final_state_vector/{vk}", actual=vv, message="final_state_vector value must be a finite number"))

    steps = trace.get("steps")
    if not isinstance(steps, list):
        issues.append(issue("TYPE", pointer="/steps", message="steps must be an array"))
        return issues

    prev_hash = "0" * 64
    for idx, step in enumerate(steps):
        p = f"/steps/{idx}"
        if not isinstance(step, dict):
            issues.append(issue("TYPE", pointer=p, message="step must be an object"))
            continue

        for sk in sorted(step.keys()):
            if sk not in ALLOWED_STEP_KEYS:
                issues.append(issue("UNKNOWN_FIELD", pointer=f"{p}/{sk}", actual=sk, message=f"unknown field '{sk}' in step"))

        for sf in REQUIRED_STEP_KEYS:
            if sf not in step:
                issues.append(issue("MISSING_FIELD", pointer=f"{p}/{sf}", message=f"step missing required field {sf}"))

        # Check step counter
        if "step" in step:
            st_num = step["step"]
            if not _is_integer(st_num):
                issues.append(issue("TYPE", pointer=f"{p}/step", actual=st_num, message="step must be an integer"))
            elif st_num < 1:
                issues.append(issue("RANGE", pointer=f"{p}/step", actual=st_num, message="step must be >= 1"))

        # Check sample_id bounds
        if "sample_id" in step:
            sample_id = step["sample_id"]
            if not _is_integer(sample_id):
                issues.append(issue("TYPE", pointer=f"{p}/sample_id", actual=sample_id, message="sample_id must be an integer"))
            elif sample_id < 0:
                issues.append(issue("RANGE", pointer=f"{p}/sample_id", actual=sample_id, message="sample_id must be >= 0"))
            elif _is_integer(total_samples) and sample_id >= total_samples:
                issues.append(issue("OUT_OF_BOUNDS", pointer=f"{p}/sample_id", actual=sample_id, message="sample_id out of range"))

        # Check tick, emission_tick, delivery_tick
        for tick_field in ["tick", "emission_tick", "delivery_tick"]:
            if tick_field in step:
                tf_val = step[tick_field]
                if not _is_integer(tf_val):
                    issues.append(issue("TYPE", pointer=f"{p}/{tick_field}", actual=tf_val, message=f"{tick_field} must be an integer"))
                elif tf_val < 0:
                    issues.append(issue("RANGE", pointer=f"{p}/{tick_field}", actual=tf_val, message=f"{tick_field} must be >= 0"))

        if _is_integer(step.get("emission_tick")) and _is_integer(step.get("delivery_tick")):
            if step["delivery_tick"] < step["emission_tick"]:
                issues.append(issue("RANGE", pointer=f"{p}/delivery_tick", actual=step["delivery_tick"], message="delivery_tick must be >= emission_tick"))

        # Check edge_id format and existence
        if "edge_id" in step:
            edge_id_raw = step["edge_id"]
            if not isinstance(edge_id_raw, str):
                issues.append(issue("TYPE", pointer=f"{p}/edge_id", actual=edge_id_raw, message="edge_id must be a string"))
            elif not EDGE_ID_RE.match(edge_id_raw):
                issues.append(issue("FORMAT", pointer=f"{p}/edge_id", actual=edge_id_raw, message="must match ^(causal|edge):[0-9a-zA-Z_-]+$"))
            elif edge_by_id is not None:
                raw_id = edge_id_raw.split(":", 1)[1] if ":" in edge_id_raw else edge_id_raw
                if edge_id_raw not in edge_by_id and raw_id not in edge_by_id:
                    issues.append(issue("UNKNOWN_REF", pointer=f"{p}/edge_id", actual=edge_id_raw))

        # Check node_id existence
        if "node_id" in step:
            node_id_raw = step["node_id"]
            if not isinstance(node_id_raw, str):
                issues.append(issue("TYPE", pointer=f"{p}/node_id", actual=node_id_raw, message="node_id must be a string"))
            elif len(node_id_raw) < 1:
                issues.append(issue("EMPTY_ID", pointer=f"{p}/node_id", actual=node_id_raw, message="node_id must be non-empty"))
            elif node_ids is not None and node_id_raw not in node_ids:
                issues.append(issue("UNKNOWN_REF", pointer=f"{p}/node_id", actual=node_id_raw))

        # Check drawn_strength
        if "drawn_strength" in step:
            ds = step["drawn_strength"]
            if not _is_finite_number(ds):
                issues.append(issue("TYPE", pointer=f"{p}/drawn_strength", actual=ds, message="drawn_strength must be a finite number"))

        # Check source_state
        if "source_state" in step:
            ss = step["source_state"]
            if ss is None or not _is_finite_number(ss):
                issues.append(issue("TYPE", pointer=f"{p}/source_state", actual=ss, message="source_state must be a finite number"))

        # Check target_state_before & target_state_after
        if "target_state_before" in step:
            tsb = step["target_state_before"]
            if tsb is None or not _is_finite_number(tsb):
                issues.append(issue("TYPE", pointer=f"{p}/target_state_before", actual=tsb, message="target_state_before must be a finite number"))
        if "target_state_after" in step:
            tsa = step["target_state_after"]
            if tsa is None or not _is_finite_number(tsa):
                issues.append(issue("TYPE", pointer=f"{p}/target_state_after", actual=tsa, message="target_state_after must be a finite number"))

        # Check sampled_parameters
        if "sampled_parameters" in step:
            sp = step["sampled_parameters"]
            if sp is None or not isinstance(sp, dict):
                issues.append(issue("TYPE", pointer=f"{p}/sampled_parameters", actual=sp, message="sampled_parameters must be an object"))
            else:
                for sp_k, sp_v in sp.items():
                    if isinstance(sp_v, bool):
                        continue
                    elif isinstance(sp_v, (int, float)):
                        if not _is_finite_number(sp_v):
                            issues.append(issue("TYPE", pointer=f"{p}/sampled_parameters/{sp_k}", actual=sp_v, message="number must be finite"))
                    elif isinstance(sp_v, str):
                        continue
                    else:
                        issues.append(issue("TYPE", pointer=f"{p}/sampled_parameters/{sp_k}", actual=sp_v, message="sampled_parameters values must be number, string, or boolean"))

        # Check stock_flow_integrations
        if "stock_flow_integrations" in step:
            sfi = step["stock_flow_integrations"]
            if sfi is None or not isinstance(sfi, dict):
                issues.append(issue("TYPE", pointer=f"{p}/stock_flow_integrations", actual=sfi, message="stock_flow_integrations must be an object"))
            else:
                for req in ["stock_variable", "inflow_rate", "outflow_rate", "retention_factor", "integrated_level"]:
                    if req not in sfi:
                        issues.append(issue("MISSING_FIELD", pointer=f"{p}/stock_flow_integrations/{req}", message=f"missing required field {req}"))
                if "stock_variable" in sfi and not isinstance(sfi["stock_variable"], str):
                    issues.append(issue("TYPE", pointer=f"{p}/stock_flow_integrations/stock_variable", actual=sfi["stock_variable"], message="stock_variable must be a string"))
                for num_field in ["inflow_rate", "outflow_rate", "integrated_level", "decay_constant", "dt"]:
                    if num_field in sfi and not _is_finite_number(sfi[num_field]):
                        issues.append(issue("TYPE", pointer=f"{p}/stock_flow_integrations/{num_field}", actual=sfi[num_field], message=f"{num_field} must be a finite number"))
                if "retention_factor" in sfi:
                    rf = sfi["retention_factor"]
                    if not _is_finite_number(rf):
                        issues.append(issue("TYPE", pointer=f"{p}/stock_flow_integrations/retention_factor", actual=rf, message="retention_factor must be a finite number"))
                    elif not (0.0 <= float(rf) <= 1.0):
                        issues.append(issue("RANGE", pointer=f"{p}/stock_flow_integrations/retention_factor", actual=rf, message="retention_factor must be between 0 and 1"))

        # Check transform_applied
        if "transform_applied" in step:
            tfa = step["transform_applied"]
            if tfa is None or not isinstance(tfa, dict):
                issues.append(issue("TYPE", pointer=f"{p}/transform_applied", actual=tfa, message="transform_applied must be an object"))
            else:
                if "mode" in tfa:
                    raw_mode = tfa["mode"]
                    if raw_mode is None or not isinstance(raw_mode, str):
                        issues.append(issue("TYPE", pointer=f"{p}/transform_applied/mode", actual=raw_mode, message="transform_applied mode must be a string"))
                    elif raw_mode not in {
                        "linear", "threshold", "logistic", "elasticity", "identity", "saturation",
                        "above", "below", "deadband", "hysteresis",
                    }:
                        issues.append(issue("ENUM", pointer=f"{p}/transform_applied/mode", actual=raw_mode, message="invalid transform mode"))
                elif "transform" in tfa:
                    raw_mode = tfa["transform"]
                    if raw_mode is None or not isinstance(raw_mode, str):
                        issues.append(issue("TYPE", pointer=f"{p}/transform_applied/transform", actual=raw_mode, message="transform_applied mode must be a string"))
                    elif raw_mode not in {
                        "linear", "threshold", "logistic", "elasticity", "identity", "saturation",
                        "above", "below", "deadband", "hysteresis",
                    }:
                        issues.append(issue("ENUM", pointer=f"{p}/transform_applied/transform", actual=raw_mode, message="invalid transform mode"))
                if "threshold_active_before" in tfa and not isinstance(tfa["threshold_active_before"], bool):
                    issues.append(issue("TYPE", pointer=f"{p}/transform_applied/threshold_active_before", actual=tfa["threshold_active_before"], message="must be boolean"))
                if "threshold_active_after" in tfa and not isinstance(tfa["threshold_active_after"], bool):
                    issues.append(issue("TYPE", pointer=f"{p}/transform_applied/threshold_active_after", actual=tfa["threshold_active_after"], message="must be boolean"))

        # Check state_transitions
        if "state_transitions" in step:
            st = step["state_transitions"]
            if not isinstance(st, dict):
                issues.append(issue("TYPE", pointer=f"{p}/state_transitions", actual=st, message="state_transitions must be an object"))
            else:
                for req_st in ["delta", "previous_value", "new_value", "mechanism"]:
                    if req_st not in st:
                        issues.append(issue("MISSING_FIELD", pointer=f"{p}/state_transitions/{req_st}", message=f"state_transitions missing required field {req_st}"))
                for num_st in ["delta", "previous_value", "new_value"]:
                    if num_st in st and not _is_finite_number(st[num_st]):
                        issues.append(issue("TYPE", pointer=f"{p}/state_transitions/{num_st}", actual=st[num_st], message=f"{num_st} must be a finite number"))
                if "mechanism" in st and not isinstance(st["mechanism"], str):
                    issues.append(issue("TYPE", pointer=f"{p}/state_transitions/mechanism", actual=st["mechanism"], message="mechanism must be a string"))

        # Check evidence_refs (optional)
        if "evidence_refs" in step:
            erefs = step["evidence_refs"]
            if not isinstance(erefs, list):
                issues.append(issue("TYPE", pointer=f"{p}/evidence_refs", actual=erefs, message="evidence_refs must be an array"))
            else:
                for e_idx, eref in enumerate(erefs):
                    if not isinstance(eref, str) or not EVIDENCE_REF_RE.match(eref):
                        issues.append(issue("FORMAT", pointer=f"{p}/evidence_refs/{e_idx}", actual=eref, message="must match ^evidence:[0-9a-zA-Z_-]+$"))

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

    if steps and rhash and prev_hash != rhash:
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

