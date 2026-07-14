"""Transactional migration from Aleph 1.x workspaces to schema 2.0."""

from __future__ import annotations

import copy
import shutil
import uuid
from pathlib import Path
from typing import Any

from . import LEGACY_SCHEMA_VERSION, SCHEMA_VERSION
from .io import canonical_hash, load_json_secure, sha256_file, write_json_atomic


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _workspace_digest(root: Path) -> tuple[str, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append({"path": relative, "type": "symlink", "target": str(path.readlink())})
        elif path.is_file():
            entries.append({"path": relative, "type": "file", "size": path.stat().st_size, "sha256": sha256_file(path)})
    return canonical_hash(entries), entries


def _load_json(path: Path) -> Any:
    data, problems = load_json_secure(path)
    if problems:
        details = "; ".join(problem.legacy_string() for problem in problems)
        raise ValueError(f"invalid JSON artifact {path}: {details}")
    return data


def plan_migration(source: Path) -> dict[str, Any]:
    source = source.resolve()
    manifest_path = source / "simulation-manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": "missing simulation-manifest.json"}
    try:
        manifest = _load_json(manifest_path)
    except ValueError as exc:
        return {"ok": False, "error": f"invalid simulation-manifest.json: {exc}"}
    version = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or version not in {
        LEGACY_SCHEMA_VERSION,
        "1.0.0",
        "1.1.0",
        "1.2.0",
        SCHEMA_VERSION,
    }:
        return {"ok": False, "error": f"unsupported source schema {version}"}
    try:
        source_digest, source_files = _workspace_digest(source)
    except OSError as exc:
        return {"ok": False, "error": f"cannot hash complete source: {exc}"}
    symlinks = [entry for entry in source_files if entry["type"] == "symlink"]
    if symlinks:
        return {"ok": False, "error": "source contains symlinks; migration refuses ambiguous trees", "symlinks": symlinks}
    if version == SCHEMA_VERSION:
        return {
            "ok": True,
            "already_current": True,
            "source_schema": version,
            "target_schema": version,
            "transforms": [],
            "unresolved": [],
            "source_digest": source_digest,
            "source_files": source_files,
        }

    transforms = [
        "set schema_version to 2.0.0",
        "materialize the change-point assumption record",
        "map edge.confidence to evidence_confidence",
        "map base_strength to draft effect_parameter.reference_value",
        "invalidate legacy validation and quality receipts",
        "downgrade branch probability to relative_weight",
        "clear uncalibrated node probability",
        "mark migrated execution claims for re-execution",
    ]
    unresolved: list[dict[str, Any]] = []
    for name in ("validation-report.json", "quality-report.json"):
        if (source / name).exists():
            unresolved.append({"item": name, "field": "receipt", "note": "legacy result invalidated; re-run required"})
    edges_path = source / "edges.json"
    if edges_path.is_file():
        try:
            edges = _load_json(edges_path)
        except ValueError as exc:
            return {"ok": False, "error": f"invalid edges.json: {exc}"}
        for edge in edges if isinstance(edges, list) else []:
            if isinstance(edge, dict) and "base_strength" in edge and "effect_parameter" not in edge:
                unresolved.append(
                    {"item": edge.get("id"), "field": "effect_parameter", "note": "draft reference requires review"}
                )
    actors_path = source / "actors.json"
    nodes_path = source / "nodes.json"
    if actors_path.is_file() and nodes_path.is_file():
        try:
            actors = _load_json(actors_path)
            nodes = _load_json(nodes_path)
        except ValueError as exc:
            return {"ok": False, "error": f"invalid actor/node artifact: {exc}"}
        node_types = (
            {
                value["id"]: value.get("type")
                for value in nodes
                if isinstance(value, dict) and isinstance(value.get("id"), str)
            }
            if isinstance(nodes, list)
            else {}
        )
        for actor in actors if isinstance(actors, list) else []:
            if not isinstance(actor, dict):
                continue
            person_node = actor.get("person_node")
            if not isinstance(person_node, str) or node_types.get(person_node) != "entity":
                unresolved.append({"item": actor.get("id"), "field": "person_node", "note": "must reference entity"})
            materiality = actor.get("materiality")
            if not isinstance(materiality, str) or materiality not in {"material", "non_material"}:
                unresolved.append({"item": actor.get("id"), "field": "materiality", "note": "requires classification"})
    return {
        "ok": True,
        "source_schema": version,
        "target_schema": SCHEMA_VERSION,
        "transforms": transforms,
        "unresolved": unresolved,
        "source_digest": source_digest,
        "source_files": source_files,
    }


