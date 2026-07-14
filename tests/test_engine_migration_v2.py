from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    _sample_lag,
    compile_model,
    run_deterministic,
    run_monte_carlo,
)
from aleph.migrate import migrate_workspace, plan_migration  # noqa: E402
from aleph.packs import validate_pack  # noqa: E402


class EngineV2RegressionTests(unittest.TestCase):
    def test_compile_uses_observed_baseline_not_state_after(self) -> None:
        model = compile_model(
            [
                {
                    "id": "x",
                    "role": "endogenous",
                    "baseline": 1.0,
                    "state_before": {"value": 2.0},
                    "state_after": {"value": 999.0},
                }
            ],
            [],
        )
        self.assertEqual(model.variables["x"].baseline, 1.0)

    def test_do_set_blocks_incoming_edges_until_release(self) -> None:
        model = ComputationalModel(
            variables={
                "a": Variable(id="a", role="exogenous", baseline=1.0),
                "b": Variable(id="b", role="endogenous", baseline=0.0),
            },
            edges=[ModelEdge(id="ab", source="a", target="b", sign=1, strength=1.0)],
            interventions=[{"id": "do-b", "target": "b", "op": "set", "value": 10.0, "start_tick": 0, "end_tick": 1}],
        )
        result = run_deterministic(model, EngineConfig(), ticks=2)
        self.assertEqual(result["history"][0]["b"], 10.0)
        self.assertEqual(result["history"][1]["b"], 1.0)

    def test_lag_delivers_emission_snapshot(self) -> None:
        model = ComputationalModel(
            variables={
                "a": Variable(id="a", role="exogenous", baseline=1.0),
                "b": Variable(id="b", role="endogenous", baseline=0.0),
            },
            edges=[ModelEdge(id="ab", source="a", target="b", sign=1, strength=1.0, lag_ticks=1)],
            interventions=[{"id": "change-a", "target": "a", "op": "set", "value": 2.0, "start_tick": 1}],
        )
        result = run_deterministic(model, EngineConfig(), ticks=2)
        self.assertEqual(result["history"][1]["b"], 1.0)

    def test_stable_self_loop_solved_once(self) -> None:
        model = ComputationalModel(
            variables={"x": Variable(id="x", role="endogenous", baseline=1.0)},
            edges=[ModelEdge(id="xx", source="x", target="x", sign=1, strength=0.5)],
        )
        result = run_deterministic(model, EngineConfig(jacobi_max_iter=500), ticks=1)
        self.assertTrue(result["ok"], result["issues"])
        self.assertAlmostEqual(result["payload"]["final_state"]["x"], 2.0, places=5)

    def test_divergent_self_loop_fails(self) -> None:
        model = ComputationalModel(
            variables={"x": Variable(id="x", role="endogenous", baseline=1.0)},
            edges=[ModelEdge(id="xx", source="x", target="x", sign=1, strength=2.0)],
        )
        result = run_deterministic(model, EngineConfig(jacobi_max_iter=10), ticks=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 4)

    def test_run_hash_is_bound_to_model(self) -> None:
        def candidate(edge_id: str) -> ComputationalModel:
            return ComputationalModel(
                variables={
                    "a": Variable(id="a", role="exogenous", baseline=1.0),
                    "b": Variable(id="b", role="endogenous", baseline=0.0),
                },
                edges=[ModelEdge(id=edge_id, source="a", target="b", sign=1, strength=0.0)],
            )

        left = run_deterministic(candidate("edge-left"), EngineConfig(), ticks=1)
        right = run_deterministic(candidate("edge-right"), EngineConfig(), ticks=1)
        self.assertNotEqual(left["run_hash"], right["run_hash"])

    def test_mc_invalid_fraction_is_hard_gate_and_invalid_samples_are_filtered(self) -> None:
        model = ComputationalModel(
            variables={
                "a": Variable(id="a", role="exogenous", baseline=1.0),
                "b": Variable(id="b", role="endogenous", baseline=0.0),
            },
            edges=[ModelEdge(id="ab", source="a", target="b", sign=1, strength=1.0)],
        )
        config = EngineConfig(mode="monte_carlo", min_runs=20, max_runs=20, batch_size=10, max_events=0)
        result = run_monte_carlo(model, config, ticks=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["summary"]["invalid_runs"], 20)
        self.assertEqual(result["summary"]["branches"], [])
        self.assertAlmostEqual(result["summary"]["unresolved_mass"], 1.0)

    def test_level_equations_do_not_accumulate_static_effects_across_ticks(self) -> None:
        model = ComputationalModel(
            variables={
                "a": Variable(id="a", role="exogenous", baseline=1.0),
                "b": Variable(id="b", role="endogenous", baseline=0.0),
            },
            edges=[ModelEdge(id="ab", source="a", target="b", sign=1, strength=0.5)],
        )
        result = run_deterministic(model, EngineConfig(), ticks=5)
        self.assertEqual([row["b"] for row in result["history"]], [0.5] * 5)

    def test_stable_self_loop_has_same_equilibrium_each_tick(self) -> None:
        model = ComputationalModel(
            variables={"x": Variable(id="x", role="endogenous", baseline=1.0)},
            edges=[ModelEdge(id="xx", source="x", target="x", sign=1, strength=0.5)],
        )
        result = run_deterministic(model, EngineConfig(jacobi_max_iter=500), ticks=3)
        self.assertTrue(result["ok"], result["issues"])
        for row in result["history"]:
            self.assertAlmostEqual(row["x"], 2.0, places=5)

    def test_compile_rejects_invalid_sign_and_parses_full_iso_lag(self) -> None:
        nodes = [{"id": "a", "baseline": 1.0}, {"id": "b", "baseline": 0.0}]
        with self.assertRaises(ValueError):
            compile_model(nodes, [{"id": "e", "from": "a", "to": "b", "sign": 0}])
        model = compile_model(
            nodes,
            [{"id": "e", "from": "a", "to": "b", "sign": 1, "lag_ticks": "P1DT12H"}],
        )
        self.assertEqual(model.edges[0].lag_ticks, 2)
        self.assertEqual(_sample_lag(model.edges[0], EngineConfig(timestep=2.0), 0), 1)

    def test_compiled_context_multiplier_and_saturation_affect_engine_output(self) -> None:
        model = compile_model(
            [{"id": "a", "baseline": 1.0}, {"id": "b", "baseline": 0.0}],
            [
                {
                    "id": "e",
                    "from": "a",
                    "to": "b",
                    "sign": 1,
                    "base_strength": 1.0,
                    "context_modifiers": [
                        {"context": "context:x", "multiplier": 2.0, "active": True},
                        {"context": "context:y", "multiplier": 100.0, "active": False},
                    ],
                    "saturation": 1.0,
                }
            ],
        )
        result = run_deterministic(model, EngineConfig(), ticks=1)
        self.assertAlmostEqual(result["history"][0]["b"], 0.9640275801)

    def test_lag_distribution_is_preserved_and_sampled_in_monte_carlo(self) -> None:
        model = compile_model(
            [{"id": "a", "baseline": 1.0}, {"id": "b", "baseline": 0.0}],
            [
                {
                    "id": "e",
                    "from": "a",
                    "to": "b",
                    "sign": 1,
                    "lag_distribution": {
                        "type": "triangular",
                        "min": "P1D",
                        "mode": "P3D",
                        "max": "P5D",
                    },
                }
            ],
        )
        edge = model.edges[0]
        self.assertEqual(edge.lag_ticks, 3)
        self.assertEqual(edge.lag_distribution["mode"], 3)
        config = EngineConfig(mode="monte_carlo", seed="lag-sampling")
        samples = {_sample_lag(edge, config, run_id) for run_id in range(32)}
        self.assertGreater(len(samples), 1)
        self.assertTrue(samples <= {1, 2, 3, 4, 5})


