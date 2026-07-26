"""Monotonic capability baseline guard.

Asserts the current tree exposes a *superset* of the public capability surface
frozen in ``tests/baseline/capability-baseline.json``: gateway routes, CLI
scripts, adapters, domain packs, schemas, references, templates, ledger header
widths and record types, supported formula versions, and the readable workspace
schema versions. Removing or narrowing any of these fails the suite; adding is
always allowed.

Regenerate the frozen snapshot (release-maintainer operation, only when the
baseline itself is being advanced on purpose):

    python -m tests.test_capability_baseline --capture
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline" / "capability-baseline.json"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def capture_surface() -> dict:
    """Collect the current public capability surface (static, offline)."""
    from research_gateway import COMMAND_ROUTES  # noqa: PLC0415
    from aleph import (  # noqa: PLC0415
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
        SUPPORTED_FORMULA_VERSIONS,
    )
    from aleph import import_ledger  # noqa: PLC0415

    adapters = json.loads((REPO_ROOT / "adapters" / "registry.json").read_text(encoding="utf-8"))
    adapter_ids = sorted(
        str(entry.get("id") or entry.get("name"))
        for entry in adapters.get("adapters", [])
        if isinstance(entry, dict)
    )

    readable_schema_versions = sorted({LEGACY_SCHEMA_VERSION, SCHEMA_VERSION})
    extra_readable = getattr(sys.modules.get("aleph"), "READABLE_SCHEMA_VERSIONS", None)
    if extra_readable:
        readable_schema_versions = sorted(set(readable_schema_versions) | set(extra_readable))

    return {
        "baseline_schema": 1,
        "gateway_routes": sorted(COMMAND_ROUTES.keys()),
        "cli_scripts": sorted(
            p.name for p in SCRIPTS.glob("*.py") if not p.name.startswith("_")
        ),
        "aleph_modules": sorted(
            p.name for p in (SCRIPTS / "aleph").glob("*.py") if p.name != "__init__.py"
        ),
        "adapters": adapter_ids,
        "packs": sorted(p.name for p in (REPO_ROOT / "packs").iterdir() if p.is_dir()),
        "schemas": sorted(p.name for p in (REPO_ROOT / "schemas").glob("*.json")),
        "references": sorted(p.name for p in (REPO_ROOT / "references").glob("*.md")),
        "templates": sorted(p.name for p in (REPO_ROOT / "templates").iterdir() if p.is_file()),
        "ledger": {
            "header_widths": sorted({len(f) for f in import_ledger.ACCEPTED_FIELD_SETS}),
            "record_types": sorted(t for t in import_ledger.VALID_RECORD_TYPES if t),
        },
        "formula_versions": sorted(SUPPORTED_FORMULA_VERSIONS),
        "readable_workspace_schemas": readable_schema_versions,
    }


class CapabilityBaselineTest(unittest.TestCase):
    """The current tree must be a superset of the frozen baseline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        cls.current = capture_surface()

    def _assert_superset(self, key: str) -> None:
        baseline = set(self.baseline[key])
        current = set(self.current[key])
        missing = sorted(baseline - current)
        self.assertFalse(
            missing,
            f"capability regression in {key}: removed {missing}",
        )

    def test_gateway_routes_superset(self) -> None:
        self._assert_superset("gateway_routes")

    def test_cli_scripts_superset(self) -> None:
        self._assert_superset("cli_scripts")

    def test_aleph_modules_superset(self) -> None:
        self._assert_superset("aleph_modules")

    def test_adapters_superset(self) -> None:
        self._assert_superset("adapters")

    def test_packs_superset(self) -> None:
        self._assert_superset("packs")

    def test_schemas_superset(self) -> None:
        self._assert_superset("schemas")

    def test_references_superset(self) -> None:
        self._assert_superset("references")

    def test_templates_superset(self) -> None:
        self._assert_superset("templates")

    def test_ledger_contract_superset(self) -> None:
        for key in ("header_widths", "record_types"):
            baseline = set(self.baseline["ledger"][key])
            current = set(self.current["ledger"][key])
            missing = sorted(baseline - current)
            self.assertFalse(
                missing,
                f"ledger contract regression in {key}: removed {missing}",
            )

    def test_formula_versions_superset(self) -> None:
        self._assert_superset("formula_versions")

    def test_readable_workspace_schemas_superset(self) -> None:
        self._assert_superset("readable_workspace_schemas")


def _capture_main() -> int:
    surface = capture_surface()
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(surface, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote capability baseline -> {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    if "--capture" in sys.argv:
        raise SystemExit(_capture_main())
    unittest.main()
