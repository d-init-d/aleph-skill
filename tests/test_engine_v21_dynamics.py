from __future__ import annotations

import math
import unittest

from aleph import FORMULA_VERSION, LEGACY_FORMULA_VERSION
from aleph.engine import (
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    _signature,
    compile_model,
    run_deterministic,
    sampled_transform_parameters,
)
from aleph.execution_binding import (
    _hysteresis_timeline,
    _target_is_blocked,
    _trajectory_value_equal,
    build_trace_execution_binding,
)


class EngineV21DynamicsTests(unittest.TestCase):
    def test_execution_binding_defensive_helpers_and_error_paths(self) -> None:
        self.assertTrue(_trajectory_value_equal(1, 1))
        self.assertFalse(_trajectory_value_equal(True, 1))
        self.assertFalse(_trajectory_value_equal("1", 1.0))
        self.assertFalse(_trajectory_value_equal(float("nan"), 1.0))

        model = ComputationalModel(
            variables={
                "factor:x": Variable(id="factor:x", role="exogenous", baseline=1.0),
                "factor:y": Variable(id="factor:y", role="endogenous", baseline=0.0),
            },
            edges=[
                ModelEdge(
                    id="edge:xy",
                    source="factor:x",
                    target="factor:y",
                    sign=1,
                    strength=1.0,
                )
            ],
            formula_version=FORMULA_VERSION,
        )
        config = EngineConfig()
        result = run_deterministic(model, config, ticks=1)
        manifest = {"temporal_frame": {"simulation_start": "2026-01-01T00:00:00Z"}}
        self.assertIsNone(
            build_trace_execution_binding(
                [],
                model,
                config,
                ticks=1,
                result=result,
                manifest=manifest,
                binding_version="unsupported",
            )[0]
        )
        self.assertIsNone(
            build_trace_execution_binding(
                [],
                model,
                config,
                ticks=1,
                result=result,
                manifest={},
            )[0]
        )
        bad_config = EngineConfig(timestep=float("nan"))
        self.assertTrue(
            build_trace_execution_binding(
                [],
                model,
                bad_config,
                ticks=1,
                result=result,
                manifest=manifest,
            )[1]
        )
        bad_type_config = EngineConfig(timestep="invalid")  # type: ignore[arg-type]
        self.assertTrue(
            build_trace_execution_binding(
                [],
                model,
                bad_type_config,
                ticks=1,
                result=result,
                manifest=manifest,
            )[1]
        )

        base_row = {
            "sample_refs": ["run:0"],
            "run_id": 0,
            "edge_id": "edge:xy",
            "formula_version": FORMULA_VERSION,
            "time": "2026-01-01T00:00:00Z",
            "tick": 0,
            "source_tick": 0,
            "source_state": 1.0,
            "target_state": 1.0,
            "sampled_strength": 1.0,
            "input_effect": 1.0,
            "noise": 0.0,
        }
        mutations = [
            {"sample_refs": []},
            {"run_id": 2},
            {"edge_id": "edge:missing"},
            {"formula_version": LEGACY_FORMULA_VERSION},
            {"time": "not-a-time"},
            {"time": "2026-01-01T12:00:00Z"},
            {"source_tick": 9},
            {"source_state": 9.0},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                binding, issues = build_trace_execution_binding(
                    [{**base_row, **mutation}],
                    model,
                    config,
                    ticks=1,
                    result=result,
                    manifest=manifest,
                )
                self.assertIsNone(binding)
                self.assertTrue(issues)

        outside_row = {**base_row, "time": "2026-01-02T00:00:00Z", "tick": 1}
        self.assertTrue(
            build_trace_execution_binding(
                [outside_row],
                model,
                config,
                ticks=1,
                result=result,
                manifest=manifest,
            )[1]
        )
        self.assertTrue(
            build_trace_execution_binding(
                [base_row],
                model,
                config,
                ticks=1,
                result={"ok": False, "history": []},
                manifest=manifest,
            )[1]
        )

        absent_model = ComputationalModel(
            variables=model.variables,
            edges=[
                ModelEdge(
                    id="edge:xy",
                    source="factor:x",
                    target="factor:y",
                    sign=1,
                    strength=1.0,
                    existence_prob=0.0,
                )
            ],
            formula_version=FORMULA_VERSION,
        )
        monte_carlo_config = EngineConfig(mode="monte_carlo")
        self.assertTrue(
            build_trace_execution_binding(
                [base_row],
                absent_model,
                monte_carlo_config,
                ticks=1,
                result={"summary": {"n_runs": 1}},
                manifest=manifest,
            )[1]
        )

        invalid_parameter_model = ComputationalModel(
            variables=model.variables,
            edges=[
                ModelEdge(
                    id="edge:xy",
                    source="factor:x",
                    target="factor:y",
                    sign=1,
                    strength=1.0,
                    transform="logistic",
                    transform_parameters={"steepness": 0.0},
                )
            ],
            formula_version=FORMULA_VERSION,
        )
        self.assertTrue(
            build_trace_execution_binding(
                [base_row],
                invalid_parameter_model,
                config,
                ticks=1,
                result=result,
                manifest=manifest,
            )[1]
        )

        blocked_model = ComputationalModel(
            variables=model.variables,
            edges=model.edges,
            interventions=[
                {
                    "target": "factor:y",
                    "op": "set",
                    "value": 4.0,
                    "start_tick": 0,
                    "end_tick": 1,
                },
            ],
            formula_version=FORMULA_VERSION,
        )
        self.assertTrue(_target_is_blocked(blocked_model, "factor:y", 0))
        self.assertFalse(_target_is_blocked(blocked_model, "factor:y", 1))
        self.assertFalse(
            _target_is_blocked(
                ComputationalModel(
                    variables=model.variables,
                    interventions=[{"target": "factor:y", "op": "set", "start_tick": "bad"}],
                ),
                "factor:y",
                0,
            )
        )
        blocked_result = run_deterministic(blocked_model, config, ticks=1)
        self.assertTrue(
            build_trace_execution_binding(
                [base_row],
                blocked_model,
                config,
                ticks=1,
                result=blocked_result,
                manifest=manifest,
            )[1]
        )

        hysteresis_edge = ModelEdge(
            id="edge:h",
            source="factor:x",
            target="factor:y",
            sign=1,
            strength=1.0,
            transform="threshold",
            transform_parameters={"mode": "hysteresis", "theta_on": 0.5, "theta_off": 0.25},
        )
        timeline = _hysteresis_timeline(
            model,
            hysteresis_edge,
            [{"factor:x": 1.0}, {"factor:x": 0.1}],
            sampled_strength=1.0,
            sampled_lag_ticks=0,
            resolved_parameters=hysteresis_edge.transform_parameters,
        )
        self.assertEqual(timeline, [(False, True), (True, False)])
        blocked_timeline = _hysteresis_timeline(
            blocked_model,
            hysteresis_edge,
            [{"factor:x": 1.0}],
            sampled_strength=1.0,
            sampled_lag_ticks=0,
            resolved_parameters=hysteresis_edge.transform_parameters,
        )
        self.assertEqual(blocked_timeline, [(False, False)])
        with self.assertRaises(ValueError):
            _hysteresis_timeline(
                model,
                hysteresis_edge,
                [{"factor:x": "bad"}],
                sampled_strength=1.0,
                sampled_lag_ticks=0,
                resolved_parameters=hysteresis_edge.transform_parameters,
            )
        with self.assertRaises(ValueError):
            _hysteresis_timeline(
                model,
                model.edges[0],
                [{"factor:x": 1.0}],
                sampled_strength=1.0,
                sampled_lag_ticks=0,
                resolved_parameters={},
            )

    def test_decay_is_invariant_to_timestep(self) -> None:
        model = ComputationalModel(
            variables={
                "stock:s": Variable(
                    id="stock:s",
                    role="endogenous",
                    scale="stock",
                    baseline=100.0,
                    decay_rate=0.1,
                )
            }
        )
        daily = run_deterministic(model, EngineConfig(timestep=1.0), ticks=10)
        half_daily = run_deterministic(model, EngineConfig(timestep=0.5), ticks=20)
        expected = 100.0 * math.exp(-1.0)
        self.assertAlmostEqual(daily["payload"]["final_state"]["stock:s"], expected, places=12)
        self.assertAlmostEqual(half_daily["payload"]["final_state"]["stock:s"], expected, places=12)

    def test_rate_and_impulse_have_distinct_stock_units(self) -> None:
        variables = {
            "flow:f": Variable(id="flow:f", role="exogenous", scale="flow", baseline=2.0),
            "stock:s": Variable(id="stock:s", role="endogenous", scale="stock", baseline=0.0),
        }
        rate = ComputationalModel(
            variables=variables,
            edges=[ModelEdge(id="edge:rate", source="flow:f", target="stock:s", sign=1, strength=1.0, integration="rate")],
        )
        impulse = ComputationalModel(
            variables=variables,
            edges=[ModelEdge(id="edge:impulse", source="flow:f", target="stock:s", sign=1, strength=1.0, integration="impulse")],
        )
        rate_result = run_deterministic(rate, EngineConfig(timestep=0.5), ticks=2)
        impulse_result = run_deterministic(impulse, EngineConfig(timestep=0.5), ticks=2)
        self.assertEqual([row["stock:s"] for row in rate_result["history"]], [1.0, 2.0])
        self.assertEqual([row["stock:s"] for row in impulse_result["history"]], [2.0, 4.0])

    def test_stock_set_release_policy_is_explicit(self) -> None:
        def execute(policy: str) -> list[float]:
            model = ComputationalModel(
                variables={
                    "stock:s": Variable(id="stock:s", role="endogenous", scale="stock", baseline=10.0)
                },
                interventions=[
                    {
                        "id": "intervention:set",
                        "target": "stock:s",
                        "op": "set",
                        "value": 100.0,
                        "start_tick": 0,
                        "end_tick": 2,
                        "release_policy": policy,
                    }
                ],
            )
            result = run_deterministic(model, EngineConfig(), ticks=3)
            return [row["stock:s"] for row in result["history"]]

        self.assertEqual(execute("retain"), [100.0, 100.0, 100.0])
        self.assertEqual(execute("reset_baseline"), [100.0, 100.0, 10.0])

    def test_intervention_contract_rejects_invalid_release_and_ticks(self) -> None:
        nodes = [
            {"id": "factor:x", "baseline": 1.0, "scale": "level"},
            {"id": "stock:s", "baseline": 1.0, "scale": "stock"},
        ]
        with self.assertRaisesRegex(ValueError, "requires a set intervention on a stock"):
            compile_model(
                nodes,
                [],
                [
                    {
                        "id": "intervention:invalid-release",
                        "target": "factor:x",
                        "op": "set",
                        "value": 2.0,
                        "start_tick": 0,
                        "release_policy": "reset_baseline",
                    }
                ],
            )
        with self.assertRaisesRegex(ValueError, "start_tick must be a non-negative integer"):
            compile_model(
                nodes,
                [],
                [
                    {
                        "id": "intervention:invalid-tick",
                        "target": "stock:s",
                        "op": "set",
                        "value": 2.0,
                        "start_tick": 0.5,
                    }
                ],
            )

    def test_transform_parameter_sampling_is_addressed(self) -> None:
        model = compile_model(
            [{"id": "factor:a", "baseline": 1.0}, {"id": "factor:b", "baseline": 0.0}],
            [
                {
                    "id": "edge:ab",
                    "from": "factor:a",
                    "to": "factor:b",
                    "sign": 1,
                    "base_strength": 1.0,
                    "transform": "logistic",
                    "transform_parameters": {
                        "midpoint": {"distribution": "uniform", "min": -1.0, "max": 1.0},
                        "steepness": {"distribution": "uniform", "min": 0.5, "max": 2.0},
                    },
                    "context_modifiers": [{"multiplier": 1.0}],
                }
            ],
        )
        config = EngineConfig(mode="monte_carlo", seed="stable")
        first = sampled_transform_parameters(model.edges[0], config, 7)
        second = sampled_transform_parameters(model.edges[0], config, 7)
        other = sampled_transform_parameters(model.edges[0], config, 8)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_effect_distribution_does_not_replace_deterministic_strength(self) -> None:
        model = compile_model(
            [
                {"id": "factor:a", "baseline": 2.0},
                {"id": "factor:b", "baseline": 0.0},
            ],
            [
                {
                    "id": "edge:ab",
                    "from": "factor:a",
                    "to": "factor:b",
                    "sign": 1,
                    "base_strength": 3.0,
                    "effect_distribution": {"distribution": "fixed", "value": 7.0},
                    "context_modifiers": [{"multiplier": 1.0}],
                }
            ],
        )
        result = run_deterministic(model, EngineConfig(mode="deterministic"), ticks=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["final_state"]["factor:b"], 6.0)

    def test_distributed_stock_dynamics_round_trip_compiled_payload(self) -> None:
        model = compile_model(
            [
                {
                    "id": "stock:s",
                    "role": "endogenous",
                    "scale": "stock",
                    "baseline": 100.0,
                    "decay_rate": {"distribution": "fixed", "value": 0.1},
                }
            ],
            [],
        )
        from aleph.engine import model_payload

        payload = model_payload(model)
        rehydrated = ComputationalModel(
            variables={
                key: Variable(**value) for key, value in payload["variables"].items()
            },
            formula_version=model.formula_version,
        )
        original = run_deterministic(model, EngineConfig(), ticks=2)
        replay = run_deterministic(rehydrated, EngineConfig(), ticks=2)
        self.assertTrue(replay["ok"], replay["issues"])
        self.assertEqual(replay["run_hash"], original["run_hash"])

    def test_stock_trajectory_signature_uses_magnitude_and_path(self) -> None:
        model = ComputationalModel(
            variables={
                "stock:s": Variable(id="stock:s", role="endogenous", scale="stock", baseline=0.0)
            }
        )
        small = _signature({"stock:s": 2.0}, model, [{"stock:s": 1.0}, {"stock:s": 2.0}])
        large = _signature({"stock:s": 1024.0}, model, [{"stock:s": 512.0}, {"stock:s": 1024.0}])
        same_endpoint_peak = _signature(
            {"stock:s": 2.0},
            model,
            [{"stock:s": 128.0}, {"stock:s": 2.0}],
        )
        self.assertNotEqual(small, large)
        self.assertNotEqual(small, same_endpoint_peak)

    def test_stock_signature_is_timestep_aware_and_includes_initial_state(self) -> None:
        model = ComputationalModel(
            variables={
                "stock:s": Variable(
                    id="stock:s", role="endogenous", scale="stock", baseline=10.0
                )
            }
        )
        daily = _signature(
            {"stock:s": 11.0},
            model,
            [{"stock:s": 11.0}, {"stock:s": 11.0}],
            timestep=1.0,
        )
        half_daily = _signature(
            {"stock:s": 11.0},
            model,
            [
                {"stock:s": 11.0},
                {"stock:s": 11.0},
                {"stock:s": 11.0},
                {"stock:s": 11.0},
            ],
            timestep=0.5,
        )
        declining = _signature(
            {"stock:s": 6.0},
            model,
            [{"stock:s": 5.0}, {"stock:s": 6.0}],
        )
        self.assertEqual(daily, half_daily)
        self.assertIn("stock:s:peak=0", declining)

    def test_execution_binding_v2_binds_resolved_dynamics(self) -> None:
        model = ComputationalModel(
            variables={
                "flow:f": Variable(id="flow:f", role="exogenous", scale="flow", baseline=2.0),
                "stock:s": Variable(id="stock:s", role="endogenous", scale="stock", baseline=0.0),
            },
            edges=[ModelEdge(id="edge:rate", source="flow:f", target="stock:s", sign=1, strength=1.0, integration="rate")],
        )
        config = EngineConfig(timestep=1.0)
        result = run_deterministic(model, config, ticks=2)
        rows = [
            {
                "step": 1,
                "time": "2026-01-01T00:00:00Z",
                "edge_id": "edge:rate",
                "sample_refs": ["run:0"],
                "run_id": 0,
                "tick": 0,
                "source_tick": 0,
                "source_state": 2.0,
                "target_state": 2.0,
                "sampled_strength": 1.0,
                "input_effect": 2.0,
                "output_effect": 2.0,
                "integrated_effect": 2.0,
                "target_retention_factor": 1.0,
                "noise": 0.0,
            }
        ]
        binding, issues = build_trace_execution_binding(
            rows,
            model,
            config,
            ticks=2,
            result=result,
            manifest={"temporal_frame": {"simulation_start": "2026-01-01T00:00:00Z"}},
        )
        self.assertEqual(issues, [])
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(binding["version"], "aleph-trace-execution-binding-v2")
        self.assertEqual(binding["formula_version"], "2.1.0")
        self.assertEqual(binding["rows"][0]["integration_factor"], 1.0)
        self.assertEqual(binding["rows"][0]["target_retention_factor"], 1.0)
        self.assertEqual(binding["rows"][0]["dynamics_hash"], result["payload"]["dynamics_hash"])

    def test_execution_binding_version_cannot_downgrade_or_upgrade_formula(self) -> None:
        variables = {
            "factor:x": Variable(id="factor:x", role="exogenous", baseline=1.0),
            "factor:y": Variable(id="factor:y", role="endogenous", baseline=0.0),
        }
        edges = [
            ModelEdge(
                id="edge:xy",
                source="factor:x",
                target="factor:y",
                sign=1,
                strength=1.0,
            )
        ]
        row = {
            "step": 1,
            "time": "2026-01-01T00:00:00Z",
            "edge_id": "edge:xy",
            "sample_refs": ["run:0"],
            "run_id": 0,
            "tick": 0,
            "source_tick": 0,
            "source_state": 1.0,
            "target_state": 1.0,
            "sampled_strength": 1.0,
            "input_effect": 1.0,
            "output_effect": 1.0,
            "noise": 0.0,
        }
        manifest = {"temporal_frame": {"simulation_start": "2026-01-01T00:00:00Z"}}
        current_model = ComputationalModel(
            variables=variables,
            edges=edges,
            formula_version=FORMULA_VERSION,
        )
        current_result = run_deterministic(current_model, EngineConfig(), ticks=1)
        binding, issues = build_trace_execution_binding(
            [{**row, "formula_version": FORMULA_VERSION}],
            current_model,
            EngineConfig(),
            ticks=1,
            result=current_result,
            manifest=manifest,
            binding_version="aleph-trace-execution-binding-v1",
        )
        self.assertIsNone(binding)
        self.assertTrue(issues)

        legacy_model = ComputationalModel(
            variables=variables,
            edges=edges,
            formula_version=LEGACY_FORMULA_VERSION,
        )
        legacy_result = run_deterministic(legacy_model, EngineConfig(), ticks=1)
        binding, issues = build_trace_execution_binding(
            [{**row, "formula_version": LEGACY_FORMULA_VERSION}],
            legacy_model,
            EngineConfig(),
            ticks=1,
            result=legacy_result,
            manifest=manifest,
            binding_version="aleph-trace-execution-binding-v2",
        )
        self.assertIsNone(binding)
        self.assertTrue(issues)

    def test_execution_binding_reconstructs_hysteresis_latch(self) -> None:
        model = ComputationalModel(
            variables={
                "factor:x": Variable(id="factor:x", role="exogenous", baseline=3.0),
                "factor:y": Variable(id="factor:y", role="endogenous", baseline=0.0),
            },
            edges=[
                ModelEdge(
                    id="edge:xy",
                    source="factor:x",
                    target="factor:y",
                    sign=1,
                    strength=1.0,
                    transform="threshold",
                    transform_parameters={
                        "mode": "hysteresis",
                        "threshold": 0.0,
                        "theta_on": 2.0,
                        "theta_off": 1.0,
                    },
                )
            ],
            interventions=[
                {
                    "id": "intervention:set-x",
                    "target": "factor:x",
                    "op": "set",
                    "value": 1.5,
                    "start_tick": 1,
                    "end_tick": None,
                    "release_policy": "retain",
                }
            ],
            formula_version=FORMULA_VERSION,
        )
        config = EngineConfig()
        result = run_deterministic(model, config, ticks=2)
        base_row = {
            "step": 1,
            "time": "2026-01-02T00:00:00Z",
            "edge_id": "edge:xy",
            "sample_refs": ["run:0"],
            "run_id": 0,
            "tick": 1,
            "source_tick": 1,
            "source_state": 1.5,
            "target_state": 1.5,
            "sampled_strength": 1.0,
            "input_effect": 1.5,
            "output_effect": 1.5,
            "integrated_effect": 1.5,
            "noise": 0.0,
            "formula_version": FORMULA_VERSION,
            "resolved_transform_parameters": {
                "mode": "hysteresis",
                "threshold": 0.0,
                "theta_on": 2.0,
                "theta_off": 1.0,
            },
            "threshold_active_before": True,
            "threshold_active_after": True,
        }
        manifest = {"temporal_frame": {"simulation_start": "2026-01-01T00:00:00Z"}}
        binding, issues = build_trace_execution_binding(
            [base_row],
            model,
            config,
            ticks=2,
            result=result,
            manifest=manifest,
        )
        self.assertEqual(issues, [])
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertTrue(binding["rows"][0]["threshold_active_before"])
        self.assertTrue(binding["rows"][0]["threshold_active_after"])

        forged = {
            **base_row,
            "output_effect": 0.0,
            "integrated_effect": 0.0,
            "threshold_active_before": False,
            "threshold_active_after": False,
        }
        binding, issues = build_trace_execution_binding(
            [forged],
            model,
            config,
            ticks=2,
            result=result,
            manifest=manifest,
        )
        self.assertIsNone(binding)
        self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()
