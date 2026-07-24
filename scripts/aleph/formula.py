"""Trace formula replay — recompute effects; never trust artifact values."""

from __future__ import annotations

import math
from typing import Any

from . import FORMULA_VERSION, LEGACY_FORMULA_VERSION, SUPPORTED_FORMULA_VERSIONS
from .issues import Issue, issue
from .schema import TRANSFORMS, nonempty_str, refuse_string_number

DEFAULT_TOLERANCE_ABS = 1e-9
DEFAULT_TOLERANCE_REL = 1e-6


def context_multiplier(modifiers: list[Any], node_ids: set[str], issues: list[Issue], pointer: str) -> float:
    mult = 1.0
    if not modifiers:
        issues.append(issue("CONTEXT", pointer=pointer, message="requires at least one context modifier"))
        return mult
    for idx, mod in enumerate(modifiers):
        mp = f"{pointer}[{idx}]"
        if not isinstance(mod, dict):
            issues.append(issue("TYPE", pointer=mp, message="context modifier must be object"))
            continue
        ctx = mod.get("context")
        if not nonempty_str(ctx) or str(ctx) not in node_ids:
            issues.append(
                issue("CONTEXT_MISSING", pointer=f"{mp}.context", message="context node must exist", actual=ctx)
            )
        m = refuse_string_number(mod.get("multiplier"), f"{mp}.multiplier", issues)
        if m is None:
            continue
        if not math.isfinite(m) or m <= 0:
            issues.append(
                issue("MULTIPLIER", pointer=f"{mp}.multiplier", message="must be finite and positive", actual=m)
            )
            continue
        if not nonempty_str(mod.get("rationale")):
            issues.append(issue("MULTIPLIER", pointer=f"{mp}.rationale", message="rationale required"))
        active = mod.get("active", True)
        if not isinstance(active, bool):
            issues.append(issue("TYPE", pointer=f"{mp}.active", message="must be boolean"))
            continue
        if active:
            mult *= m
    return mult


