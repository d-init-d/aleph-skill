from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aleph.schema import ADJUDICATION_RESULT_FIELDS, RESEARCH_CONTROL_FIELDS  # noqa: E402
from aleph.validator import validate_actors, validate_branches, validate_manifest_core  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "schema-2.0-valid"


def load_json(relative: str) -> Any:
    return json.loads((FIXTURE / relative).read_text(encoding="utf-8"))


class LikelihoodContractV201Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest: dict[str, Any] = load_json("simulation-manifest.json")
        self.branches: dict[str, Any] = load_json("branch-ledger.json")
        self.actors: list[Any] = load_json("actors.json")
        self.nodes: list[Any] = load_json("nodes.json")

    def validate_branch_ledger(
        self,
        ledger: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> set[str]:
        result = validate_branches(
            ledger or self.branches,
            {"causal:rate-to-gap"},
            {"actor:governor"},
            {"evidence:macro-series"},
            manifest or self.manifest,
            {"factor:policy-rate", "factor:output-gap", "entity:central-bank", "context:baseline"},
        )
        return {item.code for item in result.issues if item.severity == "error"}

    def validate_actor_dossiers(
        self,
        actors: list[Any] | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> set[str]:
        result, _ = validate_actors(
            actors or self.actors,
            {"factor:policy-rate", "factor:output-gap", "entity:central-bank", "context:baseline"},
            {"evidence:policy-statute", "evidence:macro-series"},
            self.nodes,
            manifest or self.manifest,
        )
        return {item.code for item in result.issues if item.severity == "error"}

    def test_relative_weight_mode_requires_every_weight_and_normalized_mass(self) -> None:
        self.assertEqual(self.validate_branch_ledger(), set())
        missing = copy.deepcopy(self.branches)
        missing["branches"][0].pop("relative_weight")
        self.assertTrue({"MISSING_FIELD", "RELATIVE_WEIGHT_ONLY"} <= self.validate_branch_ledger(missing))

        bad_sum = copy.deepcopy(self.branches)
        bad_sum["branches"][0]["relative_weight"] = 0.40
        self.assertIn("RELATIVE_WEIGHT_ONLY", self.validate_branch_ledger(bad_sum))

    def test_calibrated_mode_requires_probabilities_metadata_and_no_mixing(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["likelihood_mode"] = "calibrated_probability"
        manifest["artifact_paths"]["calibration_report"] = "calibration-report.json"
        ledger = copy.deepcopy(self.branches)
        ledger.update(
            {
                "likelihood_mode": "calibrated_probability",
                "calibrated": True,
                "calibration": {
                    "method": "hindcast-calibration",
                    "sample_count": 1000,
                    "interval": [0.05, 0.95],
                    "calibration_policy_ref": "policy:calibration-v1",
                    "model_version": "model-v1",
                    "model_hash": "a" * 64,
                    "hindcast_report_ref": "hindcast-report.json",
                },
            }
        )
        ledger["branches"][0].pop("relative_weight")
        ledger["branches"][0]["probability"] = 0.6
        ledger["branches"][1].pop("relative_weight")
        ledger["branches"][1]["probability"] = 0.4
        ledger["branches"][2].pop("relative_weight")
        self.assertEqual(self.validate_branch_ledger(ledger, manifest), set())

        missing_probability = copy.deepcopy(ledger)
        missing_probability["branches"][0].pop("probability")
        self.assertTrue(
            {"MISSING_FIELD", "PROBABILITY_SUM"}
            <= self.validate_branch_ledger(missing_probability, manifest)
        )
        mixed = copy.deepcopy(ledger)
        mixed["branches"][1]["relative_weight"] = 0.4
        self.assertIn("RELATIVE_WEIGHT_ONLY", self.validate_branch_ledger(mixed, manifest))

    def test_deterministic_mode_forbids_probability_and_alternatives(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["likelihood_mode"] = "deterministic"
        ledger = copy.deepcopy(self.branches)
        ledger["likelihood_mode"] = "deterministic"
        ledger["calibrated"] = False
        ledger["branches"] = [ledger["branches"][0]]
        ledger["branches"][0].pop("relative_weight")
        self.assertEqual(self.validate_branch_ledger(ledger, manifest), set())

        ledger["branches"][0]["probability"] = 1.0
        self.assertIn("PROBABILITY_UNCALIBRATED", self.validate_branch_ledger(ledger, manifest))

    def test_adjudication_cannot_self_authorize_calibration(self) -> None:
        self.assertEqual(self.validate_actor_dossiers(), set())
        actors = copy.deepcopy(self.actors)
        adjudication = actors[0]["adjudication"]
        adjudication["calibrated"] = True
        adjudication["results"][0]["probability"] = 0.55
        codes = self.validate_actor_dossiers(actors)
        self.assertTrue({"TRACK_MISMATCH", "PROBABILITY_UNCALIBRATED"} <= codes)

    def test_predicted_response_must_match_adjudication(self) -> None:
        actors = copy.deepcopy(self.actors)
        actors[0]["predicted_responses"][0]["relative_weight"] = 0.6
        self.assertIn("TRACK_MISMATCH", self.validate_actor_dossiers(actors))

        different_action_set = copy.deepcopy(self.actors)
        different_action_set[0]["adjudication"]["results"] = [
            {"action": "hike_25bp", "relative_weight": 1.0, "probability": None}
        ]
        different_action_set[0]["predicted_responses"] = [
            {"action": "hold", "relative_weight": 1.0, "status": "simulation"}
        ]
        self.assertIn("TRACK_MISMATCH", self.validate_actor_dossiers(different_action_set))

    def test_nested_private_actor_data_is_rejected_before_roleplay(self) -> None:
        actors = copy.deepcopy(self.actors)
        actors[0]["biographical_foundation"] = {"personal_email": "private@example.com"}
        self.assertIn("PRIVACY_REFUSAL", self.validate_actor_dossiers(actors))

        diagnosis = copy.deepcopy(self.actors)
        diagnosis[0]["biographical_foundation"] = {"diagnosis": "bipolar disorder"}
        self.assertIn("PRIVACY_REFUSAL", self.validate_actor_dossiers(diagnosis))

    def test_adjudication_hypothesis_references_are_resolved_and_disjoint(self) -> None:
        actors = copy.deepcopy(self.actors)
        actors[0]["adjudication"]["accepted_hypotheses"] = ["hypothesis:missing"]
        self.assertIn("UNKNOWN_REF", self.validate_actor_dossiers(actors))

        overlap = copy.deepcopy(self.actors)
        overlap[0]["adjudication"]["accepted_hypotheses"] = ["hypothesis:hike"]
        overlap[0]["adjudication"]["rejected_hypotheses"] = ["hypothesis:hike"]
        self.assertIn("TRACK_MISMATCH", self.validate_actor_dossiers(overlap))

    def test_d_research_states_and_host_limit_queue_are_semantically_coupled(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["execution"]["d_research"] = {"status": "available", "invoked": True}
        codes = {item.code for item in validate_manifest_core(manifest, "final").issues}
        self.assertTrue({"D_RESEARCH", "LEDGER_MAJOR"} <= codes)

        control = manifest["execution"]["research_control"]
        manifest["status"] = "draft"
        manifest["execution"]["research_quality"] = "unknown"
        manifest["execution"]["d_research"] = {"status": "unknown", "invoked": False}
        control.update(
            {
                "saturation_reached": False,
                "stop_reason": "host_limit:context-window",
                "unresolved_critical_gaps": ["Confirm the primary-source chronology."],
                "next_wave_queue": ["Open and verify the queued primary source."],
            }
        )
        codes = {item.code for item in validate_manifest_core(manifest, "draft").issues}
        self.assertNotIn("EVIDENCE_SATURATION", codes)
        control["next_wave_queue"] = []
        codes = {item.code for item in validate_manifest_core(manifest, "draft").issues}
        self.assertIn("EVIDENCE_SATURATION", codes)

    def test_shipped_json_schemas_expose_the_same_new_nested_fields(self) -> None:
        manifest_schema = json.loads(
            (ROOT / "schemas" / "simulation-manifest.schema.json").read_text(encoding="utf-8")
        )
        control_properties = manifest_schema["properties"]["execution"]["properties"][
            "research_control"
        ]["properties"]
        self.assertEqual(set(control_properties), set(RESEARCH_CONTROL_FIELDS))
        self.assertEqual(control_properties["next_wave_queue"]["items"]["type"], "string")
        execution_schema = manifest_schema["properties"]["execution"]["properties"]
        self.assertEqual(
            execution_schema["adaptive_scope"]["properties"]["assessed"]["type"],
            "boolean",
        )
        self.assertIn("unknown", execution_schema["research_quality"]["enum"])
        self.assertTrue(manifest_schema["allOf"], "completed-manifest constraints must remain explicit")

        actor_schema = json.loads(
            (ROOT / "schemas" / "actor-dossier.schema.json").read_text(encoding="utf-8")
        )
        result_properties = actor_schema["$defs"]["adjudication_result"]["properties"]
        self.assertEqual(set(result_properties), set(ADJUDICATION_RESULT_FIELDS))


if __name__ == "__main__":
    unittest.main()