class MigrationV2RegressionTests(unittest.TestCase):
    def test_destination_ancestor_is_refused_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "parent"
            source = parent / "source"
            shutil.copytree(FIXTURES / "schema-1.2-valid", source)
            result = migrate_workspace(source, parent)
            self.assertFalse(result["ok"])
            self.assertTrue(source.is_dir())
            self.assertTrue((source / "simulation-manifest.json").is_file())

    def test_existing_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "existing"
            destination.mkdir()
            sentinel = destination / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            result = migrate_workspace(FIXTURES / "schema-1.2-valid", destination)
            self.assertFalse(result["ok"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_source_digest_covers_non_manifest_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(FIXTURES / "schema-1.2-valid", source)
            first = plan_migration(source)["source_digest"]
            nodes = json.loads((source / "nodes.json").read_text(encoding="utf-8"))
            nodes[0]["name"] = "changed"
            (source / "nodes.json").write_text(json.dumps(nodes), encoding="utf-8")
            second = plan_migration(source)["source_digest"]
            self.assertNotEqual(first, second)

    def test_in_place_requires_external_backup_and_preserves_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            backup_root = root / "backups"
            shutil.copytree(FIXTURES / "schema-1.2-valid", source)
            legacy_manifest_path = source / "simulation-manifest.json"
            legacy_manifest = json.loads(legacy_manifest_path.read_text(encoding="utf-8"))
            legacy_manifest["assumptions"] = [
                "A preserved legacy premise.",
                {"id": "assumption:kept", "statement": "A structured legacy premise."},
            ]
            legacy_manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")
            result = migrate_workspace(source, in_place=True, backup_dir=backup_root)
            self.assertTrue(result["ok"], result)
            migrated = json.loads((source / "simulation-manifest.json").read_text(encoding="utf-8"))
            original = json.loads((backup_root / "source" / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], "2.0.0")
            self.assertEqual(original["schema_version"], "1.2.0")
            self.assertEqual(
                migrated["migration"]["source_schema_version"],
                "1.2.0",
            )
            self.assertEqual(migrated["migration"]["target_schema_version"], "2.0.0")
            assumption_ref = migrated["change_point"]["assumption_ref"]
            self.assertIn(assumption_ref, {item["id"] for item in migrated["assumptions"]})
            migrated_nodes = json.loads((source / "nodes.json").read_text(encoding="utf-8"))
            self.assertTrue(all("probability" not in node for node in migrated_nodes))
            self.assertEqual(
                {item["statement"] for item in migrated["assumptions"]},
                {
                    "A preserved legacy premise.",
                    "A structured legacy premise.",
                    "Migrated change-point assumption; review before finalization.",
                },
            )
            migration_report = json.loads((source / "migration-report.json").read_text(encoding="utf-8"))
            unresolved_codes = {item.get("code") for item in migration_report["unresolved_fields"]}
            self.assertIn("TRACK_LEDGER", unresolved_codes)
            self.assertEqual(migration_report["post_migration_validation_status"], "fail")
            self.assertEqual(
                migrated["migration"]["unresolved_fields"],
                migration_report["unresolved_fields"],
            )


class PackV2RegressionTests(unittest.TestCase):
    def test_invalid_json_cannot_be_validated_by_directory_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "economics"
            shutil.copytree(ROOT / "packs" / "economics", pack)
            (pack / "variables.json").write_text("{broken", encoding="utf-8")
            result = validate_pack(pack)
            self.assertFalse(result["ok"])

    def test_fake_calibrated_claim_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "economics"
            shutil.copytree(ROOT / "packs" / "economics", pack)
            manifest_path = pack / "pack-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["maturity"] = "calibrated"
            manifest["calibration_cases"] = 999
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = validate_pack(pack)
            self.assertFalse(result["ok"])
            self.assertFalse(result["can_emit_probability"])


class RunContractRegressionTests(unittest.TestCase):
    def test_saved_run_replays_and_detects_model_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(FIXTURES / "schema-2.0-valid", workspace)
            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_simulation.py"), "--workspace", str(workspace), "--ticks", "182"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            replay = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            nodes_path = workspace / "nodes.json"
            nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
            nodes[0]["baseline"] = 9.0
            nodes_path.write_text(json.dumps(nodes), encoding="utf-8")
            mismatch = subprocess.run(
                [sys.executable, str(SCRIPTS / "replay_simulation.py"), "--workspace", str(workspace)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(mismatch.returncode, 0)


if __name__ == "__main__":
    unittest.main()
