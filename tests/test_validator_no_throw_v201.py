from __future__ import annotations

import copy
import json
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.validator import validate_actors, validate_branches, validate_manifest_core  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"
BAD_VALUES: tuple[Any, ...] = (None, 1, True, "x", {}, [])


def _load(relative: str) -> Any:
    return json.loads((FIXTURE / relative).read_text(encoding="utf-8"))


def _paths(value: Any, pointer: tuple[str | int, ...] = ()) -> Iterator[tuple[str | int, ...]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = (*pointer, key)
            yield child_pointer
            yield from _paths(child, child_pointer)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_pointer = (*pointer, index)
            yield child_pointer
            yield from _paths(child, child_pointer)


def _replace(value: Any, pointer: tuple[str | int, ...], replacement: Any) -> None:
    current = value
    for part in pointer[:-1]:
        current = current[part]
    current[pointer[-1]] = replacement


class ValidatorNoThrowV201Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest: dict[str, Any] = _load("simulation-manifest.json")
        cls.actors: list[Any] = _load("actors.json")
        cls.branches: dict[str, Any] = _load("branch-ledger.json")
        cls.nodes: list[Any] = _load("nodes.json")

    def _assert_mutations_do_not_throw(self, value: Any, validator: Any) -> None:
        for pointer in list(_paths(value)):
            for bad in BAD_VALUES:
                candidate = copy.deepcopy(value)
                _replace(candidate, pointer, copy.deepcopy(bad))
                with self.subTest(pointer=pointer, bad_type=type(bad).__name__):
                    validator(candidate)

    def test_manifest_scalar_and_container_mutations_never_throw(self) -> None:
        self._assert_mutations_do_not_throw(
            self.manifest,
            lambda value: validate_manifest_core(value, "final"),
        )

    def test_actor_scalar_and_container_mutations_never_throw(self) -> None:
        self._assert_mutations_do_not_throw(
            self.actors,
            lambda value: validate_actors(
                value,
                {"factor:policy-rate", "factor:output-gap", "entity:central-bank", "context:baseline"},
                {"evidence:policy-statute", "evidence:macro-series"},
                self.nodes,
                self.manifest,
            ),
        )

    def test_branch_scalar_and_container_mutations_never_throw(self) -> None:
        self._assert_mutations_do_not_throw(
            self.branches,
            lambda value: validate_branches(
                value,
                {"causal:rate-to-gap"},
                {"actor:governor"},
                {"evidence:policy-statute", "evidence:macro-series"},
                self.manifest,
                {"factor:policy-rate", "factor:output-gap", "entity:central-bank", "context:baseline"},
            ),
        )

    def test_known_malformed_nested_values_return_typed_failures(self) -> None:
        actors = copy.deepcopy(self.actors)
        actors[0]["decision_graph"] = {"allowed_actions": 1}
        actor_result, _ = validate_actors(
            actors,
            {"factor:policy-rate", "factor:output-gap", "entity:central-bank", "context:baseline"},
            {"evidence:policy-statute", "evidence:macro-series"},
            self.nodes,
            self.manifest,
        )
        self.assertIn("TYPE", {item.code for item in actor_result.issues})

        branches = copy.deepcopy(self.branches)
        branches["branches"][1]["end_state"] = "malformed"
        branch_result = validate_branches(
            branches,
            {"causal:rate-to-gap"},
            {"actor:governor"},
            {"evidence:policy-statute", "evidence:macro-series"},
            self.manifest,
            {"factor:policy-rate", "factor:output-gap", "entity:central-bank", "context:baseline"},
        )
        self.assertIn("TYPE", {item.code for item in branch_result.issues})


if __name__ == "__main__":
    unittest.main()
