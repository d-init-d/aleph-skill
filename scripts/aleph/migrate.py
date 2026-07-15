"""Transactional migration from Aleph 1.x workspaces to schema 2.0."""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from . import LEGACY_SCHEMA_VERSION, SCHEMA_VERSION
from .io import canonical_hash, load_json_secure, sha256_file, write_json_atomic
from .paths import resolve_in_workspace

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_EXTERNAL_SNAPSHOT_RUNTIME_DIRS = frozenset(
    {
        ".cache",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)
_BIND_REPORT = "migration-bind-report.json"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _workspace_digest(
    root: Path,
    *,
    excluded: frozenset[str] = frozenset(),
) -> tuple[str, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
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


def _tree_link_or_special_entries(root: Path) -> list[str]:
    """Return symlink, reparse-point, or non-file/non-directory entries without following them."""
    unsafe: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    relative = Path(entry.path).relative_to(root).as_posix()
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        unsafe.append(relative)
                        continue
                    reparse = bool(
                        getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
                    )
                    if entry.is_symlink() or reparse:
                        unsafe.append(relative)
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif not entry.is_file(follow_symlinks=False):
                        unsafe.append(relative)
        except OSError:
            unsafe.append(current.relative_to(root).as_posix() or ".")
    return sorted(set(unsafe))


def _copytree_without_links(source: Path, destination: Path) -> None:
    unsafe = _tree_link_or_special_entries(source)
    if unsafe:
        raise ValueError(f"workspace contains links or special files: {unsafe[0]}")
    shutil.copytree(source, destination, symlinks=True)
    copied_unsafe = _tree_link_or_special_entries(destination)
    if copied_unsafe:
        raise ValueError(f"copied workspace contains links or special files: {copied_unsafe[0]}")


def _component_lock_entry(skill_root: Path) -> dict[str, Any]:
    lock = _load_json(skill_root / "component-lock.json")
    if not isinstance(lock, dict):
        raise ValueError("component-lock.json must be an object")
    components = lock.get("components")
    if not isinstance(components, dict):
        raise ValueError("component-lock.json components must be an object")
    entry = components.get("d-research")
    if not isinstance(entry, dict) or not isinstance(entry.get("files"), list):
        raise ValueError("component-lock.json d-research entry is invalid")
    return entry


def _snapshot_recipe_exclusions(entry: dict[str, Any]) -> frozenset[str]:
    recipe = entry.get("snapshot_recipe")
    if not isinstance(recipe, dict):
        raise ValueError("component lock snapshot_recipe must be an object")
    raw_excluded = recipe.get("excluded_paths")
    if not isinstance(raw_excluded, list) or not all(
        isinstance(value, str) for value in raw_excluded
    ):
        raise ValueError("component lock snapshot_recipe.excluded_paths must be a string array")
    if raw_excluded != sorted(set(raw_excluded)):
        raise ValueError(
            "component lock snapshot_recipe.excluded_paths must be sorted and unique"
        )
    for relative in raw_excluded:
        normalized = relative.replace("\\", "/")
        parts = normalized.split("/")
        if (
            not normalized
            or normalized != relative
            or normalized.startswith("/")
            or len(normalized) >= 2
            and normalized[0].isalpha()
            and normalized[1] == ":"
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError(
                "component lock snapshot_recipe contains an unsafe excluded path"
            )
    return frozenset(raw_excluded)


def _snapshot_file_paths(
    root: Path,
    *,
    excluded_paths: frozenset[str],
) -> tuple[dict[str, Path], list[str]]:
    files: dict[str, Path] = {}
    unsafe: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative_path = path.relative_to(root)
                relative = relative_path.as_posix()
                if relative in excluded_paths:
                    continue
                stat = entry.stat(follow_symlinks=False)
                reparse = bool(
                    getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
                )
                if entry.is_symlink() or reparse:
                    unsafe.append(relative)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _EXTERNAL_SNAPSHOT_RUNTIME_DIRS:
                        stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    unsafe.append(relative)
                    continue
                files[relative] = path
    return files, sorted(unsafe)


def _external_snapshot_equivalence(
    external_root: Path,
    *,
    skill_root: Path,
) -> dict[str, Any]:
    """Compare an external installation with every byte in the locked embedded snapshot."""
    try:
        external_root = external_root.resolve(strict=True)
        if not external_root.is_dir():
            raise ValueError("external D Research root is not a directory")
        locked = _component_lock_entry(skill_root)
        excluded_paths = _snapshot_recipe_exclusions(locked)
        raw_files = locked.get("files")
        assert isinstance(raw_files, list)
        expected: dict[str, tuple[int, str]] = {}
        for item in raw_files:
            if not isinstance(item, dict):
                raise ValueError("component lock contains a malformed file entry")
            relative = item.get("path")
            size = item.get("size")
            digest = item.get("sha256")
            if not isinstance(relative, str) or not isinstance(size, int) or not isinstance(
                digest, str
            ):
                raise ValueError("component lock contains a malformed file entry")
            expected[relative] = (size, digest.removeprefix("sha256:"))
        overlap = sorted(set(expected) & excluded_paths)
        if overlap:
            raise ValueError(
                "component lock snapshot_recipe excludes a locked snapshot file: "
                f"{overlap[0]}"
            )
        actual, unsafe = _snapshot_file_paths(
            external_root,
            excluded_paths=excluded_paths,
        )
        if unsafe:
            return {
                "ok": False,
                "reason": "external D Research contains links, reparse points, or special files",
                "unsafe": unsafe[:20],
            }
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        mismatched: list[str] = []
        for relative, (size, digest) in expected.items():
            path = actual.get(relative)
            if path is None:
                continue
            if path.stat().st_size != size or sha256_file(path) != digest:
                mismatched.append(relative)
        ok = not missing and not extra and not mismatched
        return {
            "ok": ok,
            "comparison": "locked-snapshot-byte-exact",
            "expected_file_count": len(expected),
            "actual_file_count": len(actual),
            "missing": missing[:20],
            "extra": extra[:20],
            "mismatched": mismatched[:20],
            "component_tree_sha256": locked.get("tree_sha256"),
            "package_version": locked.get("version"),
            "upstream_commit": locked.get("upstream_commit"),
            "upstream_tag_object": locked.get("upstream_tag_object"),
            "snapshot_recipe_excluded_count": len(excluded_paths),
        }
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}


def _dual_run_research_canonical(helper: Path, ledger: Path) -> dict[str, Any]:
    """Execute the locked upstream canonicaliser and compare it with Aleph's implementation."""
    from .import_ledger import canonicalise_d_research_csv

    try:
        raw = ledger.read_bytes()
        aleph_bytes, _, _, issues = canonicalise_d_research_csv(raw)
        if aleph_bytes is None or issues:
            return {
                "ok": False,
                "reason": "Aleph canonicaliser rejected the ledger",
                "issues": [item.to_dict() for item in issues],
            }
        namespace: dict[str, Any] = {
            "__file__": str(helper),
            "__name__": "_aleph_locked_d_research_evidence_ledger",
        }
        source = helper.read_bytes()
        exec(compile(source, str(helper), "exec"), namespace)  # noqa: S102 - locked component
        canonicalise = namespace.get("canonicalise")
        if not callable(canonicalise):
            return {"ok": False, "reason": "locked upstream canonicalise() is unavailable"}
        upstream_bytes = canonicalise(ledger)
        if not isinstance(upstream_bytes, bytes):
            return {"ok": False, "reason": "locked upstream canonicalise() returned non-bytes"}
        aleph_sha256 = hashlib.sha256(aleph_bytes).hexdigest()
        upstream_sha256 = hashlib.sha256(upstream_bytes).hexdigest()
        return {
            "ok": aleph_bytes == upstream_bytes,
            "aleph_canonical_sha256": aleph_sha256,
            "upstream_canonical_sha256": upstream_sha256,
            "byte_equal": aleph_bytes == upstream_bytes,
            "helper_sha256": sha256_file(helper),
        }
    except (OSError, SyntaxError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}


def _invalidate_finalization(workspace: Path, manifest: dict[str, Any]) -> list[str]:
    invalidated: list[str] = []
    manifest["status"] = "draft"
    manifest["assurance_tier"] = None
    for field in ("artifact_index", "validation_receipt", "quality_receipt", "finalization"):
        if field in manifest:
            manifest.pop(field, None)
            invalidated.append(f"simulation-manifest.json#{field}")
    for name in (
        "validation-receipt.json",
        "quality-receipt.json",
        "validation-report.json",
        "quality-report.json",
    ):
        path = workspace / name
        if path.is_file():
            path.unlink()
            invalidated.append(name)
    return invalidated


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


def bind_bundled_d_research(
    workspace: Path,
    *,
    skill_root: Path | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    """Rewrite absolute external D Research paths to portable component URI when equivalent.

    Always writes sibling output unless check_only. Does not mutate source on --check.
    Keeps schema_version 2.0.0; only rewrites execution.d_research.path when the
    complete external snapshot is byte-equivalent to the locked component.
    """
    from .component_registry import (
        COMPONENT_URI,
        ComponentError,
        resolve_component,
        skill_root_from,
    )

    source = workspace.resolve()
    unsafe_source = _tree_link_or_special_entries(source) if source.is_dir() else []
    if unsafe_source:
        return {
            "ok": False,
            "error": "source contains links, reparse points, or special files",
            "unsafe": unsafe_source[:20],
        }
    manifest_path = source / "simulation-manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": "missing simulation-manifest.json"}
    try:
        manifest = _load_json(manifest_path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "manifest must be an object"}
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return {"ok": False, "error": f"schema must be {SCHEMA_VERSION}", "schema": manifest.get("schema_version")}

    root = skill_root if skill_root is not None else skill_root_from()
    try:
        resolution = resolve_component(COMPONENT_URI, skill_root=root, require_verified=True)
    except ComponentError as exc:
        return {"ok": False, "error": exc.message, "error_code": exc.code}

    raw_execution = manifest.get("execution")
    execution: dict[str, Any] = raw_execution if isinstance(raw_execution, dict) else {}
    raw_research = execution.get("d_research")
    research: dict[str, Any] = raw_research if isinstance(raw_research, dict) else {}
    old_path = research.get("path")
    prior_status = research.get("status")
    transforms: list[str] = []
    unresolved: list[dict[str, Any]] = []
    dual_run: dict[str, Any] | None = None
    equivalence: dict[str, Any] | None = None

    receipt_ref: str | None = None
    raw_artifact_paths = manifest.get("artifact_paths")
    artifact_paths: dict[str, Any] = (
        raw_artifact_paths if isinstance(raw_artifact_paths, dict) else {}
    )
    if isinstance(artifact_paths.get("research_import_receipt"), str):
        receipt_ref = artifact_paths["research_import_receipt"]

    can_rewrite = False
    if isinstance(old_path, str) and old_path not in {COMPONENT_URI, ""}:
        external_path = Path(old_path)
        if external_path.is_absolute():
            equivalence = _external_snapshot_equivalence(external_path, skill_root=root)
        else:
            equivalence = {
                "ok": False,
                "reason": "legacy D Research path must be absolute before it can be rebound",
            }
        can_rewrite = bool(equivalence.get("ok"))
        if can_rewrite:
            transforms.append(
                "rewrite byte-equivalent external D Research path to aleph-component://d-research"
            )
        else:
            unresolved.append(
                {
                    "item": "execution.d_research.path",
                    "field": "path",
                    "code": "COMPONENT_DRIFT",
                    "note": "external installation is not byte-equivalent to the locked snapshot",
                    "details": equivalence,
                }
            )
    elif old_path == COMPONENT_URI:
        transforms.append("path already portable component URI")
        can_rewrite = True
    else:
        if prior_status in {"imported", "verified"}:
            unresolved.append(
                {
                    "item": "execution.d_research.path",
                    "field": "path",
                    "code": "COMPONENT_IDENTITY_MISMATCH",
                    "note": "an imported/verified execution without a source path cannot be rebound",
                }
            )
        else:
            transforms.append("bind unused or missing path to bundled component URI")
            can_rewrite = True

    if prior_status in {"imported", "verified"} and receipt_ref is None:
        unresolved.append(
            {
                "item": "artifact_paths.research_import_receipt",
                "field": "research_import_receipt",
                "code": "MISSING_ARTIFACT",
                "note": "imported/verified research requires its preserved import receipt",
            }
        )
        can_rewrite = False

    # Execute both Aleph and the exact locked upstream canonicalisers when a ledger exists.
    ledger_ref = research.get("ledger_ref")
    if isinstance(ledger_ref, str):
        ledger_path, ledger_issues = resolve_in_workspace(
            source,
            ledger_ref,
            must_exist=True,
            require_file=True,
        )
        if ledger_path is None or ledger_issues:
            dual_run = {
                "ok": False,
                "issues": [item.to_dict() for item in ledger_issues],
            }
        else:
            dual_run = _dual_run_research_canonical(
                Path(resolution.root) / "scripts" / "evidence_ledger.py",
                ledger_path,
            )
        if not dual_run.get("ok"):
            unresolved.append(
                {
                    "item": ledger_ref,
                    "field": "ledger",
                    "code": "LEDGER_TAMPER",
                    "note": "Aleph and locked upstream canonicalisers did not match",
                    "details": dual_run,
                }
            )
            can_rewrite = False

    try:
        source_digest, _ = _workspace_digest(source)
    except OSError as exc:
        return {"ok": False, "error": f"cannot hash source workspace: {exc}"}

    report = {
        "schema_version": SCHEMA_VERSION,
        "ok": can_rewrite and not unresolved,
        "source_digest": source_digest,
        "target_digest": None,
        "target_digest_scope": f"workspace excluding {_BIND_REPORT}",
        "transforms": transforms,
        "unresolved_fields": unresolved,
        "dual_run": dual_run,
        "external_equivalence": equivalence,
        "component_uri": COMPONENT_URI,
        "component_binding": resolution.binding(),
        "mode": "check" if check_only else "write",
        "source_mutated": False,
        "finalization_invalidated": [],
    }
    if check_only or not can_rewrite:
        report["ok"] = can_rewrite and not unresolved
        return report

    destination = source.parent / f"{source.name}-bundled-bind"
    if destination.exists():
        return {"ok": False, "error": "destination exists; refusing overwrite", "destination": str(destination)}
    stage = source.parent / f".{source.name}.bind-stage-{uuid.uuid4().hex}"
    try:
        _copytree_without_links(source, stage)
        if _workspace_digest(source)[0] != source_digest:
            raise ValueError("source changed while creating the staged migration")
        stage_manifest = _load_json(stage / "simulation-manifest.json")
        if not isinstance(stage_manifest, dict):
            raise ValueError("invalid staged manifest")
        stage_execution = stage_manifest.setdefault("execution", {})
        if not isinstance(stage_execution, dict):
            stage_execution = {}
            stage_manifest["execution"] = stage_execution
        stage_research = stage_execution.setdefault("d_research", {})
        if not isinstance(stage_research, dict):
            stage_research = {}
            stage_execution["d_research"] = stage_research
        stage_research["path"] = COMPONENT_URI
        # Keep schema/formula stable.
        stage_manifest["schema_version"] = SCHEMA_VERSION
        # Rewrite receipt identity path if present and equivalent.
        if receipt_ref:
            receipt_path, receipt_issues = resolve_in_workspace(
                stage,
                receipt_ref,
                must_exist=True,
                require_file=True,
            )
            if receipt_path is None or receipt_issues:
                raise ValueError("research import receipt path is missing or unsafe")
            if receipt_path.is_file():
                receipt = _load_json(receipt_path)
                if isinstance(receipt, dict):
                    identity = receipt.get("d_research_identity")
                    if not isinstance(identity, dict):
                        raise ValueError("research import receipt lacks d_research_identity")
                    identity["path"] = COMPONENT_URI
                    identity["package_name"] = resolution.package_name
                    identity["package_version"] = resolution.package_version
                    identity["package_major"] = resolution.package_major
                    identity["upstream_commit"] = resolution.upstream_commit
                    identity["upstream_tag_object"] = resolution.upstream_tag_object
                    identity["ledger_helper_sha256"] = resolution.entrypoint_sha256.removeprefix(
                        "sha256:"
                    )
                    receipt["component_binding"] = resolution.binding()
                    body = {key: value for key, value in receipt.items() if key != "receipt_hash"}
                    receipt["receipt_hash"] = canonical_hash(body)
                    write_json_atomic(receipt_path, receipt)
                    transforms.append("rewrite import receipt to portable component binding")
                else:
                    raise ValueError("research import receipt must be an object")

        invalidated = _invalidate_finalization(stage, stage_manifest)
        report["finalization_invalidated"] = invalidated
        if invalidated:
            transforms.append("invalidate stale finalization, artifact index, and assurance receipts")
        write_json_atomic(stage / "simulation-manifest.json", stage_manifest)

        target_digest, _ = _workspace_digest(stage, excluded=frozenset({_BIND_REPORT}))
        report.update(
            {
                "ok": True,
                "destination": str(destination),
                "target_digest": target_digest,
                "transforms": transforms,
                "source_mutated": False,
            }
        )
        report_body = {key: value for key, value in report.items() if key != "report_hash"}
        report["report_hash"] = canonical_hash(report_body)
        write_json_atomic(stage / _BIND_REPORT, report)
        verified_target_digest, _ = _workspace_digest(
            stage,
            excluded=frozenset({_BIND_REPORT}),
        )
        if verified_target_digest != target_digest:
            raise ValueError("staged target digest changed while writing the migration report")
        if _workspace_digest(source)[0] != source_digest:
            raise ValueError("source changed before atomic publication")
        stage.rename(destination)
    except Exception as exc:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        return {"ok": False, "error": f"bind rolled back: {exc}"}
    try:
        if _workspace_digest(source)[0] != source_digest:
            shutil.rmtree(destination)
            return {
                "ok": False,
                "error": "source changed during sibling migration",
                "source_mutated": True,
            }
    except OSError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        return {"ok": False, "error": f"cannot reverify source after publication: {exc}"}
    return report
