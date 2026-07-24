from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aleph import FORMULA_VERSION, LEGACY_FORMULA_VERSION
from aleph.engine import compile_model, model_hash, model_payload
from aleph.formula import (
    amplification_ratio,
    context_multiplier,
    evaluate_output_effect,
    expected_output_effect,
    nearly_equal,
    replay_trace_row,
)
from aleph.io import canonical_hash
from aleph.sensitivity import sobol_saltelli_optional
from aleph.validator import _validate_scalar_spec, validate_trace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


class FormulaV21ContractTests(unittest.TestCase):
    def test_scalar_distribution_validator_covers_every_shape(self) -> None:
        cases = [
            2.0,
            True,
            {"distribution": "fixed", "type": "uniform", "value": 1.0},
            {"distribution": "unsupported"},
            {"distribution": "fixed"},
            {"distribution": "fixed", "value": "invalid"},
            {"distribution": "fixed", "value": 1.5},
            {"type": "fixed", "value": 1.5},
            {"distribution": "uniform", "min": 0.0, "max": 2.0},
            {"distribution": "uniform", "min": 1.0, "max": 1.0},
            {"distribution": "triangular", "min": 0.0, "mode": 1.0, "max": 2.0},
            {"distribution": "triangular", "min": 0.0, "mode": 3.0, "max": 2.0},
            {"distribution": "triangular", "min": 1.0, "mode": 1.0, "max": 1.0},
            {"distribution": "normal", "mean": 0.0, "sd": 1.0},
            {"distribution": "normal", "mean": 0.0, "sd": 0.0},
            {"distribution": "fixed", "value": 1.0, "unexpected": True},
        ]
        results = []
        all_codes: set[str] = set()
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                issues = []
                results.append(_validate_scalar_spec(value, f"/value/{index}", issues))
                all_codes.update(item.code for item in issues)

        self.assertEqual(results[0], (2.0, (2.0, 2.0)))
        self.assertEqual(results[6], (1.5, (1.5, 1.5)))
        self.assertEqual(results[8], (1.0, (0.0, 2.0)))
        self.assertEqual(results[10], (1.0, (0.0, 2.0)))
        self.assertEqual(results[13], (0.0, None))
        self.assertTrue({"TYPE", "SCHEMA", "ENUM", "MISSING_FIELD", "RANGE", "UNKNOWN_FIELD"} <= all_codes)

    def test_formula_defensive_paths_fail_closed(self) -> None:
        issues = []
        self.assertEqual(context_multiplier([], set(), issues, "/contexts"), 1.0)
        modifiers = [
            "invalid",
            {"context": "context:missing", "multiplier": 1.0},
            {"context": "context:ok", "multiplier": "invalid"},
            {"context": "context:ok", "multiplier": -1.0},
            {"context": "context:ok", "multiplier": 2.0, "active": "yes"},
            {
                "context": "context:ok",
                "multiplier": 3.0,
                "rationale": "Inactive test modifier",
                "active": False,
            },
        ]
        self.assertEqual(
            context_multiplier(modifiers, {"context:ok"}, issues, "/contexts"),
            1.0,
        )
        self.assertTrue({"CONTEXT", "TYPE", "CONTEXT_MISSING", "MULTIPLIER"} <= {item.code for item in issues})

        self.assertLess(
            expected_output_effect(
                base_strength=1.0,
                sign=1,
                context_mult=1.0,
                input_effect=-100.0,
                transform="logistic",
            ),
            0.0,
        )
        invalid_calls = [
            {"formula_version": "unsupported"},
            {"transform": "logistic", "transform_parameters": {"midpoint": math.nan}},
            {"transform": "logistic", "transform_parameters": {"steepness": 0.0}},
            {"transform": "threshold", "transform_parameters": {"threshold": math.nan}},
            {"transform": "threshold", "transform_parameters": {"mode": "deadband", "deadband": -1.0}},
            {"transform": "threshold", "transform_parameters": {"mode": "hysteresis", "theta_on": 0.0, "theta_off": 1.0}},
            {"transform": "threshold", "transform_parameters": {"mode": "unsupported"}},
            {"transform": "unsupported"},
        ]
        for kwargs in invalid_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                evaluate_output_effect(
                    base_strength=1.0,
                    sign=1,
                    context_mult=1.0,
                    **kwargs,
                )
        self.assertEqual(
            expected_output_effect(
                base_strength=1.0,
                sign=1,
                context_mult=1.0,
                input_effect=-2.0,
                transform="threshold",
                transform_parameters={"mode": "below", "threshold": 0.0},
            ),
            2.0,
        )
        self.assertEqual(
            expected_output_effect(
                base_strength=1.0,
                sign=1,
                context_mult=1.0,
                input_effect=0.0,
                transform="threshold",
                transform_parameters={"mode": "deadband", "threshold": 0.0, "deadband": 1.0},
            ),
            0.0,
        )
        self.assertEqual(amplification_ratio(0.0, 0.0, 1.0, formula_version=LEGACY_FORMULA_VERSION), 0.0)
        self.assertTrue(math.isinf(amplification_ratio(1.0, 0.0, 1.0, formula_version=LEGACY_FORMULA_VERSION)))
        self.assertEqual(amplification_ratio(0.0, 0.0, 1.0), 0.0)
        self.assertTrue(math.isinf(amplification_ratio(1.0, 0.0, 1.0)))
        self.assertFalse(nearly_equal(math.inf, math.inf))

    def test_formula_replay_malformed_inputs_return_structured_issues(self) -> None:
        pointer = "/propagation_trace/0"
        self.assertEqual(
            {item.code for item in replay_trace_row({}, None, set(), set(), pointer=pointer)},
            {"UNKNOWN_REF"},
        )
        edge = {
            "id": "edge:xy",
            "from": "factor:x",
            "to": "factor:y",
            "base_strength": 1.0,
            "sign": 0,
            "transform": "unsupported",
            "context_modifiers": "invalid",
            "saturation": -1.0,
        }
        row = {
            "edge_id": "edge:xy",
            "from": "factor:wrong",
            "to": "factor:y",
            "sampled_strength": "invalid",
            "input_effect": "invalid",
            "resolved_transform_parameters": "invalid",
            "noise": "invalid",
            "formula_version": "unsupported",
            "threshold_active_before": "invalid",
            "threshold_active_after": "invalid",
            "output_effect": "invalid",
        }
        malformed = replay_trace_row(
            row,
            edge,
            {"factor:x", "factor:y"},
            {"edge:xy"},
            pointer=pointer,
        )
        self.assertTrue(
            {"TRACE_ENDPOINT", "SIGN", "TYPE", "SCHEMA", "RANGE"}
            <= {item.code for item in malformed}
        )

        valid_edge = {
            **edge,
            "sign": 1,
            "transform": "linear",
            "context_modifiers": [
                {
                    "context": "context:ok",
                    "multiplier": 1.0,
                    "rationale": "Replay context",
                }
            ],
        }
        mismatch_row = {
            "edge_id": "edge:xy",
            "from": "factor:x",
            "to": "factor:y",
            "input_effect": 1.0,
            "output_effect": 3.0,
            "amplification": 9.0,
            "formula_version": FORMULA_VERSION,
        }
        mismatch = replay_trace_row(
            mismatch_row,
            valid_edge,
            {"factor:x", "factor:y", "context:ok"},
            {"edge:xy"},
            pointer=pointer,
        )
        self.assertTrue(
            {"TRACE_FORMULA_MISMATCH", "TRACE_AMPLIFICATION"}
            <= {item.code for item in mismatch}
        )

    def test_sobol_optional_executes_available_and_constant_paths(self) -> None:
        available = sobol_saltelli_optional(
            {"x": (0.0, 1.0), "y": (-1.0, 1.0)},
            lambda params: params["x"] + 2.0 * params["y"],
            n=16,
            seed="available",
        )
        self.assertTrue(available["available"])
        self.assertEqual(set(available["first_order"]), {"x", "y"})
        constant = sobol_saltelli_optional(
            {"x": (0.0, 1.0)},
            lambda _params: 1.0,
            n=8,
            seed="constant",
        )
        self.assertEqual(constant["first_order"]["x"], 0.0)
        self.assertEqual(constant["total_order"]["x"], 0.0)

    def test_logistic_dual_read_is_explicit(self) -> None:
        legacy = expected_output_effect(
            base_strength=1.0,
            sign=1,
            context_mult=1.0,
            input_effect=0.0,
            transform="logistic",
            formula_version=LEGACY_FORMULA_VERSION,
        )
        current = expected_output_effect(
            base_strength=1.0,
            sign=1,
            context_mult=1.0,
            input_effect=0.0,
            transform="logistic",
            formula_version=FORMULA_VERSION,
        )
        self.assertEqual(legacy, 0.5)
        self.assertEqual(current, 0.0)
        self.assertAlmostEqual(
            expected_output_effect(
                base_strength=1.0,
                sign=1,
                context_mult=1.0,
                input_effect=100.0,
                transform="logistic",
            ),
            1.0,
        )

    def test_elasticity_uses_log_change_semantics(self) -> None:
        output = expected_output_effect(
            base_strength=2.0,
            sign=1,
            context_mult=1.0,
            input_effect=math.log(1.1),
            transform="elasticity",
        )
        self.assertAlmostEqual(output, 0.21, places=12)

    def test_threshold_modes_and_hysteresis(self) -> None:
        deadband = expected_output_effect(
            base_strength=2.0,
            sign=1,
            context_mult=1.0,
            input_effect=-3.0,
            transform="threshold",
            transform_parameters={"mode": "deadband", "threshold": 0.0, "deadband": 1.0},
        )
        self.assertEqual(deadband, -4.0)
        on, active = evaluate_output_effect(
            base_strength=1.0,
            sign=1,
            context_mult=1.0,
            input_effect=3.0,
            transform="threshold",
            transform_parameters={"mode": "hysteresis", "theta_on": 2.0, "theta_off": 1.0},
            threshold_active=False,
        )
        held, held_active = evaluate_output_effect(
            base_strength=1.0,
            sign=1,
            context_mult=1.0,
            input_effect=1.5,
            transform="threshold",
            transform_parameters={"mode": "hysteresis", "theta_on": 2.0, "theta_off": 1.0},
            threshold_active=active,
        )
        off, off_active = evaluate_output_effect(
            base_strength=1.0,
            sign=1,
            context_mult=1.0,
            input_effect=0.5,
            transform="threshold",
            transform_parameters={"mode": "hysteresis", "theta_on": 2.0, "theta_off": 1.0},
            threshold_active=held_active,
        )
        self.assertEqual((on, active), (3.0, True))
        self.assertEqual((held, held_active), (1.5, True))
        self.assertEqual((off, off_active), (0.0, False))

    def test_identity_gain_ignores_strength_without_infinity(self) -> None:
        output = expected_output_effect(
            base_strength=0.0,
            sign=1,
            context_mult=1.0,
            input_effect=2.0,
            transform="identity",
        )
        self.assertEqual(output, 2.0)
        self.assertEqual(
            amplification_ratio(
                output,
                0.0,
                1.0,
                input_effect=2.0,
                transform="identity",
            ),
            1.0,
        )

    def test_legacy_hash_omits_new_defaults(self) -> None:
        nodes = [
            {"id": "factor:a", "role": "exogenous", "baseline": 1.0, "scale": "level"},
            {"id": "factor:b", "role": "endogenous", "baseline": 0.0, "scale": "level"},
        ]
        edges = [
            {
                "id": "edge:ab",
                "from": "factor:a",
                "to": "factor:b",
                "sign": 1,
                "base_strength": 0.5,
                "transform": "linear",
                "context_modifiers": [{"multiplier": 1.0}],
            }
        ]
        legacy = compile_model(nodes, edges, formula_version=LEGACY_FORMULA_VERSION)
        current = compile_model(nodes, edges, formula_version=FORMULA_VERSION)
        self.assertEqual(model_hash(legacy), model_hash(current))
        payload = model_payload(current)
        self.assertNotIn("retention", payload["variables"]["factor:a"])
        self.assertNotIn("transform_parameters", payload["edges"][0])

    def test_identity_rejects_unused_effect_distribution(self) -> None:
        nodes = [
            {"id": "factor:a", "baseline": 1.0},
            {"id": "factor:b", "baseline": 0.0},
        ]
        edges = [
            {
                "id": "edge:ab",
                "from": "factor:a",
                "to": "factor:b",
                "sign": 1,
                "base_strength": 1.0,
                "transform": "identity",
                "effect_distribution": {"distribution": "uniform", "min": 0.0, "max": 1.0},
                "context_modifiers": [{"multiplier": 1.0}],
            }
        ]
        with self.assertRaisesRegex(ValueError, "identity transform cannot use effect_distribution"):
            compile_model(nodes, edges)

    def test_trace_formula_version_must_match_manifest(self) -> None:
        edge = {
            "id": "edge:xy",
            "from": "factor:x",
            "to": "factor:y",
            "base_strength": 1.0,
            "sign": 1,
            "transform": "linear",
            "context_modifiers": [
                {
                    "context": "context:c",
                    "multiplier": 1.0,
                    "rationale": "Declared context",
                }
            ],
        }
        row = {
            "step": 1,
            "time": "2026-01-01T00:00:00Z",
            "edge_id": "edge:xy",
            "from": "factor:x",
            "to": "factor:y",
            "input_effect": 1.0,
            "output_effect": 1.0,
            "noise": 0.0,
            "mechanism": "Declared mechanism",
            "evidence_ids": [],
            "formula_version": LEGACY_FORMULA_VERSION,
            "sample_refs": ["run:0"],
            "run_id": 0,
            "tick": 0,
            "source_tick": 0,
            "source_state": 1.0,
            "target_state": 1.0,
            "sampled_strength": 1.0,
        }
        row["hash_chain"] = canonical_hash({"previous_hash": None, "row": row})
        result = validate_trace(
            [row],
            {"factor:x", "factor:y", "context:c"},
            {"edge:xy": edge},
            set(),
            {
                "formula_version": FORMULA_VERSION,
                "temporal_frame": {
                    "simulation_start": "2026-01-01T00:00:00Z",
                    "simulation_end": "2026-01-02T00:00:00Z",
                },
            },
            {},
        )
        self.assertEqual(result.status, "fail")
        self.assertIn("TRACK_MISMATCH", {item.code for item in result.issues})

    def test_formula_replay_validates_hysteresis_transition(self) -> None:
        edge = {
            "id": "edge:xy",
            "from": "factor:x",
            "to": "factor:y",
            "base_strength": 1.0,
            "sign": 1,
            "transform": "threshold",
            "transform_parameters": {
                "mode": "hysteresis",
                "threshold": 0.0,
                "theta_on": 2.0,
                "theta_off": 1.0,
            },
            "context_modifiers": [
                {
                    "context": "context:c",
                    "multiplier": 1.0,
                    "rationale": "Declared context",
                }
            ],
        }
        row = {
            "edge_id": "edge:xy",
            "from": "factor:x",
            "to": "factor:y",
            "input_effect": 1.5,
            "output_effect": 1.5,
            "noise": 0.0,
            "formula_version": FORMULA_VERSION,
            "threshold_active_before": True,
            "threshold_active_after": False,
        }
        issues = replay_trace_row(
            row,
            edge,
            {"factor:x", "factor:y", "context:c"},
            {"edge:xy"},
            pointer="/propagation_trace/0",
        )
        self.assertIn("TRACE_HYSTERESIS_STATE", {item.code for item in issues})

    def test_replay_rejects_cross_version_run_and_binding_combinations(self) -> None:
        mutations = (
            {
                "run_contract_version": "aleph-run-2.0",
                "formula_version": FORMULA_VERSION,
            },
            {
                "run_contract_version": "aleph-run-2.1",
                "formula_version": FORMULA_VERSION,
            },
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary) / "workspace"
                shutil.copytree(FIXTURE, workspace)
                run_path = workspace / "simulation-run.json"
                run = json.loads(run_path.read_text(encoding="utf-8"))
                run.update(mutation)
                run["contract_hash"] = canonical_hash(
                    {key: value for key, value in run.items() if key != "contract_hash"}
                )
                run_path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "replay_simulation.py"),
                        "--workspace",
                        str(workspace),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("versions disagree", completed.stdout)


if __name__ == "__main__":
    unittest.main()
