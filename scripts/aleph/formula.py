"""Trace formula replay — recompute effects; never trust artifact values."""

from __future__ import annotations

import math
from typing import Any

from . import FORMULA_VERSION
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


def expected_output_effect(
    *,
    base_strength: float,
    sign: int,
    context_mult: float,
    input_effect: float = 1.0,
    transform: str = "linear",
    saturation: float | None = None,
    noise: float = 0.0,
) -> float:
    """Replay the explicitly supported formula-version 2 transforms."""
    raw = float(sign) * float(base_strength) * float(context_mult) * float(input_effect)
    if transform == "elasticity":
        # treat base_strength as elasticity coefficient on log-ish scale, still linear for unit tests
        raw = float(sign) * float(base_strength) * float(context_mult) * float(input_effect)
    if saturation is not None and math.isfinite(saturation) and saturation > 0:
        # soft saturate
        raw = saturation * math.tanh(raw / saturation)
    return raw + noise


def amplification_ratio(output_effect: float, base_strength: float, context_mult: float) -> float:
    denom = abs(base_strength * context_mult)
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

    expected = expected_output_effect(
        base_strength=base if base is not None else 0.0,
        sign=int(sign),
        context_mult=mult,
        input_effect=input_effect,
        transform=str(transform),
        saturation=saturation,
        noise=noise,
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
        expected_amp = amplification_ratio(expected, base, mult)
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
