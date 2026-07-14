from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _lib import SKILL_NAME, skill_root
from aleph import EXIT_OK, EXIT_SECURITY, EXIT_USAGE
from aleph.adapters_registry import ALL_TARGETS, PORTABLE_CORE_PATH, TARGET_SPECS
from aleph.installer import (
    discard_install_backup,
    install,
    install_adapter_file,
    plan_install,
    receipt_path_issues,
    rollback_install_result,
)
from aleph.io import canonical_hash, write_json_atomic
from aleph.paths import assert_install_paths_safe


def destination(target: str, scope: str, project_dir: Path, custom_dest: Path | None = None) -> Path:
    if custom_dest is not None:
        return custom_dest.absolute()
    if target in {"generic", "custom"}:
        return (project_dir / ".agents" / "skills" / SKILL_NAME) if scope == "project" else Path.home() / ".agents" / "skills" / SKILL_NAME
    spec = TARGET_SPECS.get(target)
    if spec is None:
        raise ValueError(f"Unknown target {target}")
    template = spec.get("project_path") if scope == "project" else spec.get("user_path")
    if not template:
        raise ValueError(f"Target {target} has no {scope} default; pass --dest or use --scope project")
    if str(template).startswith("~/"):
        return Path.home() / str(template)[2:]
    return project_dir / str(template)


def _write_bundle_receipt(result: dict[str, Any], receipt_path: Path | None) -> dict[str, Any]:
    if receipt_path is None:
        return result
    source_raw = result.get("source")
    core_raw = result.get("core_destination")
    destination_raw = result.get("destination")
    current_issues = []
    if source_raw and core_raw:
        current_issues.extend(
            receipt_path_issues(
                receipt_path,
                source=Path(str(source_raw)),
                destination=Path(str(core_raw)),
                destination_is_directory=True,
            )
        )
    if source_raw and destination_raw:
        current_issues.extend(
            receipt_path_issues(
                receipt_path,
                source=Path(str(source_raw)),
                destination=Path(str(destination_raw)),
                destination_is_directory=False,
            )
        )
    if current_issues:
        unique_issues = {
            (
                value.code,
                value.artifact,
                value.pointer,
                value.message,
            ): value.to_dict()
            for value in current_issues
        }
        result.update(
            {
                "ok": False,
                "status": "failed",
                "issues": [
                    *list(result.get("issues") or []),
                    *unique_issues.values(),
                ],
            }
        )
        return result
    receipt = {
        "schema_version": "2.0.0",
        "target": result.get("target"),
        "mode": result.get("mode"),
        "status": result.get("status"),
        "ok": result.get("ok"),
        "destination": result.get("destination"),
        "core_destination": result.get("core_destination"),
        "core": result.get("core"),
        "adapter": result.get("adapter"),
        "issues": result.get("issues", []),
    }
    receipt["receipt_hash"] = canonical_hash(receipt)
    try:
        write_json_atomic(receipt_path, receipt)
    except OSError as exc:
        result.update(
            {
                "ok": False,
                "status": "failed",
                "issues": [
                    *list(result.get("issues") or []),
                    {"code": "INSTALL_SOURCE_DEST", "severity": "error", "message": f"receipt write failed: {exc}"},
                ],
            }
        )
        return result
    result["receipt"] = str(receipt_path)
    result["receipt_hash"] = receipt["receipt_hash"]
    return result


def _rollback_core(core_result: dict[str, Any], core_destination: Path) -> str:
    return rollback_install_result(core_result, core_destination)


def _rollback_adapter(adapter_result: dict[str, Any], destination_file: Path) -> str:
    return rollback_install_result(adapter_result, destination_file)


