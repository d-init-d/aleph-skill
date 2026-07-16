"""Gateway security and roleplay isolation tests."""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import preflight
from research_gateway import (
    COMMAND_ROUTES,
    MODE_ROLEPLAY,
    NON_DISPATCHABLE_SCRIPTS,
    SCRIPT_INVENTORY,
    _component_internal_reference_reconciliation,
    _filter_research_env,
    _reconcile_component_acceptance,
    _run_bounded_process,
    assert_roleplay_isolation,
    build_preflight,
    main,
    roleplay_env,
    run_command,
)

ROOT = Path(__file__).resolve().parents[1]


class ResearchGatewayTests(unittest.TestCase):
    def test_external_path_requires_separate_allow_flag(self) -> None:
        external = str(ROOT / "components" / "d-research")
        expected = {"status": "ok", "exit_code": 0}
        with mock.patch("research_gateway.run_command", return_value=expected) as runner:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as context:
                main(["research:preflight", "--external-d-research", external, "--json"])
        self.assertEqual(context.exception.code, 0)
        self.assertFalse(runner.call_args.kwargs["allow_external"])
        self.assertEqual(runner.call_args.kwargs["external"], external)

        with mock.patch("research_gateway.run_command", return_value=expected) as runner:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as context:
                main(
                    [
                        "research:preflight",
                        "--external-d-research",
                        external,
                        "--allow-external",
                        "--json",
                    ]
                )
        self.assertEqual(context.exception.code, 0)
        self.assertTrue(runner.call_args.kwargs["allow_external"])

        args = argparse.Namespace(
            external_d_research=external,
            d_research=None,
            allow_external=False,
        )
        discovery = {
            "status": "unavailable",
            "compatible": False,
        }
        verification = mock.Mock(ok=False, error_code="COMPONENT_LOCK_INVALID")
        with (
            mock.patch("preflight.discover_d_research", return_value=discovery) as discover,
            mock.patch("preflight.verify_component_lock", return_value=verification),
            mock.patch(
                "preflight.validate_all_packs",
                return_value={"ok": True, "all_validated": False, "count": 0},
            ),
        ):
            preflight.build_report(args)
        self.assertFalse(discover.call_args.kwargs["allow_external"])
        self.assertTrue(discover.call_args.kwargs["require_bundled"])

    def test_preflight_binds_bundled(self) -> None:
        report = build_preflight(skill_root=ROOT)
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["source"], "bundled")
        self.assertEqual(report["path"], "aleph-component://d-research")
        binding = report["component_binding"]
        self.assertIsInstance(binding, dict)
        self.assertEqual(binding["component_uri"], "aleph-component://d-research")
        self.assertTrue(str(binding["component_lock_sha256"]).startswith("sha256:"))

    def test_roleplay_mode_refused(self) -> None:
        result = run_command("research:preflight", skill_root=ROOT, mode=MODE_ROLEPLAY)
        self.assertEqual(result["status"], "refused")
        self.assertEqual(result["error_code"], "ROLEPLAY_NETWORK")

    def test_roleplay_env_redacts_research_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            packet = Path(temporary)
            env = roleplay_env(
                packet_dir=packet,
                base={
                    "PATH": os.environ.get("PATH", ""),
                    "D_RESEARCH_ROOT": "C:/leak",
                    "D_RESEARCH_LEDGER_KEY": "super-secret",
                    "D_RESEARCH_SKILL": "C:/fake",
                    "PLAYWRIGHT_BROWSERS_PATH": "C:/browsers",
                    "TEMP": temporary,
                },
            )
            leaks = assert_roleplay_isolation(env)
            self.assertEqual(leaks, [])
            self.assertNotIn("D_RESEARCH_ROOT", env)
            self.assertNotIn("D_RESEARCH_LEDGER_KEY", env)
            self.assertNotIn("PATH", env)
            self.assertEqual(env.get("TEMP"), str(packet.resolve()))
            self.assertEqual(env.get("ALEPH_ROLEPLAY_MODE"), "1")

    def test_parent_enforces_hard_combined_output_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outcome = _run_bounded_process(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"],
                cwd=Path(temporary),
                env=os.environ.copy(),
                timeout_sec=30,
                output_limit=4096,
            )
        self.assertTrue(outcome["output_exceeded"], outcome)
        self.assertLessEqual(len(outcome["stdout"]) + len(outcome["stderr"]), 4096)

    def test_repo_only_acceptance_reconciliation_is_exact(self) -> None:
        references = _component_internal_reference_reconciliation(ROOT)
        self.assertTrue(references["ok"], references)
        self.assertTrue(references["missing_repo_only_refs"])
        self.assertIn(
            ".github/workflows/lint-and-self-test.yml",
            references["repo_contract_exclusions"],
        )
        reconciled = _reconcile_component_acceptance(
            root=ROOT,
            returncode=1,
            stdout=(
                b"  [PASS] 01_x\n"
                b"  [FAIL] 10_undeclared_stale_citations\n"
                b"  [FAIL] 23_unsafe_runtime_config - Traceback: scripts/check_contract.py "
                b"FileNotFoundError: .github/workflows/lint-and-self-test.yml\n"
            ),
        )
        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled["upstream_repo_only_cases_reconciled"], 2)
        hidden_failure = _reconcile_component_acceptance(
            root=ROOT,
            returncode=1,
            stdout=(
                b"  [FAIL] 10_undeclared_stale_citations\n"
                b"  [FAIL] 23_unsafe_runtime_config - Traceback: scripts/check_contract.py "
                b"FileNotFoundError: .github/workflows/lint-and-self-test.yml\n"
                b"  [FAIL] 11_unrelated_runtime_failure\n"
            ),
        )
        self.assertIsNone(hidden_failure)
        missing_exact_trace = _reconcile_component_acceptance(
            root=ROOT,
            returncode=1,
            stdout=(
                b"  [FAIL] 10_undeclared_stale_citations\n"
                b"  [FAIL] 23_unsafe_runtime_config - unrelated failure\n"
            ),
        )
        self.assertIsNone(missing_exact_trace)

    def test_workspace_cannot_be_ancestor_of_skill(self) -> None:
        result = run_command(
            "research:run",
            skill_root=ROOT,
            extra_args=["--help"],
            workspace=ROOT.parent,
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["error_code"], "PATH_ESCAPE")

    def test_script_allowlist_and_shell_false(self) -> None:
        result = run_command("research:manifest", skill_root=ROOT)
        self.assertIn(result["status"], {"ok", "available", "fail"})
        # Internal commands do not spawn shell
        self.assertIsNone(result.get("shell") or None)
        # Attempt injection-like extra args on locked run should still shell=False when executed
        result = run_command(
            "research:run",
            skill_root=ROOT,
            extra_args=["--help"],
            timeout_sec=60,
        )
        if result.get("status") in {"ok", "fail"} and "shell" in result:
            self.assertIs(result["shell"], False)
            self.assertTrue(Path(result["cwd"]).is_absolute())

    def test_unknown_command_refused(self) -> None:
        result = run_command("research:not-a-command", skill_root=ROOT)  # type: ignore[arg-type]
        self.assertEqual(result["status"], "refused")

    def test_runtime_presence_does_not_claim_fetch_or_search(self) -> None:
        report = build_preflight(skill_root=ROOT)
        capabilities = report["capabilities"]
        self.assertTrue(capabilities["python"])
        self.assertFalse(capabilities["fetch"])
        self.assertFalse(capabilities["search"])
        if not capabilities["playwright_browser"]:
            self.assertEqual(report["selected_route"], "structured-blocker")

        declared = build_preflight(
            skill_root=ROOT,
            capability_assertions={"fetch": True},
        )
        if not declared["capabilities"]["playwright_browser"]:
            self.assertEqual(declared["selected_route"], "fetch")

    def test_inventory_has_exact_routes_without_arbitrary_launcher(self) -> None:
        route_paths = {
            str(route["script"])
            for route in COMMAND_ROUTES.values()
            if route.get("script") is not None
        }
        expected = set(SCRIPT_INVENTORY) - set(NON_DISPATCHABLE_SCRIPTS)
        self.assertEqual(expected - route_paths, set())
        self.assertNotIn("scripts/run_python.mjs", route_paths)
        for route in COMMAND_ROUTES.values():
            script = route.get("script")
            if script is not None:
                self.assertFalse(Path(str(script)).is_absolute())
                self.assertNotIn("..", Path(str(script)).parts)

    def test_workspace_is_cwd_and_component_is_never_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            result = run_command(
                "research:run",
                skill_root=ROOT,
                extra_args=["self-test"],
                workspace=workspace,
            )
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(Path(result["cwd"]), workspace.resolve())
            self.assertNotEqual(
                Path(result["cwd"]),
                (ROOT / "components" / "d-research").resolve(),
            )

    def test_existing_and_nonexisting_path_escapes_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            outside = Path(temporary) / "not-created.json"
            absolute = run_command(
                "research:run",
                skill_root=ROOT,
                extra_args=["init", f"--out={outside}"],
                workspace=workspace,
            )
            self.assertEqual(absolute["error_code"], "PATH_ESCAPE")
            self.assertFalse(outside.exists())

            relative = run_command(
                "research:run",
                skill_root=ROOT,
                extra_args=["init", "--out", "../escape.csv"],
                workspace=workspace,
            )
            self.assertEqual(relative["error_code"], "PATH_ESCAPE")
            self.assertFalse((Path(temporary) / "escape.csv").exists())

    def test_workspace_inside_component_is_refused(self) -> None:
        result = run_command(
            "research:run",
            skill_root=ROOT,
            extra_args=["--help"],
            workspace=ROOT / "components" / "d-research" / "unsafe-workspace",
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["error_code"], "PATH_ESCAPE")

    def test_hmac_is_forwarded_only_for_eligible_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            old = os.environ.get("D_RESEARCH_LEDGER_KEY")
            os.environ["D_RESEARCH_LEDGER_KEY"] = "gateway-test-key"
            try:
                harmless = run_command(
                    "research:run",
                    skill_root=ROOT,
                    extra_args=["self-test"],
                    include_hmac=True,
                    workspace=workspace,
                )
                self.assertEqual(harmless["status"], "ok", harmless)
                self.assertFalse(harmless["hmac_forwarded"])

                signing = run_command(
                    "research:run",
                    skill_root=ROOT,
                    extra_args=["sign", "--help"],
                    workspace=workspace,
                )
                self.assertEqual(signing["status"], "ok", signing)
                self.assertTrue(signing["hmac_forwarded"])
                self.assertNotIn("gateway-test-key", signing["stdout"])
                self.assertNotIn("gateway-test-key", signing["stderr"])
            finally:
                if old is None:
                    os.environ.pop("D_RESEARCH_LEDGER_KEY", None)
                else:
                    os.environ["D_RESEARCH_LEDGER_KEY"] = old

    def test_ledger_cannot_select_an_unrelated_environment_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_command(
                "research:run",
                skill_root=ROOT,
                extra_args=["sign", "--key-env", "PATH", "--file", "evidence.csv"],
                workspace=Path(temporary),
            )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["error_code"], "HMAC_ENV_REFUSED")
        self.assertEqual(result["exit_code"], 3)

    def test_environment_secrets_are_operation_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            filtered = _filter_research_env(
                component_root=(ROOT / "components" / "d-research").resolve(),
                skill_root=ROOT,
                workspace=workspace,
                include_hmac=False,
                network_allowed=True,
                extra_env_allow={"BRAVE_API_KEY"},
                base={
                    "PATH": os.environ.get("PATH", ""),
                    "BRAVE_API_KEY": "brave-secret-value",
                    "DEEPL_API_KEY": "deepl-secret-value",
                    "D_RESEARCH_LEDGER_KEY": "ledger-secret-value",
                    "D_RESEARCH_OUT": str(workspace.parent / "outside"),
                },
            )
        self.assertEqual(filtered["BRAVE_API_KEY"], "brave-secret-value")
        self.assertNotIn("DEEPL_API_KEY", filtered)
        self.assertNotIn("D_RESEARCH_LEDGER_KEY", filtered)
        self.assertNotIn("D_RESEARCH_OUT", filtered)
        self.assertEqual(filtered["D_RESEARCH_ROOT"], str(ROOT / "components" / "d-research"))

    def test_offline_routes_force_optional_model_backends_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            filtered = _filter_research_env(
                component_root=(ROOT / "components" / "d-research").resolve(),
                skill_root=ROOT,
                workspace=workspace,
                include_hmac=False,
                network_allowed=False,
                base={
                    "HF_HUB_OFFLINE": "0",
                    "TRANSFORMERS_OFFLINE": "0",
                    "HF_DATASETS_OFFLINE": "0",
                    "HF_HUB_DISABLE_TELEMETRY": "0",
                },
            )
        self.assertEqual(filtered["D_RESEARCH_NO_NETWORK"], "1")
        self.assertEqual(filtered["HF_HUB_OFFLINE"], "1")
        self.assertEqual(filtered["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(filtered["HF_DATASETS_OFFLINE"], "1")
        self.assertEqual(filtered["HF_HUB_DISABLE_TELEMETRY"], "1")

    def test_component_self_test_does_not_run_repo_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_command(
                "research:self-test",
                skill_root=ROOT,
                workspace=Path(temporary),
            )
        self.assertIn(result["status"], {"ok", "degraded"}, result)
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["result"]["component_aware"])
        names = {item["name"] for item in result["result"]["checks"]}
        self.assertIn("component-lock", names)
        self.assertIn("evidence-ledger-self-test", names)
        self.assertNotIn("upstream-repository-contract", names)

    def test_network_route_without_explicit_capability_is_delegated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_command(
                "research:api-fetch",
                skill_root=ROOT,
                extra_args=["--url", "https://example.com"],
                workspace=Path(temporary),
            )
        self.assertEqual(result["status"], "delegated")
        self.assertEqual(result["error_code"], "CAPABILITY_NETWORK_UNASSERTED")
        self.assertEqual(result["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
