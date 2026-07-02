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
from init_simulation_workspace import infer_timeline_mode, parse_date  # noqa: E402
from render_simulation_report import render  # noqa: E402
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
            "# Report\n\n"
            "## Executive summary\n\n"
            "## Methodology and scope\n\n"
            "## Baseline and change point\n\n"
            "## Evidence and source quality\n\n"
            "## Causal architecture and propagation\n\n"
            "## Scenario branches\n\n"
            "## Future monitoring and probability updates\n\n"
            "## Human decision tracks\n\n"
            "## Sensitivity, contradictions, and limitations\n\n"
            "## Validation and audit\n\n"
            "## Source appendix\n\n"
            "## Warnings and next steps\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self, mode: str = "final", require_report: bool = True) -> dict[str, object]:
        return validate_workspace(self.workspace, mode=mode, require_report=require_report)

    def test_valid_fixture_passes(self) -> None:
        self.assertEqual(self.validate()["status"], "pass")

    def test_valid_fixture_scores_excellent(self) -> None:
        result = evaluate(self.workspace)
        self.assertEqual(result["grade"], "excellent")
        self.assertGreaterEqual(result["score"], 90)

    def test_renderer_never_presents_stale_validation_as_final(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["status"] = "draft"
        manifest["execution"]["research_control"]["saturation_reached"] = False
        write_json(self.workspace / "simulation-manifest.json", manifest)
        write_json(
            self.workspace / "validation-report.json",
            {"mode": "final", "status": "pass", "warnings": [], "errors": []},
        )
        report = render(self.workspace)
        self.assertIn("draft-not-ready", report)
        self.assertIn("## Future monitoring and probability updates", report)

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

    def test_adaptive_complexity_must_match_dimensions(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["execution"]["adaptive_scope"]["overall_complexity"] = 0.90
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("ADAPTIVE_SCOPE" in error for error in result["errors"]))

    def test_legacy_execution_profiles_are_rejected(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["execution"]["profile"] = "deep"
        manifest["execution"]["research_budget"] = {"max_sources": 100}
        manifest["execution"]["research_control"]["time_limit"] = "P1D"
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("LEGACY_EXECUTION_CONTROL" in error for error in result["errors"]))

    def test_research_quality_aliases_are_rejected(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["execution"]["research_quality"] = "standard"
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("RESEARCH_QUALITY" in error or "ENUM" in error for error in result["errors"]))

    def test_high_complexity_rejects_shallow_execution(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        adaptive = manifest["execution"]["adaptive_scope"]
        adaptive["overall_complexity"] = 1.0
        adaptive["dimensions"] = {key: 1.0 for key in adaptive["dimensions"]}
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("ADAPTIVE_DEPTH" in error for error in result["errors"]))
        self.assertTrue(any("SOURCE_QUALITY" in error for error in result["errors"]))
        self.assertTrue(any("BRANCH_COUNT" in error for error in result["errors"]))
        self.assertTrue(any("FUTURE_MONITORING" in error for error in result["errors"]))

    def test_completed_run_requires_evidence_saturation(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["execution"]["research_control"]["saturation_reached"] = False
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("EVIDENCE_SATURATION" in error for error in result["errors"]))

    def test_draft_allows_research_to_be_in_progress(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["status"] = "draft"
        manifest["execution"]["research_control"]["sources_examined"] = 0
        manifest["execution"]["research_control"]["saturation_reached"] = False
        manifest["execution"]["research_control"]["stop_reason"] = ""
        manifest["temporal_frame"]["monitoring_indicators"] = []
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate(mode="draft", require_report=False)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(any("RESEARCH_CONTROL" in warning for warning in result["warnings"]))

    def test_future_nodes_cannot_be_facts(self) -> None:
        nodes = load_json(self.workspace / "nodes.json")
        nodes[0]["time"] = "2027-01-01"
        nodes[0]["status"] = "fact"
        nodes[0]["timeline"] = "simulated_branch"
        write_json(self.workspace / "nodes.json", nodes)
        result = self.validate()
        self.assertTrue(any("FUTURE_FACT" in error for error in result["errors"]))

    def test_future_branches_require_monitoring_conditions(self) -> None:
        ledger = load_json(self.workspace / "branch-ledger.json")
        ledger["branches"][0]["leading_indicators"] = []
        ledger["branches"][0]["disconfirming_conditions"] = []
        write_json(self.workspace / "branch-ledger.json", ledger)
        result = self.validate()
        self.assertTrue(any("FUTURE_MONITORING" in error for error in result["errors"]))

    def test_timeline_mode_must_match_dates(self) -> None:
        manifest = load_json(self.workspace / "simulation-manifest.json")
        manifest["temporal_frame"]["mode"] = "retrospective_counterfactual"
        write_json(self.workspace / "simulation-manifest.json", manifest)
        result = self.validate()
        self.assertTrue(any("TIMELINE_MODE" in error for error in result["errors"]))

    def test_timeline_mode_inference_covers_all_directions(self) -> None:
        self.assertEqual(
            infer_timeline_mode(parse_date("2000-01-01"), parse_date("2026-01-01"), parse_date("2010-01-01")),
            "retrospective_counterfactual",
        )
        self.assertEqual(
            infer_timeline_mode(parse_date("2026-01-01"), parse_date("2026-01-01"), parse_date("2028-01-01")),
            "prospective_intervention",
        )
        self.assertEqual(
            infer_timeline_mode(parse_date("2000-01-01"), parse_date("2026-01-01"), parse_date("2030-01-01")),
            "hybrid_projection",
        )


if __name__ == "__main__":
    unittest.main()
