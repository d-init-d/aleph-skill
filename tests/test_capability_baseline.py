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
import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
BASELINE_PATH = Path(__file__).resolve().parent / "baseline" / "capability-baseline.json"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _literal_or_source(node: ast.AST | None) -> object:
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)
    if isinstance(value, tuple):
        return list(value)
    return value


def capture_cli_options(scripts: Path) -> dict[str, list[dict[str, object]]]:
    """Capture public argparse flags and behavior without importing commands."""
    result: dict[str, list[dict[str, object]]] = {}
    for path in sorted(scripts.glob("*.py")):
        if path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        descriptors: list[dict[str, object]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_argument" or not node.args:
                continue
            flags = [_literal_or_source(argument) for argument in node.args]
            keyword_nodes = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            action = _literal_or_source(keyword_nodes.get("action")) or "store"
            if "default" in keyword_nodes:
                default = _literal_or_source(keyword_nodes["default"])
            elif action == "store_true":
                default = False
            elif action == "store_false":
                default = True
            else:
                default = None
            descriptor: dict[str, object] = {
                "flags": flags,
                "action": action,
                "default": default,
                "required": bool(_literal_or_source(keyword_nodes.get("required")) or False),
                "nargs": _literal_or_source(keyword_nodes.get("nargs")),
                "choices": _literal_or_source(keyword_nodes.get("choices")),
                "type": _literal_or_source(keyword_nodes.get("type")),
            }
            descriptors.append(descriptor)
        result[path.name] = sorted(
            descriptors,
            key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False),
        )
    return result


def capture_surface() -> dict:
    """Collect the current public capability surface (static, offline)."""
    from research_gateway import COMMAND_ROUTES  # noqa: PLC0415
    from aleph import (  # noqa: PLC0415
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
        SUPPORTED_FORMULA_VERSIONS,
        SUPPORTED_SCHEMA_VERSIONS,
    )
    from aleph import import_ledger  # noqa: PLC0415

    adapters = json.loads((REPO_ROOT / "adapters" / "registry.json").read_text(encoding="utf-8"))
    raw_adapters = adapters.get("adapters", {})
    if isinstance(raw_adapters, dict):
        adapter_ids = sorted(str(value) for value in raw_adapters)
    else:
        adapter_ids = sorted(
            str(entry.get("id") or entry.get("name"))
            for entry in raw_adapters
            if isinstance(entry, dict)
        )

    readable_schema_versions = sorted({LEGACY_SCHEMA_VERSION, SCHEMA_VERSION})
    readable_schema_versions = sorted(
        set(readable_schema_versions) | set(SUPPORTED_SCHEMA_VERSIONS)
    )

    return {
        "baseline_schema": 1,
        "gateway_routes": sorted(COMMAND_ROUTES.keys()),
        "cli_scripts": sorted(
            p.name for p in SCRIPTS.glob("*.py") if not p.name.startswith("_")
        ),
        "cli_options": capture_cli_options(SCRIPTS),
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

    def test_cli_options_and_defaults_do_not_narrow(self) -> None:
        baseline_scripts = self.baseline.get("cli_options", {})
        current_scripts = self.current.get("cli_options", {})
        for script, baseline_options in baseline_scripts.items():
            self.assertIn(script, current_scripts, f"CLI removed: {script}")
            remaining = list(current_scripts[script])
            for baseline_option in baseline_options:
                match_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining)
                        if candidate.get("flags") == baseline_option.get("flags")
                        and candidate.get("action") == baseline_option.get("action")
                        and candidate.get("default") == baseline_option.get("default")
                        and candidate.get("nargs") == baseline_option.get("nargs")
                        and candidate.get("type") == baseline_option.get("type")
                        and not (
                            baseline_option.get("required") is False
                            and candidate.get("required") is True
                        )
                        and (
                            baseline_option.get("choices") is None
                            or baseline_option.get("choices") == candidate.get("choices")
                            or (
                                isinstance(baseline_option.get("choices"), list)
                                and isinstance(candidate.get("choices"), list)
                                and set(baseline_option["choices"]) <= set(candidate["choices"])
                            )
                        )
                    ),
                    None,
                )
                self.assertIsNotNone(
                    match_index,
                    f"CLI option/default narrowed in {script}: {baseline_option}",
                )
                remaining.pop(int(match_index))

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
        json.dumps(surface, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote capability baseline -> {BASELINE_PATH}")
    return 0


def _augment_from_source(source: Path) -> int:
    """Repair a frozen baseline from its historical source tree only."""
    surface = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    registry = json.loads(
        (source / "adapters" / "registry.json").read_text(encoding="utf-8")
    )
    raw_adapters = registry.get("adapters", {})
    if isinstance(raw_adapters, dict):
        surface["adapters"] = sorted(raw_adapters)
    else:
        surface["adapters"] = sorted(
            str(entry.get("id") or entry.get("name"))
            for entry in raw_adapters
            if isinstance(entry, dict)
        )
    surface["cli_options"] = capture_cli_options(source / "scripts")
    surface["baseline_source"] = "v2.3.1"
    surface["baseline_schema"] = 2
    BASELINE_PATH.write_text(
        json.dumps(surface, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"augmented capability baseline from {source} -> {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    if "--augment-from-source" in sys.argv:
        source_index = sys.argv.index("--augment-from-source") + 1
        raise SystemExit(_augment_from_source(Path(sys.argv[source_index])))
    if "--capture" in sys.argv:
        raise SystemExit(_capture_main())
    unittest.main()
