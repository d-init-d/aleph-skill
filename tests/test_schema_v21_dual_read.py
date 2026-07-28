"""Schema 2.1.0 additive dual-read: details/extensions, alias, empty modifiers.

2.0.0 artifacts keep their exact contract (details stay refused there); a
2.1.0 workspace accepts optional node ``details``/``extensions``, an empty
``context_modifiers`` list, and the edge ``confidence`` alias with canonical
``evidence_confidence`` preference. The explicit sibling upgrade is
idempotent and preserves every artifact byte except the manifest and audit
trail.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aleph import SCHEMA_VERSION, SCHEMA_VERSION_2_1  # noqa: E402
from aleph.io import write_json_atomic  # noqa: E402
from aleph.migrate import upgrade_workspace_schema  # noqa: E402
from aleph.validator import (  # noqa: E402
    validate_edges,
    validate_nodes,
    validate_workspace,
)

FIXTURES = ROOT / "tests" / "fixtures"
VALID = FIXTURES / "schema-2.0-valid"


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(version: str) -> dict:
    manifest = json.loads((VALID / "simulation-manifest.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = version
    return manifest


class SchemaV21NodeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = _load(VALID / "nodes.json")
        self.evidence_ids = {
            ref
            for node in self.nodes
            for ref in (node.get("evidence_ids") or [])
        }

    def test_2_1_accepts_details_and_extensions(self) -> None:
        nodes = json.loads(json.dumps(self.nodes))
        entity = next(n for n in nodes if n["type"] == "entity")
        entity["details"] = {
            "entity_type": "organization",
            "attributes": {"mandate": "price stability"},
        }
        entity["extensions"] = {"org.example/rating": "AA", "vendor.pack/flag": True}
        result, _ids = validate_nodes(nodes, self.evidence_ids, _manifest(SCHEMA_VERSION_2_1))
        codes = {i.code for i in result.issues if i.severity == "error"}
        self.assertNotIn("UNKNOWN_FIELD", codes, [i.to_dict() for i in result.issues])

    def test_2_1_refuses_wrong_typed_details_and_bad_extension_keys(self) -> None:
        nodes = json.loads(json.dumps(self.nodes))
        entity = next(n for n in nodes if n["type"] == "entity")
        entity["details"] = {"start_time": "2020-01-01"}  # event-only detail
        entity["extensions"] = {"notnamespaced": 1}
        result, _ids = validate_nodes(nodes, self.evidence_ids, _manifest(SCHEMA_VERSION_2_1))
        codes = {i.code for i in result.issues}
        self.assertIn("UNKNOWN_FIELD", codes)
        self.assertTrue(
            any(
                i.code == "SCHEMA" and "extensions" in i.pointer
                for i in result.issues
            ),
            [i.to_dict() for i in result.issues],
        )

    def test_2_0_still_refuses_details(self) -> None:
        nodes = json.loads(json.dumps(self.nodes))
        entity = next(n for n in nodes if n["type"] == "entity")
        entity["details"] = {"entity_type": "organization"}
        result, _ids = validate_nodes(nodes, self.evidence_ids, _manifest(SCHEMA_VERSION))
        self.assertTrue(
            any(i.code == "UNKNOWN_FIELD" and "details" in i.pointer for i in result.issues)
        )


class SchemaV21EdgeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = _load(VALID / "nodes.json")
        self.edges = _load(VALID / "edges.json")
        self.node_ids = {n["id"] for n in self.nodes}
        self.node_types = {n["id"]: n.get("type") for n in self.nodes}
        self.evidence_ids = {
            ref for edge in self.edges for ref in (edge.get("evidence") or [])
        }

    def test_empty_context_modifiers_accepted(self) -> None:
        edges = json.loads(json.dumps(self.edges))
        edges[0]["context_modifiers"] = []
        result, _by_id = validate_edges(
            edges, self.node_ids, self.evidence_ids, self.node_types
        )
        self.assertFalse(
            [i.to_dict() for i in result.issues if i.code == "CONTEXT"],
        )

    def test_missing_context_modifiers_still_refused(self) -> None:
        edges = json.loads(json.dumps(self.edges))
        edges[0].pop("context_modifiers", None)
        result, _by_id = validate_edges(
            edges, self.node_ids, self.evidence_ids, self.node_types
        )
        self.assertTrue(any(i.code == "CONTEXT" for i in result.issues))

    def test_confidence_alias_equal_is_silent_divergent_warns(self) -> None:
        edges = json.loads(json.dumps(self.edges))
        edges[0]["confidence"] = edges[0]["evidence_confidence"]
        result, _by_id = validate_edges(
            edges, self.node_ids, self.evidence_ids, self.node_types
        )
        self.assertFalse([i for i in result.issues if i.code == "CONFIDENCE_ALIAS"])

        edges[0]["confidence"] = 0.2
        self.assertNotEqual(edges[0]["confidence"], edges[0]["evidence_confidence"])
        result, _by_id = validate_edges(
            edges, self.node_ids, self.evidence_ids, self.node_types
        )
        alias_issues = [i for i in result.issues if i.code == "CONFIDENCE_ALIAS"]
        self.assertEqual(len(alias_issues), 1)
        self.assertEqual(alias_issues[0].severity, "warning")
        self.assertEqual(result.status, "pass", [i.to_dict() for i in result.issues])

    def test_alias_alone_still_validates_range(self) -> None:
        edges = json.loads(json.dumps(self.edges))
        edges[0].pop("evidence_confidence", None)
        edges[0]["confidence"] = 1.5
        result, _by_id = validate_edges(
            edges, self.node_ids, self.evidence_ids, self.node_types
        )
        self.assertTrue(any(i.code == "RANGE" for i in result.issues))

    def test_alias_alone_is_returned_with_canonical_confidence(self) -> None:
        edges = json.loads(json.dumps(self.edges))
        edges[0]["confidence"] = edges[0].pop("evidence_confidence")
        result, by_id = validate_edges(
            edges, self.node_ids, self.evidence_ids, self.node_types
        )
        self.assertEqual(result.status, "pass", [i.to_dict() for i in result.issues])
        normalized = by_id[edges[0]["id"]]
        self.assertEqual(normalized["evidence_confidence"], edges[0]["confidence"])


class SchemaV21WorkspaceTests(unittest.TestCase):
    def test_fresh_2_1_workspace_passes_draft_validation(self) -> None:
        from types import SimpleNamespace

        import init_simulation_workspace as init

        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(
                slug="v21-clean",
                change_point="Demo change point",
                time="2026-01-01",
                horizon="P12M",
                simulation_end=None,
                observation_cutoff="2026-01-01",
                domain="economics",
                geography="global",
                out_dir=temporary,
                force=False,
                schema_version=SCHEMA_VERSION_2_1,
            )
            workspace = init.build_workspace(args)
            manifest = json.loads(
                (Path(workspace) / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], SCHEMA_VERSION_2_1)
            actors = json.loads(
                (Path(workspace) / "actors.json").read_text(encoding="utf-8")
            )
            self.assertEqual(actors[0]["actor_basis"], "evidence")
            result = validate_workspace(Path(workspace), mode="draft", require_report=False)
            self.assertEqual(result["status"], "pass", result.get("errors"))

    def test_2_1_workspace_validates_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "ws-2-1"
            shutil.copytree(VALID, workspace)
            manifest = json.loads(
                (workspace / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            manifest["schema_version"] = SCHEMA_VERSION_2_1
            write_json_atomic(workspace / "simulation-manifest.json", manifest)
            nodes = _load(workspace / "nodes.json")
            entity = next(n for n in nodes if n["type"] == "entity")
            entity["details"] = {"entity_type": "organization"}
            entity["extensions"] = {"org.example/note": "additive"}
            write_json_atomic(workspace / "nodes.json", nodes)
            result = validate_workspace(workspace, mode="final", require_report=True)
            # The 2.1 surface itself must be schema-clean. Because the fixture's
            # compiled model and sealed packets hash the pre-edit manifest and
            # node bytes, the only acceptable errors are those re-run bindings —
            # never a schema/unknown-field refusal of details/extensions.
            codes = set(result.get("error_codes") or [])
            self.assertFalse(
                codes - {"STALE_ARTIFACT", "TRACK_MISMATCH"},
                json.dumps(
                    {"errors": result.get("errors"), "codes": result.get("error_codes")},
                    indent=2,
                ),
            )

    def test_upgrade_is_sibling_idempotent_and_lossless(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "ws-2-0"
            shutil.copytree(VALID, source)
            source_bytes = {
                name: (source / name).read_bytes()
                for name in ("nodes.json", "edges.json", "actors.json", "evidence-map.csv")
            }
            destination = Path(temporary) / "ws-upgraded"
            result = upgrade_workspace_schema(source, destination)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("target_schema"), SCHEMA_VERSION_2_1)

            # Source untouched; artifacts byte-preserved in the sibling.
            for name, payload in source_bytes.items():
                self.assertEqual((source / name).read_bytes(), payload)
                self.assertEqual((destination / name).read_bytes(), payload, name)
            source_manifest = json.loads(
                (source / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(source_manifest["schema_version"], SCHEMA_VERSION)

            manifest = json.loads(
                (destination / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], SCHEMA_VERSION_2_1)
            migration = manifest["migration"]
            self.assertEqual(migration["source_schema_version"], SCHEMA_VERSION)
            self.assertEqual(migration["target_schema_version"], SCHEMA_VERSION_2_1)
            self.assertTrue((destination / "migration-report.json").is_file())

            validated = validate_workspace(destination, mode="draft", require_report=False)
            codes = set(validated.get("error_codes") or [])
            # Upgrade must not make old content schema-invalid; only pre-upgrade
            # hash bindings (model/packets) may demand a re-run, and they are
            # declared in the migration audit trail.
            self.assertFalse(codes - {"STALE_ARTIFACT", "TRACK_MISMATCH"}, validated.get("errors"))
            unresolved_items = {
                entry.get("item") for entry in migration.get("unresolved_fields", [])
            }
            self.assertIn("simulation-model.json", unresolved_items)

            again = upgrade_workspace_schema(destination)
            self.assertTrue(again.get("ok"), again)
            self.assertTrue(again.get("already_current"))

            collision = upgrade_workspace_schema(source, destination)
            self.assertFalse(collision.get("ok"))


if __name__ == "__main__":
    unittest.main()
