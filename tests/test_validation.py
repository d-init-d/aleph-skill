from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _lib import load_json, write_json  # noqa: E402
from evaluate_simulation_quality import evaluate  # noqa: E402
from validate_simulation_artifacts import validate_workspace  # noqa: E402


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="aleph-skill-test-")
        self.workspace = Path(self.temp.name)
        templates = ROOT / "templates"
        write_json(self.workspace / "simulation-manifest.json", load_json(templates / "simulation-manifest.json"))
        write_json(self.workspace / "nodes.json", [load_json(templates / "timeline-node.json")])
        write_json(self.workspace / "edges.json", [load_json(templates / "causal-edge.json")])
        write_json(self.workspace / "actors.json", [load_json(templates / "actor-dossier.json")])
        write_json(self.workspace / "branch-ledger.json", load_json(templates / "branch-ledger.json"))
        for name in ["evidence-map.csv", "propagation-trace.jsonl", "human-track-ledger.jsonl"]:
            shutil.copyfile(templates / name, self.workspace / name)
        (self.workspace / "REPORT.md").write_text(
            "# Report\n\nChange point, evidence, propagation, branch, human, validation, warning.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self) -> dict[str, object]:
        return validate_workspace(self.workspace, mode="final", require_report=True)

    def test_valid_fixture_passes(self) -> None:
        self.assertEqual(self.validate()["status"], "pass")

    def test_valid_fixture_scores_excellent(self) -> None:
        result = evaluate(self.workspace)
        self.assertEqual(result["grade"], "excellent")
        self.assertGreaterEqual(result["score"], 90)

    def test_unknown_edge_reference_fails(self) -> None:
        edges = load_json(self.workspace / "edges.json")
        edges[0]["from"] = "factor:missing"
        write_json(self.workspace / "edges.json", edges)
        result = self.validate()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("UNKNOWN_REF" in error for error in result["errors"]))

    def test_roleplay_cannot_be_evidence(self) -> None:
        actors = load_json(self.workspace / "actors.json")
        actors[0]["roleplay_track"]["hypotheses"][0]["evidence_ids"] = ["evidence:example"]
        write_json(self.workspace / "actors.json", actors)
        result = self.validate()
        self.assertTrue(any("ROLEPLAY_EVIDENCE" in error for error in result["errors"]))

    def test_available_subagents_require_distinct_subagent_tracks(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["execution"]["subagents"] = {
            "status": "available",
            "tool": "task",
            "detection_method": "runtime tool inventory",
            "fallback_reason": "",
        }
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("SUBAGENT_REQUIRED" in error for error in result["errors"]))

    def test_human_track_ledger_must_match_actor_dossier(self) -> None:
        ledger_path = self.workspace / "human-track-ledger.jsonl"
        rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
        rows[0]["agent_ref"] = "different-agent"
        ledger_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        result = self.validate()
        self.assertTrue(any("TRACK_MISMATCH" in error for error in result["errors"]))

    def test_search_snippet_confidence_is_capped(self) -> None:
        path = self.workspace / "evidence-map.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        rows[0]["retrieval_status"] = "search-snippet"
        rows[0]["confidence"] = "0.80"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        result = self.validate()
        self.assertTrue(any("SNIPPET_CONFIDENCE" in error for error in result["errors"]))

    def test_search_snippets_cannot_dominate_ledger(self) -> None:
        path = self.workspace / "evidence-map.csv"
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        second = dict(rows[0])
        second["evidence_id"] = "evidence:snippet"
        second["retrieval_status"] = "search-snippet"
        second["confidence"] = "0.40"
        third = dict(second)
        third["evidence_id"] = "evidence:snippet-2"
        rows.extend([second, third])
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        result = self.validate()
        self.assertTrue(any("DIRECT_ACCESS" in error for error in result["errors"]))

    def test_branch_cap_is_a_hard_error(self) -> None:
        ledger = load_json(self.workspace / "branch-ledger.json")
        ledger["branches"][0]["probability"] = 0.70
        ledger["branches"][1]["probability"] = 0.20
        ledger["branches"][2]["probability"] = 0.10
        write_json(self.workspace / "branch-ledger.json", ledger)
        result = self.validate()
        self.assertTrue(any("BRANCH_CAP" in error for error in result["errors"]))

    def test_profile_budget_cannot_be_expanded(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["execution"]["profile"] = "quick"
        manifest["execution"]["research_budget"]["max_sources"] = 25
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("PROFILE_BUDGET" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
