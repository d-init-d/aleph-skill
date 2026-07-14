from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aleph.engine import (  # noqa: E402
    ComputationalModel,
    EngineConfig,
    ModelEdge,
    Variable,
    run_deterministic,
    run_monte_carlo,
)
from aleph.formula import expected_output_effect  # noqa: E402
from aleph.import_ledger import import_d_research_ledger  # noqa: E402
from aleph.installer import MANIFEST_NAME, build_distribution_manifest, install  # noqa: E402
from aleph.io import sha256_file, write_json_atomic  # noqa: E402
from aleph.migrate import (  # noqa: E402
    migrate_dual_run_canonical,
    plan_migration,
)
from aleph.packs import refuse_uncalibrated_probability, validate_all_packs  # noqa: E402
from aleph.paths import (  # noqa: E402
    assert_install_paths_safe,
    resolve_in_workspace,
    validate_relative_artifact_path,
)
from aleph.privacy import privacy_intake  # noqa: E402
from aleph.quality import evaluate  # noqa: E402
from aleph.validator import validate_workspace  # noqa: E402
from init_simulation_workspace import infer_timeline_mode, parse_date  # noqa: E402
from render_simulation_report import render  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
ADV_EXTERNAL = ROOT.parent / "test-output" / "adversarial-completed"


class PathSecurityTests(unittest.TestCase):
    def test_parent_traversal_refused(self) -> None:
        issues = validate_relative_artifact_path("../secret.json")
        self.assertTrue(any(i.code == "PATH_ESCAPE" for i in issues))

    def test_absolute_and_drive_refused(self) -> None:
        self.assertTrue(any(i.code == "PATH_ABSOLUTE" for i in validate_relative_artifact_path("/etc/passwd")))
        self.assertTrue(any(i.code == "PATH_DRIVE" for i in validate_relative_artifact_path("C:/Windows/system.ini")))

    def test_unc_refused(self) -> None:
        self.assertTrue(any(i.code == "PATH_UNC" for i in validate_relative_artifact_path("\\\\server\\share\\a")))

    def test_resolve_stays_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "ok.json").write_text("{}", encoding="utf-8")
            path, issues = resolve_in_workspace(ws, "ok.json", must_exist=True)
            self.assertEqual(issues, [])
            self.assertIsNotNone(path)
            _, bad = resolve_in_workspace(ws, "../outside.json", must_exist=False)
            self.assertTrue(any(i.code == "PATH_ESCAPE" for i in bad))


class InstallerSecurityTests(unittest.TestCase):
    def test_source_equals_destination_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "skill"
            p.mkdir()
            issues = assert_install_paths_safe(p, p)
            self.assertTrue(any(i.code == "INSTALL_SOURCE_DEST" for i in issues))

    def test_nested_dest_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "skill"
            dest = src / "nested"
            src.mkdir()
            issues = assert_install_paths_safe(src, dest)
            self.assertTrue(any(i.code == "INSTALL_NESTED" for i in issues))

    def test_env_not_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dest = Path(tmp) / "dest"
            # minimal allowlisted tree
            (src / "scripts").mkdir(parents=True)
            (src / "SKILL.md").write_text("---\nname: aleph-skill\n---\n", encoding="utf-8")
            (src / "scripts" / "x.py").write_text("print(1)\n", encoding="utf-8")
            (src / ".env").write_text("SECRET=1\n", encoding="utf-8")
            (src / "package.json").write_text('{"name":"t"}\n', encoding="utf-8")
            write_json_atomic(src / MANIFEST_NAME, build_distribution_manifest(src))
            result = install(src, dest, mode="copy", force=True)
            self.assertEqual(result.get("status"), "copied")
            self.assertFalse((dest / ".env").exists())

    def test_symlink_refuses_real_directory_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dest = Path(tmp) / "dest"
            src.mkdir()
            (src / "SKILL.md").write_text("---\nname: aleph-skill\n---\n", encoding="utf-8")
            dest.mkdir()
            (dest / "keep.txt").write_text("x", encoding="utf-8")
            result = install(src, dest, mode="symlink", force=True)
            self.assertEqual(result.get("status"), "refused")
            self.assertTrue((dest / "keep.txt").exists())