def install_portable_adapter(
    source_root: Path,
    source_file: Path,
    destination_file: Path,
    project_dir: Path,
    *,
    target: str,
    mode: str,
    force: bool,
    receipt_path: Path | None,
) -> dict[str, Any]:
    """Install a thin host adapter together with its verified portable core."""
    core_destination = (project_dir / PORTABLE_CORE_PATH).absolute()
    base: dict[str, Any] = {
        "ok": False,
        "target": target,
        "mode": mode,
        "source": str(source_root),
        "destination": str(destination_file),
        "core_destination": str(core_destination),
        "issues": [],
    }
    if mode not in {"dry-run", "copy"}:
        base.update(
            {
                "status": "refused",
                "issues": [
                    {
                        "code": "INSTALL_SOURCE_DEST",
                        "severity": "error",
                        "message": "portable adapter mode must be dry-run or copy",
                    }
                ],
            }
        )
        return base
    overlap_issues = assert_install_paths_safe(core_destination, destination_file)
    receipt_issues = [
        *receipt_path_issues(
            receipt_path,
            source=source_root,
            destination=core_destination,
            destination_is_directory=True,
        ),
        *receipt_path_issues(
            receipt_path,
            source=source_root,
            destination=destination_file,
            destination_is_directory=False,
        ),
    ]
    if overlap_issues or receipt_issues:
        base.update(
            {
                "status": "refused",
                "issues": [
                    value.to_dict() for value in [*overlap_issues, *receipt_issues]
                ],
            }
        )
        return base
    if mode == "copy" and not force:
        occupied = []
        if core_destination.exists() or core_destination.is_symlink():
            occupied.append(str(core_destination))
        if destination_file.exists() or destination_file.is_symlink():
            occupied.append(str(destination_file))
        if occupied:
            base.update(
                {
                    "status": "refused",
                    "issues": [
                        {
                            "code": "INSTALL_SOURCE_DEST",
                            "severity": "error",
                            "message": f"destination exists; use force: {occupied}",
                        }
                    ],
                }
            )
            return _write_bundle_receipt(base, receipt_path)

    core_plan = plan_install(source_root, core_destination, "dry-run" if mode == "dry-run" else "copy")
    adapter_plan = install_adapter_file(
        source_file,
        destination_file,
        mode="dry-run",
        force=force,
        source_root=source_root,
    )
    base.update({"core": core_plan, "adapter": adapter_plan})
    preflight_issues = [
        *list(core_plan.get("issues") or []),
        *list(adapter_plan.get("issues") or []),
    ]
    if not core_plan.get("ok") or not adapter_plan.get("ok"):
        base.update({"status": "refused", "issues": preflight_issues})
        return _write_bundle_receipt(base, receipt_path)
    if mode == "dry-run":
        base.update({"ok": True, "status": "dry-run", "issues": []})
        return _write_bundle_receipt(base, receipt_path)

    core_result = install(
        source_root,
        core_destination,
        mode="copy",
        force=force,
        retain_backup=True,
    )
    base["core"] = core_result
    if not core_result.get("ok") or core_result.get("status") != "copied":
        base.update(
            {
                "status": str(core_result.get("status") or "failed"),
                "issues": list(core_result.get("issues") or []),
            }
        )
        return _write_bundle_receipt(base, receipt_path)
    adapter_result = install_adapter_file(
        source_file,
        destination_file,
        mode="copy",
        force=force,
        source_root=source_root,
        retain_backup=True,
    )
    base["adapter"] = adapter_result
    if not adapter_result.get("ok") or adapter_result.get("status") != "copied":
        rollback = _rollback_core(core_result, core_destination)
        base.update(
            {
                "status": "failed",
                "rollback_status": rollback,
                "issues": list(adapter_result.get("issues") or []),
            }
        )
        return _write_bundle_receipt(base, receipt_path)
    base.update(
        {
            "ok": True,
            "status": "copied",
            "file_count": int(core_result.get("file_count") or 0) + 1,
            "issues": [],
        }
    )
    finished = _write_bundle_receipt(base, receipt_path)
    if finished.get("status") == "failed":
        finished["rollback_status"] = {
            "adapter": _rollback_adapter(adapter_result, destination_file),
            "core": _rollback_core(core_result, core_destination),
        }
    else:
        finished["backup_cleanup"] = {
            "adapter": discard_install_backup(adapter_result),
            "core": discard_install_backup(core_result),
        }
    return finished


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Aleph Skill into agent skill paths.")
    parser.add_argument("--target", default="agents", help=f"One of {ALL_TARGETS} or custom")
    parser.add_argument("--scope", default="user", choices=["user", "project"])
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--dest", help="Custom destination path")
    parser.add_argument("--mode", default="dry-run", choices=["dry-run", "copy", "symlink"])
    parser.add_argument("--dry-run", action="store_true", help="Alias for --mode dry-run")
    parser.add_argument("--copy", action="store_true", help="Alias for --mode copy")
    parser.add_argument("--symlink", action="store_true", help="Alias for --mode symlink")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--receipt", help="Write installation receipt JSON")
    args = parser.parse_args()

    mode = args.mode
    if args.dry_run:
        mode = "dry-run"
    if args.copy:
        mode = "copy"
    if args.symlink:
        mode = "symlink"

    src = skill_root()
    try:
        dest = destination(
            args.target,
            args.scope,
            Path(args.project_dir).resolve(),
            Path(args.dest).absolute() if args.dest else None,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from exc

    receipt = Path(args.receipt).resolve() if args.receipt else None
    spec = TARGET_SPECS.get(args.target)
    if spec and spec.get("install_kind") in {"instruction_file", "external_profile"}:
        if args.scope != "project":
            result = {
                "ok": False,
                "status": "refused",
                "mode": mode,
                "source": str(src),
                "destination": str(dest),
                "issues": [
                    {
                        "code": "INSTALL_SOURCE_DEST",
                        "severity": "error",
                        "message": "thin IDE/CLI adapters require --scope project so the verified core has a stable project path",
                    }
                ],
            }
        else:
            source_file = src / str(spec["source_path"])
            result = install_portable_adapter(
                src,
                source_file,
                dest,
                Path(args.project_dir).resolve(),
                target=args.target,
                mode=mode,
                force=args.force,
                receipt_path=receipt,
            )
    else:
        result = install(src, dest, mode=mode, force=args.force, receipt_path=receipt)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"source={result.get('source')}")
        print(f"destination={result.get('destination')}")
        print(f"mode={result.get('mode')}")
        print(f"status={result.get('status')}")
        print(f"file_count={result.get('file_count')}")
        if result.get("issues"):
            for iss in result["issues"]:
                print(f"ISSUE: {iss}", file=sys.stderr)

    status = result.get("status")
    if status in {"refused", "failed"} or not result.get("ok", True) and mode != "dry-run":
        if result.get("issues"):
            raise SystemExit(EXIT_SECURITY)
    if mode == "dry-run" and not result.get("ok"):
        raise SystemExit(EXIT_SECURITY)
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
