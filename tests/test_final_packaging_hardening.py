from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph import installer as installer_module  # noqa: E402
from aleph.adapters_registry import (  # noqa: E402
    PORTABLE_CORE_PATH,
    TARGET_SPECS,
    generate_external_profile,
    generate_instruction_adapter,
)
from aleph.installer import (  # noqa: E402
    MANIFEST_NAME,
    build_distribution_manifest,
    install,
    install_adapter_file,
    plan_install,
    rollback_install_result,
    verify_distribution_manifest,
)
from aleph.io import write_json_atomic  # noqa: E402
from aleph.migrate import migrate_workspace  # noqa: E402
from aleph.paths import assert_install_paths_safe  # noqa: E402
from aleph.validator import (  # noqa: E402
    validate_branches,
    validate_manifest_core,
    validate_numerical_artifacts,
    validate_paths,
)
from install_adapters import destination, install_portable_adapter  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _verified_adapter_source(root: Path, *, secret: bool = False) -> Path:
    adapter = root / "adapters" / "generated" / "cursor.md"
    adapter.parent.mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: aleph-skill\ndescription: test\n---\n", encoding="utf-8"
    )
    (root / "scripts" / "preflight.py").write_text("print('ok')\n", encoding="utf-8")
    content = "api_key=ABCDEFGHIJKLMNOPQRSTUV\n" if secret else generate_instruction_adapter("cursor", root)
    adapter.write_text(content, encoding="utf-8")
    write_json_atomic(root / MANIFEST_NAME, build_distribution_manifest(root))
    return adapter


def _create_directory_link(link: Path, target: Path) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        return subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    os.symlink(target, link, target_is_directory=True)
    return subprocess.CompletedProcess([], 0, "", "")


def _remove_directory_link(link: Path) -> None:
    if not os.path.lexists(link):
        return
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


class PublishedSchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        value = _load_json(FIXTURES / "schema-2.0-valid" / "simulation-manifest.json")
        assert isinstance(value, dict)
        self.manifest: dict[str, object] = value

    def test_valid_manifest_contract_passes(self) -> None:
        result = validate_manifest_core(self.manifest, "final")
        self.assertEqual(result.status, "pass", [item.to_dict() for item in result.issues])

    def test_manifest_pattern_and_nested_required_fields_are_enforced(self) -> None:
        cases: list[tuple[str, object]] = []

        bad_id = copy.deepcopy(self.manifest)
        bad_id["simulation_id"] = "not-a-simulation-id"
        cases.append(("simulation_id", bad_id))

        missing_change_fields = copy.deepcopy(self.manifest)
        assumptions = missing_change_fields["assumptions"]
        assert isinstance(assumptions, list) and isinstance(assumptions[0], dict)
        missing_change_fields["change_point"] = {"assumption_ref": assumptions[0]["id"]}
        cases.append(("change_point", missing_change_fields))

        missing_execution_fields = copy.deepcopy(self.manifest)
        execution = missing_execution_fields["execution"]
        assert isinstance(execution, dict)
        execution.pop("d_research")
        execution.pop("subagents")
        cases.append(("execution", missing_execution_fields))

        invalid_types = copy.deepcopy(self.manifest)
        change_point = invalid_types["change_point"]
        artifact_paths = invalid_types["artifact_paths"]
        assert isinstance(change_point, dict) and isinstance(artifact_paths, dict)
        change_point["magnitude"] = True
        artifact_paths["nodes"] = 1
        invalid_types["migration"] = []
        cases.append(("published-types", invalid_types))

        for label, manifest in cases:
            with self.subTest(label=label):
                assert isinstance(manifest, dict)
                result = validate_manifest_core(manifest, "final")
                self.assertEqual(result.status, "fail")

    def test_branch_ledger_requires_published_top_level_contract(self) -> None:
        ledger = _load_json(FIXTURES / "schema-2.0-valid" / "branch-ledger.json")
        assert isinstance(ledger, dict)
        ledger.pop("calibrated")
        result = validate_branches(
            ledger,
            {"causal:rate-to-gap"},
            {"actor:governor"},
            {"evidence:macro-series"},
            self.manifest,
            {"factor:policy-rate", "factor:output-gap"},
        )
        self.assertEqual(result.status, "fail")
        self.assertIn("branch_ledger.calibrated", {item.pointer for item in result.issues})

    def test_migrator_writes_uncalibrated_branch_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination_path = Path(temporary) / "migrated"
            result = migrate_workspace(FIXTURES / "schema-1.2-valid", destination_path)
            self.assertTrue(result["ok"], result)
            ledger = _load_json(destination_path / "branch-ledger.json")
            assert isinstance(ledger, dict)
            self.assertIs(ledger["calibrated"], False)

    def test_verified_d_research_requires_resolvable_import_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            manifest = copy.deepcopy(self.manifest)
            execution = manifest["execution"]
            assert isinstance(execution, dict)
            d_research = execution["d_research"]
            assert isinstance(d_research, dict)
            d_research["status"] = "verified"
            missing = validate_paths(manifest, workspace)
            self.assertEqual(missing.status, "fail")

            artifact_paths = manifest["artifact_paths"]
            assert isinstance(artifact_paths, dict)
            artifact_paths["research_import_receipt"] = "research-import-receipt.json"
            write_json_atomic(workspace / "research-import-receipt.json", {"status": "verified"})
            resolved = validate_paths(manifest, workspace)
            self.assertEqual(resolved.status, "pass", [item.to_dict() for item in resolved.issues])