def _stable_logistic(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def evaluate_output_effect(
    *,
    base_strength: float,
    sign: int,
    context_mult: float,
    input_effect: float = 1.0,
    transform: str = "linear",
    transform_parameters: dict[str, Any] | None = None,
    saturation: float | None = None,
    noise: float = 0.0,
    formula_version: str = FORMULA_VERSION,
    threshold_active: bool | None = None,
) -> tuple[float, bool | None]:
    """Evaluate one edge and return its output plus hysteresis latch state."""
    if formula_version not in SUPPORTED_FORMULA_VERSIONS:
        raise ValueError(f"unsupported formula version {formula_version}")
    parameters = transform_parameters or {}
    source = float(input_effect)
    coefficient = 1.0 if transform == "identity" else float(base_strength)
    next_threshold_active: bool | None = None
    if transform in {"linear", "identity"}:
        response = source
    elif transform == "elasticity":
        if formula_version == LEGACY_FORMULA_VERSION:
            response = source
        else:
            # In formula 2.1 the input is a log-change. Convert the resulting
            # log response back to a fractional change after applying elasticity.
            response = math.expm1(coefficient * source)
            coefficient = 1.0
    elif transform == "logistic":
        midpoint = float(parameters.get("midpoint", 0.0))
        steepness = float(parameters.get("steepness", 1.0))
        if not math.isfinite(midpoint) or not math.isfinite(steepness) or steepness <= 0:
            raise ValueError("logistic transform requires finite midpoint and positive steepness")
        logistic = _stable_logistic(steepness * (source - midpoint))
        response = logistic if formula_version == LEGACY_FORMULA_VERSION else 2.0 * logistic - 1.0
    elif transform == "threshold":
        mode = str(parameters.get("mode", "above"))
        threshold = float(parameters.get("threshold", 0.0))
        if not math.isfinite(threshold):
            raise ValueError("threshold transform requires a finite threshold")
        centered = source - threshold
        if formula_version == LEGACY_FORMULA_VERSION or mode == "above":
            response = max(0.0, centered)
        elif mode == "below":
            response = max(0.0, -centered)
        elif mode == "deadband":
            deadband = float(parameters.get("deadband", 0.0))
            if not math.isfinite(deadband) or deadband < 0:
                raise ValueError("threshold deadband must be finite and non-negative")
            response = math.copysign(max(0.0, abs(centered) - deadband), centered) if centered else 0.0
        elif mode == "hysteresis":
            theta_on = float(parameters.get("theta_on", math.nan))
            theta_off = float(parameters.get("theta_off", math.nan))
            if not (math.isfinite(theta_on) and math.isfinite(theta_off) and theta_on >= theta_off >= 0):
                raise ValueError("hysteresis requires finite theta_on >= theta_off >= 0")
            was_active = bool(threshold_active)
            magnitude = abs(centered)
            next_threshold_active = magnitude > theta_off if was_active else magnitude >= theta_on
            response = centered if next_threshold_active else 0.0
        else:
            raise ValueError(f"unsupported threshold mode {mode}")
    else:
        raise ValueError(f"unsupported transform {transform}")
    raw = float(sign) * coefficient * float(context_mult) * response
    if saturation is not None and math.isfinite(saturation) and saturation > 0:
        # soft saturate
        raw = saturation * math.tanh(raw / saturation)
    return raw + noise, next_threshold_active


def expected_output_effect(
    *,
    base_strength: float,
    sign: int,
    context_mult: float,
    input_effect: float = 1.0,
    transform: str = "linear",
    transform_parameters: dict[str, Any] | None = None,
    saturation: float | None = None,
    noise: float = 0.0,
    formula_version: str = FORMULA_VERSION,
    threshold_active: bool | None = None,
) -> float:
    """Replay one supported version of the edge formula contract."""
    output, _ = evaluate_output_effect(
        base_strength=base_strength,
        sign=sign,
        context_mult=context_mult,
        input_effect=input_effect,
        transform=transform,
        transform_parameters=transform_parameters,
        saturation=saturation,
        noise=noise,
        formula_version=formula_version,
        threshold_active=threshold_active,
    )
    return output


def amplification_ratio(
    output_effect: float,
    base_strength: float,
    context_mult: float,
    *,
    input_effect: float = 1.0,
    transform: str = "linear",
    transform_parameters: dict[str, Any] | None = None,
    formula_version: str = FORMULA_VERSION,
    threshold_active: bool | None = None,
) -> float:
    if formula_version == LEGACY_FORMULA_VERSION:
        denom = abs(base_strength * context_mult)
        if denom < 1e-15:
            return 0.0 if abs(output_effect) < 1e-15 else float("inf")
        return abs(output_effect) / denom
    if transform != "identity" and abs(base_strength) < 1e-15:
        return 0.0 if abs(output_effect) < 1e-15 else float("inf")
    reference = expected_output_effect(
        base_strength=1.0,
        sign=1,
        context_mult=context_mult,
        input_effect=input_effect,
        transform=transform,
        transform_parameters=transform_parameters,
        formula_version=formula_version,
        threshold_active=threshold_active,
    )
    denom = abs(reference)
    if denom < 1e-15:
        return 0.0 if abs(output_effect) < 1e-15 else float("inf")
    return abs(output_effect) / denom


def nearly_equal(expected: float, actual: float, *, abs_tol: float = DEFAULT_TOLERANCE_ABS, rel_tol: float = DEFAULT_TOLERANCE_REL) -> bool:
    if not (math.isfinite(expected) and math.isfinite(actual)):
        return False
    return abs(expected - actual) <= max(abs_tol, rel_tol * max(abs(expected), abs(actual)))


def replay_trace_row(
    row: dict[str, Any],
    edge: dict[str, Any] | None,
    node_ids: set[str],
    edge_ids: set[str],
    *,
    pointer: str,
    abs_tol: float = DEFAULT_TOLERANCE_ABS,
    rel_tol: float = DEFAULT_TOLERANCE_REL,
) -> list[Issue]:
    issues: list[Issue] = []
    if edge is None:
        issues.append(issue("UNKNOWN_REF", pointer=f"{pointer}.edge_id", message="edge not found", actual=row.get("edge_id")))
        return issues

    if row.get("from") != edge.get("from") or row.get("to") != edge.get("to"):
        issues.append(
            issue(
                "TRACE_ENDPOINT",
                pointer=pointer,
                message="trace from/to must match edge",
                expected={"from": edge.get("from"), "to": edge.get("to")},
                actual={"from": row.get("from"), "to": row.get("to")},
            )
        )

    strength_source = row.get("sampled_strength", edge.get("base_strength"))
    base = refuse_string_number(strength_source, f"{pointer}/sampled_strength", issues)
    # legacy confidence is evidence only — not used in formula
    sign = edge.get("sign")
    if sign not in (-1, 1):
        issues.append(issue("SIGN", pointer=f"{pointer}/edge.sign", message="must be -1 or 1", actual=sign))
        sign = 1
    mods = edge.get("context_modifiers") or []
    if not isinstance(mods, list):
        issues.append(issue("TYPE", pointer=f"{pointer}/edge.context_modifiers", message="must be list"))
        mods = []
    mult = context_multiplier(mods, node_ids, issues, f"{pointer}/edge.context_modifiers")
    input_effect = 1.0
    if "input_effect" in row:
        ie = refuse_string_number(row.get("input_effect"), f"{pointer}.input_effect", issues)
        if ie is not None:
            input_effect = ie

    transform = edge.get("transform", "linear")
    if transform not in TRANSFORMS:
        issues.append(issue("SCHEMA", pointer=f"{pointer}/edge.transform", message="unsupported replay transform", actual=transform))
        transform = "linear"
    raw_transform_parameters = row.get("resolved_transform_parameters", edge.get("transform_parameters"))
    transform_parameters: dict[str, Any] = {}
    if raw_transform_parameters is not None:
        if not isinstance(raw_transform_parameters, dict):
            issues.append(issue("TYPE", pointer=f"{pointer}/edge.transform_parameters", message="must be object"))
        else:
            for key in ("midpoint", "steepness", "threshold", "deadband", "theta_on", "theta_off"):
                if key not in raw_transform_parameters:
                    continue
                parsed = refuse_string_number(
                    raw_transform_parameters.get(key),
                    f"{pointer}/edge.transform_parameters/{key}",
                    issues,
                )
                if parsed is not None:
                    transform_parameters[key] = parsed
            mode = raw_transform_parameters.get("mode")
            if mode is not None:
                transform_parameters["mode"] = str(mode)
    noise = 0.0
    if "noise" in row:
        parsed_noise = refuse_string_number(row.get("noise"), f"{pointer}.noise", issues)
        if parsed_noise is not None:
            noise = parsed_noise
    saturation = None
    if "saturation" in edge:
        saturation = refuse_string_number(edge.get("saturation"), f"{pointer}/edge.saturation", issues)
        if saturation is not None and saturation <= 0:
            issues.append(issue("RANGE", pointer=f"{pointer}/edge.saturation", message="must be positive", actual=saturation))
            saturation = None

    row_formula_version = row.get("formula_version")
    if row_formula_version not in SUPPORTED_FORMULA_VERSIONS:
        issues.append(
            issue(
                "SCHEMA",
                pointer=f"{pointer}/formula_version",
                message="unsupported trace formula version",
                expected=list(SUPPORTED_FORMULA_VERSIONS),
                actual=row_formula_version,
            )
        )
        row_formula_version = FORMULA_VERSION
    threshold_before = row.get("threshold_active_before")
    if threshold_before is not None and not isinstance(threshold_before, bool):
        issues.append(issue("TYPE", pointer=f"{pointer}/threshold_active_before", message="must be boolean"))
        threshold_before = None
    is_hysteresis = (
        row_formula_version != LEGACY_FORMULA_VERSION
        and transform == "threshold"
        and transform_parameters.get("mode", "above") == "hysteresis"
    )
    threshold_after = row.get("threshold_active_after")
    if is_hysteresis:
        if not isinstance(row.get("threshold_active_before"), bool):
            issues.append(
                issue(
                    "MISSING_FIELD",
                    pointer=f"{pointer}/threshold_active_before",
                    message="formula 2.1 hysteresis trace requires a boolean latch state",
                )
            )
        if not isinstance(threshold_after, bool):
            issues.append(
                issue(
                    "MISSING_FIELD",
                    pointer=f"{pointer}/threshold_active_after",
                    message="formula 2.1 hysteresis trace requires a boolean latch transition",
                )
            )
            threshold_after = None
    elif threshold_after is not None and not isinstance(threshold_after, bool):
        issues.append(issue("TYPE", pointer=f"{pointer}/threshold_active_after", message="must be boolean"))
        threshold_after = None
    try:
        expected, expected_threshold_after = evaluate_output_effect(
            base_strength=base if base is not None else 0.0,
            sign=int(sign),
            context_mult=mult,
            input_effect=input_effect,
            transform=str(transform),
            transform_parameters=transform_parameters,
            saturation=saturation,
            noise=noise,
            formula_version=str(row_formula_version),
            threshold_active=threshold_before,
        )
    except (OverflowError, TypeError, ValueError) as exc:
        issues.append(issue("SCHEMA", pointer=f"{pointer}/edge.transform_parameters", message=str(exc)))
        expected = 0.0
        expected_threshold_after = None
    if (
        is_hysteresis
        and isinstance(threshold_after, bool)
        and expected_threshold_after is not None
        and threshold_after != expected_threshold_after
    ):
        issues.append(
            issue(
                "TRACE_HYSTERESIS_STATE",
                artifact="propagation_trace",
                pointer=f"{pointer}/threshold_active_after",
                message="hysteresis latch transition does not match the declared input state",
                expected=expected_threshold_after,
                actual=threshold_after,
            )
        )
    actual = refuse_string_number(row.get("output_effect"), f"{pointer}.output_effect", issues)
    if actual is not None and base is not None:
        if not nearly_equal(expected, actual, abs_tol=abs_tol, rel_tol=rel_tol):
            issues.append(
                issue(
                    "TRACE_FORMULA_MISMATCH",
                    severity="error",
                    artifact="propagation_trace",
                    pointer=f"{pointer}.output_effect",
                    message="recomputed effect does not match artifact",
                    expected=expected,
                    actual=actual,
                )
            )
        # Amplification: recompute; do not trust artifact
        expected_amp = amplification_ratio(
            expected,
            base,
            mult,
            input_effect=input_effect,
            transform=str(transform),
            transform_parameters=transform_parameters,
            formula_version=str(row_formula_version),
            threshold_active=threshold_before,
        )
        if "amplification" in row or "amplification_ratio" in row:
            art_amp = row.get("amplification", row.get("amplification_ratio"))
            amp_val = refuse_string_number(art_amp, f"{pointer}.amplification", issues)
            if amp_val is not None and not nearly_equal(expected_amp, amp_val, abs_tol=1e-6, rel_tol=1e-4):
                issues.append(
                    issue(
                        "TRACE_AMPLIFICATION",
                        artifact="propagation_trace",
                        pointer=f"{pointer}.amplification",
                        message="amplification must be recomputed",
                        expected=expected_amp,
                        actual=amp_val,
                    )
                )
    return issues


def formula_version() -> str:
    return FORMULA_VERSION
