"""Strict D Research discovery — bundled component first, external opt-in only."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .io import DEFAULT_MAX_FILE_BYTES, load_json_secure
from .issues import issue

SUPPORTED_MAJORS = frozenset({3})
EXPECTED_SKILL_NAME = "d-research"
EXPECTED_PACKAGE_NAMES = frozenset({"d-research-skill-tools", "d-research"})
SUPPORTED_INTEROP_CONTRACTS = frozenset({"1.0.0"})
REQUIRED_INTEROP_ROUTES = frozenset(
    {
        "academic_review",
        "api_collection",
        "atomic_fact",
        "broad_research",
        "creative_or_cultural_research",
        "dataset_collection",
        "due_diligence_or_investigation",
        "frontier_search",
        "investigative_osint",
        "large_scale",
        "leaked_data_handling",
        "legal_government_financial",
        "long_horizon",
        "market_competitor",
        "medical_or_safety",
        "monitoring_change",
        "multilingual",
        "person_osint_scoped",
        "policy_or_standards_analysis",
        "register_jargon",
        "self_exposure_audit",
        "semantic_retrieval",
        "single_url",
        "social_cross_platform",
        "social_media_archival",
        "systematic_review",
        "technical_research",
        "visualization_report",
    }
)
REQUIRED_INTEROP_ENTRYPOINTS = frozenset(
    {
        "scripts/evidence_ledger.py",
        "scripts/investigation_policy.py",
        "scripts/research_plan.py",
    }
)

# Importer-side ledger contract (what this Aleph can actually consume). The
# authoritative values live in import_ledger; read lazily to avoid cycles.


def _importer_ledger_contract() -> dict[str, Any]:
    from .import_ledger import (  # noqa: PLC0415
        ACCEPTED_FIELD_SETS,
        D_RESEARCH_CANONICALIZATION_VERSION,
        D_RESEARCH_SIGNATURE_VERSION,
        VALID_RECORD_TYPES,
    )

    return {
        "header_widths": sorted({len(fields) for fields in ACCEPTED_FIELD_SETS}),
        "record_types": sorted(value for value in VALID_RECORD_TYPES if value),
        "canonicalization": D_RESEARCH_CANONICALIZATION_VERSION,
        "signature": D_RESEARCH_SIGNATURE_VERSION,
        "contract_versions": sorted(SUPPORTED_INTEROP_CONTRACTS),
        "required_routes": sorted(REQUIRED_INTEROP_ROUTES),
        "required_entrypoints": sorted(REQUIRED_INTEROP_ENTRYPOINTS),
        "required_artifact_profiles": ["runtime"],
    }


def _read_interop_contract(resolved: Path) -> dict[str, Any] | None:
    """Read the component's machine-checkable interop contract when present.

    D Research >= 3.4.0 ships ``templates/interop-contract.json`` generated from
    its live ledger constants. Candidates without the file (older versions,
    external installs) keep the historical major-version acceptance, so this is
    purely additive capability discovery, never a new gate on old inputs.
    """
    contract_path = resolved / "templates" / "interop-contract.json"
    if not contract_path.is_file():
        return None
    contract, contract_issues = load_json_secure(contract_path)
    if contract_issues or not isinstance(contract, dict):
        return {"present": True, "readable": False}
    ledger = contract.get("ledger") if isinstance(contract.get("ledger"), dict) else {}
    importer = _importer_ledger_contract()
    declared_widths = ledger.get("header_sizes")
    declared_types = ledger.get("record_types")
    declared_routes = contract.get("routes")
    declared_entrypoints = contract.get("entrypoints")
    declared_profiles = contract.get("artifact_profiles")
    widths_supported = (
        isinstance(declared_widths, list)
        and set(declared_widths) <= set(importer["header_widths"])
    )
    types_supported = (
        isinstance(declared_types, list)
        and set(declared_types) <= set(importer["record_types"])
    )
    signature_supported = ledger.get("signature") == importer["signature"]
    canonicalization_supported = (
        ledger.get("canonicalization") == importer["canonicalization"]
    )
    contract_version_supported = contract.get("contract_version") in importer["contract_versions"]
    package_version_matches = contract.get("package_version") == _package_version(resolved)
    routes_supported = (
        isinstance(declared_routes, list)
        and all(isinstance(value, str) and value for value in declared_routes)
        and REQUIRED_INTEROP_ROUTES <= set(declared_routes)
    )
    entrypoints_supported = (
        isinstance(declared_entrypoints, list)
        and all(isinstance(value, str) and value for value in declared_entrypoints)
        and REQUIRED_INTEROP_ENTRYPOINTS <= set(declared_entrypoints)
    )
    entrypoint_files_present = all(
        (resolved / Path(relative)).is_file()
        for relative in REQUIRED_INTEROP_ENTRYPOINTS
    )
    profiles_supported = (
        isinstance(declared_profiles, list)
        and all(isinstance(value, str) and value for value in declared_profiles)
        and "runtime" in declared_profiles
    )
    return {
        "present": True,
        "readable": True,
        "contract_version": contract.get("contract_version"),
        "package_version": contract.get("package_version"),
        "ledger": {
            "header_sizes": declared_widths,
            "record_types": declared_types,
            "canonicalization": ledger.get("canonicalization"),
            "signature": ledger.get("signature"),
        },
        "routes": contract.get("routes"),
        "entrypoints": contract.get("entrypoints"),
        "artifact_profiles": contract.get("artifact_profiles"),
        "importer_supports_declared_headers": widths_supported,
        "importer_supports_declared_record_types": types_supported,
        "importer_supports_declared_canonicalization": canonicalization_supported,
        "importer_supports_declared_signature": signature_supported,
        "importer_supports_contract_version": contract_version_supported,
        "component_package_version_matches_contract": package_version_matches,
        "importer_supports_required_routes": routes_supported,
        "importer_supports_required_entrypoints": entrypoints_supported,
        "component_has_required_entrypoints": entrypoint_files_present,
        "importer_supports_required_artifact_profiles": profiles_supported,
    }


def _package_version(resolved: Path) -> str | None:
    package, problems = load_json_secure(resolved / "package.json")
    if problems or not isinstance(package, dict):
        return None
    version = package.get("version")
    return str(version) if isinstance(version, str) else None


def _parse_frontmatter_name(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    match = re.match(r"^---\s*\n(.*?)\n---(?:\s*\n|$)", text, flags=re.DOTALL)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.strip().startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _candidate_report(source: str, path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"source": source, "path": str(path), "exists": path.is_dir()}
    if not path.is_dir():
        entry.update({"ok": False, "compatible": False, "reason": "directory missing"})
        return entry
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        entry.update({"ok": False, "compatible": False, "reason": str(exc)})
        return entry
    skill_md = resolved / "SKILL.md"
    package_json = resolved / "package.json"
    ledger_helper = resolved / "scripts" / "evidence_ledger.py"
    if not skill_md.is_file() or not package_json.is_file() or not ledger_helper.is_file():
        entry.update({"ok": False, "compatible": False, "reason": "missing identity/ledger contract files"})
        return entry
    try:
        if skill_md.stat().st_size > DEFAULT_MAX_FILE_BYTES:
            raise ValueError("SKILL.md exceeds the file-size limit")
        skill_name = _parse_frontmatter_name(skill_md.read_text(encoding="utf-8"))
        package, package_issues = load_json_secure(package_json)
        if package_issues:
            raise ValueError("; ".join(value.legacy_string() for value in package_issues))
        if not isinstance(package, dict):
            raise ValueError("package.json must be an object")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        entry.update({"ok": False, "compatible": False, "reason": str(exc)})
        return entry
    package_name = package.get("name")
    version = package.get("version")
    try:
        major = int(str(version).split(".", 1)[0])
    except (TypeError, ValueError):
        major = None
    identity_ok = skill_name == EXPECTED_SKILL_NAME and package_name in EXPECTED_PACKAGE_NAMES
    interop = _read_interop_contract(resolved)
    if interop is not None:
        # Capability-based compatibility: the component declares its exact
        # ledger contract and the importer must support every declared width,
        # record type, and the signature scheme. Major-version acceptance
        # remains only for candidates that predate the interop contract.
        contract_supported = bool(
            interop.get("readable")
            and interop.get("importer_supports_declared_headers")
            and interop.get("importer_supports_declared_record_types")
            and interop.get("importer_supports_declared_canonicalization")
            and interop.get("importer_supports_declared_signature")
            and interop.get("importer_supports_contract_version")
            and interop.get("component_package_version_matches_contract")
            and interop.get("importer_supports_required_routes")
            and interop.get("importer_supports_required_entrypoints")
            and interop.get("component_has_required_entrypoints")
            and interop.get("importer_supports_required_artifact_profiles")
        )
        compatible = identity_ok and contract_supported
    else:
        compatible = identity_ok and major in SUPPORTED_MAJORS
    entry.update(
        {
            "resolved_path": str(resolved),
            "skill_name": skill_name,
            "package_name": package_name,
            "package_version": version,
            "package_major": major,
            "identity_ok": identity_ok,
            "compatible": compatible,
            "ok": compatible,
        }
    )
    if interop is not None:
        entry["interop_contract"] = interop
        entry["importer_ledger_contract"] = _importer_ledger_contract()
    if not identity_ok:
        entry["reason"] = "D Research identity mismatch"
    elif not compatible:
        if interop is not None:
            entry["reason"] = "declared interop ledger contract exceeds importer support"
        else:
            entry["reason"] = f"unsupported D Research major {major}"
    return entry


def discover_d_research(
    *,
    explicit: str | Path | None = None,
    capability_file: Path | None = None,
    conventional_roots: list[Path] | None = None,
    skill_root: Path | None = None,
    allow_external: bool = False,
    require_bundled: bool = True,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Discover D Research with bundled component preferred by default.

    ``D_RESEARCH_SKILL`` never silently overrides a verified bundled component.
    External paths require ``allow_external=True`` (CLI ``--allow-external``
    together with ``--external-d-research``).
    When ``explicit`` is a portable URI ``aleph-component://d-research``, resolve
    the bundle. When ``explicit`` is a filesystem path and ``allow_external`` is
    false, the bundled component still wins if present; an explicit incompatible
    external with ``allow_external`` hard-fails.
    """
    # Lazy import avoids circular dependency at module load.
    from .component_registry import (
        COMPONENT_URI,
        skill_root_from,
    )
    from .component_registry import (
        discover_d_research as _bundled_discover,
    )

    environ = dict(os.environ) if env is None else dict(env)
    root = skill_root if skill_root is not None else skill_root_from(env=environ)

    # Portable URI always means bundled resolve.
    if explicit is not None and str(explicit).strip() == COMPONENT_URI:
        return _bundled_discover(
            skill_root=root,
            explicit=None,
            allow_external=False,
            require_bundled=True,
            env=environ,
        )

    # Explicit filesystem path is authoritative for identity: incompatible hard-fails
    # even when a bundle exists (no silent fallback). Compatible explicit paths only
    # replace the bundle when allow_external is set.
    if explicit is not None:
        report = _candidate_report("explicit", Path(explicit).expanduser())
        if not report.get("ok"):
            incompatible: dict[str, Any] = {
                "status": "incompatible",
                "path": report.get("resolved_path") or str(explicit),
                "source": "explicit",
                "source_kind": "external",
                "package_major": report.get("package_major"),
                "package_version": report.get("package_version"),
                "compatible": False,
                "identity_verified": bool(report.get("identity_ok")),
                "supported_majors": [3],
                "tried": [report],
                "assurance_cap": "experimental",
                "error_code": "COMPONENT_IDENTITY_MISMATCH",
                "issues": [
                    issue(
                        "D_RESEARCH",
                        message=report.get("reason", "configured D Research is unavailable or incompatible"),
                    ).to_dict()
                ],
            }
            # Keep the declared-vs-supported contract comparison visible on the
            # refusal itself so callers can explain the incompatibility.
            if "interop_contract" in report:
                incompatible["interop_contract"] = report["interop_contract"]
                incompatible["importer_ledger_contract"] = report.get(
                    "importer_ledger_contract"
                )
            return incompatible
        if not allow_external:
            bundled = _bundled_discover(
                skill_root=root,
                explicit=None,
                allow_external=False,
                require_bundled=True,
                env=environ,
            )
            tried = list(bundled.get("tried") or [])
            tried.append(
                {
                    **report,
                    "ok": False,
                    "compatible": True,
                    "reason": "COMPONENT_OVERRIDE_REFUSED: use allow_external for external path",
                }
            )
            bundled["tried"] = tried
            return bundled
        if allow_external:
            return _bundled_discover(
                skill_root=root,
                explicit=explicit,
                allow_external=True,
                require_bundled=require_bundled,
                capability_file=capability_file,
                conventional_roots=conventional_roots,
                env=environ,
            )

    # Default: bundled first; env never overrides.
    if require_bundled or not allow_external:
        result = _bundled_discover(
            skill_root=root,
            explicit=None,
            allow_external=False,
            require_bundled=True,
            capability_file=capability_file,
            conventional_roots=conventional_roots,
            env=environ,
        )
        if result.get("status") == "available" or not allow_external:
            return result
        return _bundled_discover(
            skill_root=root,
            explicit=None,
            allow_external=True,
            require_bundled=False,
            capability_file=capability_file,
            conventional_roots=conventional_roots,
            env=environ,
        )

    return _bundled_discover(
        skill_root=root,
        explicit=None,
        allow_external=True,
        require_bundled=False,
        capability_file=capability_file,
        conventional_roots=conventional_roots,
        env=environ,
    )