class InstallerAttestationTests(unittest.TestCase):
    def test_realpath_spelling_difference_alone_is_not_a_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination_path = root / "destination"
            source.mkdir()
            original_realpath = os.path.realpath
            destination_key = os.path.normcase(os.path.abspath(destination_path))

            def alternate_spelling(path: os.PathLike[str] | str, **kwargs: Any) -> str:
                resolved = original_realpath(path, **kwargs)
                if os.path.normcase(os.path.abspath(path)) == destination_key:
                    return resolved.swapcase()
                return resolved

            with patch("aleph.paths.os.path.realpath", side_effect=alternate_spelling):
                problems = assert_install_paths_safe(source, destination_path)
            self.assertFalse(
                [
                    value
                    for value in problems
                    if value.code == "INSTALL_SOURCE_DEST" and "reparse" in value.message
                ],
                problems,
            )

    def test_copy_refuses_absent_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("unattested\n", encoding="utf-8")
            receipt = root / "refusal-receipt.json"
            result = install(
                source,
                root / "destination",
                mode="copy",
                receipt_path=receipt,
            )
            self.assertEqual(result["status"], "refused")
            self.assertFalse(result["ok"])
            self.assertTrue(receipt.is_file())

    def test_single_file_requires_attestation_and_rejects_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            verified = root / "verified"
            adapter = _verified_adapter_source(verified)
            installed = install_adapter_file(
                adapter,
                root / "installed.mdc",
                mode="copy",
                source_root=verified,
            )
            self.assertEqual(installed["status"], "copied", installed)

            unattested = verified / "adapters" / "generated" / "private.bin"
            unattested.write_bytes(b"not attested")
            refused = install_adapter_file(
                unattested,
                root / "private.bin",
                mode="copy",
                source_root=verified,
            )
            self.assertEqual(refused["status"], "refused")

            secret_root = root / "secret"
            secret_adapter = _verified_adapter_source(secret_root, secret=True)
            secret_result = install_adapter_file(
                secret_adapter,
                root / "secret.mdc",
                mode="copy",
                receipt_path=root / "secret-refusal-receipt.json",
                source_root=secret_root,
            )
            self.assertEqual(secret_result["status"], "refused")
            self.assertTrue((root / "secret-refusal-receipt.json").is_file())

    def test_single_file_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _verified_adapter_source(source)
            relative = adapter.relative_to(source).as_posix()
            real_is_symlink = Path.is_symlink

            def symlink_probe(path: Path) -> bool:
                if path.absolute() == adapter.absolute():
                    return True
                return real_is_symlink(path)

            with (
                patch(
                    "aleph.installer.verify_distribution_manifest",
                    return_value={"status": "verified", "files": [relative], "issues": []},
                ),
                patch("aleph.installer.Path.is_symlink", new=symlink_probe),
            ):
                result = install_adapter_file(
                    adapter,
                    root / "installed.mdc",
                    mode="copy",
                    source_root=source,
                )
            self.assertEqual(result["status"], "refused")

    def test_copy_refuses_destination_reparse_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _verified_adapter_source(source)
            real_destination = root / "real-destination"
            real_destination.mkdir()
            marker = real_destination / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            linked_destination = root / "linked-destination"
            if os.name == "nt":
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(linked_destination), str(real_destination)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                os.symlink(real_destination, linked_destination, target_is_directory=True)
                created = subprocess.CompletedProcess([], 0)
            if created.returncode != 0:
                self.skipTest(f"cannot create directory link: {created.stderr}")
            result = install(source, linked_destination, mode="copy", force=True)
            self.assertEqual(result["status"], "refused", result)
            self.assertTrue(marker.is_file())
            self.assertTrue(linked_destination.exists())

    def test_manifest_digest_uses_the_same_bounded_buffer_as_json_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _verified_adapter_source(source)
            destination = root / "destination"

            def mutate_after_parse(candidate: Path) -> dict[str, object]:
                expected = build_distribution_manifest(candidate)
                (source / MANIFEST_NAME).write_text("[]", encoding="utf-8")
                return expected

            with patch(
                "aleph.installer.build_distribution_manifest",
                side_effect=mutate_after_parse,
            ):
                result = install(source, destination, mode="copy")

            self.assertEqual(result["status"], "failed", result)
            self.assertFalse(destination.exists())

            (source / MANIFEST_NAME).write_text(
                json.dumps({"padding": "x" * 128}),
                encoding="utf-8",
            )
            with patch("aleph.installer.MANIFEST_MAX_BYTES", 64):
                oversized = verify_distribution_manifest(source)
            self.assertEqual(oversized["status"], "invalid")
            self.assertIn(
                "RESOURCE_LIMIT",
                {value["code"] for value in oversized["issues"]},
            )

    def test_plan_caps_symlink_install_assurance_at_limited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _verified_adapter_source(source)
            result = plan_install(source, root / "linked", "symlink")
            self.assertEqual(result["assurance_cap"], "limited")

    def test_copy_rechecks_destination_parent_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _verified_adapter_source(source)
            parent = root / "safe-parent"
            parent.mkdir()
            outside = root / "outside"
            outside.mkdir()
            destination_path = parent / "installed"

            def swap_parent(
                candidate_source: Path,
                candidate_destination: Path,
                mode: str,
            ) -> dict[str, object]:
                result = plan_install(candidate_source, candidate_destination, mode)
                parent.rmdir()
                created = _create_directory_link(parent, outside)
                if created.returncode != 0:
                    raise unittest.SkipTest(
                        f"cannot create directory link: {created.stderr}"
                    )
                return result

            try:
                with patch("aleph.installer.plan_install", side_effect=swap_parent):
                    result = install(source, destination_path, mode="copy")
                self.assertIn(result["status"], {"refused", "failed"}, result)
                self.assertFalse((outside / "installed").exists())
            finally:
                _remove_directory_link(parent)

    def test_adapter_rechecks_destination_parent_after_manifest_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _verified_adapter_source(source)
            parent = root / "safe-parent"
            parent.mkdir()
            outside = root / "outside"
            outside.mkdir()
            destination_path = parent / "adapter.mdc"
            original_verify = verify_distribution_manifest

            def swap_parent(candidate: Path, *, require: bool = True) -> dict[str, object]:
                result = original_verify(candidate, require=require)
                parent.rmdir()
                created = _create_directory_link(parent, outside)
                if created.returncode != 0:
                    raise unittest.SkipTest(
                        f"cannot create directory link: {created.stderr}"
                    )
                return result

            try:
                with patch(
                    "aleph.installer.verify_distribution_manifest",
                    side_effect=swap_parent,
                ):
                    result = install_adapter_file(
                        adapter,
                        destination_path,
                        mode="copy",
                        source_root=source,
                    )
                self.assertIn(result["status"], {"refused", "failed"}, result)
                self.assertFalse((outside / "adapter.mdc").exists())
            finally:
                _remove_directory_link(parent)

    def test_receipt_parent_is_rechecked_immediately_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _verified_adapter_source(source)
            destination_path = root / "destination"
            receipt_parent = root / "safe-receipts"
            receipt_parent.mkdir()
            outside = root / "outside"
            outside.mkdir()
            receipt = receipt_parent / "receipt.json"
            original_digest = installer_module._destination_digest
            calls = 0

            def swap_receipt_parent(candidate: Path, files: list[str]) -> str:
                nonlocal calls
                digest = original_digest(candidate, files)
                calls += 1
                if calls == 2:
                    receipt_parent.rmdir()
                    created = _create_directory_link(receipt_parent, outside)
                    if created.returncode != 0:
                        raise unittest.SkipTest(
                            f"cannot create directory link: {created.stderr}"
                        )
                return digest

            try:
                with patch(
                    "aleph.installer._destination_digest",
                    side_effect=swap_receipt_parent,
                ):
                    result = install(
                        source,
                        destination_path,
                        mode="copy",
                        receipt_path=receipt,
                    )
                self.assertEqual(result["status"], "failed", result)
                self.assertFalse(destination_path.exists())
                self.assertFalse((outside / "receipt.json").exists())
            finally:
                _remove_directory_link(receipt_parent)

    def test_rollback_refuses_to_traverse_a_swapped_destination_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            victim = outside / "installed" / "keep.txt"
            victim.parent.mkdir(parents=True)
            victim.write_text("keep\n", encoding="utf-8")
            linked_parent = root / "linked-parent"
            created = _create_directory_link(linked_parent, outside)
            if created.returncode != 0:
                self.skipTest(f"cannot create directory link: {created.stderr}")
            try:
                status = rollback_install_result(
                    {"backup": None},
                    linked_parent / "installed",
                )
                self.assertTrue(status.startswith("rollback-failed"), status)
                self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")
            finally:
                _remove_directory_link(linked_parent)


