"""Sensitivity analysis — OAT, Morris (stdlib); Sobol optional extras."""

from __future__ import annotations

import hashlib
import importlib
import math
from collections.abc import Callable
from typing import Any

from .rng import uniform01


def one_at_a_time(
    base_params: dict[str, float],
    evaluate: Callable[[dict[str, float]], float],
    *,
    delta: float = 0.1,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    if not math.isfinite(delta) or delta <= 0:
        raise ValueError("OAT delta must be finite and positive")
    base = evaluate(base_params)
    effects = {}
    for key, val in sorted(base_params.items()):
        if not math.isfinite(val):
            raise ValueError(f"OAT baseline for {key} must be finite")
        low, high = (bounds or {}).get(key, (-math.inf, math.inf))
        if not (math.isfinite(low) or low == -math.inf) or not (math.isfinite(high) or high == math.inf):
            raise ValueError(f"OAT bounds for {key} must be finite or unbounded")
        if low > high or not low <= val <= high:
            raise ValueError(f"OAT baseline for {key} must lie within bounds")
        up_value = val * (1 + delta) if val != 0 else delta
        down_value = val * (1 - delta) if val != 0 else -delta
        up_value = min(high, max(low, up_value))
        down_value = min(high, max(low, down_value))
        up = dict(base_params)
        up[key] = up_value
        down = dict(base_params)
        down[key] = down_value
        up_effect = evaluate(up) - base
        down_effect = evaluate(down) - base
        effects[key] = {
            "up": up_effect,
            "down": down_effect,
            "abs_max": max(abs(up_effect), abs(down_effect)),
            "up_value": up_value,
            "down_value": down_value,
            "up_delta": up_value - val,
            "down_delta": down_value - val,
        }
    return {"base": base, "effects": effects, "method": "OAT"}


def morris_screening(
    param_bounds: dict[str, tuple[float, float]],
    evaluate: Callable[[dict[str, float]], float],
    *,
    seed: str = "morris",
    trajectories: int | None = None,
    levels: int = 6,
) -> dict[str, Any]:
    """Elementary effects (Morris) with counter RNG."""
    keys = sorted(param_bounds)
    trajectories = trajectories if trajectories is not None else max(10, 2 * len(keys))
    mu: dict[str, list[float]] = {k: [] for k in keys}
    for t in range(trajectories):
        # start point
        x = {}
        for i, k in enumerate(keys):
            lo, hi = param_bounds[k]
            u = uniform01(seed, t, i, "start")
            # discrete levels
            level = int(u * levels) / max(levels - 1, 1)
            x[k] = lo + level * (hi - lo)
        y0 = evaluate(x)
        for i, k in enumerate(keys):
            lo, hi = param_bounds[k]
            step = (hi - lo) / max(levels - 1, 1)
            x2 = dict(x)
            direction = 1 if uniform01(seed, t, i, "dir") > 0.5 else -1
            x2[k] = min(hi, max(lo, x[k] + direction * step))
            y1 = evaluate(x2)
            actual_step = x2[k] - x[k]
            if actual_step == 0.0 and step > 0.0:
                x2[k] = min(hi, max(lo, x[k] - direction * step))
                y1 = evaluate(x2)
                actual_step = x2[k] - x[k]
            ee = (y1 - y0) / actual_step if actual_step else 0.0
            mu[k].append(ee)
            x = x2
            y0 = y1
    summary = {}
    for k, vals in mu.items():
        mean = sum(vals) / len(vals) if vals else 0.0
        mean_abs = sum(abs(v) for v in vals) / len(vals) if vals else 0.0
        var = sum((v - mean) ** 2 for v in vals) / len(vals) if vals else 0.0
        summary[k] = {"mu": mean, "mu_star": mean_abs, "sigma": math.sqrt(var)}
    return {"method": "morris", "summary": summary, "trajectories": trajectories}


def conditional_contrast(
    base_params: dict[str, Any],
    factor: str,
    levels: list[Any],
    evaluate: Callable[[dict[str, Any]], float],
) -> dict[str, Any]:
    outcomes = {}
    for level in levels:
        p = dict(base_params)
        p[factor] = level
        outcomes[str(level)] = evaluate(p)
    return {"method": "conditional", "factor": factor, "outcomes": outcomes}


def sobol_saltelli_optional(
    param_bounds: dict[str, tuple[float, float]],
    evaluate: Callable[[dict[str, float]], float],
    *,
    n: int = 256,
    seed: str = "sobol",
) -> dict[str, Any]:
    """Sobol if numpy available; else soft-degrade message."""
    try:
        np = importlib.import_module("numpy")
    except ImportError:
        return {
            "method": "sobol",
            "available": False,
            "degraded": True,
            "message": "NumPy absent; use Morris/OAT; assurance remains limited for sensitivity extras",
        }
    # Minimal Saltelli-style first-order estimate
    keys = sorted(param_bounds)
    d = len(keys)
    seed_bytes = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(seed_bytes[:8], "big"))
    A = rng.random((n, d))
    B = rng.random((n, d))
    for j, k in enumerate(keys):
        lo, hi = param_bounds[k]
        A[:, j] = lo + A[:, j] * (hi - lo)
        B[:, j] = lo + B[:, j] * (hi - lo)

    def row_to_params(row: Any) -> dict[str, float]:
        return {k: float(row[i]) for i, k in enumerate(keys)}

    fA = np.array([evaluate(row_to_params(A[i])) for i in range(n)])
    fB = np.array([evaluate(row_to_params(B[i])) for i in range(n)])
    var_y = np.var(np.concatenate([fA, fB]))
    first = {}
    total = {}
    for j, k in enumerate(keys):
        AB = A.copy()
        AB[:, j] = B[:, j]
        fAB = np.array([evaluate(row_to_params(AB[i])) for i in range(n)])
        # Jansen estimator
        si = np.mean(fB * (fAB - fA)) / var_y if var_y > 1e-15 else 0.0
        first[k] = float(si)
        total[k] = float(0.5 * np.mean((fA - fAB) ** 2) / var_y) if var_y > 1e-15 else 0.0
    return {
        "method": "sobol",
        "available": True,
        "first_order": first,
        "total_order": total,
        "n": n,
        "seed_digest": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
        "estimator": "Saltelli-first/Jansen-total",
    }