def _transform_edge(edge: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(edge)
    if "confidence" in result and "evidence_confidence" not in result:
        result["evidence_confidence"] = result["confidence"]
    if "base_strength" in result and "effect_parameter" not in result:
        result["effect_parameter"] = {
            "kind": "draft_reference",
            "reference_value": result["base_strength"],
            "note": "migrated from base_strength; review required",
        }
    return result


def _transform_node(node: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(node)
    result.pop("probability", None)
    return result


def _transform_branch(branch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(branch)
    if "probability" in result:
        result["relative_weight"] = result.pop("probability")
    result["likelihood_mode"] = "relative_weight"
    return result


def _post_migration_unresolved(destination: Path, unresolved: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Materialize every remaining draft-validation blocker in the migration receipt."""
    from .validator import validate_workspace

    validation = validate_workspace(destination, mode="draft", require_report=False)
    merged = list(unresolved)
    observed = {
        (item.get("item"), item.get("field"), item.get("code"), item.get("note"))
        for item in merged
    }
    for raw in validation.get("issues", []):
        if not isinstance(raw, dict):
            continue
        record = {
            "item": raw.get("artifact") or "workspace",
            "field": raw.get("pointer") or "",
            "code": raw.get("code") or "MIGRATION_REVIEW",
            "note": raw.get("message") or "post-migration validation requires review",
        }
        key = (record["item"], record["field"], record["code"], record["note"])
        if key not in observed:
            merged.append(record)
            observed.add(key)
    return merged, str(validation.get("status") or "fail")


def _apply_transforms(destination: Path, plan: dict[str, Any], source: Path, final_destination: Path) -> dict[str, Any]:
    manifest_path = destination / "simulation-manifest.json"
    manifest = _load_json(manifest_path)
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["likelihood_mode"] = "relative_weight"
    manifest["simulation_mode"] = manifest.get("simulation_mode") or "qualitative"
    manifest["assurance_tier"] = None
    manifest.pop("validation_receipt", None)
    manifest.pop("quality_receipt", None)
    for stale in ("validation-report.json", "quality-report.json"):
        path = destination / stale
        if path.exists():
            path.unlink()
    unresolved = list(plan.get("unresolved") or [])
    change_point = manifest.get("change_point")
    if not isinstance(change_point, dict):
        change_point = {}
        manifest["change_point"] = change_point
    assumption_ref = change_point.get("assumption_ref")
    if not isinstance(assumption_ref, str) or not assumption_ref.startswith("assumption:"):
        assumption_ref = "assumption:migrated-change-point"
        change_point["assumption_ref"] = assumption_ref
    raw_assumptions = manifest.get("assumptions")
    assumptions: list[dict[str, str]] = []
    declared_assumptions: set[str] = set()
    for index, item in enumerate(raw_assumptions if isinstance(raw_assumptions, list) else []):
        if isinstance(item, str):
            statement = item.strip()
            candidate_id: Any = None
        elif isinstance(item, dict):
            raw_statement = item.get("statement")
            statement = raw_statement.strip() if isinstance(raw_statement, str) else ""
            candidate_id = item.get("id")
        else:
            continue
        if not statement:
            continue
        if (
            not isinstance(candidate_id, str)
            or not candidate_id.startswith("assumption:")
            or candidate_id in declared_assumptions
        ):
            candidate_id = f"assumption:migrated-{index + 1:03d}"
            suffix = 1
            while candidate_id in declared_assumptions:
                suffix += 1
                candidate_id = f"assumption:migrated-{index + 1:03d}-{suffix}"
        assumptions.append({"id": candidate_id, "statement": statement})
        declared_assumptions.add(candidate_id)
    if assumption_ref not in declared_assumptions:
        assumptions.append(
            {
                "id": assumption_ref,
                "statement": "Migrated change-point assumption; review before finalization.",
            }
        )
    manifest["assumptions"] = assumptions
    manifest["status"] = "draft"
    manifest["migration"] = {
        "source_schema_version": plan.get("source_schema"),
        "target_schema_version": SCHEMA_VERSION,
        "source_digest": plan.get("source_digest"),
        "transforms": list(plan.get("transforms") or []),
        "unresolved_fields": unresolved,
    }
    edges_path = destination / "edges.json"
    if edges_path.is_file():
        edges = _load_json(edges_path)
        if isinstance(edges, list):
            write_json_atomic(edges_path, [_transform_edge(value) if isinstance(value, dict) else value for value in edges])
    nodes_path = destination / "nodes.json"
    if nodes_path.is_file():
        nodes = _load_json(nodes_path)
        if isinstance(nodes, list):
            write_json_atomic(
                nodes_path,
                [_transform_node(value) if isinstance(value, dict) else value for value in nodes],
            )
    branch_path = destination / "branch-ledger.json"
    if branch_path.is_file():
        ledger = _load_json(branch_path)
        if isinstance(ledger, dict) and isinstance(ledger.get("branches"), list):
            ledger["schema_version"] = SCHEMA_VERSION
            ledger["likelihood_mode"] = "relative_weight"
            ledger["calibrated"] = False
            ledger["branches"] = [_transform_branch(value) if isinstance(value, dict) else value for value in ledger["branches"]]
            write_json_atomic(branch_path, ledger)
    write_json_atomic(manifest_path, manifest)
    unresolved, validation_status = _post_migration_unresolved(destination, unresolved)
    manifest["migration"]["unresolved_fields"] = unresolved
    write_json_atomic(manifest_path, manifest)
    report_body = {
        "schema_version": SCHEMA_VERSION,
        "source_digest": plan.get("source_digest"),
        "source_file_count": len(plan.get("source_files") or []),
        "source_schema": plan.get("source_schema"),
        "transforms": plan.get("transforms"),
        "unresolved_fields": unresolved,
        "post_migration_validation_status": validation_status,
        "post_migration_issue_count": len(unresolved),
        "repair_checklist": [
            "Review draft effect parameters",
            "Recompile and replay numerical traces",
            "Rebuild roleplay execution receipts",
            "Run strict final validation",
        ],
        "status": "draft",
    }
    report_body["report_hash"] = canonical_hash(report_body)
    report = {**report_body, "source": str(source), "destination": str(final_destination)}
    write_json_atomic(destination / "migration-report.json", report)
    return report


def migrate_workspace(
    source: Path,
    out: Path | None = None,
    *,
    check_only: bool = False,
    in_place: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    plan = plan_migration(source)
    if not plan.get("ok"):
        return plan
    if check_only:
        return {**plan, "mode": "check"}
    if plan.get("already_current"):
        return {**plan, "destination": str(source), "source_mutated": False}

    if in_place:
        if backup_dir is None:
            return {"ok": False, "error": "in-place requires explicit backup_dir"}
        backup_root = backup_dir.resolve()
        backup_target = backup_root / source.name
        if (
            backup_target.exists()
            or (backup_root.exists() and not backup_root.is_dir())
            or _inside(backup_root, source)
            or _inside(source, backup_root)
        ):
            return {"ok": False, "error": "backup must be a new, non-overlapping external path"}
        destination = source
        stage = source.parent / f".{source.name}.migration-stage-{uuid.uuid4().hex}"
    else:
        destination = (out if out is not None else source.parent / f"{source.name}-v2").resolve()
        if destination.exists():
            return {"ok": False, "error": "destination exists; refusing destructive overwrite"}
        if _inside(destination, source) or _inside(source, destination):
            return {"ok": False, "error": "source and destination trees must not overlap"}
        stage = destination.parent / f".{destination.name}.migration-stage-{uuid.uuid4().hex}"
    if stage.exists():
        return {"ok": False, "error": "unexpected migration staging collision"}

    try:
        shutil.copytree(source, stage, symlinks=True)
        report = _apply_transforms(stage, plan, source, destination)
        migrated_digest, _ = _workspace_digest(stage)
        if in_place:
            backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, backup_target, symlinks=True)
            hold = source.parent / f".{source.name}.migration-old-{uuid.uuid4().hex}"
            source.rename(hold)
            try:
                stage.rename(source)
            except Exception:
                hold.rename(source)
                raise
            try:
                shutil.rmtree(hold)
            except OSError:
                # The migrated workspace and explicit backup are already sound.
                # A retained hold directory is safer than attempting another
                # destructive rollback after the atomic swap succeeded.
                pass
        else:
            stage.rename(destination)
    except Exception as exc:
        if stage.exists():
            shutil.rmtree(stage)
        return {"ok": False, "error": f"migration rolled back: {exc}"}

    if not in_place:
        after_digest, _ = _workspace_digest(source)
        if after_digest != plan["source_digest"]:
            if destination.exists():
                shutil.rmtree(destination)
            return {"ok": False, "error": "source changed during sibling migration", "source_mutated": True}
    return {
        "ok": True,
        "destination": str(destination),
        "report": report,
        "canonical_hash": report["report_hash"],
        "source_digest": plan["source_digest"],
        "migrated_digest": migrated_digest,
        "source_mutated": in_place,
    }


def migrate_dual_run_canonical(source: Path, out_a: Path, out_b: Path) -> dict[str, Any]:
    first = migrate_workspace(source, out_a)
    second = migrate_workspace(source, out_b)
    if not first.get("ok") or not second.get("ok"):
        return {"ok": False, "r1": first, "r2": second}
    files = ["simulation-manifest.json", "migration-report.json"]
    if (out_a / "edges.json").is_file():
        files.append("edges.json")
    if (out_a / "branch-ledger.json").is_file():
        files.append("branch-ledger.json")
    hashes: dict[str, Any] = {}
    for name in files:
        left = _load_json(out_a / name)
        right = _load_json(out_b / name)
        if name == "migration-report.json":
            left = {key: value for key, value in left.items() if key not in {"source", "destination"}}
            right = {key: value for key, value in right.items() if key not in {"source", "destination"}}
        left_hash, right_hash = canonical_hash(left), canonical_hash(right)
        hashes[name] = {"a": left_hash, "b": right_hash, "equal": left_hash == right_hash}
    return {"ok": all(value["equal"] for value in hashes.values()), "hashes": hashes, "r1": first, "r2": second}