class PortableAdapterBundleTests(unittest.TestCase):
    def test_gemini_uses_native_skill_directories(self) -> None:
        self.assertEqual(TARGET_SPECS["gemini-cli"]["user_path"], "~/.gemini/skills/aleph-skill")
        self.assertEqual(TARGET_SPECS["gemini-cli"]["project_path"], ".gemini/skills/aleph-skill")
        project = Path("C:/portable-project")
        self.assertEqual(
            destination("gemini-cli", "project", project),
            project / ".gemini" / "skills" / "aleph-skill",
        )

    def test_generated_adapters_bind_stable_verified_core(self) -> None:
        instruction = generate_instruction_adapter("cursor", ROOT)
        profile = generate_external_profile("grok-build")
        self.assertIn(f"python {PORTABLE_CORE_PATH}/scripts/preflight.py --json", instruction)
        self.assertEqual(profile["core_path"], PORTABLE_CORE_PATH)

    def test_invalid_portable_mode_is_refused_without_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _verified_adapter_source(source)
            original_skill = (source / "SKILL.md").read_bytes()
            project = root / "project"
            destination_path = project / ".cursor" / "rules" / "aleph.mdc"
            result = install_portable_adapter(
                source,
                adapter,
                destination_path,
                project,
                target="cursor",
                mode="bogus",
                force=False,
                receipt_path=source / "SKILL.md",
            )
            self.assertEqual(result["status"], "refused", result)
            self.assertFalse(project.exists())
            self.assertEqual((source / "SKILL.md").read_bytes(), original_skill)

    def test_bundle_rechecks_receipt_parent_and_rolls_back_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _verified_adapter_source(source)
            project = root / "project"
            destination_path = project / ".cursor" / "rules" / "aleph.mdc"
            receipt_parent = project / ".receipts"
            receipt_parent.mkdir(parents=True)
            receipt = receipt_parent / "install.json"
            outside = root / "outside"
            outside.mkdir()
            original_install_adapter = installer_module.install_adapter_file

            def install_then_swap(*args: object, **kwargs: object) -> dict[str, object]:
                result = original_install_adapter(*args, **kwargs)  # type: ignore[arg-type]
                if result.get("status") == "copied":
                    receipt_parent.rmdir()
                    created = _create_directory_link(receipt_parent, outside)
                    if created.returncode != 0:
                        raise unittest.SkipTest(
                            f"cannot create directory link: {created.stderr}"
                        )
                return result

            try:
                with patch(
                    "install_adapters.install_adapter_file",
                    side_effect=install_then_swap,
                ):
                    result = install_portable_adapter(
                        source,
                        adapter,
                        destination_path,
                        project,
                        target="cursor",
                        mode="copy",
                        force=False,
                        receipt_path=receipt,
                    )
                self.assertEqual(result["status"], "failed", result)
                self.assertFalse(destination_path.exists())
                self.assertFalse((project / PORTABLE_CORE_PATH).exists())
                self.assertFalse((outside / "install.json").exists())
            finally:
                _remove_directory_link(receipt_parent)

    def test_project_adapter_installs_core_adapter_and_combined_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _verified_adapter_source(source)
            project = root / "project"
            destination_path = project / ".cursor" / "rules" / "aleph.mdc"
            receipt = project / ".aleph" / "install-receipt.json"
            result = install_portable_adapter(
                source,
                adapter,
                destination_path,
                project,
                target="cursor",
                mode="copy",
                force=False,
                receipt_path=receipt,
            )
            self.assertEqual(result["status"], "copied", result)
            self.assertTrue(destination_path.is_file())
            self.assertTrue((project / PORTABLE_CORE_PATH / "SKILL.md").is_file())
            self.assertTrue(receipt.is_file())
            receipt_data = _load_json(receipt)
            assert isinstance(receipt_data, dict)
            self.assertTrue(receipt_data["ok"])
            self.assertEqual(receipt_data["adapter"]["manifest"]["status"], "verified")

            replaced = install_portable_adapter(
                source,
                adapter,
                destination_path,
                project,
                target="cursor",
                mode="copy",
                force=True,
                receipt_path=receipt,
            )
            self.assertEqual(replaced["status"], "copied", replaced)
            self.assertEqual(
                replaced["backup_cleanup"],
                {"adapter": "backup-discarded", "core": "backup-discarded"},
            )
            self.assertFalse(
                list((project / PORTABLE_CORE_PATH).parent.glob(".aleph-skill.aleph-backup-*"))
            )
            self.assertFalse(list(destination_path.parent.glob(".aleph.mdc.aleph-backup-*")))

    def test_failed_bundle_writes_failure_receipt_without_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _verified_adapter_source(source)
            adapter.write_text("tampered after attestation\n", encoding="utf-8")
            project = root / "project"
            receipt = project / ".aleph" / "failed-install.json"
            result = install_portable_adapter(
                source,
                adapter,
                project / ".cursor" / "rules" / "aleph.mdc",
                project,
                target="cursor",
                mode="copy",
                force=False,
                receipt_path=receipt,
            )
            self.assertEqual(result["status"], "refused")
            self.assertFalse((project / PORTABLE_CORE_PATH).exists())
            self.assertTrue(receipt.is_file())
            receipt_data = _load_json(receipt)
            assert isinstance(receipt_data, dict)
            self.assertFalse(receipt_data["ok"])


