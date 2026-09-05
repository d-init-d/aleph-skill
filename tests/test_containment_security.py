from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.io import canonical_hash, write_json_atomic  # noqa: E402
from aleph.paths import output_alias_issues, resolve_in_workspace  # noqa: E402
from aleph.validator import (  # noqa: E402
    artifact_integrity_hash,
    validate_numerical_artifacts,
)

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def _setup_base_workspace(temporary_dir: str):
    workspace = Path(temporary_dir) / "workspace"
    shutil.copytree(FIXTURE, workspace)
    manifest_path = workspace / "simulation-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model = json.loads((workspace / "simulation-model.json").read_text(encoding="utf-8"))
    model_digest = model.get("model_hash")
    return workspace, manifest, manifest_path, model, model_digest


def _sync_manifest_and_model(workspace: Path, manifest: dict) -> None:
    manifest_path = workspace / "simulation-manifest.json"
    write_json_atomic(manifest_path, manifest)
    model_path = workspace / "simulation-model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    if "source_hashes" in model and isinstance(model["source_hashes"], dict):
        for rel in list(model["source_hashes"].keys()):
            p = workspace / rel
            if p.is_file():
                model["source_hashes"][rel] = artifact_integrity_hash(p, rel, manifest)
        model["source_set_hash"] = canonical_hash(model["source_hashes"])
        write_json_atomic(model_path, model)


