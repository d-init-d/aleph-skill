"""Docs and validator share one source of truth for node/edge artifacts.

The canonical minimal graph fixture must validate cleanly under both schema
versions (including the edge ``confidence`` alias), the builder docs must
embed exactly that fixture's node and edge JSON, and every fenced JSON block
in the two protocol docs must parse.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aleph import SUPPORTED_SCHEMA_VERSIONS  # noqa: E402
from aleph.engine import compile_model  # noqa: E402
from aleph.validator import validate_edges, validate_nodes  # noqa: E402

CANONICAL = ROOT / "tests" / "fixtures" / "canonical" / "minimal-graph.json"
NODE_DOC = ROOT / "references" / "node-builder.md"
EDGE_DOC = ROOT / "references" / "causal-edge-protocol.md"
JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _graph() -> dict:
    return json.loads(CANONICAL.read_text(encoding="utf-8"))


class CanonicalFixtureTests(unittest.TestCase):
    def test_fixture_validates_under_both_schema_versions(self) -> None:
        graph = _graph()
        evidence_ids = set(graph["evidence_ids"])
        for version in SUPPORTED_SCHEMA_VERSIONS:
            manifest = {
                "schema_version": version,
                "temporal_frame": {"observation_cutoff": "2026-01-01"},
            }
            node_result, node_ids = validate_nodes(graph["nodes"], evidence_ids, manifest)
            self.assertEqual(
                node_result.status,
                "pass",
                (version, [i.to_dict() for i in node_result.issues]),
            )
            node_types = {n["id"]: n["type"] for n in graph["nodes"]}
            edge_result, _by_id = validate_edges(
                graph["edges"], set(node_ids), evidence_ids, node_types
            )
            self.assertEqual(
                edge_result.status,
                "pass",
                (version, [i.to_dict() for i in edge_result.issues]),
            )

    def test_fixture_edge_validates_with_confidence_alias(self) -> None:
        graph = _graph()
        evidence_ids = set(graph["evidence_ids"])
        manifest = {
            "schema_version": "2.0.0",
            "temporal_frame": {"observation_cutoff": "2026-01-01"},
        }
        _result, node_ids = validate_nodes(graph["nodes"], evidence_ids, manifest)
        edge = dict(graph["edges"][0])
        edge["confidence"] = edge.pop("evidence_confidence")
        node_types = {n["id"]: n["type"] for n in graph["nodes"]}
        edge_result, by_id = validate_edges(
            [edge], set(node_ids), evidence_ids, node_types
        )
        self.assertEqual(
            edge_result.status, "pass", [i.to_dict() for i in edge_result.issues]
        )
        self.assertEqual(by_id[edge["id"]]["evidence_confidence"], edge["confidence"])

    def test_fixture_compiles_with_empty_context_modifiers(self) -> None:
        graph = _graph()
        model = compile_model(graph["nodes"], graph["edges"])
        self.assertEqual(len(model.edges), 1)
        self.assertEqual(model.edges[0].context_multiplier, 1.0)


class DocSyncTests(unittest.TestCase):
    def test_node_builder_embeds_exact_canonical_node(self) -> None:
        graph = _graph()
        expected = json.dumps(graph["nodes"][0], indent=2)
        self.assertIn(expected, NODE_DOC.read_text(encoding="utf-8"))

    def test_edge_protocol_embeds_exact_canonical_edge(self) -> None:
        graph = _graph()
        expected = json.dumps(graph["edges"][0], indent=2)
        self.assertIn(expected, EDGE_DOC.read_text(encoding="utf-8"))

    def test_all_fenced_json_blocks_parse_and_validate_semantically(self) -> None:
        graph = _graph()
        evidence_ids = set(graph["evidence_ids"])
        manifest = {
            "schema_version": "2.1.0",
            "temporal_frame": {"observation_cutoff": "2026-01-01"},
        }
        for doc in (NODE_DOC, EDGE_DOC):
            text = doc.read_text(encoding="utf-8")
            blocks = JSON_FENCE_RE.findall(text)
            self.assertTrue(blocks, f"{doc.name} advertises no JSON examples")
            for index, block in enumerate(blocks):
                try:
                    value = json.loads(block)
                except json.JSONDecodeError as exc:
                    self.fail(f"{doc.name} fenced JSON block {index} is invalid: {exc}")
                if doc == NODE_DOC:
                    if "id" in value:
                        candidate = value
                    elif set(value) <= {"details", "extensions"}:
                        candidate = {**graph["nodes"][0], **value}
                    else:
                        self.fail(f"{doc.name} fenced JSON block {index} has no semantic route")
                    result, _node_ids = validate_nodes([candidate], evidence_ids, manifest)
                elif "id" in value:
                    node_ids = {node["id"] for node in graph["nodes"]}
                    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
                    result, _edges = validate_edges(
                        [value], node_ids, evidence_ids, node_types
                    )
                elif set(value) <= {"context", "multiplier", "rationale", "active"}:
                    candidate = {**graph["edges"][0], "context_modifiers": [value]}
                    node_ids = {node["id"] for node in graph["nodes"]} | {
                        str(value.get("context"))
                    }
                    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
                    node_types[str(value.get("context"))] = "context"
                    result, _edges = validate_edges(
                        [candidate], node_ids, evidence_ids, node_types
                    )
                else:
                    self.fail(f"{doc.name} fenced JSON block {index} has no semantic route")
                self.assertEqual(
                    result.status,
                    "pass",
                    (doc.name, index, [item.to_dict() for item in result.issues]),
                )

    def test_details_example_keys_match_schema_allowlist(self) -> None:
        from aleph.schema import NODE_DETAILS_FIELDS

        text = NODE_DOC.read_text(encoding="utf-8")
        # Every backticked field bullet under an "Add under `details`" section
        # must exist in the validator's per-type allowlist.
        sections = re.split(r"^## ", text, flags=re.MULTILINE)
        type_names = {
            "Entity nodes": "entity",
            "Event nodes": "event",
            "Factor nodes": "factor",
            "Context nodes": "context",
            "Indicator nodes": "indicator",
            "Claim nodes": "claim",
            "Source nodes": "source",
        }
        checked = 0
        for section in sections:
            title = section.splitlines()[0].strip() if section else ""
            node_type = type_names.get(title)
            if node_type is None or "Add under `details`" not in section:
                continue
            advertised = re.findall(r"^- `([a-z_]+)`[,.\s]*$", section, flags=re.MULTILINE)
            advertised += re.findall(r"^- `([a-z_]+)` \(", section, flags=re.MULTILINE)
            self.assertTrue(advertised, title)
            for field in advertised:
                self.assertIn(
                    field,
                    NODE_DETAILS_FIELDS[node_type],
                    f"{title} advertises {field!r} outside the schema allowlist",
                )
                checked += 1
        self.assertGreaterEqual(checked, 40)

    def test_vux01_aleph_skill_docs_cold_start_instruction(self) -> None:
        """VUX01: Agent reading Aleph docs uses engine-derived cold-start flow, not manually authored trace."""
        skill_doc = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        prop_doc = (ROOT / "references" / "propagation-engine.md").read_text(encoding="utf-8")

        # 1. Cold start workflow must be engine-derived
        self.assertIn("engine_derived", skill_doc)
        self.assertIn("Cold-start numerical workflow", skill_doc)
        self.assertIn("engine_derived", prop_doc)

        # 2. Must not contain obsolete instructions to manually author trace numbers before running simulation
        self.assertNotIn("Manually author numbers", skill_doc)
        self.assertNotIn("replace propagation-trace.jsonl with an audited trace before simulation", skill_doc.lower())
        self.assertNotIn("author trace numbers first", skill_doc.lower())

        # 3. Legacy trace replay path is distinct (analyst_authored_legacy)
        self.assertIn("analyst_authored_legacy", skill_doc)
        self.assertIn("analyst_authored_legacy", prop_doc)

    def test_vux05_cli_flags_and_schema_links_validation(self) -> None:
        """VUX05: CLI docs, script entry points, and schema links in documentation are valid and resolved."""
        skill_doc = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        # 1. Check all schema catalog files and referenced schema artifacts
        for cat_name in ("schema-catalog.json", "schema-catalog-2.1.json"):
            cat_path = ROOT / "schemas" / cat_name
            self.assertTrue(cat_path.is_file(), f"Catalog {cat_name} must exist")
            cat_data = json.loads(cat_path.read_text(encoding="utf-8"))
            for art_name, rel_file in cat_data.get("artifacts", {}).items():
                target = ROOT / "schemas" / rel_file
                self.assertTrue(
                    target.is_file(),
                    f"Catalog {cat_name} references {art_name} -> {rel_file} which does not exist",
                )

        # 2. Check scripts referenced in SKILL.md exist
        script_refs = set(re.findall(r"scripts/([a-zA-Z0-9_\-]+\.py)", skill_doc))
        self.assertTrue(script_refs, "SKILL.md should reference scripts")
        for script_ref in script_refs:
            script_path = ROOT / "scripts" / script_ref
            self.assertTrue(
                script_path.is_file(),
                f"Script {script_ref} referenced in SKILL.md does not exist at {script_path}",
            )

        # 3. Check reference docs linked in SKILL.md exist
        ref_docs = set(re.findall(r"references/([a-zA-Z0-9_\-]+\.md)", skill_doc))
        self.assertTrue(ref_docs, "SKILL.md should reference reference docs")
        for ref_doc in ref_docs:
            ref_path = ROOT / "references" / ref_doc
            self.assertTrue(
                ref_path.is_file(),
                f"Reference doc {ref_doc} referenced in SKILL.md does not exist at {ref_path}",
            )


if __name__ == "__main__":
    unittest.main()

