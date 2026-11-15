"""Transactional finalization and cryptographic artifact receipts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import FORMULA_VERSION, SCHEMA_VERSION, SUPPORTED_FORMULA_VERSIONS, VALIDATOR_VERSION
from .io import (
    canonical_hash,
    load_json_secure,
    sha256_bytes,
    sha256_file,
    write_bytes_atomic,
    write_json_atomic,
)
from .quality import evaluate
from .schema import CHECKPOINT_FIELDS
from .validator import artifact_integrity_hash, manifest_integrity_payload, validate_workspace


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _media(relative: str) -> str:
    if relative.endswith(".json"):
        return "application/json"
    if relative.endswith(".jsonl"):
        return "application/x-ndjson"
    if relative.endswith(".csv"):
        return "text/csv"
    if relative.endswith(".md"):
        return "text/markdown"
    return "application/octet-stream"


def _artifact_paths(manifest: dict[str, Any], workspace: Path | None = None) -> list[str]:
    raw_declared = manifest.get("artifact_paths")
    declared: dict[str, Any] = raw_declared if isinstance(raw_declared, dict) else {}
    excluded = {"validation_report", "quality_report"}
    paths = ["simulation-manifest.json"]
    for key, relative in declared.items():
        if key not in excluded and isinstance(relative, str):
            paths.append(relative.replace("\\", "/"))
    if workspace is not None:
        if "interventions" not in declared and (workspace / "interventions.json").is_file():
            paths.append("interventions.json")
        if "execution_trace" not in declared and "propagation_trace" not in declared and (workspace / "execution-trace.json").is_file():
            paths.append("execution-trace.json")
    return list(dict.fromkeys(paths))


def build_artifact_index(
    workspace: Path,
    relative_paths: list[str],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        path = workspace / normalized
        if normalized == "simulation-manifest.json":
            size = len(_json_bytes(manifest))
            digest = canonical_hash(manifest_integrity_payload(manifest))
            hash_scope = "manifest_input_contract"
        else:
            if not path.is_file():
                continue
            size = path.stat().st_size
            digest = artifact_integrity_hash(path, normalized, manifest)
            hash_scope = "full_file"
        index.append(
            {
                "path": normalized,
                "media_type": _media(normalized),
                "size": size,
                "sha256": digest,
                "hash_scope": hash_scope,
            }
        )
    return index


def _schema_digest(workspace: Path) -> str:
    schema_dir = Path(__file__).resolve().parents[2] / "schemas"
    hashes: dict[str, str] = {}
    if schema_dir.is_dir():
        for path in sorted(schema_dir.rglob("*.json")):
            hashes[path.relative_to(schema_dir).as_posix()] = sha256_file(path)
    return canonical_hash(hashes)


def _stable_manifest_index(workspace: Path, manifest: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    """Resolve the manifest size field to a stable value (hash excludes index)."""
    index: list[dict[str, Any]] = []
    for _ in range(8):
        manifest["artifact_index"] = index
        candidate = build_artifact_index(workspace, paths, manifest)
        if candidate == index:
            return candidate
        index = candidate
    manifest["artifact_index"] = index
    return build_artifact_index(workspace, paths, manifest)


def _restore(backups: dict[Path, bytes | None]) -> None:
    for path, content in backups.items():
        if content is None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            write_bytes_atomic(path, content)


def finalize_workspace(
    workspace: Path,
    *,
    require_report: bool = True,
    complete: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    manifest_path = workspace / "simulation-manifest.json"
    pre_complete_backup: bytes | None = None
    if complete and manifest_path.is_file():
        pre_manifest, pre_issues = load_json_secure(manifest_path)
        if not pre_issues and isinstance(pre_manifest, dict):
            pre_complete_backup = manifest_path.read_bytes()
            pre_manifest["status"] = "completed"
            execution = pre_manifest.get("execution")
            if isinstance(execution, dict):
                raw_checkpoints = execution.get("checkpoints")
                checkpoints = dict(raw_checkpoints) if isinstance(raw_checkpoints, dict) else {}
                for cp_name in CHECKPOINT_FIELDS:
                    checkpoints[cp_name] = True
                execution["checkpoints"] = checkpoints
            write_bytes_atomic(manifest_path, _json_bytes(pre_manifest))

    validation = validate_workspace(
        workspace,
        mode="final",
        require_report=require_report,
        verify_integrity=False,
    )
    if validation.get("status") != "pass":
        if pre_complete_backup is not None:
            write_bytes_atomic(manifest_path, pre_complete_backup)
        return {"ok": False, "exit_code": 1, "validation": validation, "finalized": False}

    manifest, manifest_issues = load_json_secure(manifest_path)
    if manifest_issues or not isinstance(manifest, dict):
        details = "; ".join(value.legacy_string() for value in manifest_issues)
        return {
            "ok": False,
            "exit_code": 1,
            "finalized": False,
            "issues": [
                {
                    "code": "INVALID_ARTIFACT",
                    "severity": "error",
                    "artifact": "simulation-manifest.json",
                    "message": details or "manifest must be a JSON object",
                }
            ],
        }

    transaction_id = f"finalize:{uuid.uuid4()}"
    committed_at = _utc()
    quality = evaluate(workspace, validation=validation, final_receipt_verified=True)
    assurance_tier = quality.get("assurance_tier")
    raw_exec = manifest.get("execution")
    raw_rc = raw_exec.get("research_control") if isinstance(raw_exec, dict) else None
    if isinstance(raw_rc, dict) and raw_rc.get("offline_provisional") is True and assurance_tier in {"verified", "calibrated"}:
        assurance_tier = "limited"
    manifest["assurance_tier"] = assurance_tier
    manifest["finalization"] = {
        "status": "committed",
        "committed_at": committed_at,
        "transaction_id": transaction_id,
    }
    paths = _artifact_paths(manifest, workspace)
    index = _stable_manifest_index(workspace, manifest, paths)
    manifest["artifact_index"] = index
    artifact_hashes = {entry["path"]: entry["sha256"] for entry in index}
    receipt_formula_version = manifest.get("formula_version", FORMULA_VERSION)
    raw_paths = manifest.get("artifact_paths")
    run_relative = raw_paths.get("run_ledger") if isinstance(raw_paths, dict) else None
    if isinstance(run_relative, str):
        run_contract, run_issues = load_json_secure(workspace / run_relative)
        if not run_issues and isinstance(run_contract, dict):
            receipt_formula_version = run_contract.get("formula_version", "2.0.0")
    if receipt_formula_version not in SUPPORTED_FORMULA_VERSIONS:
        receipt_formula_version = FORMULA_VERSION

    validation_receipt = {
        "schema_version": SCHEMA_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "formula_version": receipt_formula_version,
        "schema_digest": _schema_digest(workspace),
        "bundle_digest": canonical_hash(artifact_hashes),
        "artifact_hashes": artifact_hashes,
        "status": "pass",
        "assurance_status": validation.get("assurance_status"),
        "created_at": committed_at,
        "transaction_id": transaction_id,
    }
    validation_receipt_hash = sha256_bytes(_json_bytes(validation_receipt))
    quality_receipt = {
        "schema_version": SCHEMA_VERSION,
        "validation_receipt_hash": validation_receipt_hash,
        "artifact_hashes": artifact_hashes,
        "assurance_tier": quality.get("assurance_tier"),
        "diagnostic_score": quality.get("diagnostic_score"),
        "status": quality.get("assurance_status"),
        "created_at": committed_at,
        "transaction_id": transaction_id,
    }
    quality_receipt_hash = sha256_bytes(_json_bytes(quality_receipt))
    manifest["validation_receipt"] = {
        "path": "validation-receipt.json",
        "sha256": validation_receipt_hash,
        "bundle_digest": validation_receipt["bundle_digest"],
    }
    manifest["quality_receipt"] = {
        "path": "quality-receipt.json",
        "sha256": quality_receipt_hash,
        "assurance_tier": quality.get("assurance_tier"),
    }
    # Receipt references alter file size but not the normalized manifest hash.
    manifest["artifact_index"] = _stable_manifest_index(workspace, manifest, paths)
    artifact_hashes = {entry["path"]: entry["sha256"] for entry in manifest["artifact_index"]}
    validation_receipt["artifact_hashes"] = artifact_hashes
    validation_receipt["bundle_digest"] = canonical_hash(artifact_hashes)
    manifest["validation_receipt"]["bundle_digest"] = validation_receipt["bundle_digest"]
    validation_receipt_hash = sha256_bytes(_json_bytes(validation_receipt))
    quality_receipt["validation_receipt_hash"] = validation_receipt_hash
    quality_receipt["artifact_hashes"] = artifact_hashes
    manifest["validation_receipt"]["sha256"] = validation_receipt_hash
    manifest["quality_receipt"]["sha256"] = sha256_bytes(_json_bytes(quality_receipt))
    # One last size stabilization after receipt hash strings are final.
    manifest["artifact_index"] = _stable_manifest_index(workspace, manifest, paths)

    targets = {
        workspace / "validation-receipt.json": _json_bytes(validation_receipt),
        workspace / "quality-receipt.json": _json_bytes(quality_receipt),
        workspace / "validation-report.json": _json_bytes(validation),
        workspace / "quality-report.json": _json_bytes(quality),
        manifest_path: _json_bytes(manifest),
    }
    backups: dict[Path, bytes | None] = {}
    try:
        for path in targets:
            backups[path] = path.read_bytes() if path.exists() else None
        # Manifest is the transaction marker and is always replaced last.
        for path, content in targets.items():
            if path != manifest_path:
                write_bytes_atomic(path, content)
        write_bytes_atomic(manifest_path, targets[manifest_path])
        post_validation = validate_workspace(
            workspace,
            mode="final",
            require_report=require_report,
            verify_integrity=True,
            require_receipts=True,
        )
        if post_validation.get("status") != "pass":
            _restore(backups)
            if pre_complete_backup is not None:
                write_bytes_atomic(manifest_path, pre_complete_backup)
            return {
                "ok": False,
                "exit_code": 3,
                "finalized": False,
                "validation": post_validation,
                "rolled_back": True,
            }
        write_json_atomic(workspace / "validation-report.json", post_validation)
        post_quality = evaluate(workspace, validation=post_validation)
        write_json_atomic(workspace / "quality-report.json", post_quality)
    except (OSError, ValueError, TypeError) as exc:
        _restore(backups)
        if pre_complete_backup is not None:
            write_bytes_atomic(manifest_path, pre_complete_backup)
        return {
            "ok": False,
            "exit_code": 3,
            "finalized": False,
            "rolled_back": True,
            "issues": [{"code": "VALIDATION_FAILED", "severity": "error", "message": f"finalization failed closed: {type(exc).__name__}: {exc}"}],
        }

    return {
        "ok": True,
        "exit_code": 0,
        "finalized": True,
        "validation": post_validation,
        "quality": post_quality,
        "validation_receipt": validation_receipt,
        "quality_receipt": quality_receipt,
        "transaction_id": transaction_id,
    }