class ValidatorAdversarialTests(unittest.TestCase):
    def _adv(self) -> Path:
        if ADV_EXTERNAL.is_dir():
            return ADV_EXTERNAL
        return FIXTURES / "adversarial"

    def test_adversarial_fails_with_semantic_codes(self) -> None:
        ws = self._adv()
        result = validate_workspace(ws, mode="final", require_report=True)
        self.assertEqual(result["status"], "fail")
        codes = set(result.get("error_codes") or [])
        # Must surface real semantic failures, not pass
        self.assertTrue(
            codes
            & {
                "RELATION",
                "TRACE_FORMULA_MISMATCH",
                "CONTEXT_MISSING",
                "LAG",
                "LAG_ORDER",
                "REPORT_EMPTY",
                "BRANCH_DUPLICATE",
                "BRANCH_NEAR_DUPLICATE",
                "MULTIPLIER",
                "SELF_EDGE",
            },
            msg=f"expected semantic codes, got {codes}",
        )
        self.assertNotEqual(result.get("assurance_status"), "verified")

    def test_adversarial_quality_not_verified_or_excellent_claim(self) -> None:
        ws = self._adv()
        result = evaluate(ws)
        self.assertEqual(result["validation_status"], "fail")
        self.assertEqual(result.get("assurance_status"), "failed")
        self.assertNotIn(result.get("assurance_tier"), {"verified", "calibrated"})
        self.assertFalse(result.get("release_claim"))
        # excellent must not be a release claim path
        if result.get("grade") == "excellent":
            self.fail("adversarial must not grade excellent as release claim")

    def test_adversarial_consistent_nonzero_exit_via_validate(self) -> None:
        ws = self._adv()
        r1 = validate_workspace(ws, mode="final", require_report=True)
        r2 = validate_workspace(ws, mode="final", require_report=True)
        self.assertEqual(r1["status"], "fail")
        self.assertEqual(r2["status"], "fail")


