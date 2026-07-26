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
        edge_result, _by_id = validate_edges(
            [edge], set(node_ids), evidence_ids, node_types
        )
        self.assertEqual(
            edge_result.status, "pass", [i.to_dict() for i in edge_result.issues]
        )


class DocSyncTests(unittest.TestCase):
    def test_node_builder_embeds_exact_canonical_node(self) -> None:
        graph = _graph()
        expected = json.dumps(graph["nodes"][0], indent=2)
        self.assertIn(expected, NODE_DOC.read_text(encoding="utf-8"))

    def test_edge_protocol_embeds_exact_canonical_edge(self) -> None:
        graph = _graph()
        expected = json.dumps(graph["edges"][0], indent=2)
        self.assertIn(expected, EDGE_DOC.read_text(encoding="utf-8"))

    def test_all_fenced_json_blocks_parse(self) -> None:
        for doc in (NODE_DOC, EDGE_DOC):
            text = doc.read_text(encoding="utf-8")
            blocks = JSON_FENCE_RE.findall(text)
            self.assertTrue(blocks, f"{doc.name} advertises no JSON examples")
            for index, block in enumerate(blocks):
                try:
                    json.loads(block)
                except json.JSONDecodeError as exc:
                    self.fail(f"{doc.name} fenced JSON block {index} is invalid: {exc}")

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


if __name__ == "__main__":
    unittest.main()
