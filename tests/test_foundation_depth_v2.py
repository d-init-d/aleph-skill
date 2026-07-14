from __future__ import annotations

import builtins
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.formula import (  # noqa: E402
    amplification_ratio,
    context_multiplier,
    expected_output_effect,
    formula_version,
    nearly_equal,
    replay_trace_row,
)
from aleph.installer import (  # noqa: E402
    MANIFEST_NAME,
    build_distribution_manifest,
    install,
    install_adapter_file,
    plan_install,
    scan_secret_like_files,
    source_symlink_issues,
    verify_distribution_manifest,
)
from aleph.io import (  # noqa: E402
    ResourceLimitError,
    canonical_hash,
    load_json_secure,
    load_jsonl_secure,
    load_workspace_artifact,
    sha256_bytes,
    sha256_file,
    sha256_text,
    stream_csv_rows,
    validate_workspace_budget,
    write_bytes_atomic,
    write_json_atomic,
    write_text_atomic,
)
from aleph.packs import (  # noqa: E402
    _validate_mechanisms,
    _validate_priors,
    _validate_variables,
    discover_pack_roots,
    evaluate_hindcast_case,
    refuse_uncalibrated_probability,
    validate_all_packs,
)
from aleph.paths import (  # noqa: E402
    assert_install_paths_safe,
    is_distribution_path,
    resolve_in_workspace,
    validate_relative_artifact_path,
)
from aleph.rng import (  # noqa: E402
    choose_index,
    counter_digest,
    normal01,
    run_hash,
    sample_triangular,
    sample_uniform,
    uniform01,
)
from aleph.schema import (  # noqa: E402
    ensure_list,
    has_id_prefix,
    is_bool,
    is_int,
    is_number,
    parse_duration_seconds,
    parse_time,
    refuse_string_bool,
    refuse_string_number,
    reject_unknown_fields,
    schema_is_current,
    schema_is_legacy,
    unit_interval,
)
from aleph.sensitivity import (  # noqa: E402
    conditional_contrast,
    morris_screening,
    one_at_a_time,
    sobol_saltelli_optional,
)


class RngAndSensitivityTests(unittest.TestCase):
    def test_counter_rng_is_deterministic_and_bounded(self) -> None:
        self.assertEqual(counter_digest(b"seed", 1), counter_digest(b"seed", 1))
        self.assertNotEqual(counter_digest("seed", 1), counter_digest("seed", 2))
        self.assertNotEqual(counter_digest("a\x1fb"), counter_digest("a", "b"))
        self.assertGreaterEqual(uniform01("seed", "x"), 0.0)
        self.assertLess(uniform01("seed", "x"), 1.0)
        self.assertTrue(math.isfinite(normal01("seed", "x")))
        self.assertAlmostEqual(sample_uniform("seed", 2.0, 4.0, "x"), 2.0 + 2.0 * uniform01("seed", "x"))
        for mode in (2.0, 3.0, 4.0):
            value = sample_triangular("seed", 2.0, mode, 4.0, mode)
            self.assertGreaterEqual(value, 2.0)
            self.assertLessEqual(value, 4.0)
        self.assertEqual(sample_triangular("seed", 3.0, 3.0, 3.0), 3.0)
        with self.assertRaises(ValueError):
            sample_triangular("seed", 0.0, 2.0, 1.0)
        self.assertEqual(choose_index("seed", [0.0, 0.0]), 0)
        self.assertIn(choose_index("seed", [0.2, 0.8], "weighted"), {0, 1})
        self.assertEqual(len(run_hash("seed", -1, b"payload")), 64)

    def test_sensitivity_methods_exercise_numeric_contracts(self) -> None:
        def evaluate(values: dict[str, float]) -> float:
            return 2.0 * values["a"] - values.get("b", 0.0)

        oat = one_at_a_time({"a": 2.0, "b": 0.0}, evaluate, delta=0.25)
        self.assertEqual(oat["method"], "OAT")
        self.assertGreater(oat["effects"]["a"]["abs_max"], 0.0)
        self.assertGreater(oat["effects"]["b"]["abs_max"], 0.0)
        morris = morris_screening(
            {"a": (0.0, 2.0), "b": (1.0, 1.0)},
            evaluate,
            seed="stable",
            trajectories=3,
            levels=1,
        )
        self.assertEqual(morris["trajectories"], 3)
        self.assertEqual(set(morris["summary"]), {"a", "b"})
        identity = morris_screening(
            {"x": (0.0, 1.0)}, lambda values: values["x"], seed="identity", trajectories=20, levels=6
        )
        self.assertAlmostEqual(identity["summary"]["x"]["mu"], 1.0)
        self.assertAlmostEqual(identity["summary"]["x"]["mu_star"], 1.0)
        contrast = conditional_contrast({"a": 1.0}, "a", [0.0, 2.0], evaluate)
        self.assertEqual(contrast["outcomes"], {"0.0": 0.0, "2.0": 4.0})

    def test_sobol_has_explicit_optional_dependency_degrade(self) -> None:
        real_import = builtins.__import__

        def without_numpy(name: str, *args: object, **kwargs: object) -> object:
            if name == "numpy":
                raise ImportError("deliberately absent")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=without_numpy):
            degraded = sobol_saltelli_optional({"x": (0.0, 1.0)}, lambda values: values["x"], n=8)
        self.assertFalse(degraded["available"])
        self.assertTrue(degraded["degraded"])
        try:
            import numpy  # noqa: F401
        except ImportError:
            return
        result = sobol_saltelli_optional(
            {"x": (0.0, 1.0)}, lambda values: values["x"], n=32, seed="stable"
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["seed_digest"], sobol_saltelli_optional(
            {"x": (0.0, 1.0)}, lambda values: values["x"], n=32, seed="stable"
        )["seed_digest"])


