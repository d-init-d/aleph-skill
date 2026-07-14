"""Counter-based SHA-256 RNG — no global random state."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable
from typing import Any


def _to_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return b"bytes:" + value
    if isinstance(value, int):
        return b"int:" + str(value).encode("utf-8")
    return b"str:" + str(value).encode("utf-8")


def _update_field(digest: Any, value: object) -> None:
    encoded = _to_bytes(value)
    digest.update(struct.pack(">Q", len(encoded)))
    digest.update(encoded)


def counter_digest(
    seed: str | int,
    *parts: object,
) -> bytes:
    """Deterministic 32-byte digest from seed and counter parts."""
    h = hashlib.sha256()
    h.update(b"aleph-counter-rng-v2\x00")
    _update_field(h, seed)
    for part in parts:
        _update_field(h, part)
    return h.digest()


def uniform01(seed: str | int, *parts: object) -> float:
    """Uniform float in [0, 1) from counter digest."""
    digest = counter_digest(seed, *parts)
    # Use first 8 bytes as big-endian uint64
    n = int.from_bytes(digest[:8], "big")
    return n / 2**64


def normal01(seed: str | int, *parts: object) -> float:
    """Box-Muller normal using two counter uniforms (stable)."""
    u1 = max(1e-15, uniform01(seed, *parts, "n1"))
    u2 = uniform01(seed, *parts, "n2")
    import math

    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def sample_uniform(seed: str | int, low: float, high: float, *parts: object) -> float:
    return low + (high - low) * uniform01(seed, *parts)


def sample_triangular(seed: str | int, low: float, mode: float, high: float, *parts: object) -> float:
    """Inverse-CDF triangular."""
    import math

    if not all(math.isfinite(value) for value in (low, mode, high)) or not low <= mode <= high:
        raise ValueError("triangular parameters require finite low <= mode <= high")
    if high == low:
        return low
    u = uniform01(seed, *parts)
    c = (mode - low) / (high - low)
    if u < c:
        return low + math.sqrt(u * (high - low) * (mode - low))
    return high - math.sqrt((1 - u) * (high - low) * (high - mode))


def choose_index(seed: str | int, weights: Iterable[float], *parts: object) -> int:
    vals = list(weights)
    total = sum(vals)
    if total <= 0:
        return 0
    r = uniform01(seed, *parts) * total
    acc = 0.0
    for i, w in enumerate(vals):
        acc += w
        if r <= acc:
            return i
    return len(vals) - 1


def run_hash(seed: str | int, run_id: int, payload: bytes) -> str:
    h = hashlib.sha256()
    h.update(b"aleph-run-hash-v2\x00")
    _update_field(h, seed)
    h.update(struct.pack(">Q", run_id & 0xFFFFFFFFFFFFFFFF))
    _update_field(h, payload)
    return h.hexdigest()
