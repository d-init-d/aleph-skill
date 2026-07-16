#!/usr/bin/env python3
"""The only Aleph -> D Research execution boundary.

The gateway deliberately has a boring interface.  A caller chooses one of the
literal routes below, supplies a workspace, and receives a structured result.
There is no ``shell=True`` escape hatch and there is no "run an arbitrary
script" route.  This matters because the bundled component is trusted by its
content lock, while its working directory is not a writable part of the
distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# Allow ``python scripts/research_gateway.py`` from a fresh checkout.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from aleph import EXIT_OK, EXIT_SECURITY, EXIT_SEMANTIC, EXIT_USAGE  # noqa: E402
from aleph.component_registry import (  # noqa: E402
    COMPONENT_URI,
    ComponentError,
    discover_d_research,
    locked_script_paths,
    resolve_component,
    skill_root_from,
    verify_component_lock,
)
from aleph.io import canonical_json_bytes  # noqa: E402
from aleph.paths import path_contains_link_or_reparse  # noqa: E402

DEFAULT_TIMEOUT_SEC = 120
MAX_TIMEOUT_SEC = 3600
DEFAULT_OUTPUT_LIMIT = 2 * 1024 * 1024
MODE_RESEARCH = "research"
MODE_ROLEPLAY = "roleplay"


def _run_bounded_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_sec: int,
    output_limit: int,
) -> dict[str, Any]:
    """Run a child with a hard combined stdout/stderr memory ceiling."""

    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout = bytearray()
    stderr = bytearray()
    lock = threading.Lock()
    output_exceeded = threading.Event()
    observed = 0

    def drain(stream: Any, sink: bytearray) -> None:
        nonlocal observed
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, output_limit - observed)
                    if remaining:
                        sink.extend(chunk[:remaining])
                    observed += len(chunk)
                    if observed > output_limit and not output_exceeded.is_set():
                        output_exceeded.set()
                        try:
                            process.kill()
                        except OSError:
                            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    assert process.stdout is not None
    assert process.stderr is not None
    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = process.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)
    if any(thread.is_alive() for thread in threads):
        process.kill()
        raise OSError("child output readers did not terminate")
    return {
        "returncode": returncode,
        "stdout": bytes(stdout),
        "stderr": bytes(stderr),
        "timed_out": timed_out,
        "output_exceeded": output_exceeded.is_set(),
        "observed_output_bytes": observed,
    }


# These are the runnable files named by D Research's script inventory.  The
# launcher itself is intentionally excluded: exposing run_python.mjs would
# turn a locked-script allowlist into an arbitrary-script launcher.
SCRIPT_INVENTORY: tuple[str, ...] = (
    "scripts/_ssrf_helpers.py",
    "scripts/adversarial_acceptance.py",
    "scripts/api_fetch.mjs",
    "scripts/bench_harness_check.py",
    "scripts/browser_smoke.mjs",
    "scripts/check_contract.py",
    "scripts/check_internal_refs.py",
    "scripts/check_no_plan_files.py",
    "scripts/check_node_syntax.py",
    "scripts/citation_export.py",
    "scripts/citation_graph.py",
    "scripts/citation_render.py",
    "scripts/citation_resolver.py",
    "scripts/content_sanitize.py",
    "scripts/data_clean.py",
    "scripts/dedup_near.py",
    "scripts/embed_corpus.py",
    "scripts/evidence_ledger.py",
    "scripts/extract_tables.py",
    "scripts/generate_test_pdf.py",
    "scripts/harvest_terms.py",
    "scripts/http_cache.py",
    "scripts/multi_extract.py",
    "scripts/ocr.py",
    "scripts/package_manifest_check.mjs",
    "scripts/pdf_extract.py",
    "scripts/playwright_crawl.mjs",
    "scripts/playwright_extract.mjs",
    "scripts/playwright_probe.mjs",
    "scripts/quality_eval.py",
    "scripts/release_verify.py",
    "scripts/report_render.py",
    "scripts/research_plan.py",
    "scripts/resource_limits.py",
    "scripts/run_dogfood.py",
    "scripts/run_metadata.py",
    "scripts/run_python.mjs",
    "scripts/score_source.py",
    "scripts/social_snapshot.py",
    "scripts/translate.py",
    "scripts/wayback.py",
    "scripts/web_search.mjs",
    "scripts/wikidata.py",
    "scripts/lib/browser_limits.mjs",
    "scripts/lib/browser_ssrf.mjs",
    "scripts/lib/credentials.mjs",
    "scripts/lib/http_cache.mjs",
    "scripts/lib/ssrf_guards.mjs",
)

# A few inventory files are package/test internals, not safe public commands.
# They still appear in ``SCRIPT_INVENTORY`` and are reported explicitly by the
# self-test, instead of being silently omitted from the audit.
NON_DISPATCHABLE_SCRIPTS = frozenset(
    {
        "scripts/_ssrf_helpers.py",
        "scripts/content_sanitize.py",
        "scripts/generate_test_pdf.py",  # hard-codes writes beside __file__
        "scripts/run_python.mjs",  # accepts an arbitrary script path
        "scripts/lib/browser_limits.mjs",
        "scripts/lib/browser_ssrf.mjs",
        "scripts/lib/credentials.mjs",
        "scripts/lib/http_cache.mjs",
        "scripts/lib/ssrf_guards.mjs",
    }
)

FALLBACK_CHAIN = [
    "playwright+node+browser",
    "host-browser",
    "fetch",
    "search",
    "structured-blocker",
]

# Only these option values are interpreted as filesystem paths.  URLs, DOI
# identifiers, search terms, and free-form text are not path-checked.  The
# validator also rejects traversal in any known path option, including paths
# that do not exist yet (the old implementation checked ``exists()`` first).
PATH_OPTIONS = frozenset(
    {
        "--artifact",
        "--baseline-metrics",
        "--bench",
        "--bib",
        "--cache-path",
        "--ci-evidence",
        "--csl",
        "--config",
        "--file",
        "--findings-ledger",
        "--fixtures",
        "--forward-artifacts",
        "--graph",
        "--in",
        "--index",
        "--input",
        "--ledger",
        "--ledgers-dir",
        "--out",
        "--out-dir",
        "--outDir",
        "--out-row",
        "--repo",
        "--report",
        "--run-result",
        "--run-dir",
        "--runs-dir",
        "--schema",
        "--screenshot",
        "--sig",
        "--source-file",
        "--workspace",
        "--workflow-path",
    }
)
MULTI_PATH_OPTIONS = frozenset({"--files", "--inputs", "--outputs"})
CONDITIONAL_PATH_OPTIONS = frozenset({"--style"})
PATH_ENV_OPTIONS = frozenset(
    {
        "D_RESEARCH_OUT",
        "D_RESEARCH_CACHE",
        "D_RESEARCH_CONFIG",
        "D_RESEARCH_CSL_CACHE",
        "D_RESEARCH_HTTP_CACHE_PATH",
    }
)

RESEARCH_ENV_ALLOW = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "D_RESEARCH_NO_NETWORK",
        "D_RESEARCH_MODE",
        "ALEPH_SKILL_ROOT",
        # This is a non-secret test switch.  It is still passed only when the
        # caller explicitly supplied it, never inferred from a browser file.
        "D_RESEARCH_SKIP_BROWSER_SMOKE",
        "D_RESEARCH_HTTP_MAX_BYTES",
        "D_RESEARCH_HTTP_TIMEOUT_SEC",
        "D_RESEARCH_DOWNLOAD_MAX_BYTES",
        "D_RESEARCH_EXCEL_MAX_COL",
        "D_RESEARCH_EXCEL_MAX_CELLS",
        "D_RESEARCH_XLSX_MAX_UNCOMPRESSED",
        "D_RESEARCH_XLSX_MAX_COMPRESSION_RATIO",
        "D_RESEARCH_PDF_MAX_PAGES",
        "D_RESEARCH_PDF_MAX_BYTES",
        "D_RESEARCH_OCR_MAX_PAGES",
        "D_RESEARCH_OCR_MAX_PIXELS",
        "D_RESEARCH_OCR_MAX_IMAGE_BYTES",
        "D_RESEARCH_SUBPROCESS_TIMEOUT_SEC",
        "D_RESEARCH_SUBPROCESS_MAX_OUTPUT_BYTES",
        "D_RESEARCH_TABLE_MAX_ROWS",
        "D_RESEARCH_TABLE_MAX_CELLS",
        "D_RESEARCH_WAYBACK_MAX_BYTES",
        "D_RESEARCH_SOCIAL_MAX_BYTES",
    }
)
HMAC_ENV_KEYS = frozenset({"D_RESEARCH_LEDGER_KEY"})
ROUTE_SECRET_ENV_KEYS = frozenset(
    {
        "BRAVE_API_KEY",
        "COHERE_API_KEY",
        "DEEPL_API_KEY",
        "GOOGLE_CSE_KEY",
        "GOOGLE_TRANSLATE_API_KEY",
    }
)
ROLEPLAY_ENV_DENY_PREFIXES = (
    "D_RESEARCH_",
    "PLAYWRIGHT_",
    "BROWSER_",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)


def _route(
    script: str,
    *,
    kind: str = "python",
    prefix: tuple[str, ...] = (),
    network: bool = False,
    browser: bool = False,
    hmac: bool = False,
    disabled: bool = False,
    env_allow: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Create an immutable-in-practice route descriptor."""

    return {
        "script": script,
        "kind": kind,
        "args": list(prefix),
        "network": network,
        "requires_browser": browser,
        "hmac": hmac,
        "disabled": disabled,
        "env_allow": list(env_allow),
    }