class FormulaAndSchemaTests(unittest.TestCase):
    def test_formula_replay_detects_semantic_tampering(self) -> None:
        issues = []
        mult = context_multiplier(
            [
                "not-an-object",
                {"context": "missing", "multiplier": "2", "rationale": ""},
                {"context": "ctx", "multiplier": -1, "rationale": "bad"},
                {"context": "ctx", "multiplier": 2, "rationale": "active", "active": "yes"},
                {"context": "ctx", "multiplier": 2, "rationale": "inactive", "active": False},
            ],
            {"ctx"},
            issues,
            "/mods",
        )
        self.assertEqual(mult, 1.0)
        self.assertGreaterEqual(len(issues), 5)
        self.assertEqual(context_multiplier([], {"ctx"}, [], "/mods"), 1.0)
        self.assertEqual(amplification_ratio(0.0, 0.0, 1.0), 0.0)
        self.assertTrue(math.isinf(amplification_ratio(1.0, 0.0, 1.0)))
        saturated = expected_output_effect(
            base_strength=10, sign=1, context_mult=1, transform="elasticity", saturation=1, noise=0.1
        )
        self.assertLess(saturated, 1.11)
        self.assertFalse(nearly_equal(float("inf"), float("inf")))
        self.assertTrue(formula_version())

        edge = {
            "id": "edge:test",
            "from": "a",
            "to": "b",
            "base_strength": 1.0,
            "sign": 1,
            "transform": "unsupported",
            "saturation": -1,
            "context_modifiers": "not-a-list",
        }
        row = {
            "edge_id": "edge:test",
            "from": "wrong",
            "to": "b",
            "input_effect": "1",
            "noise": "0",
            "output_effect": 99,
            "amplification": 99,
        }
        codes = {item.code for item in replay_trace_row(row, edge, {"a", "b"}, {"edge:test"}, pointer="/0")}
        self.assertTrue({"TRACE_ENDPOINT", "SCHEMA", "TRACE_FORMULA_MISMATCH"} <= codes)
        self.assertEqual(replay_trace_row(row, None, {"a"}, set(), pointer="/0")[0].code, "UNKNOWN_REF")

    def test_schema_helpers_refuse_implicit_coercion(self) -> None:
        issues = []
        self.assertIsNone(refuse_string_number("1", "/n", issues))
        self.assertIsNone(refuse_string_number(True, "/n", issues))
        self.assertIsNone(refuse_string_number(float("nan"), "/n", issues))
        self.assertIsNone(refuse_string_number(None, "/n", issues))
        self.assertEqual(refuse_string_number(1, "/n", issues), 1.0)
        self.assertTrue(refuse_string_bool(True, "/b", issues))
        self.assertIsNone(refuse_string_bool("true", "/b", issues))
        self.assertIsNone(refuse_string_bool(1, "/b", issues))
        self.assertIsNone(unit_interval("0.5", "/u", issues))
        self.assertEqual(unit_interval(2.0, "/u", issues), 2.0)
        reject_unknown_fields({"known": 1, "extra": 2}, {"known"}, "/x", issues)
        self.assertFalse(is_number(True))
        self.assertFalse(is_number(float("inf")))
        self.assertTrue(is_number(1.5))
        self.assertTrue(is_bool(False))
        self.assertTrue(is_int(1))
        self.assertFalse(is_int(True))
        self.assertIsNotNone(parse_time("2026"))
        self.assertIsNotNone(parse_time("2026-01-01T00:00:00Z"))
        self.assertIsNone(parse_time("bad"))
        self.assertEqual(parse_duration_seconds("P1Y1M1W1DT1H1M1.5S"), 34822861.5)
        self.assertEqual(parse_duration_seconds("P0D"), 0.0)
        self.assertIsNone(parse_duration_seconds("P"))
        self.assertIsNone(parse_duration_seconds(1))
        self.assertTrue(has_id_prefix("entity:x", "node"))
        self.assertEqual(ensure_list(None), [])
        self.assertEqual(ensure_list("x"), ["x"])
        self.assertTrue(schema_is_current("2.0.0"))
        self.assertTrue(schema_is_legacy("1.2.0"))
        self.assertGreater(len(issues), 5)


