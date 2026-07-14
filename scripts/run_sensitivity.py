from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, cast

from _lib import load_json
from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.engine import EngineConfig, compile_model, model_hash, run_deterministic
from aleph.io import canonical_hash, write_json_atomic
from aleph.paths import output_alias_issues, resolve_in_workspace
from aleph.sensitivity import morris_screening, one_at_a_time, sobol_saltelli_optional
from compile_model import load_interventions


def main() -> None:
    parser = argparse.ArgumentParser(description="Run sensitivity analysis against a workspace model.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--spec", default="sensitivity-config.json")
    parser.add_argument("--method", choices=["oat", "morris", "sobol"])
    parser.add_argument("--seed", default="0")
    parser.add_argument("--out", default="sensitivity-report.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        raise SystemExit(EXIT_USAGE)
    spec_path, spec_issues = resolve_in_workspace(workspace, args.spec, must_exist=True)
    out_path, out_issues = resolve_in_workspace(workspace, args.out, must_exist=False, require_file=False)
    if out_path is not None:
        out_issues.extend(
            output_alias_issues(
                out_path,
                [
                    spec_path if spec_path is not None else workspace / args.spec,
                    workspace / "simulation-manifest.json",
                ],
            )
        )
    if spec_issues or out_issues or spec_path is None or out_path is None:
        print(json.dumps({"ok": False, "issues": [value.to_dict() for value in spec_issues + out_issues]}, indent=2))
        raise SystemExit(EXIT_USAGE)
    spec = load_json(spec_path)
    if not isinstance(spec, dict) or not isinstance(spec.get("parameters"), list) or not isinstance(spec.get("output"), dict):
        print(json.dumps({"ok": False, "error": "invalid sensitivity spec"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)
    manifest = load_json(workspace / "simulation-manifest.json") if (workspace / "simulation-manifest.json").is_file() else {}
    raw_paths = manifest.get("artifact_paths") if isinstance(manifest, dict) else None
    artifact_paths = raw_paths if isinstance(raw_paths, dict) else {}
    node_path, node_issues = resolve_in_workspace(
        workspace, str(artifact_paths.get("nodes", "nodes.json")), must_exist=True
    )
    edge_path, edge_issues = resolve_in_workspace(
        workspace, str(artifact_paths.get("edges", "edges.json")), must_exist=True
    )
    if out_path is not None and node_path is not None and edge_path is not None:
        out_issues.extend(output_alias_issues(out_path, [node_path, edge_path]))
    if node_issues or edge_issues or out_issues or node_path is None or edge_path is None:
        print(
            json.dumps(
                {"ok": False, "issues": [value.to_dict() for value in [*node_issues, *edge_issues, *out_issues]]},
                indent=2,
            )
        )
        raise SystemExit(EXIT_USAGE)
    nodes = load_json(node_path)
    edges = load_json(edge_path)
    try:
        model = compile_model(
            nodes if isinstance(nodes, list) else [],
            edges if isinstance(edges, list) else [],
            load_interventions(workspace, manifest if isinstance(manifest, dict) else {}),
        )
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "code": "MODEL_COMPILE"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    output_id = str(spec["output"].get("variable", ""))
    parameters: dict[str, dict[str, Any]] = {}
    base: dict[str, float] = {}
    bounds: dict[str, tuple[float, float]] = {}
    edge_ids = {edge.id for edge in model.edges}
    for raw in spec["parameters"]:
        if not isinstance(raw, dict):
            continue
        parameter_id = str(raw.get("id", ""))
        edge_id = str(raw.get("edge_id", ""))
        if not parameter_id or edge_id not in edge_ids:
            continue
        minimum_raw = raw.get("min")
        maximum_raw = raw.get("max")
        baseline_raw = raw.get(
            "baseline", next(edge.strength for edge in model.edges if edge.id == edge_id)
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (minimum_raw, maximum_raw, baseline_raw)
        ):
            continue
        minimum = float(cast(int | float, minimum_raw))
        maximum = float(cast(int | float, maximum_raw))
        baseline = float(cast(int | float, baseline_raw))
        if not minimum <= baseline <= maximum:
            continue
        parameters[parameter_id] = raw
        base[parameter_id] = baseline
        bounds[parameter_id] = (minimum, maximum)
    if not parameters or output_id not in model.variables:
        print(json.dumps({"ok": False, "error": "spec references no valid parameters or output"}, indent=2))
        raise SystemExit(EXIT_SEMANTIC)
    ticks = int(spec.get("ticks", 1))

    def evaluate(values: dict[str, float]) -> float:
        candidate = copy.deepcopy(model)
        by_id = {edge.id: edge for edge in candidate.edges}
        for parameter_id, value in values.items():
            by_id[str(parameters[parameter_id]["edge_id"])].strength = float(value)
            by_id[str(parameters[parameter_id]["edge_id"])].effect_distribution = None
        result = run_deterministic(candidate, EngineConfig(seed=args.seed), ticks=ticks)
        if not result["ok"]:
            raise ValueError("sensitivity evaluation did not converge")
        return float(result["payload"]["final_state"][output_id])

    method = args.method or str(spec.get("method", "morris"))
    try:
        if method == "oat":
            analysis = one_at_a_time(
                base,
                evaluate,
                delta=float(spec.get("delta", 0.1)),
                bounds=bounds,
            )
        elif method == "morris":
            analysis = morris_screening(
                bounds,
                evaluate,
                seed=args.seed,
                trajectories=int(spec.get("trajectories", max(10, 2 * len(bounds)))),
                levels=int(spec.get("levels", 6)),
            )
        elif method == "sobol":
            analysis = sobol_saltelli_optional(bounds, evaluate, n=int(spec.get("n", 256)), seed=args.seed)
        else:
            raise ValueError(f"unsupported method {method}")
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        raise SystemExit(EXIT_SEMANTIC) from exc
    report = {
        "schema_version": "2.0.0",
        "model_hash": model_hash(model),
        "spec_hash": canonical_hash(spec),
        "output": output_id,
        "parameters": sorted(parameters),
        "analysis": analysis,
        "degraded": bool(analysis.get("degraded", False)),
    }
    report["report_hash"] = canonical_hash(report)
    write_json_atomic(out_path, report)
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