# Friendly routes are kept stable for adapters and old workspaces.  Every
# other inventory script gets a deterministic ``research:script:<path>`` alias
# below, so adding a helper cannot accidentally make it unreachable.
COMMAND_ROUTES: dict[str, dict[str, Any]] = {
    "research:preflight": {"script": None, "kind": "internal"},
    "research:manifest": {"script": None, "kind": "internal"},
    "research:route": {"script": None, "kind": "internal"},
    "research:self-test": {"script": None, "kind": "component-self-test"},
    "research:acceptance": _route("scripts/adversarial_acceptance.py", network=False),
    "research:browser-smoke": _route("scripts/browser_smoke.mjs", kind="node", browser=True),
    "research:browser-probe": _route(
        "scripts/playwright_probe.mjs", kind="node", network=True, browser=True
    ),
    "research:browser-extract": _route(
        "scripts/playwright_extract.mjs", kind="node", network=True, browser=True
    ),
    "research:browser-crawl": _route(
        "scripts/playwright_crawl.mjs", kind="node", network=True, browser=True
    ),
    "research:api-fetch": _route("scripts/api_fetch.mjs", kind="node", network=True),
    "research:web-search": _route(
        "scripts/web_search.mjs",
        kind="node",
        network=True,
        env_allow=("BRAVE_API_KEY", "GOOGLE_CSE_KEY", "GOOGLE_CSE_ID", "SEARXNG_INSTANCE"),
    ),
    "research:evidence-ledger": _route("scripts/evidence_ledger.py", hmac=True),
    # ``run`` is the pre-2.1 alias; it remains an evidence-ledger route, not a
    # free-form script selector.
    "research:run": _route("scripts/evidence_ledger.py", hmac=True),
    # Import means verify a signed ledger.  ``canonicalise`` is a Python API,
    # not a CLI subcommand, so the old route was never executable.
    "research:import": _route("scripts/evidence_ledger.py", prefix=("verify",), hmac=True),
    "research:plan": _route("scripts/research_plan.py", hmac=True),
    "research:package-check": _route("scripts/package_manifest_check.mjs", kind="node"),
    "research:check-contract": _route("scripts/check_contract.py"),
    "research:check-refs": _route("scripts/check_internal_refs.py"),
    "research:quality": _route("scripts/quality_eval.py"),
    "research:report": _route("scripts/report_render.py", hmac=True),
    "research:citation-resolver": _route("scripts/citation_resolver.py", network=True),
    "research:citation-export": _route("scripts/citation_export.py", network=True),
    "research:citation-render": _route("scripts/citation_render.py", network=True),
    "research:wayback": _route("scripts/wayback.py", network=True),
    "research:wikidata": _route("scripts/wikidata.py", network=True),
    "research:social": _route("scripts/social_snapshot.py", network=True),
    "research:translate": _route(
        "scripts/translate.py",
        network=True,
        env_allow=("DEEPL_API_KEY", "GOOGLE_TRANSLATE_API_KEY"),
    ),
    "research:graph": _route("scripts/citation_graph.py", network=True),
    "research:embed": _route(
        "scripts/embed_corpus.py", network=True, env_allow=("COHERE_API_KEY",)
    ),
    "research:extract": _route("scripts/multi_extract.py"),
    "research:pdf": _route("scripts/pdf_extract.py"),
    "research:ocr": _route("scripts/ocr.py"),
    "research:data": _route("scripts/data_clean.py"),
    "research:dedup": _route("scripts/dedup_near.py"),
    "research:cache": _route("scripts/http_cache.py"),
    "research:score": _route("scripts/score_source.py"),
    "research:metadata": _route("scripts/run_metadata.py"),
    "research:resource-limits": _route("scripts/resource_limits.py"),
    "research:dogfood": _route("scripts/run_dogfood.py"),
    "research:bench": _route("scripts/bench_harness_check.py"),
    "research:harvest": _route("scripts/harvest_terms.py"),
    "research:release-verify": _route("scripts/release_verify.py"),
    "research:adversarial": _route("scripts/adversarial_acceptance.py"),
}


def _script_alias(rel: str) -> str:
    return "research:script:" + rel.removeprefix("scripts/").replace("/", "-").replace(".", "-")


for _rel in SCRIPT_INVENTORY:
    if _rel == "scripts/run_python.mjs":
        continue
    if _rel.endswith(".mjs"):
        _kind = "node"
    else:
        _kind = "python"
    _network = _rel in {
        "scripts/api_fetch.mjs",
        "scripts/playwright_probe.mjs",
        "scripts/playwright_extract.mjs",
        "scripts/playwright_crawl.mjs",
        "scripts/web_search.mjs",
        "scripts/citation_resolver.py",
        "scripts/citation_export.py",
        "scripts/citation_render.py",
        "scripts/citation_graph.py",
        "scripts/embed_corpus.py",
        "scripts/social_snapshot.py",
        "scripts/translate.py",
        "scripts/wayback.py",
        "scripts/wikidata.py",
    }
    COMMAND_ROUTES.setdefault(
        _script_alias(_rel),
        _route(
            _rel,
            kind=_kind,
            network=_network,
            browser=_rel
            in {
                "scripts/playwright_probe.mjs",
                "scripts/playwright_extract.mjs",
                "scripts/playwright_crawl.mjs",
                "scripts/browser_smoke.mjs",
            },
            disabled=_rel in NON_DISPATCHABLE_SCRIPTS,
            env_allow={
                "scripts/web_search.mjs": (
                    "BRAVE_API_KEY",
                    "GOOGLE_CSE_KEY",
                    "GOOGLE_CSE_ID",
                    "SEARXNG_INSTANCE",
                ),
                "scripts/translate.py": (
                    "DEEPL_API_KEY",
                    "GOOGLE_TRANSLATE_API_KEY",
                ),
                "scripts/embed_corpus.py": ("COHERE_API_KEY",),
            }.get(_rel, ()),
        ),
    )


