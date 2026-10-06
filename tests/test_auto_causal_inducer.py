"""Unit tests for Auto-Causal Induction pipeline with bilingual English and Vietnamese fixtures."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aleph.auto_causal import apply_model, propose_model, review_model  # noqa: E402


class TestAutoCausalInducer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_english_grounded_causal_relationship(self) -> None:
        evidence = [
            {
                "claim_id": "claim:fed-rate-output",
                "record_type": "claim",
                "claim": "Higher Policy Rate reduces Output Gap via credit contraction channel",
                "evidence": "A policy rate hike reduces output gap through investment suppression by 0.4 with a lag of 2 ticks.",
                "confidence": "high",
            }
        ]
        proposal = propose_model(evidence)
        self.assertEqual(proposal["schema_version"], "1.0.0")

        # Validate proposal schema
        proposal_schema_path = ROOT / "schemas" / "causal-proposal.schema.json"
        if proposal_schema_path.exists():
            with open(proposal_schema_path) as f:
                schema = json.load(f)
            try:
                import jsonschema
                jsonschema.validate(instance=proposal, schema=schema)
            except ImportError:
                pass

        self.assertEqual(len(proposal["candidate_edges"]), 1)
        edge = proposal["candidate_edges"][0]
        self.assertEqual(edge["sign"], -1)
        self.assertEqual(edge["status"], "proposed")
        self.assertEqual(edge["mechanism"], "credit contraction channel")
        self.assertEqual(edge["lag_ticks"], 2)
        self.assertAlmostEqual(edge["effect_size"], 0.4)
        self.assertAlmostEqual(edge["evidence_confidence"], 0.85)

        # Review
        review = review_model(proposal)
        self.assertEqual(review["overall_status"], "partial")
        self.assertEqual(review["edge_decisions"][edge["id"]]["decision"], "admit")

        # Validate review schema
        review_schema_path = ROOT / "schemas" / "causal-review.schema.json"
        if review_schema_path.exists():
            with open(review_schema_path) as f:
                r_schema = json.load(f)
            try:
                import jsonschema
                jsonschema.validate(instance=review, schema=r_schema)
            except ImportError:
                pass

        # Apply
        target_ws = self.tmp_path / "ws_en"
        res = apply_model(proposal, review, target_ws)
        self.assertTrue(res["ok"])
        self.assertEqual(res["admitted_edge_count"], 1)
        self.assertFalse(res["compilation_ok"])
        self.assertFalse(res["simulation_run_ok"])


    def test_vietnamese_grounded_causal_relationship(self) -> None:
        evidence = [
            {
                "claim_id": "claim:vn-lai-suat-lam-phat",
                "record_type": "claim",
                "claim": "Lãi suất điều hành làm giảm Lạm phát thông qua kênh tín dụng ngân hàng",
                "evidence": "Nâng lãi suất điều hành làm giảm lạm phát với độ trễ 3 tick mức 0.25",
                "confidence": "high",
            }
        ]
        proposal = propose_model(evidence)
        self.assertEqual(len(proposal["candidate_edges"]), 1)
        edge = proposal["candidate_edges"][0]
        self.assertEqual(edge["sign"], -1)
        self.assertEqual(edge["status"], "proposed")
        self.assertEqual(edge["mechanism"], "kênh tín dụng ngân hàng")
        self.assertEqual(edge["lag_ticks"], 3)
        self.assertAlmostEqual(edge["effect_size"], 0.25)

        review = review_model(proposal)
        self.assertEqual(review["overall_status"], "partial")

        target_ws = self.tmp_path / "ws_vn"
        res = apply_model(proposal, review, target_ws)
        self.assertTrue(res["ok"])
        self.assertEqual(res["admitted_edge_count"], 1)
        self.assertFalse(res["compilation_ok"])

    def test_correlation_not_causation_flagged(self) -> None:
        evidence = [
            {
                "claim_id": "claim:ice-cream-drowning",
                "record_type": "claim",
                "claim": "Ice cream consumption is correlated with drowning rates",
                "evidence": "Statistical correlation observed without causal mechanism.",
                "confidence": "medium",
            }
        ]
        proposal = propose_model(evidence)
        edge = proposal["candidate_edges"][0]
        self.assertEqual(edge["status"], "incomplete")
        self.assertTrue(any(g["gap_type"] == "correlation_not_causation" for g in proposal["gaps"]))

        review = review_model(proposal)
        self.assertEqual(review["overall_status"], "rejected")
        self.assertEqual(review["edge_decisions"][edge["id"]]["decision"], "reject")

    def test_vietnamese_correlation_flagged(self) -> None:
        evidence = [
            {
                "claim_id": "claim:vn-tuong-quan",
                "record_type": "claim",
                "claim": "Số lượng xe máy tương quan với doanh số bán ô tô",
                "evidence": "Chỉ là tương quan thống kê không có tác động trực tiếp.",
                "confidence": "medium",
            }
        ]
        proposal = propose_model(evidence)
        edge = proposal["candidate_edges"][0]
        self.assertEqual(edge["status"], "incomplete")
        self.assertTrue(any(g["gap_type"] == "correlation_not_causation" for g in proposal["gaps"]))

    def test_missing_mechanism_and_unknown_lag_gaps(self) -> None:
        evidence = [
            {
                "claim_id": "claim:incomplete-edge",
                "record_type": "claim",
                "claim": "Factor A increases Factor B",
                "evidence": "No mechanism stated, no lag stated, no effect size stated.",
                "confidence": "low",
            }
        ]
        proposal = propose_model(evidence)
        edge = proposal["candidate_edges"][0]
        self.assertEqual(edge["status"], "incomplete")
        gap_types = [g["gap_type"] for g in proposal["gaps"]]
        self.assertIn("missing_mechanism", gap_types)
        self.assertIn("unknown_lag", gap_types)
        self.assertIn("missing_effect_size", gap_types)

        review = review_model(proposal)
        self.assertEqual(review["edge_decisions"][edge["id"]]["decision"], "incomplete")

    def test_contradiction_between_sources(self) -> None:
        evidence = [
            {
                "claim_id": "claim:tariff-gdp-pos",
                "record_type": "claim",
                "claim": "Tariffs increase Domestic GDP via protection",
                "evidence": "Protectionism boosts GDP by 0.1 with a lag of 1 tick.",
                "confidence": "medium",
            },
            {
                "claim_id": "claim:tariff-gdp-neg",
                "record_type": "claim",
                "claim": "Tariffs decrease Domestic GDP via trade disruption",
                "evidence": "Trade war reduces GDP by 0.2 with a lag of 1 tick.",
                "confidence": "medium",
            },
        ]
        proposal = propose_model(evidence)
        gap_types = [g["gap_type"] for g in proposal["gaps"]]
        self.assertIn("contradiction", gap_types)

    def test_stock_flow_scale_classification(self) -> None:
        evidence = [
            {
                "claim_id": "claim:deficit-to-debt",
                "record_type": "claim",
                "claim": "Budget Deficit increases National Debt via deficit financing",
                "evidence": "Deficit flow accumulates into debt stock by 1.0 with a lag of 1 tick.",
                "confidence": "high",
            }
        ]
        proposal = propose_model(evidence)
        nodes = {n["name"]: n for n in proposal["candidate_nodes"]}
        # Debt should be classified as stock
        self.assertIn("National Debt", nodes)
        self.assertEqual(nodes["National Debt"]["scale"], "stock")
        self.assertIn("retention", nodes["National Debt"])
        # Deficit should be flow
        self.assertIn("Budget Deficit", nodes)
        self.assertEqual(nodes["Budget Deficit"]["scale"], "flow")

    def test_cryptographic_tampering_rejected(self) -> None:
        evidence = [
            {
                "claim_id": "claim:tamper-test",
                "record_type": "claim",
                "claim": "Factor X increases Factor Y via channel Z",
                "evidence": "Impact of 0.5 with a lag of 1 tick.",
                "confidence": "high",
            }
        ]
        proposal = propose_model(evidence)
        review = review_model(proposal)

        # Tamper with proposal after review
        tampered_proposal = dict(proposal)
        tampered_proposal["candidate_edges"] = []

        with self.assertRaises(ValueError):
            apply_model(tampered_proposal, review, self.tmp_path / "ws_tampered")


if __name__ == "__main__":
    unittest.main()