class RunTraceContractTests(unittest.TestCase):
    def test_published_run_schema_requires_trace_contract(self) -> None:
        schema = _load_json(ROOT / "schemas" / "run-ledger.schema.json")
        assert isinstance(schema, dict)
        self.assertIn("trace_contract", schema["required"])
        trace_schema = schema["properties"]["trace_contract"]
        self.assertEqual(set(trace_schema["required"]), {"path", "sha256", "row_count"})

    def test_validator_binds_run_ledger_to_declared_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            manifest = _load_json(workspace / "simulation-manifest.json")
            assert isinstance(manifest, dict)
            artifact_paths = manifest["artifact_paths"]
            assert isinstance(artifact_paths, dict)
            artifact_paths["computational_model"] = "simulation-model.json"
            artifact_paths["run_ledger"] = "simulation-run.json"
            write_json_atomic(workspace / "simulation-manifest.json", manifest)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_simulation.py"),
                    "--workspace",
                    str(workspace),
                    "--ticks",
                    "182",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            run = _load_json(workspace / "simulation-run.json")
            assert isinstance(run, dict)
            self.assertEqual(set(run["trace_contract"]), {"path", "sha256", "row_count"})
            self.assertIn("trace_execution_binding", run)
            valid = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(valid.status, "pass", [item.to_dict() for item in valid.issues])

            trace_path = workspace / "propagation-trace.jsonl"
            trace_path.write_text(trace_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            tampered = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(tampered.status, "fail")
            self.assertIn("REPLAY_MISMATCH", {item.code for item in tampered.issues})


if __name__ == "__main__":
    unittest.main()
