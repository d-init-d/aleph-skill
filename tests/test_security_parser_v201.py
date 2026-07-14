from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _lib import ArtifactLoadError, load_json  # noqa: E402
from acceptance import derive_adversarial_fixture  # noqa: E402
from aleph.discovery import _candidate_report  # noqa: E402
from aleph.io import (  # noqa: E402
    load_json_secure,
    load_json_secure_with_digest,
    stream_csv_rows,
    stream_jsonl,
)
from aleph.migrate import plan_migration  # noqa: E402
from aleph.packets import (  # noqa: E402
    _decision_actions,
    validate_knowledge_packet,
    validate_roleplay_output,
)
from aleph.packs import _json as load_pack_json  # noqa: E402
from aleph.paths import resolve_in_workspace, validate_relative_artifact_path  # noqa: E402
from release_gate import _static_contract  # noqa: E402
from render_simulation_report import read_jsonl  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def _paths(value: Any, prefix: tuple[str | int, ...] = ()) -> Iterator[tuple[str | int, ...]]:
    if isinstance(value, dict):
        for key, child in value.items():
            path = (*prefix, key)
            yield path
            yield from _paths(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = (*prefix, index)
            yield path
            yield from _paths(child, path)


def _replace(value: Any, path: tuple[str | int, ...], replacement: Any) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement


class StrictParserAndPathTests(unittest.TestCase):
    def test_windows_alternate_data_stream_syntax_is_refused_portably(self) -> None:
        issues = validate_relative_artifact_path("artifact.json:payload")
        self.assertIn("PATH_ESCAPE", {item.code for item in issues})
        self.assertTrue(any("alternate data streams" in item.message for item in issues))

        drive_issues = validate_relative_artifact_path("C:/workspace/artifact.json")
        self.assertEqual({item.code for item in drive_issues}, {"PATH_DRIVE"})

        with tempfile.TemporaryDirectory() as temporary:
            resolved, resolve_issues = resolve_in_workspace(
                Path(temporary),
                "carrier.txt:payload",
                must_exist=False,
            )
        self.assertIsNone(resolved)
        self.assertIn("PATH_ESCAPE", {item.code for item in resolve_issues})

    def test_strict_json_rejects_ambiguous_or_invalid_tokens(self) -> None:
        payloads = (
            b'{"status":"safe","status":"unsafe"}',
            b'{"value":NaN}',
            b'{"value":"\\ud800"}',
            b'{"\\ud800":"value"}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, payload in enumerate(payloads):
                with self.subTest(index=index):
                    path = root / f"invalid-{index}.json"
                    path.write_bytes(payload)
                    data, issues = load_json_secure(path)
                    self.assertIsNone(data)
                    self.assertTrue(issues)
                    self.assertIn(issues[0].code, {"INVALID_ARTIFACT", "NON_FINITE"})

    def test_strict_json_digest_covers_the_exact_parsed_bytes(self) -> None:
        raw = b'{"emoji":"\\ud83d\\ude00"}\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "valid.json"
            path.write_bytes(raw)
            data, digest, issues = load_json_secure_with_digest(path)
        self.assertEqual(issues, [])
        self.assertEqual(data, {"emoji": "\U0001f600"})
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_jsonl_duplicate_keys_and_lone_surrogates_are_refused(self) -> None:
        rows = b'{"id":"first","id":"second"}\n{"value":"\\ud800"}\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.jsonl"
            path.write_bytes(rows)
            parsed = list(stream_jsonl(path))
        self.assertEqual(len(parsed), 2)
        self.assertTrue(all(value is None for _, value, _ in parsed))
        self.assertEqual(
            [{item.code for item in issues} for _, _, issues in parsed],
            [{"INVALID_JSONL"}, {"INVALID_ARTIFACT"}],
        )

    def test_csv_blank_duplicate_and_excess_headers_fail_closed(self) -> None:
        payloads = (
            "id,id\nfirst,second\n",
            "id,\nfirst,second\n",
            "id\nfirst,second\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, payload in enumerate(payloads):
                with self.subTest(index=index):
                    path = root / f"invalid-{index}.csv"
                    path.write_text(payload, encoding="utf-8")
                    rows, issues = stream_csv_rows(path)
                    self.assertEqual(rows, [])
                    self.assertEqual({item.code for item in issues}, {"INVALID_ARTIFACT"})

    def test_shared_and_migration_loaders_reject_duplicate_keys(self) -> None:
        raw = b'{"schema_version":"1.2.0","schema_version":"2.0.0"}'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "simulation-manifest.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(ArtifactLoadError, "duplicate JSON object key"):
                load_json(path)
            plan = plan_migration(root)
        self.assertFalse(plan["ok"])
        self.assertIn("duplicate JSON object key", plan["error"])

    def test_secondary_entry_points_share_the_strict_parser(self) -> None:
        duplicate = '{"safe":true,"safe":false}\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            pack_path = root / "pack.json"
            pack_path.write_text(duplicate, encoding="utf-8")
            pack_issues = []
            self.assertIsNone(load_pack_json(pack_path, pack_issues))
            self.assertIn("INVALID_ARTIFACT", {value.code for value in pack_issues})

            trace_path = root / "trace.jsonl"
            trace_path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
                read_jsonl(trace_path)

            research = root / "d-research"
            (research / "scripts").mkdir(parents=True)
            (research / "SKILL.md").write_text(
                "---\nname: d-research\n---\n", encoding="utf-8"
            )
            (research / "package.json").write_text(
                '{"name":"d-research","version":"3.0.0","version":"4.0.0"}\n',
                encoding="utf-8",
            )
            (research / "scripts" / "evidence_ledger.py").write_text("", encoding="utf-8")
            discovery = _candidate_report("test", research)
            self.assertFalse(discovery["ok"])
            self.assertIn("duplicate JSON object key", discovery["reason"])

    def test_acceptance_and_release_contract_reject_duplicate_manifest_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            shutil.copytree(FIXTURE, source)
            manifest_path = source / "simulation-manifest.json"
            original = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(
                original.replace("{", '{"schema_version":"forged",', 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ArtifactLoadError, "duplicate JSON object key"):
                derive_adversarial_fixture(source, root / "derived")

            contract = root / "contract"
            (contract / "scripts").mkdir(parents=True)
            (contract / "tests").mkdir()
            (contract / "package.json").write_text(
                '{"version":"2.0.1","version":"forged"}\n', encoding="utf-8"
            )
            (contract / "package-lock.json").write_text("{}\n", encoding="utf-8")
            (contract / "pyproject.toml").write_text(
                'version = "2.0.1"\n', encoding="utf-8"
            )
            (contract / "uv.lock").write_text(
                '[[package]]\nname = "aleph-skill"\nversion = "2.0.1"\n',
                encoding="utf-8",
            )
            result = _static_contract(contract)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("duplicate JSON object key" in value for value in result["issues"])
            )


class PacketNoThrowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packet = json.loads((FIXTURE / "packet-governor.json").read_text(encoding="utf-8"))
        cls.output = json.loads((FIXTURE / "roleplay-governor.json").read_text(encoding="utf-8"))

    def test_packet_and_output_value_mutations_never_raise(self) -> None:
        invalid_values = (None, 1, True, "x", {}, [], set(), chr(0xD800))
        for path in list(_paths(self.packet)):
            for invalid in invalid_values:
                with self.subTest(artifact="packet", path=path, invalid=type(invalid).__name__):
                    candidate = copy.deepcopy(self.packet)
                    _replace(candidate, path, copy.deepcopy(invalid))
                    self.assertIsInstance(validate_knowledge_packet(candidate), list)
                    self.assertIsInstance(validate_roleplay_output(self.output, candidate), dict)
        for path in list(_paths(self.output)):
            for invalid in invalid_values:
                with self.subTest(artifact="output", path=path, invalid=type(invalid).__name__):
                    candidate = copy.deepcopy(self.output)
                    _replace(candidate, path, copy.deepcopy(invalid))
                    self.assertIsInstance(validate_roleplay_output(candidate, self.packet), dict)

    def test_malformed_decision_graph_is_not_iterated(self) -> None:
        for value in (None, 1, True, "approve", {}, set()):
            with self.subTest(value=type(value).__name__):
                actor = {"decision_graph": {"allowed_actions": value}}
                self.assertEqual(_decision_actions(actor), [])


if __name__ == "__main__":
    unittest.main()
