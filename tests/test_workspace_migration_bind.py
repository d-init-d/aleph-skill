"""Migration bind + --check non-mutation for bundled D Research URI."""

from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from aleph.migrate import bind_bundled_d_research

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceMigrationBindTests(unittest.TestCase):
    def _manifest(self, research_root: Path) -> dict[str, object]:
        return {
            "schema_version": "2.0.0",
            "status": "complete",
            "assurance_tier": "verified",
            "execution": {
                "d_research": {
                    "path": str(research_root),
                    "status": "available",
                    "invoked": False,
                }
            },
            "artifact_index": [{"path": "evidence.csv", "sha256": "stale"}],
            "validation_receipt": {"path": "validation-receipt.json", "sha256": "stale"},
            "quality_receipt": {"path": "quality-receipt.json", "sha256": "stale"},
            "finalization": {
                "status": "committed",
                "committed_at": "2026-01-01T00:00:00Z",
                "transaction_id": "stale",
            },
        }

    def _check_external(self, root: Path, external: Path) -> dict[str, object]:
        workspace = root / "ws"
        workspace.mkdir()
        (workspace / "simulation-manifest.json").write_text(
            json.dumps(self._manifest(external), indent=2),
            encoding="utf-8",
        )
        return bind_bundled_d_research(workspace, skill_root=ROOT, check_only=True)

    def test_check_does_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "ws"
            workspace.mkdir()
            manifest = {
                "schema_version": "2.0.0",
                "execution": {
                    "d_research": {
                        "path": str(ROOT / "components" / "d-research"),
                        "status": "available",
                        "invoked": False,
                    }
                },
            }
            path = workspace / "simulation-manifest.json"
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            before = path.read_bytes()
            result = bind_bundled_d_research(workspace, skill_root=ROOT, check_only=True)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(result.get("source_mutated"))

    def test_write_rewrites_to_portable_uri(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "ws"
            workspace.mkdir()
            manifest = self._manifest(ROOT / "components" / "d-research")
            (workspace / "simulation-manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
            for name in (
                "validation-receipt.json",
                "quality-receipt.json",
                "validation-report.json",
                "quality-report.json",
            ):
                (workspace / name).write_text("{}\n", encoding="utf-8")
            result = bind_bundled_d_research(workspace, skill_root=ROOT, check_only=False)
            self.assertTrue(result.get("ok"), result)
            dest = Path(result["destination"])
            rewritten = json.loads((dest / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(rewritten["execution"]["d_research"]["path"], "aleph-component://d-research")
            self.assertEqual(rewritten["schema_version"], "2.0.0")
            self.assertEqual(rewritten["status"], "draft")
            self.assertIsNone(rewritten["assurance_tier"])
            for field in (
                "artifact_index",
                "validation_receipt",
                "quality_receipt",
                "finalization",
            ):
                self.assertNotIn(field, rewritten)
            for name in (
                "validation-receipt.json",
                "quality-receipt.json",
                "validation-report.json",
                "quality-report.json",
            ):
                self.assertFalse((dest / name).exists())
            report = json.loads((dest / "migration-bind-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["target_digest"], result["target_digest"])
            self.assertTrue(report["finalization_invalidated"])
            # source untouched
            original = json.loads((workspace / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertNotEqual(original["execution"]["d_research"]["path"], "aleph-component://d-research")

    def test_non_helper_drift_refuses_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-d-research"
            shutil.copytree(ROOT / "components" / "d-research", external)
            readme = external / "README.md"
            readme.write_bytes(readme.read_bytes() + b"\nmodified\n")
            result = self._check_external(root, external)

            self.assertFalse(result.get("ok"), result)
            equivalence = result.get("external_equivalence")
            self.assertIsInstance(equivalence, dict)
            assert isinstance(equivalence, dict)
            self.assertIn("README.md", equivalence.get("mismatched", []))

    def test_npmignore_drift_refuses_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-d-research"
            shutil.copytree(ROOT / "components" / "d-research", external)
            npmignore = external / ".npmignore"
            npmignore.write_bytes(npmignore.read_bytes() + b"\nmodified\n")

            result = self._check_external(root, external)

            self.assertFalse(result.get("ok"), result)
            equivalence = result.get("external_equivalence")
            self.assertIsInstance(equivalence, dict)
            assert isinstance(equivalence, dict)
            self.assertIn(".npmignore", equivalence.get("mismatched", []))

    def test_archived_upgrade_plan_drift_refuses_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-d-research"
            shutil.copytree(ROOT / "components" / "d-research", external)
            plan = external / "docs" / ".archive" / "UPGRADE-PLAN.md"
            plan.write_bytes(plan.read_bytes() + b"\nmodified\n")

            result = self._check_external(root, external)

            self.assertFalse(result.get("ok"), result)
            equivalence = result.get("external_equivalence")
            self.assertIsInstance(equivalence, dict)
            assert isinstance(equivalence, dict)
            self.assertIn(
                "docs/.archive/UPGRADE-PLAN.md",
                equivalence.get("mismatched", []),
            )

    def test_exact_snapshot_recipe_exclusions_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-d-research"
            shutil.copytree(ROOT / "components" / "d-research", external)
            lock = json.loads((ROOT / "component-lock.json").read_text(encoding="utf-8"))
            excluded = lock["components"]["d-research"]["snapshot_recipe"][
                "excluded_paths"
            ]
            self.assertNotIn(".npmignore", excluded)
            self.assertNotIn("docs/.archive/UPGRADE-PLAN.md", excluded)
            for relative in excluded:
                path = external / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"excluded fixture: {relative}\n".encode())

            result = self._check_external(root, external)

            self.assertTrue(result.get("ok"), result)
            equivalence = result.get("external_equivalence")
            self.assertIsInstance(equivalence, dict)
            assert isinstance(equivalence, dict)
            self.assertEqual(
                equivalence.get("snapshot_recipe_excluded_count"),
                len(excluded),
            )

    def test_repository_metadata_outside_exact_exclusions_refuses_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-d-research"
            shutil.copytree(ROOT / "components" / "d-research", external)
            extra = external / ".github" / "workflows" / "unlocked.yml"
            extra.parent.mkdir(parents=True)
            extra.write_text("name: unlocked\n", encoding="utf-8")

            result = self._check_external(root, external)

            self.assertFalse(result.get("ok"), result)
            equivalence = result.get("external_equivalence")
            self.assertIsInstance(equivalence, dict)
            assert isinstance(equivalence, dict)
            self.assertIn(
                ".github/workflows/unlocked.yml",
                equivalence.get("extra", []),
            )

    def test_runtime_and_vcs_directories_do_not_count_as_snapshot_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            external = root / "external-d-research"
            shutil.copytree(ROOT / "components" / "d-research", external)
            runtime_files = (
                ".git/config",
                ".venv/pyvenv.cfg",
                ".pytest_cache/state",
                "node_modules/example/index.js",
                "scripts/__pycache__/helper.pyc",
            )
            for relative in runtime_files:
                path = external / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"runtime-only\n")

            result = self._check_external(root, external)

            self.assertTrue(result.get("ok"), result)

    def test_real_locked_upstream_canonicaliser_runs_during_bind(self) -> None:
        fields = [
            "claim_id",
            "claim",
            "sub_question",
            "source_title",
            "source_url",
            "source_type",
            "date_published",
            "date_accessed",
            "access_method",
            "evidence",
            "quote_or_anchor",
            "contradiction",
            "confidence",
            "notes",
        ]
        row = {
            "claim_id": "c1",
            "claim": "A claim",
            "source_url": "https://example.invalid/source",
            "source_type": "primary",
            "access_method": "public_file",
            "evidence": "evidence",
            "confidence": "high",
        }
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "ws"
            workspace.mkdir()
            manifest = self._manifest(ROOT / "components" / "d-research")
            research = manifest["execution"]
            assert isinstance(research, dict)
            d_research = research["d_research"]
            assert isinstance(d_research, dict)
            d_research["ledger_ref"] = "ledger.csv"
            (workspace / "simulation-manifest.json").write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
            (workspace / "ledger.csv").write_text(buffer.getvalue(), encoding="utf-8")

            result = bind_bundled_d_research(workspace, skill_root=ROOT, check_only=True)

            self.assertTrue(result.get("ok"), result)
            dual_run = result.get("dual_run")
            self.assertIsInstance(dual_run, dict)
            assert isinstance(dual_run, dict)
            self.assertTrue(dual_run.get("byte_equal"), dual_run)
            self.assertEqual(
                dual_run.get("aleph_canonical_sha256"),
                dual_run.get("upstream_canonical_sha256"),
            )


if __name__ == "__main__":
    unittest.main()
