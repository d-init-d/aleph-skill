from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import acceptance  # noqa: E402
import release_gate  # noqa: E402
from aleph import EXIT_SEMANTIC  # noqa: E402
from aleph.installer import (  # noqa: E402
    MANIFEST_NAME,
    build_distribution_manifest,
    install,
    receipt_path_issues,
)
from aleph.validator import (  # noqa: E402
    validate_manifest_core,
    validate_numerical_artifacts,
    validate_workspace,
)

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def _valid_manifest() -> dict[str, object]:
    return json.loads((FIXTURE / "simulation-manifest.json").read_text(encoding="utf-8"))


def _attested_source(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: fixture\ndescription: fixture\n---\n", encoding="utf-8")
    manifest = build_distribution_manifest(root)
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


class ReleaseGateOrchestrationTests(unittest.TestCase):
    def test_dev_gate_runs_one_unit_suite_and_one_lifecycle(self) -> None:
        observed: list[tuple[str, list[str]]] = []

        def record(name: str, command: list[str], cwd: Path) -> dict[str, object]:
            expected_cwd = (
                ROOT / "components" / "d-research"
                if name == "research-package-check"
                else ROOT
            )
            self.assertEqual(cwd, expected_cwd)
            observed.append((name, command))
            status = {
                "component-lock": "pass",
                "research-self-test": "degraded",
                "research-acceptance": "degraded",
                "preflight": "pass",
            }.get(name)
            result: dict[str, object] = {
                "name": name,
                "command": command,
                "returncode": 0,
                "ok": True,
            }
            if status is not None:
                result["reported_status"] = status
            return result

        with (
            patch.object(release_gate.sys, "argv", ["release_gate.py", "--with-dev"]),
            patch.object(release_gate, "skill_root", return_value=ROOT),
            patch.object(
                release_gate,
                "_static_contract",
                return_value={"name": "static-contract", "ok": True},
            ),
            patch.object(release_gate, "_run", side_effect=record),
            patch.object(
                release_gate,
                "_release_artifact_checks",
                return_value=[{"name": "release-artifacts", "ok": True}],
            ) as release_artifacts,
            patch.object(release_gate.shutil, "which", return_value=None),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            release_gate.main()

        self.assertEqual(raised.exception.code, 0)
        unit_commands = [command for _, command in observed if "unittest" in command]
        self.assertEqual(len(unit_commands), 1, observed)
        lifecycle = [command for name, command in observed if name == "lifecycle-acceptance"]
        self.assertEqual(len(lifecycle), 1, observed)
        self.assertIn("--skip-unit-tests", lifecycle[0])
        self.assertNotIn("--skip-component-checks", lifecycle[0])
        mandatory = {
            "component-lock",
            "research-self-test",
            "research-package-check",
            "research-acceptance",
        }
        self.assertFalse(mandatory - {name for name, _ in observed}, observed)
        research_acceptance = [
            command for name, command in observed if name == "research-acceptance"
        ]
        self.assertEqual(len(research_acceptance), 1)
        self.assertIn("--timeout", research_acceptance[0])
        timeout_index = research_acceptance[0].index("--timeout")
        self.assertEqual(research_acceptance[0][timeout_index + 1], "300")
        release_artifacts.assert_called_once_with(ROOT, release_gate.sys.executable)
        mypy = [command for name, command in observed if name == "mypy-strict"]
        self.assertEqual(len(mypy), 1)
        self.assertIn("--strict", mypy[0])
        self.assertTrue(any(value.startswith("--cache-dir=") for value in mypy[0]))
        ruff = [command for name, command in observed if name == "ruff"]
        self.assertEqual(len(ruff), 1)
        self.assertIn("--no-cache", ruff[0])

    def test_release_artifact_gate_checks_the_relocated_distribution(self) -> None:
        observed: list[tuple[str, list[str], Path]] = []

        def record(name: str, command: list[str], cwd: Path) -> dict[str, object]:
            observed.append((name, command, cwd))
            reported_status = (
                "pass"
                if name
                in {
                    "release-zip-build",
                    "release-zip-preflight",
                    "release-zip-component-lock",
                }
                else None
            )
            result: dict[str, object] = {
                "name": name,
                "command": command,
                "returncode": 0,
                "ok": True,
            }
            if reported_status is not None:
                result["reported_status"] = reported_status
            return result

        with (
            patch.object(release_gate, "_run", side_effect=record),
            patch.object(release_gate, "_extract_release_archive", return_value=ROOT),
        ):
            checks = release_gate._release_artifact_checks(ROOT, sys.executable)

        names = [str(check["name"]) for check in checks]
        self.assertEqual(
            names,
            [
                "release-zip-build",
                "release-zip-extract",
                "release-zip-preflight",
                "release-zip-component-lock",
                "release-zip-component-package",
                "release-zip-skill-package",
            ],
        )
        self.assertTrue(all(bool(check["ok"]) for check in checks), checks)
        commands = {name: command for name, command, _ in observed}
        self.assertIn("scripts/build_release_assets.py", commands["release-zip-build"])
        self.assertIn("scripts/preflight.py", commands["release-zip-preflight"])
        self.assertIn(
            "scripts/lock_bundled_component.py",
            commands["release-zip-component-lock"],
        )
        self.assertEqual(
            commands["release-zip-component-package"],
            ["node", "scripts/package_manifest_check.mjs"],
        )
        self.assertIn(
            "scripts/validate_skill_package.py",
            commands["release-zip-skill-package"],
        )

    def test_release_archive_extraction_refuses_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            archive = temporary / "malicious.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("aleph-skill/SKILL.md", "safe")
                bundle.writestr("aleph-skill/../escaped.txt", "unsafe")
            with self.assertRaisesRegex(ValueError, "unsafe release archive member"):
                release_gate._extract_release_archive(archive, temporary / "extracted")
            self.assertFalse((temporary / "extracted" / "escaped.txt").exists())

    def test_json_status_is_a_hard_gate_even_when_exit_code_is_zero(self) -> None:
        check: dict[str, Any] = {
            "name": "semantic-status",
            "returncode": 0,
            "ok": True,
            "reported_status": "delegated",
        }
        release_gate._require_reported_status(check, frozenset({"pass"}))
        self.assertFalse(check["ok"])
        self.assertIn("status_error", check)

    def test_plain_success_output_is_not_reported_as_a_json_error(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["plain-tool"],
            returncode=0,
            stdout="plain success\n",
            stderr="",
        )
        with patch.object(release_gate.subprocess, "run", return_value=completed):
            check = release_gate._run("plain-tool", ["plain-tool"], ROOT)
        self.assertTrue(check["ok"])
        self.assertNotIn("json_error", check)

    def test_missing_release_runtime_is_reported_instead_of_crashing(self) -> None:
        with patch.object(release_gate.subprocess, "run", side_effect=OSError("missing node")):
            check = release_gate._run("component-package", ["node", "check.mjs"], ROOT)
        self.assertFalse(check["ok"])
        self.assertIsNone(check["returncode"])
        self.assertIn("missing node", str(check["stderr"]))

    def test_acceptance_missing_explicit_fixture_fails_closed(self) -> None:
        observed: list[list[str]] = []

        def fake_run(command: list[str], cwd: Path) -> dict[str, object]:
            self.assertEqual(cwd, ROOT)
            observed.append(command)
            if any(value.endswith("init_simulation_workspace.py") for value in command):
                out_dir = Path(command[command.index("--out-dir") + 1]) / "portable-smoke"
                out_dir.mkdir(parents=True)
                for name in (
                    "simulation-manifest.json",
                    "nodes.json",
                    "edges.json",
                    "actors.json",
                    "branch-ledger.json",
                ):
                    (out_dir / name).write_text("{}\n", encoding="utf-8")
            return {
                "cmd": command,
                "returncode": 0,
                "stdout_full": "{}",
                "stdout": "{}",
                "stderr": "",
            }

        missing = ROOT / "tests" / "fixtures" / "does-not-exist"
        output = io.StringIO()
        with (
            patch.object(
                acceptance.sys,
                "argv",
                [
                    "acceptance.py",
                    "--adversarial",
                    str(missing),
                    "--skip-unit-tests",
                    "--skip-component-checks",
                ],
            ),
            patch.object(acceptance, "skill_root", return_value=ROOT),
            patch.object(acceptance, "run", side_effect=fake_run),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            acceptance.main()

        self.assertEqual(raised.exception.code, EXIT_SEMANTIC)
        report = json.loads(output.getvalue())
        failed = [item for item in report["results"] if item["stage"] == "adversarial-fixture"]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["pass"])
        flattened = [part for command in observed for part in command]
        self.assertNotIn("unittest", flattened)
        self.assertFalse(any(value.endswith("validate_domain_packs.py") for value in flattened))
        self.assertFalse(any(value.endswith("check_adapters.py") for value in flattened))

    def test_default_adversarial_fixture_is_derived_without_mutating_source(self) -> None:
        source_manifest = FIXTURE / "simulation-manifest.json"
        original = source_manifest.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            derived = acceptance.derive_adversarial_fixture(
                FIXTURE,
                Path(temporary) / "adversarial",
            )
            self.assertNotEqual(derived, FIXTURE)
            manifest = json.loads((derived / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["forged_release_claim"], "verified")
            result = validate_workspace(derived, mode="final", verify_integrity=False)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result["error_codes"])
        self.assertFalse(derived.exists())
        self.assertEqual(source_manifest.read_bytes(), original)

    def test_adversarial_crash_or_malformed_json_never_counts_as_success(self) -> None:
        valid_validation = json.dumps(
            {"status": "fail", "error_codes": ["UNKNOWN_FIELD"]}
        )
        valid_quality = json.dumps(
            {"assurance_tier": "experimental", "validation_status": "fail"}
        )
        scenarios = (
            (2, valid_validation, 0, valid_quality),
            (EXIT_SEMANTIC, "not-json", 0, valid_quality),
            (
                EXIT_SEMANTIC,
                json.dumps({"status": "fail", "error_codes": ["MISSING_ARTIFACT"]}),
                0,
                valid_quality,
            ),
            (EXIT_SEMANTIC, valid_validation, 0, "not-json"),
            (EXIT_SEMANTIC, valid_validation, EXIT_SEMANTIC, valid_quality),
            (
                EXIT_SEMANTIC,
                valid_validation,
                0,
                json.dumps(
                    {"assurance_tier": "verified", "validation_status": "pass"}
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for validation_code, validation_output, quality_code, quality_output in scenarios:
                with self.subTest(
                    validation_code=validation_code,
                    validation_output=validation_output,
                    quality_code=quality_code,
                    quality_output=quality_output,
                ):
                    results: list[dict[str, Any]] = []
                    responses = [
                        {
                            "cmd": ["validate"],
                            "returncode": validation_code,
                            "stdout_full": validation_output,
                            "stdout": validation_output,
                            "stderr": "",
                        },
                        {
                            "cmd": ["quality"],
                            "returncode": quality_code,
                            "stdout_full": quality_output,
                            "stdout": quality_output,
                            "stderr": "",
                        },
                    ]
                    with patch.object(acceptance, "run", side_effect=responses):
                        acceptance.append_adversarial_results(
                            workspace,
                            root=ROOT,
                            scripts=SCRIPTS,
                            results=results,
                        )
                    self.assertEqual(len(results), 2)
                    self.assertFalse(all(bool(value.get("pass")) for value in results))

    def test_ci_uses_pinned_actions_and_locked_dependency_installs(self) -> None:
        ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("uses: ./.github/workflows/verify.yml", ci_workflow)
        self.assertIn("workflow_call:", workflow)
        uses_lines = [line.strip() for line in workflow.splitlines() if "uses:" in line]
        self.assertTrue(uses_lines)
        for line in uses_lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"uses: [^@]+@[0-9a-f]{40}(?:\s+#.*)?$")
        self.assertNotIn("-latest", workflow)
        self.assertNotIn("actions/checkout@v", workflow)
        self.assertNotIn("actions/setup-python@v", workflow)
        self.assertNotIn("actions/setup-node@v", workflow)
        for digest in (
            "34e114876b0b11c390a56381ad16ebd13914f8d5",
            "a26af69be951a213d495a4c3e4e4022e16d87065",
            "49933ea5288caeca8642d1e84afbd3f7d6820020",
            "d0cc045d04ccac9d8b7881df0226f9e82c39688e",
        ):
            self.assertIn(digest, workflow)
        self.assertIn("uv sync --locked", workflow)
        self.assertIn("uv run --no-sync", workflow)
        self.assertEqual(workflow.count('version: "0.11.18"'), 2)
        self.assertIn("component-no-browser:", workflow)
        self.assertIn("browser-smoke:", workflow)
        self.assertIn("node-version: [18, 20, 22]", workflow)
        self.assertIn('{"delegated", "degraded"}', workflow)
        self.assertIn("browser_cases_delegated", workflow)
        self.assertIn("research:browser-smoke", workflow)
        self.assertIn('payload.get("status") != "ok"', workflow)
        self.assertIn(
            "node node_modules/playwright/cli.js install --with-deps chromium",
            workflow,
        )
        self.assertEqual(workflow.count("npm ci --ignore-scripts"), 3)

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("strict = true", pyproject)
        self.assertNotIn("ignore_missing_imports", pyproject)
        self.assertIn("fail_under = 82", pyproject)


class ValidatorContractCoverageTests(unittest.TestCase):
    def test_minimal_v2_manifest_fails_as_structured_issues_without_crashing(self) -> None:
        result = validate_manifest_core({"schema_version": "2.0.0"}, "final")
        self.assertEqual(result.status, "fail")
        codes = {value.code for value in result.issues}
        self.assertTrue(
            {"MISSING_FIELD", "SCHEMA", "ENUM", "TEMPORAL_FRAME", "INCOMPLETE"}
            <= codes
        )
        pointers = {value.pointer for value in result.issues}
        for required in (
            "simulation_id",
            "created_at",
            "change_point",
            "temporal_frame",
            "scope",
            "execution",
            "artifact_paths",
            "assumptions",
        ):
            self.assertIn(required, pointers)

    def test_malformed_nested_manifest_fields_are_rejected_together(self) -> None:
        manifest = _valid_manifest()
        manifest.update(
            {
                "simulation_id": "invalid",
                "status": "invalid",
                "assurance_tier": "gold",
                "active_contexts": [1],
                "migration": {
                    "source_schema_version": "",
                    "target_schema_version": "1.0.0",
                    "source_digest": "invalid",
                    "transforms": [1],
                    "unresolved_fields": [1],
                },
                "artifact_index": [
                    1,
                    {
                        "path": "",
                        "media_type": 1,
                        "size": -1,
                        "sha256": "invalid",
                        "hash_scope": "partial",
                    },
                ],
                "validation_receipt": "invalid",
                "quality_receipt": {"path": "", "sha256": "invalid"},
                "finalization": {
                    "status": "pending",
                    "committed_at": "invalid",
                    "transaction_id": "",
                },
            }
        )
        artifact_paths = manifest["artifact_paths"]
        self.assertIsInstance(artifact_paths, dict)
        artifact_paths["nodes"] = ""  # type: ignore[index]
        change = manifest["change_point"]
        self.assertIsInstance(change, dict)
        change.update(  # type: ignore[union-attr]
            {
                "type": "",
                "target": None,
                "description": "",
                "location": 1,
                "magnitude": "0.25",
                "time": "invalid",
                "assumption_ref": "invalid",
            }
        )
        frame = manifest["temporal_frame"]
        self.assertIsInstance(frame, dict)
        frame["calibration_strategy"] = ""  # type: ignore[index]
        frame["monitoring_indicators"] = "invalid"  # type: ignore[index]
        execution = manifest["execution"]
        self.assertIsInstance(execution, dict)
        adaptive = execution["adaptive_scope"]  # type: ignore[index]
        self.assertIsInstance(adaptive, dict)
        adaptive.update(  # type: ignore[union-attr]
            {
                "assessed": 1,
                "overall_complexity": 2,
                "rationale": "",
                "decomposition": {
                    "subquestions": ["", 1],
                    "critical_paths": "invalid",
                    "research_waves_completed": -1,
                },
            }
        )
        control = execution["research_control"]  # type: ignore[index]
        self.assertIsInstance(control, dict)
        control.update(  # type: ignore[union-attr]
            {
                "sources_examined": -1,
                "saturation_reached": "yes",
                "unresolved_critical_gaps": "invalid",
                "consecutive_no_new_material_claims": True,
                "stop_reason": 1,
            }
        )
        d_research = execution["d_research"]  # type: ignore[index]
        self.assertIsInstance(d_research, dict)
        d_research.update(  # type: ignore[union-attr]
            {
                "status": "",
                "invoked": 1,
                "package_major": 0,
                "path": 1,
                "ledger_ref": 1,
            }
        )
        subagents = execution["subagents"]  # type: ignore[index]
        self.assertIsInstance(subagents, dict)
        subagents.update(  # type: ignore[union-attr]
            {
                "status": "",
                "tool": 1,
                "detection_method": 1,
                "fallback_reason": 1,
            }
        )
        checkpoints = execution["checkpoints"]  # type: ignore[index]
        self.assertIsInstance(checkpoints, dict)
        checkpoints["graph_built"] = "yes"  # type: ignore[index]

        result = validate_manifest_core(manifest, "final")
        pointers = {value.pointer for value in result.issues}
        expected = {
            "simulation_id",
            "status",
            "assurance_tier",
            "artifact_paths.nodes",
            "change_point.magnitude",
            "change_point.time",
            "change_point.assumption_ref",
            "temporal_frame.calibration_strategy",
            "temporal_frame.monitoring_indicators",
            "execution.adaptive_scope.assessed",
            "execution.adaptive_scope.decomposition.research_waves_completed",
            "execution.research_control.sources_examined",
            "execution.d_research.package_major",
            "execution.subagents.tool",
            "execution.checkpoints.graph_built",
            "migration.source_digest",
            "artifact_index/1/hash_scope",
            "validation_receipt",
            "quality_receipt.sha256",
            "finalization.committed_at",
        }
        self.assertTrue(expected <= pointers, sorted(expected - pointers))
        self.assertEqual(result.status, "fail")

    def test_wrong_optional_container_types_fail_closed(self) -> None:
        manifest = copy.deepcopy(_valid_manifest())
        execution = manifest["execution"]
        self.assertIsInstance(execution, dict)
        execution.update(  # type: ignore[union-attr]
            {
                "adaptive_scope": "invalid",
                "research_control": "invalid",
                "d_research": "invalid",
                "subagents": [],
                "checkpoints": "invalid",
            }
        )
        manifest.update(
            {
                "migration": "invalid",
                "artifact_index": {},
                "validation_receipt": 1,
                "quality_receipt": [],
                "finalization": [],
            }
        )

        result = validate_manifest_core(manifest, "final")
        pointers = {value.pointer for value in result.issues}
        expected = {
            "execution.adaptive_scope",
            "execution.research_control",
            "execution.d_research",
            "execution.subagents",
            "execution.checkpoints",
            "migration",
            "artifact_index",
            "validation_receipt",
            "quality_receipt",
            "finalization",
        }
        self.assertTrue(expected <= pointers, sorted(expected - pointers))

    def test_workspace_and_actor_protocol_failures_are_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result = validate_workspace(missing, mode="final")
            self.assertEqual(result["status"], "fail")
            self.assertIn("MISSING_ARTIFACT", result["error_codes"])

        for failure in (ImportError("unavailable"), RuntimeError("malformed packet")):
            with self.subTest(failure=type(failure).__name__):
                with patch("aleph.packets.validate_actor_protocol", side_effect=failure):
                    result = validate_workspace(
                        FIXTURE,
                        mode="draft",
                        verify_integrity=False,
                    )
                protocol = result["check_results"]["actor_protocol"]
                self.assertEqual(protocol["status"], "fail")
                self.assertIn(
                    "VALIDATION_FAILED",
                    {value["code"] for value in protocol["issues"]},
                )

    def test_calibration_and_sensitivity_reports_fail_every_integrity_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest = json.loads(
                (workspace / "simulation-manifest.json").read_text(encoding="utf-8")
            )
            manifest["artifact_paths"].update(
                {
                    "calibration_report": "calibration-report.json",
                    "sensitivity_report": "sensitivity-report.json",
                }
            )
            (workspace / "calibration-report.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2.0.0",
                        "status": "fail",
                        "policy_locked": False,
                        "model_version": "aleph-engine-2.0",
                        "formula_version": "2.0.0",
                        "model_hash": "invalid",
                        "config_hash": "invalid",
                        "policy_hash": "invalid",
                        "hindcast_digest": "invalid",
                        "outcome_digest": "invalid",
                        "case_count": 29,
                        "unique_case_count": 28,
                        "metrics": {},
                        "beats_baseline": False,
                        "report_hash": "invalid",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (workspace / "sensitivity-report.json").write_text(
                json.dumps({"model_hash": "invalid", "report_hash": "invalid"}) + "\n",
                encoding="utf-8",
            )

            result = validate_numerical_artifacts(workspace, manifest)

            self.assertEqual(result.status, "fail")
            calibration_pointers = {
                value.pointer
                for value in result.issues
                if value.artifact == "calibration_report"
            }
            self.assertTrue(
                {
                    "report_hash",
                    "model_hash",
                    "config_hash",
                    "policy_hash",
                    "hindcast_digest",
                    "outcome_digest",
                    "case_count",
                    "unique_case_count",
                }
                <= calibration_pointers
            )
            sensitivity_pointers = {
                value.pointer
                for value in result.issues
                if value.artifact == "sensitivity_report"
            }
            self.assertEqual(
                sensitivity_pointers,
                {"report_hash", "model_hash", "formula_version", "model_version"},
            )


class InstallerTransactionCoverageTests(unittest.TestCase):
    def test_symlink_install_rechecks_manifest_even_after_successful_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            destination = root / "destination"
            unsafe_plan: dict[str, Any] = {
                "ok": True,
                "status": "planned",
                "mode": "symlink",
                "source": str(source),
                "destination": str(destination),
                "file_count": 0,
                "files": [],
                "manifest": {"status": "absent"},
                "issues": [],
            }

            with patch("aleph.installer.plan_install", return_value=unsafe_plan):
                result = install(source, destination, mode="symlink")

            self.assertEqual(result["status"], "refused")
            self.assertFalse(result["ok"], result)
            self.assertFalse(destination.exists())
            self.assertIn(
                "INSTALL_NOT_ALLOWLISTED",
                {value["code"] for value in result["issues"]},
            )

    def test_receipts_must_be_disjoint_from_source_and_both_target_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            destination = root / "destination"

            in_source = receipt_path_issues(
                source / "receipt.json",
                source=source,
                destination=destination,
                destination_is_directory=True,
            )
            in_directory_target = receipt_path_issues(
                destination / "receipt.json",
                source=source,
                destination=destination,
                destination_is_directory=True,
            )
            exact_file_target = receipt_path_issues(
                destination,
                source=source,
                destination=destination,
                destination_is_directory=False,
            )

            self.assertTrue(in_source)
            self.assertTrue(in_directory_target)
            self.assertTrue(exact_file_target)
            self.assertTrue(
                all(value.code == "INSTALL_SOURCE_DEST" for value in in_source + in_directory_target + exact_file_target)
            )

    def test_symlink_transaction_commits_when_platform_call_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _attested_source(root / "source")
            destination = root / "installed"

            def create_directory_link(
                target: Path,
                link: Path,
                *,
                target_is_directory: bool,
            ) -> None:
                self.assertEqual(Path(target), source.resolve())
                self.assertEqual(Path(link), destination)
                self.assertTrue(target_is_directory)
                destination.mkdir()

            with patch("aleph.installer.os.symlink", side_effect=create_directory_link):
                result = install(source, destination, mode="symlink")

            self.assertEqual(result["status"], "symlinked", result)
            self.assertTrue(destination.is_dir())
            self.assertEqual(result["assurance_cap"], "limited")

    def test_symlink_commit_discards_replaced_file_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _attested_source(root / "source")
            destination = root / "installed"
            destination.write_text("old\n", encoding="utf-8")

            def create_directory_link(*_: object, **__: object) -> None:
                destination.mkdir()

            with patch("aleph.installer.os.symlink", side_effect=create_directory_link):
                result = install(source, destination, mode="symlink", force=True)

            self.assertEqual(result["status"], "symlinked", result)
            self.assertEqual(result["rollback_status"], "backup-discarded")
            self.assertIsNone(result["backup"])
            self.assertTrue(destination.is_dir())
            self.assertFalse(list(root.glob(".installed.aleph-backup-*")))

    def test_failed_symlink_replaces_nothing_and_restores_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _attested_source(root / "source")
            destination = root / "installed"
            destination.write_text("keep\n", encoding="utf-8")

            with patch("aleph.installer.os.symlink", side_effect=OSError("denied")):
                result = install(source, destination, mode="symlink", force=True)

            self.assertEqual(result["status"], "failed", result)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["rollback_status"], "restored")
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse(list(root.glob(".installed.aleph-backup-*")))


if __name__ == "__main__":
    unittest.main()