class IoAndPathSecurityTests(unittest.TestCase):
    def test_secure_loaders_cover_resource_and_format_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.json"
            self.assertEqual(load_json_secure(missing)[1][0].code, "INVALID_ARTIFACT")
            oversized = root / "large.json"
            oversized.write_text("{}", encoding="utf-8")
            self.assertEqual(load_json_secure(oversized, max_bytes=1)[1][0].code, "RESOURCE_LIMIT")
            invalid = root / "invalid.json"
            invalid.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_json_secure(invalid)[1][0].code, "INVALID_ARTIFACT")
            nonfinite = root / "nonfinite.json"
            nonfinite.write_text('{"x": NaN}', encoding="utf-8")
            self.assertEqual(load_json_secure(nonfinite)[1][0].code, "INVALID_ARTIFACT")
            deep = root / "deep.json"
            deep.write_text(json.dumps({"a": {"b": 1}}), encoding="utf-8")
            self.assertEqual(load_json_secure(deep, max_depth=1)[1][0].code, "RESOURCE_LIMIT")

            lines = root / "rows.jsonl"
            lines.write_text('\n{"ok": 1}\nnot-json\n[1]\n{"huge":"123456"}\n', encoding="utf-8")
            rows, issues = load_jsonl_secure(lines, max_row_bytes=15)
            self.assertEqual(rows, [{"ok": 1}])
            self.assertGreaterEqual(len(issues), 3)
            _, total_issues = load_jsonl_secure(lines, max_file_bytes=2)
            self.assertEqual(total_issues[0].code, "RESOURCE_LIMIT")

            csv_path = root / "rows.csv"
            csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
            self.assertEqual(stream_csv_rows(csv_path)[0][0]["a"], "1")
            self.assertEqual(stream_csv_rows(csv_path, max_file_bytes=1)[1][0].code, "RESOURCE_LIMIT")
            self.assertEqual(stream_csv_rows(root / "none.csv")[1][0].code, "INVALID_ARTIFACT")

            text_path = root / "out.txt"
            bytes_path = root / "out.bin"
            json_path = root / "out.json"
            write_text_atomic(text_path, "hello")
            write_bytes_atomic(bytes_path, b"hello")
            write_json_atomic(json_path, {"hello": "world"})
            self.assertEqual(sha256_file(text_path), sha256_text("hello"))
            self.assertEqual(sha256_bytes(b"hello"), sha256_text("hello"))
            with self.assertRaises(ResourceLimitError):
                sha256_file(text_path, max_bytes=1)
            self.assertEqual(load_workspace_artifact(root, "out.json", kind="json")[1], {"hello": "world"})
            self.assertEqual(load_workspace_artifact(root, "rows.jsonl", kind="jsonl")[1][0], {"ok": 1})
            self.assertEqual(load_workspace_artifact(root, "rows.csv", kind="csv")[1][0]["b"], "2")
            self.assertEqual(load_workspace_artifact(root, "out.txt", kind="text")[1], "hello")
            self.assertEqual(load_workspace_artifact(root, "out.txt", kind="unknown")[2][0].code, "TYPE")
            self.assertIsNone(load_workspace_artifact(root, "../escape", kind="json")[0])
            total, budget_issues = validate_workspace_budget(root, max_bytes=1)
            self.assertGreater(total, 1)
            self.assertEqual(budget_issues[0].code, "RESOURCE_LIMIT")
            self.assertEqual(len(canonical_hash({"b": 1, "a": 2})), 64)

    def test_path_contracts_cover_absolute_traversal_and_distribution_rules(self) -> None:
        cases = ["", "../x", "/x", "C:/x", "\\\\server\\share", "~/.secret", "a\x00b"]
        self.assertTrue(all(validate_relative_artifact_path(value) for value in cases))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "dir"
            directory.mkdir()
            self.assertEqual(resolve_in_workspace(root, "missing", must_exist=True)[1][0].code, "MISSING_ARTIFACT")
            self.assertEqual(resolve_in_workspace(root, "dir", require_file=True)[1][0].code, "PATH_NOT_FILE")
            self.assertFalse(resolve_in_workspace(root, "dir", require_file=False)[1])
            self.assertTrue(assert_install_paths_safe(root, root))
            self.assertTrue(assert_install_paths_safe(root, directory))
            self.assertTrue(assert_install_paths_safe(directory, root))
        self.assertTrue(is_distribution_path("scripts/run.py"))
        self.assertTrue(is_distribution_path("README.md"))
        self.assertFalse(is_distribution_path(".env"))
        self.assertFalse(is_distribution_path("outside/data.bin"))
        self.assertFalse(is_distribution_path("scripts/cache.pyc"))
        self.assertFalse(is_distribution_path("coverage.json"))
        self.assertFalse(is_distribution_path("scripts/aleph_skill.egg-info/SOURCES.txt"))

    def test_distribution_manifest_excludes_generated_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts" / "demo.egg-info").mkdir(parents=True)
            (root / "scripts" / "demo.egg-info" / "SOURCES.txt").write_text("generated", encoding="utf-8")
            (root / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "coverage.json").write_text("{}\n", encoding="utf-8")
            files = {entry["path"] for entry in build_distribution_manifest(root)["files"]}
            self.assertEqual(files, {"scripts/run.py"})


