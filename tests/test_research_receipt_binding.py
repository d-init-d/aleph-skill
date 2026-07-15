"""Portable receipt component_binding and relocation resilience."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from aleph.component_registry import COMPONENT_URI, resolve_component
from aleph.io import canonical_hash

ROOT = Path(__file__).resolve().parents[1]


class ResearchReceiptBindingTests(unittest.TestCase):
    def test_binding_is_portable(self) -> None:
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        binding = resolution.binding()
        payload = json.dumps(binding)
        self.assertNotIn(str(ROOT), payload)
        self.assertNotIn(resolution.root.replace("\\", "\\\\"), payload)
        self.assertEqual(binding["component_uri"], COMPONENT_URI)
        self.assertEqual(binding["source_kind"], "bundled")
        self.assertTrue(binding["component_lock_sha256"].startswith("sha256:"))
        self.assertTrue(binding["component_tree_sha256"].startswith("sha256:"))
        self.assertTrue(binding["entrypoint_sha256"].startswith("sha256:"))
        self.assertEqual(binding["entrypoint"], "scripts/evidence_ledger.py")

    def test_receipt_hash_covers_binding(self) -> None:
        resolution = resolve_component(COMPONENT_URI, skill_root=ROOT)
        receipt = {
            "schema_version": "2.0.0",
            "receipt_type": "d-research-import",
            "package_major": 3,
            "component_binding": resolution.binding(),
            "raw_sha256": hashlib.sha256(b"x").hexdigest(),
        }
        digest = canonical_hash(receipt)
        receipt2 = dict(receipt)
        receipt2["component_binding"] = {
            **resolution.binding(),
            "component_tree_sha256": "sha256:" + ("0" * 64),
        }
        self.assertNotEqual(digest, canonical_hash(receipt2))


if __name__ == "__main__":
    unittest.main()