def _digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _redact(text: str) -> str:
    """Do not put a ledger key in a returned receipt if a helper prints it."""

    for key in HMAC_ENV_KEYS | ROUTE_SECRET_ENV_KEYS:
        value = os.environ.get(key)
        if value and len(value) >= 6:
            text = text.replace(value, "[REDACTED]")
    return text


_INTERNAL_REF_EXTENSIONS = frozenset(
    {".bib", ".csv", ".json", ".md", ".mjs", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
)
_INTERNAL_REF_ROOTS = frozenset(
    {".agents", ".github", "adapters", "docs", "examples", "references", "scripts", "templates"}
)
_BACKTICK_REF = re.compile(r"`([^`\s\{\}\*<>]+)`")


def _component_internal_reference_reconciliation(root: Path) -> dict[str, Any]:
    """Reconcile missing repo-only refs against the exact locked snapshot recipe."""

    lock = json.loads((root / "component-lock.json").read_text(encoding="utf-8"))
    entry = (lock.get("components") or {}).get("d-research") or {}
    recipe = entry.get("snapshot_recipe") or {}
    excluded = recipe.get("excluded_paths")
    if not isinstance(excluded, list) or not all(isinstance(value, str) for value in excluded):
        return {
            "ok": False,
            "error": "component lock lacks exact snapshot_recipe.excluded_paths",
        }
    allowed = set(excluded)
    component = root / "components" / "d-research"
    missing: set[str] = set()
    for markdown in component.rglob("*.md"):
        if markdown.name.startswith("PLAN-"):
            continue
        text = markdown.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        for match in _BACKTICK_REF.finditer(text):
            token = match.group(1).split("#", 1)[0].split("?", 1)[0]
            if "/" not in token:
                continue
            first = token.split("/", 1)[0]
            suffix = Path(token).suffix.lower()
            if first not in _INTERNAL_REF_ROOTS or suffix not in _INTERNAL_REF_EXTENSIONS:
                continue
            if not (component / token).exists():
                missing.add(token)
    unexpected = sorted(missing - allowed)
    repo_contract_paths = [
        ".github/workflows/lint-and-self-test.yml",
        ".github/workflows/release-attest.yml",
        ".github/workflows/release-source-archive.yml",
    ]
    return {
        "ok": not unexpected,
        "component_version": entry.get("version"),
        "missing_repo_only_refs": sorted(missing),
        "allowed_snapshot_exclusions": sorted(missing & allowed),
        "repo_contract_exclusions": [
            path
            for path in repo_contract_paths
            if path in allowed and not (component / path).exists()
        ],
        "unexpected": unexpected,
    }


def _reconcile_component_acceptance(
    *,
    root: Path,
    returncode: int,
    stdout: bytes,
) -> dict[str, Any] | None:
    """Adapt exact repository-only acceptance cases without hiding runtime failures."""

    if returncode != 1:
        return None
    text = stdout.decode("utf-8", errors="replace")
    failed = re.findall(r"^\s*\[FAIL\]\s+([^\s]+)", text, flags=re.MULTILINE)
    reconciliation = _component_internal_reference_reconciliation(root)
    if reconciliation.get("ok") is not True:
        return None
    expected_failures = ["10_undeclared_stale_citations"]
    if reconciliation.get("component_version") == "3.2.1":
        expected_failures.append("23_unsafe_runtime_config")
        required = ".github/workflows/lint-and-self-test.yml"
        normalized = re.sub(r"/+", "/", text.replace("\\", "/"))
        if (
            required not in reconciliation.get("repo_contract_exclusions", [])
            or required not in normalized
            or "scripts/check_contract.py" not in normalized
            or "FileNotFoundError" not in text
        ):
            return None
    if failed != expected_failures:
        return None
    reconciliation.update(
        {
            "upstream_runtime_cases_passed": len(
                re.findall(r"^\s*\[PASS\]", text, flags=re.MULTILINE)
            ),
            "upstream_repo_only_cases_reconciled": len(expected_failures),
            "browser_cases_delegated": len(
                re.findall(r"^\s*\[DELEGATED\]", text, flags=re.MULTILINE)
            ),
        }
    )
    return reconciliation


def _probe_capabilities(
    component_root: Path,
    assertions: dict[str, bool] | None = None,
    *,
    probe_playwright: bool = True,
) -> dict[str, Any]:
    """Probe runtimes without mistaking their presence for network access.

    Python and Node only prove that a process can be started.  Fetch/search and
    a host browser are capabilities supplied by the host explicitly.  A
    Playwright module counts only when its browser executable is present too;
    no install or network probe is attempted here.
    """

    declared = assertions or {}
    node = shutil.which("node")
    npm = shutil.which("npm")
    python = sys.executable
    playwright_js = False
    browser_binary = False
    browser_launch = False
    browser_path = ""
    if node and probe_playwright:
        probe_code = (
            "import fs from 'node:fs';"
            "import('playwright').then(async ({chromium})=>{"
            "const p=chromium.executablePath();"
            "const binary=Boolean(p&&fs.existsSync(p));"
            "let launch=false;"
            "if(binary){try{const b=await chromium.launch({headless:true});await b.close();launch=true;}catch{}}"
            "process.stdout.write(JSON.stringify({module:true,path:p,binary,launch}));"
            "}).catch(()=>process.stdout.write(JSON.stringify({module:false})));"
        )
        try:
            probe_env = {
                key: value
                for key, value in os.environ.items()
                if key
                in {
                    "PATH",
                    "SYSTEMROOT",
                    "SYSTEMDRIVE",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "HOME",
                    "USERPROFILE",
                    "APPDATA",
                    "LOCALAPPDATA",
                }
            }
            probe = subprocess.run(
                [node, "--input-type=module", "-e", probe_code],
                cwd=str(component_root),
                env=probe_env,
                capture_output=True,
                timeout=15,
                shell=False,
                check=False,
            )
            if probe.returncode == 0:
                payload = json.loads(probe.stdout.decode("utf-8", errors="replace") or "{}")
                playwright_js = bool(payload.get("module"))
                browser_path = str(payload.get("path") or "")
                browser_binary = bool(
                    payload.get("binary") and browser_path and Path(browser_path).is_file()
                )
                browser_launch = bool(payload.get("launch"))
        except (AttributeError, OSError, subprocess.SubprocessError, ValueError, TypeError):
            playwright_js = False
            browser_binary = False
            browser_launch = False
    capabilities: dict[str, Any] = {
        "python": bool(python),
        "python_executable": python,
        "node": bool(node),
        "node_executable": node,
        "npm": bool(npm),
        "playwright_js": playwright_js,
        "playwright_probe_skipped": not probe_playwright,
        "browser_binary": browser_binary,
        "browser_launch": browser_launch,
        "browser_binary_path": browser_path or None,
        "browser_binary_bundled": False,
        "host_browser": bool(declared.get("host_browser", False)),
        "fetch": bool(declared.get("fetch", False)),
        "search": bool(declared.get("search", False)),
        "network": bool(declared.get("network", False)),
        "declared": {
            key: bool(value)
            for key, value in declared.items()
            if key in {"host_browser", "fetch", "search", "network"}
        },
        "note": (
            "Python/Node presence is runtime-only. Fetch/search/host-browser "
            "are false unless explicitly declared by the host."
        ),
    }
    capabilities["playwright_browser"] = playwright_js and browser_binary and browser_launch
    return capabilities


def _select_route(capabilities: dict[str, Any]) -> tuple[str, list[str], list[dict[str, str]]]:
    blockers: list[dict[str, str]] = []
    if capabilities.get("playwright_browser") and capabilities.get("node"):
        return FALLBACK_CHAIN[0], FALLBACK_CHAIN[:], blockers
    blockers.append(
        {
            "code": "CAPABILITY_PLAYWRIGHT",
            "message": "Playwright module plus browser executable was not detected; no install attempted.",
        }
    )
    if capabilities.get("host_browser"):
        return FALLBACK_CHAIN[1], FALLBACK_CHAIN[1:], blockers
    blockers.append(
        {
            "code": "CAPABILITY_HOST_BROWSER",
            "message": "Host browser was not explicitly declared by the adapter.",
        }
    )
    if capabilities.get("fetch"):
        return FALLBACK_CHAIN[2], FALLBACK_CHAIN[2:], blockers
    blockers.append(
        {
            "code": "CAPABILITY_FETCH",
            "message": "Fetch/network capability was not explicitly declared; no network probe was made.",
        }
    )
    if capabilities.get("search"):
        return FALLBACK_CHAIN[3], FALLBACK_CHAIN[3:], blockers
    blockers.append(
        {
            "code": "CAPABILITY_SEARCH",
            "message": "Search capability was not explicitly declared by the host.",
        }
    )
    blockers.append(
        {
            "code": "CAPABILITY_NONE",
            "message": "No reachable research capability; emit a structured blocker, never a fabricated ledger.",
        }
    )
    return FALLBACK_CHAIN[4], FALLBACK_CHAIN[4:], blockers


def _filter_research_env(
    *,
    component_root: Path,
    skill_root: Path,
    workspace: Path,
    include_hmac: bool,
    network_allowed: bool,
    extra_env_allow: set[str] | None = None,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    source = base if base is not None else os.environ
    route_env = extra_env_allow or set()
    filtered: dict[str, str] = {}
    for key, value in source.items():
        if key in RESEARCH_ENV_ALLOW:
            filtered[key] = value
        elif network_allowed and key in route_env:
            filtered[key] = value
        elif key in PATH_ENV_OPTIONS:
            # Never forward a configured path unless it is inside the caller's
            # workspace.  The gateway's own D_RESEARCH_ROOT is authoritative.
            try:
                candidate = _resolve_workspace_path(value, workspace, allow_dash=False)
            except (OSError, ValueError):
                continue
            filtered[key] = str(candidate)
        elif include_hmac and key in HMAC_ENV_KEYS:
            filtered[key] = value
    filtered["D_RESEARCH_ROOT"] = str(component_root)
    filtered["ALEPH_SKILL_ROOT"] = str(skill_root)
    filtered["PYTHONDONTWRITEBYTECODE"] = "1"
    filtered["PYTHONIOENCODING"] = "utf-8"
    filtered["PYTHONUTF8"] = "1"
    filtered["D_RESEARCH_MODE"] = MODE_RESEARCH
    if not network_allowed:
        filtered["D_RESEARCH_NO_NETWORK"] = "1"
        # D Research 3.2.1 can use an installed sentence-transformers backend.
        # Keep model resolution cache-only unless the caller explicitly grants
        # network access; these are defense-in-depth flags, not an OS sandbox.
        filtered["HF_HUB_OFFLINE"] = "1"
        filtered["TRANSFORMERS_OFFLINE"] = "1"
        filtered["HF_DATASETS_OFFLINE"] = "1"
        filtered["HF_HUB_DISABLE_TELEMETRY"] = "1"
    else:
        # A stale host value must not make a requested network operation look
        # offline.
        filtered.pop("D_RESEARCH_NO_NETWORK", None)
    return filtered


def roleplay_env(*, packet_dir: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """Return defense-in-depth filtering for a host-sandboxed roleplay process."""

    source = base if base is not None else os.environ
    allowed_host = {
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
    filtered: dict[str, str] = {}
    for key, value in source.items():
        if key in ROLEPLAY_ENV_DENY_PREFIXES or key.startswith(ROLEPLAY_ENV_DENY_PREFIXES):
            continue
        if key in allowed_host:
            filtered[key] = value
    filtered["ALEPH_ROLEPLAY_MODE"] = "1"
    filtered["ALEPH_ROLEPLAY_NETWORK"] = "0"
    resolved_packet = packet_dir.resolve()
    filtered["ALEPH_PACKET_DIR"] = str(resolved_packet)
    for key in ("TEMP", "TMP", "TMPDIR"):
        filtered[key] = str(resolved_packet)
    return filtered


def assert_roleplay_isolation(env: dict[str, str]) -> list[str]:
    """Return names of research/browser/network secrets still present."""

    leaks: list[str] = []
    for key in env:
        upper = key.upper()
        if upper.startswith(("D_RESEARCH", "PLAYWRIGHT", "BROWSER")) or upper in {
            "ALEPH_SKILL_ROOT",
            "ALEPH_RESEARCH_ROOT",
            "D_RESEARCH_LEDGER_KEY",
            "D_RESEARCH_SKILL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "PATH",
        }:
            leaks.append(key)
    return sorted(leaks)


def _resolve_workspace_path(
    value: str,
    workspace: Path,
    *,
    allow_dash: bool = True,
) -> Path | None:
    if not isinstance(value, str):
        raise ValueError("path value must be text")
    if "\x00" in value:
        raise ValueError("NUL in path")
    if allow_dash and value == "-":
        return None
    if value in {"", "."}:
        return workspace.resolve()
    if value.startswith("~"):
        raise ValueError("home-directory expansion is not allowed")
    raw = Path(value)
    if any(part == ".." for part in raw.parts):
        raise ValueError("parent traversal is not allowed")
    candidate = raw if raw.is_absolute() else workspace / raw
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {value}") from exc
    return resolved


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "ftp://", "mailto:"))


def _path_values(args: list[str]) -> list[tuple[str, str]]:
    """Yield (option, value) pairs, including ``--option=value`` forms."""

    values: list[tuple[str, str]] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if not isinstance(arg, str):
            raise ValueError("arguments must be strings")
        if "\x00" in arg:
            raise ValueError("NUL in argument")
        key, equal, inline = arg.partition("=")
        if key in PATH_OPTIONS | CONDITIONAL_PATH_OPTIONS and equal:
            values.append((key, inline))
        elif key in PATH_OPTIONS | CONDITIONAL_PATH_OPTIONS:
            if i + 1 >= len(args):
                raise ValueError(f"missing value for {key}")
            i += 1
            values.append((key, args[i]))
        elif key in MULTI_PATH_OPTIONS:
            j = i + 1
            if equal:
                values.append((key, inline))
            while j < len(args) and not str(args[j]).startswith("-"):
                values.append((key, str(args[j])))
                j += 1
            i = j - 1
        i += 1
    return values


def _validate_args(args: list[str], workspace: Path) -> None:
    """Validate all declared path arguments before a child process starts."""

    for option, value in _path_values(args):
        if option in {"--workspace"} and value == str(workspace):
            continue
        if _looks_like_url(value):
            continue
        if option in CONDITIONAL_PATH_OPTIONS and not (
            Path(value).is_absolute()
            or "/" in value
            or "\\" in value
            or value.lower().endswith(".csl")
        ):
            continue
        _resolve_workspace_path(value, workspace)

    # Catch traversal and absolute escapes in positional arguments too.  This
    # is intentionally conservative only for path-shaped values; DOI/URL/ID
    # arguments remain valid research identifiers.
    for value in args:
        if not isinstance(value, str) or value.startswith("-") or _looks_like_url(value):
            continue
        if value in {".", "-"} or any(part == ".." for part in Path(value).parts):
            _resolve_workspace_path(value, workspace)
        elif Path(value).is_absolute():
            _resolve_workspace_path(value, workspace)
        elif any(
            value.lower().endswith(ext)
            for ext in (
                ".csv",
                ".json",
                ".jsonl",
                ".md",
                ".txt",
                ".pdf",
                ".bib",
                ".html",
                ".docx",
                ".xlsx",
            )
        ) and ("/" in value or "\\" in value):
            _resolve_workspace_path(value, workspace)


def _workspace_for(root: Path, requested: Path | None) -> tuple[Path, bool]:
    """Create/validate a workspace and return (path, ephemeral)."""

    if requested is None:
        ephemeral = True
        workspace = Path(tempfile.mkdtemp(prefix="aleph-d-research-"))
    else:
        ephemeral = False
        workspace = requested.expanduser().resolve(strict=False)
    # Check protected roots before mkdir: a rejected path must not create even
    # an empty directory inside (or around) the immutable distribution.
    for protected in (root.resolve(), (root / "components" / "d-research").resolve()):
        try:
            workspace.relative_to(protected)
            overlaps = True
        except ValueError:
            try:
                protected.relative_to(workspace)
                overlaps = True
            except ValueError:
                overlaps = False
        if overlaps:
            raise ValueError("workspace and Aleph skill directory must not overlap")
    if path_contains_link_or_reparse(workspace):
        raise ValueError("workspace path contains a symlink or reparse point")
    if workspace.exists() and not workspace.is_dir():
        raise ValueError("workspace must be a real directory")
    workspace.mkdir(parents=True, exist_ok=True)
    if path_contains_link_or_reparse(workspace):
        raise ValueError("workspace path contains a symlink or reparse point")
    return workspace, ephemeral


def _operation_needs_hmac(command: str, route: dict[str, Any], args: list[str]) -> bool:
    if not route.get("hmac"):
        return False
    script = str(route.get("script") or "")
    prefix = list(route.get("args") or [])
    effective = prefix + args
    if command == "research:import":
        return True
    if script.endswith("evidence_ledger.py"):
        return bool(effective and effective[0] in {"sign", "verify"})
    if script.endswith("research_plan.py"):
        if not effective or effective[0] != "gate":
            return False
        try:
            gate_index = effective.index("--gate")
            gate = effective[gate_index + 1]
        except (ValueError, IndexError):
            return False
        return gate in {"synthesize_ready", "release_ready"}
    if script.endswith("report_render.py"):
        return "--require-signature" in effective
    return False


def _validate_hmac_operation(route: dict[str, Any], args: list[str]) -> None:
    """Prevent a signed-ledger route from selecting an unrelated env secret."""

    script = str(route.get("script") or "")
    if not script.endswith("evidence_ledger.py"):
        return
    effective = list(route.get("args") or []) + args
    if not effective or effective[0] not in {"sign", "verify"}:
        return
    for index, value in enumerate(effective):
        if value.startswith("--key-env="):
            key_name = value.split("=", 1)[1]
        elif value == "--key-env" and index + 1 < len(effective):
            key_name = str(effective[index + 1])
        else:
            continue
        if key_name != "D_RESEARCH_LEDGER_KEY":
            raise ValueError("ledger key env must be D_RESEARCH_LEDGER_KEY")


def _route_needs_network(route: dict[str, Any], args: list[str]) -> bool:
    if not route.get("network"):
        return False
    effective = list(route.get("args") or []) + args
    # Every helper's self-test is offline by contract.
    if "self-test" in effective or "--self-test" in effective:
        return False
    if "--no-download" in effective or "--offline" in effective:
        return False
    script = str(route.get("script") or "")
    command = effective[0] if effective else ""
    if script.endswith("citation_export.py"):
        return command == "enrich"
    if script.endswith("citation_render.py"):
        return command == "render" and "--no-download" not in effective
    if script.endswith("social_snapshot.py"):
        return command == "snapshot"
    if script.endswith("citation_graph.py"):
        return command not in {"", "to-frontier"}
    if script.endswith("translate.py"):
        if command in {"", "detect", "instances"}:
            return False
        try:
            engine = str(effective[effective.index("--engine") + 1])
        except (ValueError, IndexError):
            engine = "libretranslate"
        return engine != "argos"
    if script.endswith("embed_corpus.py"):
        try:
            backend = str(effective[effective.index("--backend") + 1])
        except (ValueError, IndexError):
            backend = "stub"
        return backend == "cohere"
    return True


def _common_result(
    *,
    preflight: dict[str, Any] | None,
    status: str,
    error_code: str | None = None,
    message: str | None = None,
    exit_code: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    report = preflight or {}
    result: dict[str, Any] = {
        "status": status,
        "error_code": error_code,
        "message": message,
        "component_binding": report.get("component_binding"),
        "capabilities": report.get("capabilities") or {},
        "selected_route": report.get("selected_route"),
        "fallback_chain": report.get("fallback_chain") or [],
        "blockers": list(report.get("blockers") or []),
        "stdout_digest": None,
        "stderr_digest": None,
        "receipt_ref": None,
        "exit_code": exit_code,
    }
    result.update(extra)
    return result


def build_preflight(
    *,
    skill_root: Path | None = None,
    allow_external: bool = False,
    external: str | Path | None = None,
    capability_assertions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    root = (skill_root if skill_root is not None else skill_root_from()).resolve()
    discovery = discover_d_research(
        skill_root=root,
        explicit=external,
        allow_external=allow_external,
        require_bundled=not allow_external,
    )
    verification = verify_component_lock(skill_root=root)
    is_bundled = discovery.get("source_kind") == "bundled"
    capabilities: dict[str, Any] = {}
    selected = FALLBACK_CHAIN[4]
    chain = FALLBACK_CHAIN[:]
    blockers: list[dict[str, str]] = []
    binding = discovery.get("component_binding")
    if discovery.get("status") == "available":
        resolved = discovery.get("resolved_path") or discovery.get("path")
        if resolved and resolved != COMPONENT_URI:
            capabilities = _probe_capabilities(
                Path(str(resolved)),
                capability_assertions,
                probe_playwright=is_bundled,
            )
            selected, chain, blockers = _select_route(capabilities)
        if not is_bundled:
            blockers.append(
                {
                    "code": "EXTERNAL_COMPAT",
                    "message": "External D Research is explicit compatibility mode; assurance remains limited.",
                }
            )
    else:
        blockers.append(
            {
                "code": str(discovery.get("error_code") or "COMPONENT_NOT_FOUND"),
                "message": "Bundled D Research unavailable; do not fabricate claims or ledgers.",
            }
        )
    if is_bundled and not verification.ok:
        status = "incompatible"
    elif discovery.get("status") == "available":
        status = "available"
    else:
        status = str(discovery.get("status") or "unavailable")
    return {
        "status": status,
        "source": discovery.get("source"),
        "source_kind": discovery.get("source_kind"),
        "path": discovery.get("path"),
        "component_uri": discovery.get("component_uri")
        or (COMPONENT_URI if verification.ok and is_bundled else None),
        "component_binding": binding if isinstance(binding, dict) else None,
        "capabilities": capabilities,
        "selected_route": selected,
        "fallback_chain": chain,
        "blockers": blockers,
        "verification": verification.to_dict(),
        "package_version": discovery.get("package_version"),
        "package_major": discovery.get("package_major"),
        "compatible": discovery.get("compatible"),
        "identity_verified": discovery.get("identity_verified"),
        "assurance_cap": discovery.get("assurance_cap")
        or ("verified" if is_bundled and verification.ok else "limited"),
    }


def _component_self_test(
    *,
    root: Path,
    preflight: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    """Run checks that are valid for a vendored component, not its source repo.

    The upstream ``check_contract.py`` expects .github/.agents and release
    evidence which are deliberately not part of the Aleph snapshot.  Running
    it here would make a healthy bundle look broken.  We instead verify the
    lock/route contract and run the portable ledger self-test in a workspace.
    """

    checks: list[dict[str, Any]] = []
    delegated: list[dict[str, str]] = []
    verification = verify_component_lock(skill_root=root)
    checks.append(
        {
            "name": "component-lock",
            "status": "pass" if verification.ok else "fail",
            "details": verification.to_dict(),
        }
    )
    if not verification.ok:
        return _common_result(
            preflight=preflight,
            status="fail",
            error_code=verification.error_code or "COMPONENT_LOCK_INVALID",
            message=verification.message or "component lock verification failed",
            exit_code=EXIT_SEMANTIC,
            result={"checks": checks, "delegated": delegated, "component_aware": True},
        )
    locked = locked_script_paths(root)
    unaccounted_locked = sorted(locked - set(SCRIPT_INVENTORY))
    missing_routes = sorted(
        rel for rel in SCRIPT_INVENTORY if rel not in NON_DISPATCHABLE_SCRIPTS and rel not in locked
    )
    checks.append(
        {
            "name": "script-inventory-lock-coverage",
            "status": "pass" if not missing_routes and not unaccounted_locked else "fail",
            "missing": missing_routes,
            "unaccounted_locked": unaccounted_locked,
        }
    )
    route_paths = {
        str(route.get("script"))
        for route in COMMAND_ROUTES.values()
        if route.get("script") is not None
    }
    uncovered = sorted(set(SCRIPT_INVENTORY) - NON_DISPATCHABLE_SCRIPTS - route_paths)
    checks.append(
        {
            "name": "gateway-route-coverage",
            "status": "pass" if not uncovered else "fail",
            "uncovered": uncovered,
        }
    )
    component_root = Path(str(preflight.get("verification", {}).get("root") or ""))
    ledger = component_root / "scripts" / "evidence_ledger.py"
    env = _filter_research_env(
        component_root=component_root,
        skill_root=root,
        workspace=workspace,
        include_hmac=False,
        network_allowed=False,
    )
    try:
        completed = _run_bounded_process(
            [sys.executable, "-B", str(ledger), "self-test"],
            cwd=workspace,
            env=env,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
            output_limit=DEFAULT_OUTPUT_LIMIT,
        )
        returncode = int(completed["returncode"])
        checks.append(
            {
                "name": "evidence-ledger-self-test",
                "status": "pass"
                if returncode == 0
                and not completed["timed_out"]
                and not completed["output_exceeded"]
                else "fail",
                "exit_code": returncode,
                "stdout": _redact(bytes(completed["stdout"]).decode("utf-8", errors="replace"))[
                    -4000:
                ],
                "stderr": _redact(bytes(completed["stderr"]).decode("utf-8", errors="replace"))[
                    -4000:
                ],
                "timed_out": completed["timed_out"],
                "output_exceeded": completed["output_exceeded"],
            }
        )
    except OSError as exc:
        checks.append({"name": "evidence-ledger-self-test", "status": "fail", "error": str(exc)})
    caps = preflight.get("capabilities") or {}
    if not caps.get("node"):
        delegated.append(
            {
                "code": "CAPABILITY_NODE",
                "message": "Node self-tests delegated to a Node-enabled host/CI job.",
            }
        )
    if not caps.get("playwright_browser"):
        delegated.append(
            {
                "code": "CAPABILITY_BROWSER",
                "message": "Chromium smoke delegated; no bundled browser is assumed.",
            }
        )
    failures = [item for item in checks if item.get("status") == "fail"]
    status = (
        "fail"
        if failures or missing_routes or unaccounted_locked or uncovered
        else ("degraded" if delegated else "ok")
    )
    return _common_result(
        preflight=preflight,
        status=status,
        error_code="COMPONENT_SELF_TEST" if failures else None,
        message="component-aware self-test completed",
        exit_code=EXIT_SEMANTIC
        if failures or missing_routes or unaccounted_locked or uncovered
        else EXIT_OK,
        result={"checks": checks, "delegated": delegated, "component_aware": True},
    )


def run_command(
    command: str,
    *,
    skill_root: Path | None = None,
    extra_args: list[str] | None = None,
    mode: str = MODE_RESEARCH,
    include_hmac: bool = False,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    allow_external: bool = False,
    external: str | Path | None = None,
    workspace: Path | None = None,
    capability_assertions: dict[str, bool] | None = None,
    allow_network: bool = False,
) -> dict[str, Any]:
    if mode == MODE_ROLEPLAY:
        return _common_result(
            preflight=None,
            status="refused",
            error_code="ROLEPLAY_NETWORK",
            message="Roleplay mode cannot invoke the research gateway",
            exit_code=EXIT_SECURITY,
            blockers=[
                {"code": "ROLEPLAY_NETWORK", "message": "research gateway denied for roleplay"}
            ],
        )
    if mode != MODE_RESEARCH:
        return _common_result(
            preflight=None,
            status="refused",
            error_code="USAGE",
            message=f"unknown mode {mode!r}",
            exit_code=EXIT_USAGE,
        )
    if command not in COMMAND_ROUTES:
        return _common_result(
            preflight=None,
            status="refused",
            error_code="USAGE",
            message=f"unknown command {command!r}",
            exit_code=EXIT_USAGE,
        )
    args = list(extra_args or [])
    if timeout_sec < 1 or timeout_sec > MAX_TIMEOUT_SEC:
        return _common_result(
            preflight=None,
            status="refused",
            error_code="USAGE",
            message="timeout is outside 1..3600 seconds",
            exit_code=EXIT_USAGE,
        )
    if output_limit < 1024:
        return _common_result(
            preflight=None,
            status="refused",
            error_code="USAGE",
            message="output_limit is too small",
            exit_code=EXIT_USAGE,
        )

    root = (skill_root if skill_root is not None else skill_root_from()).resolve()
    assertions = dict(capability_assertions or {})
    if allow_network:
        assertions["network"] = True
        # This is an explicit caller grant, not an inference from Python/Node.
        assertions.setdefault("fetch", True)
    preflight = build_preflight(
        skill_root=root,
        allow_external=allow_external,
        external=external,
        capability_assertions=assertions,
    )
    route = COMMAND_ROUTES[command]

    try:
        workdir, ephemeral = _workspace_for(root, workspace)
    except (OSError, ValueError) as exc:
        return _common_result(
            preflight=preflight,
            status="fail",
            error_code="PATH_ESCAPE",
            message=str(exc),
            exit_code=EXIT_SECURITY,
        )

    try:
        _validate_args(args, workdir)
        _validate_hmac_operation(route, args)
    except ValueError as exc:
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        error_code = "HMAC_ENV_REFUSED" if "ledger key env" in str(exc) else "PATH_ESCAPE"
        return _common_result(
            preflight=preflight,
            status="fail",
            error_code=error_code,
            message=str(exc),
            exit_code=EXIT_SECURITY,
            cwd=str(workdir),
        )

    if route.get("kind") == "internal":
        if command == "research:preflight":
            payload: dict[str, Any] = preflight
        elif command == "research:manifest":
            try:
                resolution = resolve_component(COMPONENT_URI, skill_root=root)
                payload = {
                    "status": "available",
                    "component_binding": resolution.binding(),
                    "entrypoints": verify_component_lock(skill_root=root).entrypoints,
                    "routes": {
                        name: {
                            "script": descriptor.get("script"),
                            "kind": descriptor.get("kind"),
                            "network": bool(descriptor.get("network")),
                            "requires_browser": bool(descriptor.get("requires_browser")),
                            "dispatchable": not bool(descriptor.get("disabled")),
                        }
                        for name, descriptor in sorted(COMMAND_ROUTES.items())
                    },
                    "non_dispatchable_scripts": sorted(NON_DISPATCHABLE_SCRIPTS),
                    "excluded_launcher": "scripts/run_python.mjs",
                }
            except ComponentError as exc:
                payload = {"status": "fail", "error_code": exc.code, "message": exc.message}
        else:
            payload = {
                "status": preflight.get("status"),
                "selected_route": preflight.get("selected_route"),
                "fallback_chain": preflight.get("fallback_chain"),
                "blockers": preflight.get("blockers"),
                "capabilities": preflight.get("capabilities"),
                "component_binding": preflight.get("component_binding"),
            }
        encoded = canonical_json_bytes(payload)
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return _common_result(
            preflight=preflight,
            status="ok"
            if payload.get("status") in {"available", "pass", "ok"} or command == "research:route"
            else str(payload.get("status", "fail")),
            exit_code=EXIT_OK
            if preflight.get("status") == "available" or command == "research:route"
            else EXIT_SEMANTIC,
            stdout_digest=_digest_bytes(encoded),
            stderr_digest=_digest_bytes(b""),
            result=payload,
            cwd=str(workdir),
        )

    if route.get("kind") == "component-self-test":
        result = _component_self_test(root=root, preflight=preflight, workspace=workdir)
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    if preflight.get("status") != "available":
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="COMPONENT_NOT_FOUND",
            message="research component unavailable",
            exit_code=EXIT_SEMANTIC,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    if route.get("disabled"):
        result = _common_result(
            preflight=preflight,
            status="refused",
            error_code="COMPONENT_WRITE_DENIED",
            message="inventory helper is not a public gateway entrypoint",
            exit_code=EXIT_SECURITY,
            script=route.get("script"),
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    discovery = discover_d_research(
        skill_root=root,
        explicit=external,
        allow_external=allow_external,
        require_bundled=not allow_external,
    )
    resolved = discovery.get("resolved_path") or discovery.get("path")
    if not resolved or discovery.get("status") != "available":
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="COMPONENT_NOT_FOUND",
            message="research component root unresolved",
            exit_code=EXIT_SEMANTIC,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    component_root = Path(str(resolved)).resolve()
    rel_script = route.get("script")
    if not isinstance(rel_script, str):
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="USAGE",
            message="command missing script",
            exit_code=EXIT_USAGE,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    if preflight.get("source_kind") == "bundled" and rel_script not in locked_script_paths(root):
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="COMPONENT_OVERRIDE_REFUSED",
            message=f"script not in component lock: {rel_script}",
            exit_code=EXIT_SECURITY,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    script_path = (component_root / rel_script).resolve(strict=False)
    try:
        script_path.relative_to(component_root)
    except ValueError:
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="PATH_ESCAPE",
            message="script path escaped component root",
            exit_code=EXIT_SECURITY,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    if not script_path.is_file():
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="COMPONENT_FILE_MISSING",
            message=f"script missing: {rel_script}",
            exit_code=EXIT_SEMANTIC,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    effective_args = list(route.get("args") or []) + args
    needs_network = _route_needs_network(route, args)
    caps = preflight.get("capabilities") or {}
    if needs_network and not allow_network:
        result = _common_result(
            preflight=preflight,
            status="delegated",
            error_code="CAPABILITY_NETWORK_UNASSERTED",
            message="network operation delegated until the host explicitly grants network capability",
            exit_code=EXIT_OK,
            blockers=[
                {
                    "code": "CAPABILITY_NETWORK_UNASSERTED",
                    "message": "pass allow_network=True / --network after preflight",
                }
            ],
            script=rel_script,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    if route.get("requires_browser") and not caps.get("playwright_browser"):
        result = _common_result(
            preflight=preflight,
            status="delegated",
            error_code="CAPABILITY_BROWSER",
            message="browser-dependent route delegated to a browser-enabled host",
            exit_code=EXIT_OK,
            blockers=[
                {
                    "code": "CAPABILITY_BROWSER",
                    "message": "Playwright and a browser executable are both required",
                }
            ],
            script=rel_script,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    kind = str(route.get("kind"))
    if kind == "python":
        argv = [sys.executable, "-B", str(script_path), *effective_args]
    elif kind == "node":
        node = shutil.which("node")
        if not node:
            result = _common_result(
                preflight=preflight,
                status="delegated",
                error_code="CAPABILITY_NODE",
                message="Node runtime missing; not auto-installing",
                exit_code=EXIT_OK,
                script=rel_script,
                cwd=str(workdir),
            )
            if ephemeral:
                shutil.rmtree(workdir, ignore_errors=True)
            return result
        argv = [node, str(script_path), *effective_args]
    else:
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="USAGE",
            message=f"unsupported route kind {kind}",
            exit_code=EXIT_USAGE,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    hmac_eligible = _operation_needs_hmac(command, route, args)
    if hmac_eligible and preflight.get("source_kind") != "bundled":
        result = _common_result(
            preflight=preflight,
            status="refused",
            error_code="EXTERNAL_HMAC_REFUSED",
            message="ledger keys are never forwarded to an external compatibility component",
            exit_code=EXIT_SECURITY,
            script=rel_script,
            cwd=str(workdir),
            hmac_forwarded=False,
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    pass_hmac = (include_hmac or hmac_eligible) and hmac_eligible
    env = _filter_research_env(
        component_root=component_root,
        skill_root=root,
        workspace=workdir,
        include_hmac=pass_hmac,
        network_allowed=allow_network,
        extra_env_allow=(
            set(route.get("env_allow") or [])
            if preflight.get("source_kind") == "bundled"
            else set()
        ),
    )
    acceptance_browser_delegated = rel_script == "scripts/adversarial_acceptance.py" and not bool(
        caps.get("playwright_browser")
    )
    if acceptance_browser_delegated:
        env["D_RESEARCH_SKIP_BROWSER_SMOKE"] = "1"
    env["D_RESEARCH_SUBPROCESS_MAX_OUTPUT_BYTES"] = str(output_limit)
    started = time.time()
    try:
        completed = _run_bounded_process(
            argv,
            cwd=workdir,
            env=env,
            timeout_sec=timeout_sec,
            output_limit=output_limit,
        )
    except OSError as exc:
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="RESOURCE_LIMIT",
            message=str(exc),
            exit_code=EXIT_SEMANTIC,
            script=rel_script,
            cwd=str(workdir),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    stdout = bytes(completed["stdout"])
    stderr = bytes(completed["stderr"])
    if completed["timed_out"]:
        result = _common_result(
            preflight=preflight,
            status="timeout",
            error_code="RESOURCE_LIMIT",
            message=f"command exceeded {timeout_sec}s",
            exit_code=EXIT_SEMANTIC,
            stdout_digest=_digest_bytes(stdout),
            stderr_digest=_digest_bytes(stderr),
            stdout=_redact(stdout.decode("utf-8", errors="replace")),
            stderr=_redact(stderr.decode("utf-8", errors="replace")),
            duration_sec=time.time() - started,
            argv0=argv[0],
            script=rel_script,
            cwd=str(workdir),
            shell=False,
            hmac_forwarded=bool(pass_hmac and env.get("D_RESEARCH_LEDGER_KEY")),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result
    if completed["output_exceeded"]:
        result = _common_result(
            preflight=preflight,
            status="fail",
            error_code="RESOURCE_LIMIT",
            message=f"combined stdout/stderr exceeded {output_limit} bytes",
            exit_code=EXIT_SEMANTIC,
            stdout_digest=_digest_bytes(stdout),
            stderr_digest=_digest_bytes(stderr),
            stdout=_redact(stdout.decode("utf-8", errors="replace")),
            stderr=_redact(stderr.decode("utf-8", errors="replace")),
            duration_sec=time.time() - started,
            argv0=argv[0],
            script=rel_script,
            cwd=str(workdir),
            shell=False,
            output_truncated=True,
            observed_output_bytes=completed["observed_output_bytes"],
            hmac_forwarded=bool(pass_hmac and env.get("D_RESEARCH_LEDGER_KEY")),
        )
        if ephemeral:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    returncode = int(completed["returncode"])
    acceptance_reconciliation = (
        _reconcile_component_acceptance(root=root, returncode=returncode, stdout=stdout)
        if rel_script == "scripts/adversarial_acceptance.py"
        and preflight.get("source_kind") == "bundled"
        else None
    )
    completed_status = "ok" if returncode == 0 else "fail"
    completed_error: str | None = None
    completed_message: str | None = None
    completed_exit = returncode
    if acceptance_reconciliation is not None:
        reconciled_count = int(
            acceptance_reconciliation.get("upstream_repo_only_cases_reconciled", 0)
        )
        noun = "case" if reconciled_count == 1 else "cases"
        verb = "was" if reconciled_count == 1 else "were"
        completed_status = "degraded"
        completed_error = "COMPONENT_REPO_CHECK_DELEGATED"
        completed_message = (
            f"runtime acceptance passed; {reconciled_count} repository-only {noun} {verb} "
            "reconciled against exact snapshot exclusions"
        )
        completed_exit = EXIT_OK
    elif returncode == 3:
        completed_status = "degraded"
        completed_error = "UPSTREAM_BLOCKER"
        completed_message = "D Research emitted a fail-closed blocker"
    elif returncode != 0:
        completed_error = "COMPONENT_COMMAND_FAILED"
        completed_message = f"locked helper exited with code {returncode}"
    completed_blockers: list[dict[str, str]] | None = None
    if acceptance_reconciliation is not None:
        reconciled_count = int(
            acceptance_reconciliation.get("upstream_repo_only_cases_reconciled", 0)
        )
        case_labels = "10" if reconciled_count == 1 else "10 and 23"
        case_noun = "case" if reconciled_count == 1 else "cases"
        case_verb = "requires" if reconciled_count == 1 else "require"
        completed_blockers = [
            {
                "code": "COMPONENT_REPO_CHECK_DELEGATED",
                "message": (
                    f"Upstream acceptance {case_noun} {case_labels} {case_verb} "
                    "repository-only files; "
                    "all missing refs and workflow paths "
                    "matched exact locked snapshot exclusions."
                ),
            }
        ]
        if acceptance_reconciliation.get("browser_cases_delegated"):
            completed_blockers.append(
                {
                    "code": "CAPABILITY_BROWSER",
                    "message": "Browser acceptance remains delegated to the browser CI job.",
                }
            )
    elif returncode == 0 and acceptance_browser_delegated:
        completed_status = "degraded"
        completed_blockers = [
            {
                "code": "CAPABILITY_BROWSER",
                "message": "Acceptance passed without browser smoke; browser validation remains delegated.",
            }
        ]
    result = _common_result(
        preflight=preflight,
        status=completed_status,
        error_code=completed_error,
        message=completed_message,
        exit_code=completed_exit,
        stdout_digest=_digest_bytes(stdout),
        stderr_digest=_digest_bytes(stderr),
        stdout=_redact(stdout.decode("utf-8", errors="replace")),
        stderr=_redact(stderr.decode("utf-8", errors="replace")),
        duration_sec=time.time() - started,
        argv0=argv[0],
        script=rel_script,
        cwd=str(workdir),
        shell=False,
        output_truncated=False,
        observed_output_bytes=completed["observed_output_bytes"],
        hmac_forwarded=bool(pass_hmac and env.get("D_RESEARCH_LEDGER_KEY")),
    )
    if acceptance_reconciliation is not None:
        result["acceptance_reconciliation"] = acceptance_reconciliation
    if completed_blockers is not None:
        result["blockers"] = completed_blockers
    if ephemeral:
        shutil.rmtree(workdir, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Aleph research gateway (sole D Research call path)."
    )
    parser.add_argument("command", choices=sorted(COMMAND_ROUTES.keys()))
    parser.add_argument("--skill-root", help="Absolute Aleph skill root (default: derived).")
    parser.add_argument("--mode", choices=[MODE_RESEARCH, MODE_ROLEPLAY], default=MODE_RESEARCH)
    parser.add_argument(
        "--external-d-research",
        help="Explicit external D Research path; requires --allow-external.",
    )
    parser.add_argument(
        "--allow-external", action="store_true", help="Permit explicit external component."
    )
    parser.add_argument(
        "--hmac",
        action="store_true",
        help="Permit HMAC forwarding only for sign/verify/import operations.",
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="Explicitly grant network capability after preflight.",
    )
    parser.add_argument(
        "--host-browser", action="store_true", help="Declare a host browser fallback."
    )
    parser.add_argument(
        "--host-fetch", action="store_true", help="Declare host fetch/network capability."
    )
    parser.add_argument(
        "--host-search", action="store_true", help="Declare host search capability."
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument(
        "--workspace", help="Workspace root; it must be outside the skill directory."
    )
    parser.add_argument("--json", action="store_true", help="JSON only on stdout.")
    # ``parse_known_args`` keeps gateway options usable on either side of the
    # command while forwarding only the helper arguments.  Callers should use
    # ``--`` before helper flags to avoid a name collision with gateway flags.
    args, forwarded = parser.parse_known_args(argv)
    forwarded = list(forwarded)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    skill_root = Path(args.skill_root).resolve() if args.skill_root else None
    workspace = Path(args.workspace).resolve() if args.workspace else None
    result = run_command(
        args.command,
        skill_root=skill_root,
        extra_args=forwarded,
        mode=args.mode,
        include_hmac=args.hmac,
        timeout_sec=args.timeout,
        allow_external=args.allow_external,
        external=args.external_d_research,
        workspace=workspace,
        capability_assertions={
            "host_browser": args.host_browser,
            "fetch": args.host_fetch or args.network,
            "search": args.host_search,
            "network": args.network,
        },
        allow_network=args.network,
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "env"},
            indent=2,
            ensure_ascii=False,
        )
    )
    code = result.get("exit_code")
    if result.get("status") in {"ok", "available", "degraded", "delegated"}:
        raise SystemExit(EXIT_OK if code in (None, 0) else int(code or EXIT_SEMANTIC))
    if result.get("error_code") in {
        "ROLEPLAY_NETWORK",
        "COMPONENT_OVERRIDE_REFUSED",
        "PATH_ESCAPE",
        "COMPONENT_WRITE_DENIED",
    }:
        raise SystemExit(EXIT_SECURITY)
    raise SystemExit(int(code) if isinstance(code, int) and code != 0 else EXIT_SEMANTIC)


if __name__ == "__main__":
    main()
