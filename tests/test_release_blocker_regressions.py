from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    run_deterministic,
)
from aleph.execution_binding import build_trace_execution_binding  # noqa: E402
from aleph.installer import (  # noqa: E402
    MANIFEST_NAME,
    build_distribution_manifest,
    install,
    install_adapter_file,
    verify_distribution_manifest,
)
from aleph.io import canonical_hash, write_json_atomic  # noqa: E402
from aleph.trace_contract import validate_declared_trace  # noqa: E402
from aleph.validator import validate_numerical_artifacts  # noqa: E402
from install_adapters import install_portable_adapter  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def _rehash_trace(row: dict[str, object]) -> None:
    row.pop("hash_chain", None)
    row["hash_chain"] = canonical_hash({"previous_hash": None, "row": row})


def _run_script(workspace: Path, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--workspace", str(workspace), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _attested_source(root: Path) -> Path:
    adapter = root / "adapters" / "generated" / "cursor.md"
    adapter.parent.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: aleph-skill\ndescription: test\n---\n", encoding="utf-8")
    adapter.write_text("Use the verified Aleph core.\n", encoding="utf-8")
    write_json_atomic(root / MANIFEST_NAME, build_distribution_manifest(root))
    return adapter


class ExecutionContractRegressionTests(unittest.TestCase):
    def test_shared_trace_contract_loads_declared_artifacts_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest = json.loads((workspace / "simulation-manifest.json").read_text(encoding="utf-8"))
            nodes = json.loads((workspace / "nodes.json").read_text(encoding="utf-8"))
            edges = json.loads((workspace / "edges.json").read_text(encoding="utf-8"))

            trace_path, rows, issues = validate_declared_trace(workspace, manifest, nodes, edges)
            self.assertEqual(trace_path, workspace / "propagation-trace.jsonl")
            self.assertEqual(len(rows), 1)
            self.assertFalse(issues, [value.to_dict() for value in issues])

            manifest["artifact_paths"]["evidence_map"] = "missing-evidence.csv"
            _, rows, issues = validate_declared_trace(workspace, manifest, nodes, edges)
            self.assertEqual(len(rows), 1)
            self.assertIn("MISSING_ARTIFACT", {value.code for value in issues})

    def test_trace_execution_binding_rejects_each_unaddressable_trajectory(self) -> None:
        model = ComputationalModel(
            variables={
                "x": Variable(id="x", role="exogenous", baseline=1.0),
                "y": Variable(id="y", role="endogenous", baseline=0.0),
            },
            edges=[ModelEdge(id="edge:xy", source="x", target="y", sign=1, strength=1.0)],
        )
        config = EngineConfig()
        result = run_deterministic(model, config, ticks=2, run_id=0)
        manifest = {"temporal_frame": {"simulation_start": "2026-01-01"}}
        base_row: dict[str, object] = {
            "step": 1,
            "time": "2026-01-01",
            "edge_id": "edge:xy",
            "sample_refs": ["run:0"],
            "run_id": 0,
            "tick": 0,
            "source_tick": 0,
            "source_state": 1.0,
            "target_state": 1.0,
            "sampled_strength": 1.0,
            "input_effect": 1.0,
            "noise": 0.0,
        }

        binding, issues = build_trace_execution_binding(
            [base_row], model, config, ticks=2, result=result, manifest={}
        )
        self.assertIsNone(binding)
        self.assertEqual({value.code for value in issues}, {"TRACE_EXECUTION_BINDING"})

        binding, issues = build_trace_execution_binding(
            [base_row],
            model,
            EngineConfig(timestep=float("inf")),
            ticks=2,
            result=result,
            manifest=manifest,
        )
        self.assertIsNone(binding)
        self.assertEqual({value.code for value in issues}, {"TRACE_EXECUTION_BINDING"})

        mutations: tuple[tuple[str, dict[str, object]], ...] = (
            ("bad-reference", {"sample_refs": ["sample:not-a-run"]}),
            ("run-out-of-range", {"sample_refs": ["run:1"], "run_id": 1}),
            ("unknown-edge", {"edge_id": "edge:missing"}),
            ("invalid-time", {"time": "not-a-date"}),
            ("off-grid-time", {"time": "2026-01-01T12:00:00Z"}),
            ("outside-history", {"time": "2030-01-01"}),
            ("invalid-run", {}),
        )
        for label, change in mutations:
            with self.subTest(label=label):
                row = {**base_row, **change}
                candidate_result = {"ok": False} if label == "invalid-run" else result
                binding, issues = build_trace_execution_binding(
                    [row], model, config, ticks=2, result=candidate_result, manifest=manifest
                )
                self.assertIsNone(binding)
                self.assertTrue(issues)

        absent_model = ComputationalModel(
            variables=model.variables,
            edges=[
                ModelEdge(
                    id="edge:xy",
                    source="x",
                    target="y",
                    sign=1,
                    strength=1.0,
                    existence_prob=0.0,
                )
            ],
        )
        binding, issues = build_trace_execution_binding(
            [base_row],
            absent_model,
            EngineConfig(mode="monte_carlo", min_runs=1, max_runs=1, batch_size=1),
            ticks=2,
            result={"summary": {"n_runs": 1}},
            manifest=manifest,
        )
        self.assertIsNone(binding)
        self.assertTrue(issues)

    def test_initialized_workspace_is_a_coherent_valid_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "init_simulation_workspace.py"),
                    "--slug",
                    "coherent-draft",
                    "--change-point",
                    "Coherent draft change",
                    "--time",
                    "2026-07-01",
                    "--observation-cutoff",
                    "2026-07-01",
                    "--out-dir",
                    temporary,
                    "--force",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr + initialized.stdout)
            workspace = Path(temporary) / "coherent-draft"
            validated = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_simulation_artifacts.py"),
                    "--workspace",
                    str(workspace),
                    "--mode",
                    "draft",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validated.returncode, 0, validated.stderr + validated.stdout)
            self.assertEqual(json.loads(validated.stdout)["status"], "pass")

    def test_manifest_declared_custom_numerical_paths_work_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            (workspace / "graph").mkdir()
            (workspace / "contracts").mkdir()
            (workspace / "nodes.json").replace(workspace / "graph" / "nodes.json")
            (workspace / "edges.json").replace(workspace / "graph" / "edges.json")
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifact_paths = manifest["artifact_paths"]
            artifact_paths.update(
                {
                    "nodes": "graph/nodes.json",
                    "edges": "graph/edges.json",
                    "computational_model": "contracts/model.json",
                    "run_ledger": "contracts/run.json",
                    "replay_report": "contracts/replay.json",
                }
            )
            write_json_atomic(manifest_path, manifest)
            run = _run_script(workspace, "run_simulation.py", "--ticks", "182")
            replay = _run_script(workspace, "replay_simulation.py")
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            self.assertTrue((workspace / "contracts" / "model.json").is_file())
            self.assertTrue((workspace / "contracts" / "run.json").is_file())
            self.assertTrue((workspace / "contracts" / "replay.json").is_file())
            validated = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(validated.status, "pass", [value.to_dict() for value in validated.issues])

    def test_compile_model_output_cannot_alias_declared_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifact_paths"]["computational_model"] = "nodes.json"
            write_json_atomic(manifest_path, manifest)
            before = (workspace / "nodes.json").read_bytes()
            compiled = subprocess.run(
                [sys.executable, str(SCRIPTS / "compile_model.py"), "--workspace", str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(compiled.returncode, 0)
            self.assertEqual((workspace / "nodes.json").read_bytes(), before)

    def test_replay_refuses_wrong_types_modes_and_saved_result(self) -> None:
        mutations = (
            ("mode", lambda contract: contract.__setitem__("mode", "evil")),
            ("ticks", lambda contract: contract.__setitem__("ticks", "182")),
            ("result", lambda contract: contract.__setitem__("result", {})),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary) / "workspace"
                shutil.copytree(FIXTURE, workspace)
                run_path = workspace / "simulation-run.json"
                contract = json.loads(run_path.read_text(encoding="utf-8"))
                mutate(contract)
                contract["contract_hash"] = canonical_hash(
                    {key: value for key, value in contract.items() if key != "contract_hash"}
                )
                write_json_atomic(run_path, contract)
                replay = _run_script(workspace, "replay_simulation.py")
                self.assertNotEqual(replay.returncode, 0, replay.stdout)
                if label == "result":
                    report = json.loads(replay.stdout)
                    self.assertFalse(report["saved_result_ok"])
                    self.assertFalse(report["match"])

    def test_trace_formula_rehash_cannot_forge_engine_state_noise_or_run_ref(self) -> None:
        def forged_state(row: dict[str, object]) -> None:
            row.update(
                {
                    "source_state": 999.0,
                    "input_effect": 999.0,
                    "output_effect": -439.56000000000006,
                    "amplification": 999.0,
                }
            )

        def forged_noise(row: dict[str, object]) -> None:
            row.update(
                {
                    "noise": 100.0,
                    "output_effect": 98.13,
                    "amplification": 223.02272727272725,
                }
            )

        def huge_run_ref(row: dict[str, object]) -> None:
            row["sample_refs"] = ["run:" + "9" * 5000]

        for label, mutate in (
            ("engine-state", forged_state),
            ("noise", forged_noise),
            ("huge-run-ref", huge_run_ref),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary) / "workspace"
                shutil.copytree(FIXTURE, workspace)
                trace_path = workspace / "propagation-trace.jsonl"
                row = json.loads(trace_path.read_text(encoding="utf-8"))
                mutate(row)
                _rehash_trace(row)
                trace_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
                run = _run_script(workspace, "run_simulation.py", "--ticks", "182")
                self.assertNotEqual(run.returncode, 0, run.stdout)
                self.assertIn("TRACE_EXECUTION_BINDING", run.stdout)
                self.assertNotIn("Traceback", run.stderr + run.stdout)

    def test_workers_and_monte_carlo_lifecycles_replay_portably(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workers"
            shutil.copytree(FIXTURE, workspace)
            run = _run_script(workspace, "run_simulation.py", "--ticks", "182", "--workers", "4")
            replay = _run_script(workspace, "replay_simulation.py")
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            manifest = json.loads((workspace / "simulation-manifest.json").read_text(encoding="utf-8"))
            validated = validate_numerical_artifacts(workspace, manifest)
            self.assertEqual(validated.status, "pass", [value.to_dict() for value in validated.issues])

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "monte-carlo"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["simulation_mode"] = "monte_carlo"
            write_json_atomic(manifest_path, manifest)
            run = _run_script(
                workspace,
                "run_simulation.py",
                "--mode",
                "monte_carlo",
                "--ticks",
                "182",
                "--runs",
                "1",
                "--max-runs",
                "1",
                "--batch-size",
                "1",
            )
            replay = _run_script(workspace, "replay_simulation.py")
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)

    def test_set_intervention_end_tick_is_half_open(self) -> None:
        model = ComputationalModel(
            variables={
                "x": Variable(id="x", role="exogenous", baseline=1.0),
                "y": Variable(id="y", role="endogenous", baseline=0.0),
            },
            edges=[ModelEdge(id="edge:xy", source="x", target="y", sign=1, strength=1.0)],
            interventions=[
                {
                    "id": "intervention:set-y",
                    "target": "y",
                    "op": "set",
                    "value": 5.0,
                    "start_tick": 0,
                    "end_tick": 1,
                }
            ],
        )
        config = EngineConfig()
        result = run_deterministic(model, config, ticks=2, run_id=0)
        row = {
            "step": 1,
            "time": "2026-01-02",
            "edge_id": "edge:xy",
            "sample_refs": ["run:0"],
            "run_id": 0,
            "tick": 1,
            "source_tick": 1,
            "source_state": 1.0,
            "target_state": 1.0,
            "sampled_strength": 1.0,
            "input_effect": 1.0,
            "noise": 0.0,
        }
        binding, issues = build_trace_execution_binding(
            [row],
            model,
            config,
            ticks=2,
            result=result,
            manifest={"temporal_frame": {"simulation_start": "2026-01-01"}},
        )
        self.assertIsNotNone(binding, [value.to_dict() for value in issues])
        self.assertFalse(issues)

    def test_manifest_run_mode_and_branch_trace_are_independently_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURE, workspace)
            manifest_path = workspace / "simulation-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["simulation_mode"] = "monte_carlo"
            write_json_atomic(manifest_path, manifest)
            mode_result = validate_numerical_artifacts(workspace, manifest)
            self.assertIn("TRACK_MISMATCH", {value.code for value in mode_result.issues})

            manifest["simulation_mode"] = "deterministic"
            write_json_atomic(manifest_path, manifest)
            branches_path = workspace / "branch-ledger.json"
            branches = json.loads(branches_path.read_text(encoding="utf-8"))
            branches["branches"][0]["trace_hash"] = "f" * 64
            write_json_atomic(branches_path, branches)
            branch_result = validate_numerical_artifacts(workspace, manifest)
            self.assertIn("REPLAY_MISMATCH", {value.code for value in branch_result.issues})


class InstallerTransactionRegressionTests(unittest.TestCase):
    def test_receipt_alias_and_write_failure_never_leave_a_corrupt_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _attested_source(source)
            destination = root / "destination"
            alias = install(
                source,
                destination,
                mode="copy",
                receipt_path=destination / "SKILL.md",
            )
            self.assertEqual(alias["status"], "refused")
            self.assertFalse(destination.exists())

            with patch("aleph.installer.write_json_atomic", side_effect=OSError("disk full")):
                failed = install(
                    source,
                    destination,
                    mode="copy",
                    receipt_path=root / "receipt.json",
                )
            self.assertEqual(failed["status"], "failed")
            self.assertFalse(destination.exists())

    def test_successful_force_install_discards_discovery_visible_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            _attested_source(source)
            destination = root / "skills" / "aleph-skill"
            destination.mkdir(parents=True)
            (destination / "SKILL.md").write_text("old\n", encoding="utf-8")

            result = install(
                source,
                destination,
                mode="copy",
                force=True,
                receipt_path=root / "receipt.json",
            )

            self.assertEqual(result["status"], "copied")
            self.assertEqual(result["rollback_status"], "backup-discarded")
            self.assertIsNone(result["backup"])
            self.assertFalse(list(destination.parent.glob(".aleph-skill.aleph-backup-*")))

    def test_adapter_failure_before_backup_preserves_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _attested_source(source)
            destination = root / "adapter.mdc"
            destination.write_text("keep me\n", encoding="utf-8")
            with patch("aleph.installer.tempfile.NamedTemporaryFile", side_effect=OSError("disk full")):
                result = install_adapter_file(
                    adapter,
                    destination,
                    mode="copy",
                    force=True,
                    source_root=source,
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep me\n")

    def test_manifest_root_hidden_paths_and_portable_mode_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            adapter = _attested_source(source)
            hidden = source / "scripts" / ".private" / "customer.json"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("{}\n", encoding="utf-8")
            manifest = build_distribution_manifest(source)
            self.assertNotIn("scripts/.private/customer.json", {value["path"] for value in manifest["files"]})

            (source / MANIFEST_NAME).write_text("[]\n", encoding="utf-8")
            verified = verify_distribution_manifest(source)
            self.assertFalse(verified["ok"])
            self.assertIn(verified["status"], {"invalid", "stale"})

            source = root / "bundle-source"
            adapter = _attested_source(source)
            project = root / "project"
            bundle = install_portable_adapter(
                source,
                adapter,
                project / ".cursor" / "aleph.mdc",
                project,
                target="cursor",
                mode="bogus",
                force=False,
                receipt_path=None,
            )
            self.assertEqual(bundle["status"], "refused")
            self.assertFalse((project / ".aleph").exists())

    @unittest.skipUnless(os.name == "nt", "NTFS junction regression")
    def test_source_junction_is_refused_and_external_bytes_are_not_copied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("skill\n", encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            (outside / "payload.py").write_text("outside = True\n", encoding="utf-8")
            junction = source / "scripts"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                capture_output=True,
                text=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest(created.stderr)
            write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
            destination = root / "destination"
            result = install(source, destination, mode="copy")
            self.assertEqual(result["status"], "refused")
            self.assertFalse((destination / "scripts" / "payload.py").exists())


if __name__ == "__main__":
    unittest.main()