def legacy_external_discover(
    *,
    explicit: str | Path | None = None,
    capability_file: Path | None = None,
    conventional_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Pre-2.1 external-first discovery (compatibility/tests only)."""
    candidates: list[tuple[str, Path, bool]] = []
    if explicit is not None:
        candidates.append(("explicit", Path(explicit).expanduser(), True))
    env = os.environ.get("D_RESEARCH_SKILL", "").strip()
    if env:
        candidates.append(("env:D_RESEARCH_SKILL", Path(env).expanduser(), True))
    if capability_file is not None and capability_file.is_file():
        try:
            data, capability_issues = load_json_secure(capability_file)
            if capability_issues or not isinstance(data, dict):
                raise ValueError("invalid capability file")
            configured = data.get("d_research_skill") or (data.get("d_research") or {}).get("path")
            if configured:
                candidates.append(("capability_file", Path(configured).expanduser(), True))
        except (OSError, UnicodeDecodeError, ValueError, AttributeError):
            candidates.append(("capability_file", Path("__invalid_capability_file__"), True))
    roots = conventional_roots or [
        Path.home() / ".codex" / "skills" / "d-research",
        Path.home() / ".agents" / "skills" / "d-research",
        Path.home() / ".claude" / "skills" / "d-research",
        Path.home() / ".config" / "opencode" / "skills" / "d-research",
        Path.home() / ".grok" / "skills" / "d-research",
    ]
    candidates.extend(("conventional", root, False) for root in roots)

    tried: list[dict[str, Any]] = []
    incompatible: dict[str, Any] | None = None
    for source, path, authoritative in candidates:
        entry = _candidate_report(source, path)
        tried.append(entry)
        if entry.get("ok"):
            return {
                "status": "available",
                "path": entry["resolved_path"],
                "source": source,
                "name": entry["skill_name"],
                "package_name": entry["package_name"],
                "package_version": entry["package_version"],
                "package_major": entry["package_major"],
                "supported_majors": [3],
                "compatible": True,
                "identity_verified": True,
                "tried": tried,
            }
        if authoritative:
            return {
                "status": "incompatible",
                "path": entry.get("resolved_path") or str(path),
                "source": source,
                "package_major": entry.get("package_major"),
                "package_version": entry.get("package_version"),
                "compatible": False,
                "identity_verified": bool(entry.get("identity_ok")),
                "supported_majors": [3],
                "tried": tried,
                "assurance_cap": "experimental",
                "issues": [
                    issue(
                        "D_RESEARCH",
                        message=entry.get("reason", "configured D Research is unavailable or incompatible"),
                    ).to_dict()
                ],
            }
        if entry.get("exists") and entry.get("reason") not in {"directory missing"}:
            incompatible = entry

    if incompatible is not None:
        return {
            "status": "incompatible",
            "path": incompatible.get("resolved_path") or incompatible.get("path"),
            "source": incompatible.get("source"),
            "package_major": incompatible.get("package_major"),
            "package_version": incompatible.get("package_version"),
            "compatible": False,
            "identity_verified": bool(incompatible.get("identity_ok")),
            "supported_majors": [3],
            "tried": tried,
            "assurance_cap": "experimental",
            "issues": [
                issue("D_RESEARCH", message=incompatible.get("reason", "incompatible D Research")).to_dict()
            ],
        }
    return {
        "status": "unavailable",
        "path": None,
        "source": None,
        "compatible": False,
        "identity_verified": False,
        "tried": tried,
        "assurance_cap": "limited",
        "issues": [
            issue("D_RESEARCH", severity="warning", message="D Research not found; limited mode only").to_dict()
        ],
    }
