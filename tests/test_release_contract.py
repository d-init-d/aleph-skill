from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph import PACKAGE_VERSION, SCHEMA_VERSION  # noqa: E402
from aleph.installer import build_distribution_manifest  # noqa: E402
from validate_skill_package import REQUIRED_FILES  # noqa: E402


class ReleaseContractTests(unittest.TestCase):
    def test_all_published_json_schemas_are_valid_json(self) -> None:
        schemas = sorted((ROOT / "schemas").glob("*.json"))
        self.assertTrue(schemas)
        for path in schemas:
            with self.subTest(schema=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(value, dict)

    def test_schema_catalog_is_complete_and_all_references_resolve(self) -> None:
        schema_root = ROOT / "schemas"
        catalog = json.loads((schema_root / "schema-catalog.json").read_text(encoding="utf-8"))
        declared = set(catalog["artifacts"].values())
        published = {path.name for path in schema_root.glob("*.schema.json")}
        self.assertEqual(declared, published)

        documents = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in schema_root.glob("*.schema.json")
        }
        dialect = catalog["dialect"]
        identifiers: set[str] = set()

        def resolve_pointer(document: object, fragment: str) -> object:
            current = document
            if not fragment:
                return current
            self.assertTrue(fragment.startswith("/"), fragment)
            for raw_part in fragment[1:].split("/"):
                part = raw_part.replace("~1", "/").replace("~0", "~")
                self.assertIsInstance(current, dict)
                self.assertIn(part, current)
                current = current[part]
            return current

        def inspect(value: object, source_name: str, source_document: object) -> None:
            if isinstance(value, list):
                for item in value:
                    inspect(item, source_name, source_document)
                return
            if not isinstance(value, dict):
                return
            reference = value.get("$ref")
            if isinstance(reference, str):
                target_name, separator, fragment = reference.partition("#")
                target_name = target_name or source_name
                self.assertNotIn("://", target_name)
                self.assertIn(target_name, documents)
                target_document = documents[target_name]
                resolve_pointer(target_document, fragment if separator else "")
            for child in value.values():
                inspect(child, source_name, source_document)

        for name, document in documents.items():
            self.assertEqual(document.get("$schema"), dialect)
            identifier = document.get("$id")
            self.assertIsInstance(identifier, str)
            self.assertNotIn(identifier, identifiers)
            identifiers.add(identifier)
            inspect(document, name, document)

    def test_assumption_contract_matches_template_and_fixture(self) -> None:
        schema = json.loads((ROOT / "schemas" / "simulation-manifest.schema.json").read_text(encoding="utf-8"))
        assumption_schema = schema["properties"]["assumptions"]
        self.assertEqual(assumption_schema["items"]["type"], "object")
        self.assertEqual(set(assumption_schema["items"]["required"]), {"id", "statement"})
        for path in (
            ROOT / "templates" / "simulation-manifest.json",
            ROOT / "tests" / "fixtures" / "schema-2.0-valid" / "simulation-manifest.json",
        ):
            with self.subTest(manifest=str(path)):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                assumptions = manifest["assumptions"]
                self.assertTrue(assumptions)
                self.assertTrue(all(isinstance(item, dict) for item in assumptions))
                self.assertIn(manifest["change_point"]["assumption_ref"], {item["id"] for item in assumptions})

    def test_versions_and_lockfile_are_synchronized(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        self.assertEqual(package["version"], PACKAGE_VERSION)
        self.assertEqual(lock["version"], PACKAGE_VERSION)
        self.assertEqual(lock["packages"][""]["version"], PACKAGE_VERSION)
        self.assertIn(f'version = "{PACKAGE_VERSION}"', pyproject)
        self.assertIn(
            f'[[package]]\nname = "aleph-skill"\nversion = "{PACKAGE_VERSION}"',
            uv_lock,
        )
        self.assertEqual(SCHEMA_VERSION, "2.0.0")

    def test_self_test_is_non_mutating(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self_test = package["scripts"]["self-test"]
        self.assertNotIn("--generate", self_test)
        self.assertEqual(self_test, "python scripts/release_gate.py")
        gate = (ROOT / "scripts" / "release_gate.py").read_text(encoding="utf-8")
        self.assertIn("--data-file=", gate)
        self.assertIn("coverage_data.unlink(missing_ok=True)", gate)

    def test_ci_matrix_exists(self) -> None:
        ci_workflow = ROOT / ".github" / "workflows" / "ci.yml"
        verify_workflow = ROOT / ".github" / "workflows" / "verify.yml"
        self.assertTrue(ci_workflow.is_file())
        self.assertTrue(verify_workflow.is_file())
        ci_text = ci_workflow.read_text(encoding="utf-8")
        text = verify_workflow.read_text(encoding="utf-8")
        self.assertIn("uses: ./.github/workflows/verify.yml", ci_text)
        self.assertIn("workflow_call:", text)
        for os_name in ("ubuntu-24.04", "windows-2025", "macos-15"):
            self.assertIn(os_name, text)
        for version in ("3.10", "3.11", "3.12", "3.13"):
            self.assertIn(version, text)

    def test_every_runtime_required_file_is_present_in_the_distribution(self) -> None:
        manifest = build_distribution_manifest(ROOT)
        distributed = {entry["path"] for entry in manifest["files"]}
        distributed.add("distribution-manifest.json")
        self.assertFalse(set(REQUIRED_FILES) - distributed)

    def test_portable_capability_vocabulary_is_exact_and_complete(self) -> None:
        contract = (ROOT / "references" / "artifact-contract.md").read_text(encoding="utf-8")
        expected_tokens = {
            "prospective_temporal_mode": '"prospective_intervention"',
            "computed_post_cutoff_label": '"simulation"',
            "uncalibrated_likelihood_mode": '"relative_weight"',
            "material_roleplay_input": '"sealed_packet_only"',
            "roleplay_may_emit_probability": "false",
            "numerical_trace_requires_execution_binding": "true",
            "invalid_monte_carlo_mass_may_be_renormalized": "false",
            "diagnostic_score_may_override_hard_gate": "false",
            "level_engine_implicitly_claims_stock_flow_dynamics": "false",
            "d_research_compatible_major": '"3.x"',
            "may_claim_single_certain_future": "false",
        }
        self.assertIn("## Portable capability vocabulary", contract)
        for key, value in expected_tokens.items():
            with self.subTest(key=key):
                self.assertIn(f"| `{key}` | `{value}` |", contract)
        self.assertIn('Use `"pass"` as a machine-readable overall result only when every applicable', contract)


if __name__ == "__main__":
    unittest.main()