class PackAndInstallerGateTests(unittest.TestCase):
    def test_pack_discovery_and_semantic_primitives_fail_closed(self) -> None:
        issues = []
        self.assertEqual(_validate_variables(None, "demo", issues), set())
        variables = {
            "pack": "demo",
            "variables": [
                "bad",
                {"id": "wrong", "role": "bad", "datatype": "bad", "unit": "", "bounds": [2, 1], "baseline": 3},
                {"id": "demo:x", "role": "endogenous", "datatype": "continuous", "unit": "x", "bounds": [0, 1], "baseline": 0.5},
                {"id": "demo:x", "role": "endogenous", "datatype": "continuous", "unit": "x", "bounds": [0, 1], "baseline": 0.5},
            ],
        }
        self.assertEqual(_validate_variables(variables, "demo", issues), {"demo:x"})
        _validate_mechanisms(None, "demo", issues)
        _validate_mechanisms({"templates": ["bad", {"id": "x", "relation": "increases", "sign": -1, "description": "short"}]}, "demo", issues)
        _validate_priors(None, "demo", issues)
        _validate_priors({"parameters": ["bad", {"id": "prior:demo:x", "distribution": "uniform", "min": 2, "max": 1}]}, "demo", issues)
        self.assertGreater(len(issues), 10)
        self.assertIsNotNone(refuse_uncalibrated_probability("experimental"))
        self.assertIsNone(refuse_uncalibrated_probability("calibrated"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(json.dumps({"domain_packs": [str(root / "one"), str(root / "one")]}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"ALEPH_DOMAIN_PACKS": str(root / "two")}):
                roots = discover_pack_roots(config_path=config, skill_root=ROOT)
            self.assertIn((root / "one").resolve(), roots)
            self.assertIn((root / "two").resolve(), roots)
            config.write_text("broken", encoding="utf-8")
            self.assertIsInstance(discover_pack_roots(config_path=config), list)
        all_packs = validate_all_packs(ROOT)
        self.assertEqual(len(all_packs["packs"]), 7)

    def test_hindcast_enforces_cutoff_policy_and_model_quality(self) -> None:
        case = json.loads((ROOT / "packs" / "economics" / "hindcast" / "case-001.json").read_text(encoding="utf-8"))
        valid = evaluate_hindcast_case(
            case,
            policy={"precommitted": True, "commitment_version": "aleph-hindcast-commitment-v2"},
        )
        self.assertTrue(valid["ok"], valid)
        leaked = json.loads(json.dumps(case))
        leaked["cutoff"] = "bad"
        leaked["evidence_snapshot_hash"] = "bad"
        leaked["evidence"] = ["bad", {"id": "future", "available_at": "2099-01-01"}, {"id": "date", "available_at": "bad"}]
        failed = evaluate_hindcast_case(leaked, policy={"precommitted": False})
        self.assertFalse(failed["ok"])
        empty = evaluate_hindcast_case({"cutoff": "2020-01-01", "evidence_snapshot_hash": "1" * 64})
        self.assertFalse(empty["ok"])

    def test_installer_modes_manifest_and_adapter_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            (source / "scripts").mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: aleph-skill\n---\n", encoding="utf-8")
            (source / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
            self.assertEqual(verify_distribution_manifest(source)["status"], "absent")
            with self.assertRaises(ValueError):
                plan_install(source, destination, "invalid")
            self.assertEqual(install(source, destination, mode="dry-run")["status"], "dry-run")
            self.assertEqual(install(source, destination, mode="symlink")["status"], "refused")
            write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
            self.assertTrue(verify_distribution_manifest(source)["ok"])
            copied = install(source, destination, mode="copy")
            self.assertEqual(copied["status"], "copied")
            self.assertEqual(install(source, destination, mode="copy")["status"], "refused")
            replaced = install(source, destination, mode="copy", force=True, receipt_path=root / "receipt.json")
            self.assertEqual(replaced["status"], "copied")
            self.assertTrue((root / "receipt.json").is_file())

            adapter = source / "SKILL.md"
            target = root / "adapter.md"
            self.assertEqual(install_adapter_file(adapter, target, mode="symlink")["status"], "refused")
            self.assertEqual(install_adapter_file(adapter, target, mode="dry-run")["status"], "dry-run")
            self.assertEqual(install_adapter_file(adapter, target, mode="copy")["status"], "copied")
            self.assertEqual(install_adapter_file(adapter, target, mode="copy")["status"], "refused")
            target_dir = root / "adapter-dir"
            target_dir.mkdir()
            self.assertEqual(install_adapter_file(adapter, target_dir, mode="copy")["status"], "refused")

            (source / "SKILL.md").write_text("tampered", encoding="utf-8")
            stale = verify_distribution_manifest(source)
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(install(source, root / "other", mode="copy")["status"], "refused")
            self.assertEqual(source_symlink_issues(source), [])

    def test_installer_blocks_symlink_exposure_large_secrets_and_predictable_temp_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: aleph-skill\n---\n", encoding="utf-8")
            large = source / "scripts" / "large.json"
            large.write_bytes(b"x" * (2 * 1024 * 1024 + 1) + b"\napi_key=ABCDEFGHIJKLMNOPQRSTUV\n")
            write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
            findings = scan_secret_like_files(source)
            self.assertIn("scripts/large.json", {item["path"] for item in findings})
            self.assertEqual(install(source, root / "copy", mode="copy")["status"], "refused")

            large.unlink()
            (source / ".env").write_text("API_KEY=ABCDEFGHIJKLMNOPQRSTUV\n", encoding="utf-8")
            write_json_atomic(source / MANIFEST_NAME, build_distribution_manifest(source))
            symlink_plan = plan_install(source, root / "linked", "symlink")
            self.assertFalse(symlink_plan["ok"])
            self.assertEqual(install(source, root / "linked", mode="symlink")["status"], "refused")

            adapter_target = root / "adapter.md"
            predictable = adapter_target.with_name(f".{adapter_target.name}.aleph-tmp-{os.getpid()}")
            predictable.write_text("attacker-sentinel", encoding="utf-8")
            adapter_result = install_adapter_file(source / "SKILL.md", adapter_target, mode="copy")
            self.assertEqual(adapter_result["status"], "copied")
            self.assertEqual(predictable.read_text(encoding="utf-8"), "attacker-sentinel")


if __name__ == "__main__":
    unittest.main()