class ContainmentSecurityAcceptanceTests(unittest.TestCase):
    def test_a20_path_traversal_containment(self) -> None:
        """A20: Calibration case references path traversal ('../../secrets.json') or absolute path outside workspace.

        Validator containment checks reject out-of-workspace references; enforces strict path containment.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, model_digest = _setup_base_workspace(temporary)
            manifest["likelihood_mode"] = "calibrated_probability"
            manifest["artifact_paths"]["calibration_bundle"] = "calibration-bundle.json"
            _sync_manifest_and_model(workspace, manifest)

            # Bundle references path traversal outside workspace
            bundle = {
                "schema_version": "1.0.0",
                "bundle_id": "bundle:traversal-test",
                "domain": "economics",
                "target_variable": "inflation",
                "target_horizon": "1m",
                "temporal_cutoff": "2025-01-01T00:00:00Z",
                "policy_manifest": {
                    "policy_id": "policy:p1",
                    "policy_hash": "sha256:" + "0" * 64,
                    "precommitted": True,
                    "commitment_timestamp": "2024-12-01T00:00:00Z",
                    "evaluation_type": "retrospective",
                    "model_class": "structural",
                    "baseline_model": "persistence",
                    "metrics_to_evaluate": ["mae"],
                    "thresholds": {"max_mae": 1.0},
                },
                "dataset_snapshots": [
                    {
                        "dataset_id": "ds:escape",
                        "source_title": "Secrets Escape",
                        "source_url": "https://example.org/secrets",
                        "vintage_date": "2024-12-01",
                        "access_date": "2024-12-01T00:00:00Z",
                        "file_path": "../../secrets.json",  # Traversal attempt
                        "sha256_digest": "sha256:" + "0" * 64,
                        "is_synthetic": False,
                    }
                ],
                "per_case_predictions": [],
                "realized_outcomes": [],
                "recomputed_metrics": {},
                "uncertainty_bounds": {},
                "temporal_leakage_audit": {},
                "assurance_status": "uncalibrated",
            }
            bundle["bundle_hash"] = canonical_hash(bundle)
            write_json_atomic(workspace / "calibration-bundle.json", bundle)

            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "fail")
            escape_issues = [i for i in result.issues if i.code in {"PATH_ESCAPE", "INVALID_PATH"}]
            self.assertTrue(len(escape_issues) > 0)

    def test_t14_unicode_paths_with_spaces(self) -> None:
        """T14: Simulation manifest references artifact paths with spaces or Unicode characters.

        Engine loads and writes artifacts using declared paths independent of current working directory.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, model, model_digest = _setup_base_workspace(temporary)

            # Create subdirectories with unicode and spaces
            unicode_dir = workspace / "thư mục mô hình"
            unicode_dir.mkdir(parents=True, exist_ok=True)
            output_dir = workspace / "kết quả mô phỏng"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Relocate computational model
            old_model_path = workspace / "simulation-model.json"
            new_model_rel = "thư mục mô hình/mô hình dự báo.json"
            new_model_path = workspace / new_model_rel
            shutil.move(old_model_path, new_model_path)

            # Relocate run ledger and trace
            old_run_path = workspace / "simulation-run.json"
            new_run_rel = "kết quả mô phỏng/bản ghi chạy.json"
            new_run_path = workspace / new_run_rel
            shutil.move(old_run_path, new_run_path)

            old_trace_path = workspace / "propagation-trace.jsonl"
            new_trace_rel = "kết quả mô phỏng/vết lan truyền.jsonl"
            shutil.move(old_trace_path, workspace / new_trace_rel)

            # Update trace_contract in run ledger
            run_data = json.loads(new_run_path.read_text(encoding="utf-8"))
            if "trace_contract" in run_data and isinstance(run_data["trace_contract"], dict):
                run_data["trace_contract"]["path"] = new_trace_rel
            run_data.pop("contract_hash", None)
            run_data["contract_hash"] = canonical_hash(run_data)
            write_json_atomic(new_run_path, run_data)

            # Update replay report if present
            if "replay_report" in manifest.get("artifact_paths", {}):
                replay_rel = manifest["artifact_paths"]["replay_report"]
                replay_path = workspace / replay_rel
                if replay_path.is_file():
                    replay_data = json.loads(replay_path.read_text(encoding="utf-8"))
                    replay_data["recorded_contract_hash"] = run_data["contract_hash"]
                    replay_data.pop("report_hash", None)
                    replay_data["report_hash"] = canonical_hash(replay_data)
                    write_json_atomic(replay_path, replay_data)

            # Update manifest artifact paths
            manifest["artifact_paths"]["computational_model"] = new_model_rel
            manifest["artifact_paths"]["run_ledger"] = new_run_rel
            manifest["artifact_paths"]["propagation_trace"] = new_trace_rel

            # Update model source hashes
            new_model = json.loads(new_model_path.read_text(encoding="utf-8"))
            if "source_hashes" in new_model:
                new_model["source_hashes"] = {
                    new_run_rel: artifact_integrity_hash(workspace / new_run_rel, new_run_rel, manifest),
                    new_trace_rel: artifact_integrity_hash(workspace / new_trace_rel, new_trace_rel, manifest),
                }
                new_model["source_set_hash"] = canonical_hash(new_model["source_hashes"])
                write_json_atomic(new_model_path, new_model)

            write_json_atomic(manifest_path, manifest)

            # Validate numerical artifacts passes with declared unicode paths
            result = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(result.status, "pass", [i.to_dict() for i in result.issues])

            # Resolve in workspace succeeds for unicode paths
            resolved_model, issues = resolve_in_workspace(workspace, new_model_rel, must_exist=True)
            self.assertEqual(len(issues), 0)
            self.assertIsNotNone(resolved_model)
            self.assertTrue(resolved_model.is_file())

    def test_t15_symlink_escape_containment(self) -> None:
        """T15: Model manifest attempts symlink escape or input file overwrite.

        Engine rejects path escape; containment policy strictly enforced.
        """
        with tempfile.TemporaryDirectory() as temporary:
            workspace, manifest, manifest_path, _, _ = _setup_base_workspace(temporary)

            # 1. Path escape via parent traversal in artifact_paths
            manifest_escape = copy.deepcopy(manifest)
            manifest_escape["artifact_paths"]["computational_model"] = "../../outside_model.json"
            result_escape = validate_numerical_artifacts(workspace, manifest_escape)
            self.assertEqual(result_escape.status, "fail")
            self.assertTrue(any(i.code in {"PATH_ESCAPE", "INVALID_PATH"} for i in result_escape.issues))

            # 2. Direct resolve_in_workspace rejection of escape
            _, escape_issues = resolve_in_workspace(workspace, "../escaped_target.json")
            self.assertTrue(any(i.code == "PATH_ESCAPE" for i in escape_issues))

            # 3. Output aliasing input file overwrite prevention
            input_model = workspace / "simulation-model.json"
            attempted_output = workspace / "simulation-model.json"
            alias_issues = output_alias_issues(attempted_output, [input_model])
            self.assertTrue(len(alias_issues) > 0)
            self.assertEqual(alias_issues[0].code, "PATH_ALIAS")

    def test_i10_path_relocatability(self) -> None:
        """I10: Workspaces relocated across directories or paths containing non-ASCII characters."""
        from test_component_integration import ComponentIntegrationAcceptanceTests
        delegate = ComponentIntegrationAcceptanceTests()
        delegate.test_i10_path_relocatability()

    def test_vpk08_archive_traversal_and_leak_prevention(self) -> None:
        """VPK08: Traversal, external symlinks, absolute paths, and secrets are prevented in packaging/extraction."""
        from aleph.installer import scan_secret_like_files
        from aleph.paths import validate_relative_artifact_path

        # 1. Prohibited traversal and absolute path patterns are rejected
        self.assertTrue(bool(validate_relative_artifact_path("../outside.py")))
        self.assertTrue(bool(validate_relative_artifact_path("/etc/passwd")))
        self.assertTrue(bool(validate_relative_artifact_path("C:\\Windows\\system32\\cmd.exe")))
        self.assertTrue(bool(validate_relative_artifact_path("..\\traversal.txt")))

        # 2. Secret-like filenames and keys are caught by scanner
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "secrets.json").write_text('{"api_key": "123"}', encoding="utf-8")
            (p / "server.key").write_text("dummy key", encoding="utf-8")
            findings = scan_secret_like_files(p)
            self.assertGreaterEqual(len(findings), 2)
            self.assertTrue(any("secrets.json" in f.get("path", "") for f in findings))

        # 3. Test that real repo contains zero secret leaks
        real_findings = scan_secret_like_files(ROOT)
        real_secrets = [f for f in real_findings if f.get("reason") in {"secret-like filename", "secret-like content"}]
        self.assertEqual(real_secrets, [], f"Found secret leaks in repository: {real_secrets}")


if __name__ == "__main__":
    unittest.main()