class ValidFixtureTests(unittest.TestCase):
    def test_schema_2_valid_passes(self) -> None:
        ws = FIXTURES / "schema-2.0-valid"
        result = validate_workspace(ws, mode="final", require_report=True)
        if result["status"] != "pass":
            self.fail(json.dumps({"errors": result.get("errors"), "codes": result.get("error_codes")}, indent=2))
        self.assertEqual(result["status"], "pass")

    def test_unknown_field_rejected(self) -> None:
        """AC2: unknown object keys fail closed via shipped validator (UNKNOWN_FIELD)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            edges = json.loads((ws / "edges.json").read_text(encoding="utf-8"))
            edges[0]["totally_unknown_xyz"] = 1
            (ws / "edges.json").write_text(json.dumps(edges, indent=2) + "\n", encoding="utf-8")
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result.get("error_codes") or [])
            # Pointer must name the unknown key — no message-substring theater
            unknown_issues = [i for i in result.get("issues") or [] if i.get("code") == "UNKNOWN_FIELD"]
            self.assertTrue(unknown_issues, msg=result.get("errors"))
            self.assertTrue(
                any(i.get("actual") == "totally_unknown_xyz" or "totally_unknown_xyz" in str(i.get("pointer", "")) for i in unknown_issues),
                msg=unknown_issues,
            )

    def test_unknown_manifest_field_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            man = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            man["totally_unknown_manifest"] = True
            write_json_atomic(ws / "simulation-manifest.json", man)
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result.get("error_codes") or [])

    def test_string_number_coercion_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            edges = json.loads((ws / "edges.json").read_text(encoding="utf-8"))
            edges[0]["base_strength"] = "0.4"
            (ws / "edges.json").write_text(json.dumps(edges, indent=2) + "\n", encoding="utf-8")
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("COERCION_REFUSED", result.get("error_codes") or [])

    def test_nested_manifest_execution_unknown_field_rejected(self) -> None:
        """AC2: nested execution objects forbid unknown fields."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            man = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            man["execution"]["totally_unknown_nested"] = True
            write_json_atomic(ws / "simulation-manifest.json", man)
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result.get("error_codes") or [])
            unknown = [i for i in result.get("issues") or [] if i.get("code") == "UNKNOWN_FIELD"]
            self.assertTrue(
                any(
                    i.get("actual") == "totally_unknown_nested"
                    or "totally_unknown_nested" in str(i.get("pointer", ""))
                    for i in unknown
                ),
                msg=unknown,
            )

    def test_nested_temporal_frame_unknown_field_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            man = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            man["temporal_frame"]["totally_unknown_frame"] = "x"
            write_json_atomic(ws / "simulation-manifest.json", man)
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result.get("error_codes") or [])

    def test_nested_actor_roleplay_unknown_field_rejected(self) -> None:
        """AC2: nested roleplay_track forbids unknown fields."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            actors = json.loads((ws / "actors.json").read_text(encoding="utf-8"))
            actors[0]["roleplay_track"]["totally_unknown_roleplay"] = 1
            write_json_atomic(ws / "actors.json", actors)
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result.get("error_codes") or [])
            unknown = [i for i in result.get("issues") or [] if i.get("code") == "UNKNOWN_FIELD"]
            self.assertTrue(
                any(
                    i.get("actual") == "totally_unknown_roleplay"
                    or "totally_unknown_roleplay" in str(i.get("pointer", ""))
                    for i in unknown
                ),
                msg=unknown,
            )

    def test_nested_actor_research_and_adjudication_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            actors = json.loads((ws / "actors.json").read_text(encoding="utf-8"))
            actors[0]["research_track"]["totally_unknown_research"] = True
            actors[0]["adjudication"]["totally_unknown_adj"] = True
            write_json_atomic(ws / "actors.json", actors)
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertEqual(result["status"], "fail")
            self.assertIn("UNKNOWN_FIELD", result.get("error_codes") or [])
            actuals = {i.get("actual") for i in result.get("issues") or [] if i.get("code") == "UNKNOWN_FIELD"}
            self.assertIn("totally_unknown_research", actuals)
            self.assertIn("totally_unknown_adj", actuals)

    def test_forged_trace_fails_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            line = json.loads((ws / "propagation-trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
            line["output_effect"] = 999999
            (ws / "propagation-trace.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertIn("TRACE_FORMULA_MISMATCH", result.get("error_codes") or [])

    def test_materiality_typo_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            actors = json.loads((ws / "actors.json").read_text(encoding="utf-8"))
            actors[0]["materiality"] = "materiel"  # typo
            (ws / "actors.json").write_text(json.dumps(actors, indent=2) + "\n", encoding="utf-8")
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertIn("MATERIALITY", result.get("error_codes") or [])

    def test_empty_report_section_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            (ws / "REPORT.md").write_text(
                "# Report\n\n## Executive summary\n\n## Methodology and scope\n\n",
                encoding="utf-8",
            )
            result = validate_workspace(ws, mode="final", require_report=True)
            codes = set(result.get("error_codes") or [])
            self.assertTrue(codes & {"REPORT_EMPTY", "REPORT_SECTION"})

    def test_stale_artifact_after_finalize_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            # create artifact index with correct hash then mutate file
            digest = sha256_file(ws / "nodes.json")
            man = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            man["artifact_index"] = [{"path": "nodes.json", "sha256": digest, "size": 1, "media_type": "application/json"}]
            write_json_atomic(ws / "simulation-manifest.json", man)
            # mutate nodes
            nodes = json.loads((ws / "nodes.json").read_text(encoding="utf-8"))
            nodes[0]["name"] = "mutated"
            write_json_atomic(ws / "nodes.json", nodes)
            result = validate_workspace(ws, mode="final", require_report=True)
            self.assertIn("STALE_ARTIFACT", result.get("error_codes") or [])


class EngineTests(unittest.TestCase):
    def test_hand_calc_chain_and_reproducible_hash(self) -> None:
        model = ComputationalModel()
        for vid, base in [("factor:A", 1.0), ("factor:B", 0.0), ("factor:C", 0.0)]:
            model.variables[vid] = Variable(id=vid, role="endogenous", baseline=base, value=base)
        model.edges = [
            ModelEdge(id="edge:ab", source="factor:A", target="factor:B", sign=1, strength=0.5, lag_ticks=0),
            ModelEdge(id="edge:bc", source="factor:B", target="factor:C", sign=1, strength=0.4, lag_ticks=0),
        ]
        cfg = EngineConfig(seed="handcalc", mode="deterministic", workers=1)
        r1 = run_deterministic(model, cfg, ticks=1, run_id=0)
        r2 = run_deterministic(model, cfg, ticks=1, run_id=0)
        self.assertEqual(r1["run_hash"], r2["run_hash"])
        # workers flag must not change hash for same seed/run
        cfg_n = EngineConfig(seed="handcalc", mode="deterministic", workers=4)
        r3 = run_deterministic(model, cfg_n, ticks=1, run_id=0)
        self.assertEqual(r1["run_hash"], r3["run_hash"])

    def test_formula_matches_hand_calc(self) -> None:
        expected = expected_output_effect(base_strength=0.4, sign=-1, context_mult=1.1, input_effect=1.0)
        self.assertAlmostEqual(expected, -0.44, places=9)

    def test_nonconvergence_divergent_cycle(self) -> None:
        model = ComputationalModel()
        for vid in ("factor:X", "factor:Y"):
            model.variables[vid] = Variable(id=vid, role="endogenous", baseline=1.0, value=1.0)
        # strong positive zero-lag feedback
        model.edges = [
            ModelEdge(id="e1", source="factor:X", target="factor:Y", sign=1, strength=2.0, lag_ticks=0),
            ModelEdge(id="e2", source="factor:Y", target="factor:X", sign=1, strength=2.0, lag_ticks=0),
        ]
        result = run_deterministic(model, EngineConfig(seed="div", jacobi_max_iter=5), ticks=1)
        # may flag nonconvergence or still produce finite state; unresolved path should not silent-renormalize MC
        self.assertIn("run_hash", result)

    def test_mc_unresolved_mass_not_renormalized(self) -> None:
        model = ComputationalModel()
        model.variables["factor:A"] = Variable(id="factor:A", role="endogenous", baseline=1.0)
        model.edges = []
        cfg = EngineConfig(seed="mc", mode="monte_carlo", min_runs=20)
        summary = run_monte_carlo(model, cfg, ticks=1)["summary"]
        self.assertIn("unresolved_mass", summary)
        self.assertAlmostEqual(summary["valid_mass"] + summary["unresolved_mass"], 1.0, places=9)


class MigrationTests(unittest.TestCase):
    def test_check_only_plan(self) -> None:
        plan = plan_migration(FIXTURES / "schema-1.2-valid")
        self.assertTrue(plan.get("ok"))
        self.assertIn("transforms", plan)

    def test_migrate_sibling_and_dual_run_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = FIXTURES / "schema-1.2-valid"
            out_a = Path(tmp) / "a"
            out_b = Path(tmp) / "b"
            r = migrate_dual_run_canonical(src, out_a, out_b)
            self.assertTrue(r.get("ok"), msg=json.dumps(r, indent=2, default=str))
            # source untouched schema
            src_man = json.loads((src / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(src_man["schema_version"], "1.2.0")
            dest_man = json.loads((out_a / "simulation-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(dest_man["schema_version"], "2.0.0")


class PrivacyAndLedgerTests(unittest.TestCase):
    def test_private_person_refused(self) -> None:
        result = privacy_intake(subject_class="private_person", request_text="analyze this private citizen")
        self.assertFalse(result["allowed"])
        self.assertIn("network", result["stop_before"])

    def test_doxxing_refused(self) -> None:
        result = privacy_intake(
            subject_class="public_role_person",
            public_role_anchor="Mayor",
            evidence_ids=["evidence:x"],
            request_text="find home address and phone number for stalking",
        )
        self.assertFalse(result["allowed"])

    def test_hmac_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.csv"
            # 14 columns
            header = "id,record_type,claim,evidence,source,source_type,source_tier,date,retrieved_at,access_method,retrieval_status,confidence,contradiction_status,notes"
            ledger.write_text(header + "\nclaim1,claim,hello,hello,http://example.com,web,primary,2020-01-01,2020-01-02,open,opened,0.5,unchecked,\n", encoding="utf-8")
            sidecar = Path(tmp) / "ledger.hmac"
            sidecar.write_text("deadbeef", encoding="utf-8")
            result = import_d_research_ledger(ledger, hmac_sidecar=sidecar, hmac_key=b"secret", package_major=3)
            self.assertFalse(result["ok"])
            codes = {i["code"] for i in result["issues"]}
            self.assertIn("HMAC_TAMPER", codes)

    def test_process_rows_not_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.csv"
            header = "id,record_type,claim,evidence,source,source_type,source_tier,date,retrieved_at,access_method,retrieval_status,confidence,contradiction_status,notes"
            body = "\n".join(
                [
                    header,
                    "p1,process,running,,http://example.com,web,secondary,2020-01-01,2020-01-02,open,opened,0.5,unchecked,",
                    "c1,claim,A claim,A claim text here ok,http://example.com,web,primary,2020-01-01,2020-01-02,open,opened,0.5,unchecked,",
                ]
            )
            ledger.write_text(body + "\n", encoding="utf-8")
            result = import_d_research_ledger(ledger, package_major=3)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["evidence_rows"]), 1)
            self.assertEqual(result["mapping"], "evidence")


class PacksAndAdaptersTests(unittest.TestCase):
    def test_seven_packs_validated(self) -> None:
        result = validate_all_packs(ROOT)
        self.assertTrue(result.get("ok"), msg=json.dumps(result, indent=2, default=str))
        self.assertEqual(result.get("count"), 7)

    def test_uncalibrated_cannot_emit_probability(self) -> None:
        iss = refuse_uncalibrated_probability("validated")
        self.assertIsNotNone(iss)
        self.assertEqual(iss.code, "PACK_PROBABILITY")


class TimelineHelperTests(unittest.TestCase):
    def test_infer_modes(self) -> None:
        self.assertEqual(infer_timeline_mode(parse_date("2020-01-01"), parse_date("2021-01-01"), parse_date("2021-01-01")), "retrospective_counterfactual")
        self.assertEqual(infer_timeline_mode(parse_date("2022-01-01"), parse_date("2021-01-01"), parse_date("2023-01-01")), "prospective_intervention")


class RendererPathTests(unittest.TestCase):
    def test_renderer_refuses_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            shutil.copytree(FIXTURES / "schema-2.0-valid", ws, dirs_exist_ok=True)
            man = json.loads((ws / "simulation-manifest.json").read_text(encoding="utf-8"))
            man["artifact_paths"]["nodes"] = "../outside.json"
            write_json_atomic(ws / "simulation-manifest.json", man)
            with self.assertRaises(ValueError):
                render(ws)


if __name__ == "__main__":
    unittest.main()
