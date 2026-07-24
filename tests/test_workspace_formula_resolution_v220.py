from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aleph import FORMULA_VERSION, LEGACY_FORMULA_VERSION
from compile_model import compile_workspace, resolve_workspace_formula_version
from run_simulation import _formula_version


class WorkspaceFormulaResolutionV220Tests(unittest.TestCase):
    def test_legacy_run_contract_selects_legacy_formula(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "simulation-run.json").write_text(
                json.dumps({"run_contract_version": "aleph-run-2.0"}),
                encoding="utf-8",
            )
            self.assertEqual(
                resolve_workspace_formula_version(workspace, {}),
                LEGACY_FORMULA_VERSION,
            )

    def test_run_path_honors_legacy_model_contract_without_trace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "simulation-model.json").write_text(
                json.dumps({"model_version": "aleph-engine-2.0"}),
                encoding="utf-8",
            )

            self.assertEqual(_formula_version(workspace, {}), LEGACY_FORMULA_VERSION)

    def test_conflicting_workspace_formula_contracts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            manifest = {"formula_version": FORMULA_VERSION}
            (workspace / "simulation-run.json").write_text(
                json.dumps({"run_contract_version": "aleph-run-2.0"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "formula contracts disagree"):
                resolve_workspace_formula_version(workspace, manifest)

    def test_compile_workspace_honors_manifest_formula(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            manifest = {
                "formula_version": LEGACY_FORMULA_VERSION,
                "artifact_paths": {"nodes": "nodes.json", "edges": "edges.json"},
            }
            (workspace / "simulation-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (workspace / "nodes.json").write_text(
                json.dumps(
                    [
                        {"id": "factor:a", "baseline": 1.0},
                        {"id": "factor:b", "baseline": 0.0},
                    ]
                ),
                encoding="utf-8",
            )
            (workspace / "edges.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "edge:ab",
                            "from": "factor:a",
                            "to": "factor:b",
                            "sign": 1,
                            "base_strength": 1.0,
                            "context_modifiers": [{"multiplier": 1.0}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            compiled = compile_workspace(workspace)
            self.assertEqual(compiled["formula_version"], LEGACY_FORMULA_VERSION)
            self.assertEqual(compiled["model_version"], "aleph-engine-2.0")


if __name__ == "__main__":
    unittest.main()
