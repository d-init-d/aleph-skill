from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from aleph import EXIT_OK, EXIT_SECURITY, EXIT_SEMANTIC
from aleph.discovery import discover_d_research
from aleph.import_ledger import import_d_research_ledger, render_evidence_csv
from aleph.io import canonical_hash, write_bytes_atomic, write_json_atomic, write_text_atomic


def _paths_alias(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    if left_resolved == right_resolved:
        return True
    if left.exists() and right.exists():
        try:
            return os.path.samefile(left, right)
        except OSError:
            return False
    return False


def _portable_ref(path: Path, workspace: Path) -> str:
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"receipt artifact is outside workspace: {resolved}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Import verified D Research ledger into Aleph evidence map.")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--hmac", help="HMAC sidecar path")
    parser.add_argument("--hmac-key-env", default="D_RESEARCH_LEDGER_KEY")
    parser.add_argument("--major", type=int, default=3)
    parser.add_argument("--d-research", help="D Research skill directory to bind into the import receipt")
    parser.add_argument("--out", help="Write evidence CSV")
    parser.add_argument("--raw-out", help="Preserve the original ledger at this path (default: <out>.source.csv)")
    parser.add_argument("--receipt-out", help="Write the cryptographic import receipt JSON")
    parser.add_argument("--workspace", help="Workspace root for portable receipt references (default: output parent)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    key = None
    env_key = os.environ.get(args.hmac_key_env)
    if env_key:
        key = env_key.encode("utf-8")

    ledger = Path(args.ledger)
    explicit_sidecar = Path(args.hmac) if args.hmac else None
    automatic_sidecar = ledger.with_suffix(ledger.suffix + ".hmac")
    sidecar = explicit_sidecar or (automatic_sidecar if automatic_sidecar.is_file() else None)
    out = Path(args.out) if args.out else None
    raw_out = (
        Path(args.raw_out)
        if args.raw_out
        else out.with_suffix(out.suffix + ".source.csv") if out is not None else None
    )
    receipt_out = (
        Path(args.receipt_out)
        if args.receipt_out
        else out.with_suffix(out.suffix + ".import-receipt.json") if out is not None else None
    )
    receipt_workspace = (
        Path(args.workspace).resolve()
        if args.workspace
        else out.parent.resolve() if out is not None else Path.cwd().resolve()
    )
    sidecar_out = raw_out.with_suffix(raw_out.suffix + ".hmac") if raw_out is not None and sidecar is not None else None
    sources = [ledger] + ([sidecar] if sidecar is not None else [])
    targets = [value for value in (out, raw_out, receipt_out, sidecar_out) if value is not None]
    aliases: list[tuple[str, str]] = []
    for index, left in enumerate(sources + targets):
        for right in (sources + targets)[index + 1 :]:
            if _paths_alias(left, right):
                aliases.append((str(left), str(right)))
    if aliases:
        print(json.dumps({"ok": False, "code": "PATH_ALIAS", "aliases": aliases}, indent=2))
        raise SystemExit(EXIT_SECURITY)
    try:
        portable_ledger_ref = _portable_ref(raw_out, receipt_workspace) if raw_out is not None else None
        portable_evidence_ref = _portable_ref(out, receipt_workspace) if out is not None else None
        portable_sidecar_ref = (
            _portable_ref(sidecar_out, receipt_workspace) if sidecar_out is not None else None
        )
        if receipt_out is not None:
            _portable_ref(receipt_out, receipt_workspace)
    except ValueError as exc:
        print(json.dumps({"ok": False, "code": "PATH_ESCAPE", "error": str(exc)}, indent=2))
        raise SystemExit(EXIT_SECURITY) from exc

    result = import_d_research_ledger(
        ledger,
        hmac_sidecar=sidecar,
        hmac_key=key,
        package_major=args.major,
    )
    from aleph.component_registry import COMPONENT_URI

    allow_external = bool(args.d_research) and str(args.d_research).strip() != COMPONENT_URI
    discovery = (
        discover_d_research(explicit=args.d_research, allow_external=allow_external, require_bundled=not allow_external)
        if args.d_research
        else discover_d_research()
    )
    if args.d_research and discovery.get("status") != "available":
        result.setdefault("issues", []).extend(discovery.get("issues") or [])
        result["ok"] = False
    identity: dict[str, object] | None = None
    component_binding: dict[str, object] | None = None
    binding = discovery.get("component_binding")
    if isinstance(binding, dict):
        component_binding = binding
    discovered_path = discovery.get("resolved_path") or discovery.get("path")
    if discovery.get("status") == "available" and isinstance(discovered_path, str):
        # Portable URI for bundled; absolute only for external compatibility.
        if discovery.get("source_kind") == "bundled" or discovered_path == COMPONENT_URI:
            root_fs = discovery.get("resolved_path")
            helper = Path(str(root_fs)) / "scripts" / "evidence_ledger.py" if root_fs else None
            identity_path: object = COMPONENT_URI
        else:
            helper = Path(discovered_path) / "scripts" / "evidence_ledger.py"
            identity_path = str(Path(discovered_path).resolve())
        if helper is not None and helper.is_file():
            identity = {
                "name": discovery.get("name"),
                "package_name": discovery.get("package_name"),
                "package_version": discovery.get("package_version"),
                "package_major": discovery.get("package_major"),
                "path": identity_path,
                "ledger_helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                "identity_verified": discovery.get("identity_verified") is True,
            }
    print(json.dumps({k: v for k, v in result.items() if k != "evidence_rows" or args.json}, indent=2, default=str))
    if out is not None and raw_out is not None and receipt_out is not None and result.get("ok"):
        rows = result.get("evidence_rows") or []
        raw_bytes = ledger.read_bytes()
        write_bytes_atomic(raw_out, raw_bytes)
        verified_sidecar = result.get("hmac_sidecar")
        if verified_sidecar and sidecar_out is not None:
            write_bytes_atomic(sidecar_out, Path(str(verified_sidecar)).read_bytes())
        write_text_atomic(out, render_evidence_csv(rows).decode("utf-8"))
        receipt = {
            "schema_version": "2.0.0",
            "receipt_type": "d-research-import",
            "package_major": args.major,
            "mapping_contract": result.get("mapping_contract"),
            "source_contract": result.get("source_contract"),
            "ledger_ref": portable_ledger_ref,
            "evidence_map_ref": portable_evidence_ref,
            "hmac_sidecar_ref": portable_sidecar_ref,
            "raw_preserved": True,
            "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "canonical_sha256": result.get("canonical_sha256"),
            "evidence_map_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "hmac_sidecar_sha256": (
                hashlib.sha256(sidecar_out.read_bytes()).hexdigest() if sidecar_out is not None else None
            ),
            "hmac_verified": result.get("hmac_verified") is True,
            "d_research_identity": identity,
            "component_binding": component_binding,
        }
        receipt["receipt_hash"] = canonical_hash(receipt)
        write_json_atomic(receipt_out, receipt)
    if not result.get("ok"):
        codes = {i.get("code") for i in result.get("issues") or []}
        if codes & {"HMAC_TAMPER", "LEDGER_TAMPER"}:
            raise SystemExit(EXIT_SECURITY)
        raise SystemExit(EXIT_SEMANTIC)
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
