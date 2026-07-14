"""Aleph 2.0 core library — stdlib-first causal simulation toolkit."""

from __future__ import annotations

SCHEMA_VERSION = "2.0.0"
LEGACY_SCHEMA_VERSION = "1.2.0"
PACKAGE_VERSION = "2.0.1"
VALIDATOR_VERSION = "2.0.1"
FORMULA_VERSION = "2.0.0"

ASSURANCE_TIERS = ("experimental", "limited", "verified", "calibrated")
EXIT_OK = 0
EXIT_SEMANTIC = 1
EXIT_USAGE = 2
EXIT_SECURITY = 3
EXIT_NUMERICAL = 4

__all__ = [
    "SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "PACKAGE_VERSION",
    "VALIDATOR_VERSION",
    "FORMULA_VERSION",
    "ASSURANCE_TIERS",
    "EXIT_OK",
    "EXIT_SEMANTIC",
    "EXIT_USAGE",
    "EXIT_SECURITY",
    "EXIT_NUMERICAL",
]
