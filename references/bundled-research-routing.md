# Bundled D Research routing

Aleph ships D Research as a content-locked component at
`aleph-component://d-research` (`components/d-research/`). A host loads only
Aleph; it does not need to discover or install a second skill. During an Aleph
simulation or research run, every component subprocess must go through
`scripts/research_gateway.py`.

The nested `components/d-research/SKILL.md` preserves upstream workflow and
safety guidance. Its direct `node`, Python, and `scripts/run_python.mjs`
examples are not Aleph invocation instructions. Resolve their operations via
`research:manifest` and use a literal gateway route; never execute a component
script directly. Aleph's adaptive complexity and evidence-saturation protocol
also overrides the nested `fast` / `standard` / `completeness-first` depth
labels: do not impose a fixed source count or elapsed-time cap.

## Security boundary

- Resolve `ALEPH_SKILL_ROOT` from the loaded skill, never from the current
  directory.
- Verify `component-lock.json` before selecting an entrypoint.
- Dispatch only a literal gateway route. The route resolves to an exact,
  locked relative script path and always uses `shell=False`.
- Run the child with its current directory set to a user workspace outside the
  Aleph/component tree. Python runs with bytecode writes disabled.
- Resolve input and output path options against that workspace before starting
  the child. Absolute paths, parent traversal, symlink escapes, and paths that
  would be created outside the workspace fail with `PATH_ESCAPE`.
- `scripts/run_python.mjs` is intentionally not exposed: accepting a script as
  its first argument would bypass the route allowlist. Package-only libraries
  and the fixture generator are visible in the manifest but non-dispatchable.
- The gateway never auto-installs Node, Playwright, Chromium, Python packages,
  or optional binaries.

Use a persistent workspace for real work:

```text
python "$ALEPH_SKILL_ROOT/scripts/research_gateway.py" \
  research:evidence-ledger --workspace "$RUN" -- --help
```

Without `--workspace`, the gateway uses an ephemeral system-temporary
directory. Any output in it is deleted when the command finishes.

## Preflight and truthful capabilities

Run these before any network operation:

```text
python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json
python "$ALEPH_SKILL_ROOT/scripts/research_gateway.py" research:preflight --json
python "$ALEPH_SKILL_ROOT/scripts/research_gateway.py" research:manifest --json
python "$ALEPH_SKILL_ROOT/scripts/research_gateway.py" research:route --json
```

The mandatory fallback order is:

1. Playwright module plus an existing browser executable
2. Host browser explicitly declared by the adapter
3. Fetch/network explicitly declared by the host
4. Search explicitly declared by the host
5. Structured blocker

Python or Node alone is not fetch capability. An adapter document is not a
browser. Preflight makes no network request. A local network helper runs only
after the caller passes `--network`; otherwise the gateway returns a structured
`delegated` result with `CAPABILITY_NETWORK_UNASSERTED`.

Host fallback declarations are explicit:

```text
--host-browser
--host-fetch
--host-search
```

They describe tools owned by the host. They do not make the bundled helper
silently use those tools.

## Public routes

`research:manifest` is the machine-readable source of truth for route-to-script
bindings. Stable task routes include:

| Capability | Gateway route | Locked component script |
|---|---|---|
| Ledger init/validate/sign/verify/export | `research:evidence-ledger` | `scripts/evidence_ledger.py` |
| Signed-ledger import verification | `research:import` | `scripts/evidence_ledger.py verify` |
| Research plan and gates | `research:plan` | `scripts/research_plan.py` |
| Browser probe/extract/crawl | `research:browser-probe`, `research:browser-extract`, `research:browser-crawl` | `scripts/playwright_*.mjs` |
| Public API fetch | `research:api-fetch` | `scripts/api_fetch.mjs` |
| Web search | `research:web-search` | `scripts/web_search.mjs` |
| Citation resolve/export/render | `research:citation-resolver`, `research:citation-export`, `research:citation-render` | citation helpers |
| Wayback, Wikidata, social snapshot | `research:wayback`, `research:wikidata`, `research:social` | corresponding Python helpers |
| PDF, OCR, multi-format extraction | `research:pdf`, `research:ocr`, `research:extract` | extraction helpers |
| Translation and semantic retrieval | `research:translate`, `research:embed` | translation/embedding helpers |
| Citation graph and deduplication | `research:graph`, `research:dedup` | graph/dedup helpers |
| Data cleaning and source scoring | `research:data`, `research:score` | data/quality helpers |
| Report rendering | `research:report` | `scripts/report_render.py` |
| HTTP cache and run metadata | `research:cache`, `research:metadata` | cache/metadata helpers |
| Quality and dogfood evaluation | `research:quality`, `research:dogfood`, `research:bench` | evaluation helpers |
| Component package checks | `research:package-check` | `scripts/package_manifest_check.mjs` |
| Adversarial acceptance | `research:acceptance` | `scripts/adversarial_acceptance.py` |
| Browser smoke | `research:browser-smoke` | `scripts/browser_smoke.mjs` |

Every other runnable file in `references/script-inventory.md` has a deterministic
`research:script:<normalized-path>` audit alias. Use the friendly routes above
for normal work. There is no route that accepts a caller-supplied script path.

Helper arguments go after `--`. Gateway options may appear before or after the
route, but putting them before `--` avoids name collisions:

```text
python "$ALEPH_SKILL_ROOT/scripts/research_gateway.py" \
  research:api-fetch --workspace "$RUN" --network -- \
  --url "https://example.org/public.json" --out "api/result.json"
```

## HMAC boundary

`D_RESEARCH_LEDGER_KEY` is removed from the child environment by default. It is
forwarded only when the resolved operation actually performs signing or
verification:

- `evidence_ledger.py sign` or `verify`;
- `research:import` (fixed to `verify`);
- a research-plan release gate that verifies the signed ledger;
- a report operation that explicitly requires a signature.

Passing `--hmac` to any other route does not forward the key. Returned stdout
and stderr are redacted defensively and the receipt exposes only
`hmac_forwarded: true|false`, never key material.

## Component-aware verification

The snapshot recipe includes every upstream tracked file except the exact 11
paths listed in `component-lock.json` under `snapshot_recipe.excluded_paths`.
Those exclusions remove repository-only CI/agent/release-evidence files and
repository metadata; `.npmignore` and `docs/.archive/UPGRADE-PLAN.md` remain
locked component bytes. Therefore `research:self-test` does not call the
upstream repository `check_contract.py`. It instead verifies:

1. the component content lock and package identity;
2. locked-script coverage of the bundled inventory;
3. gateway route coverage with no arbitrary launcher;
4. the portable evidence-ledger offline self-test from an external workspace.

Missing optional Node/browser capability is reported as `degraded` or
`delegated` with exit code 0. A lock, route, or portable self-test failure is a
real failure. `research:acceptance` and `research:browser-smoke` remain separate
release jobs so a no-browser job cannot masquerade as a browser pass.

## External compatibility mode

The bundle wins by default. `D_RESEARCH_SKILL` never silently overrides it.
An external component requires both an explicit path and opt-in:

```text
python "$ALEPH_SKILL_ROOT/scripts/research_gateway.py" \
  research:preflight --allow-external --external-d-research "/path/to/d-research"
```

External mode is compatibility-only and cannot receive bundled-verified
assurance. The gateway never forwards `D_RESEARCH_LEDGER_KEY` to an external
component, even for a signing or verification route. Route-scoped search,
translation, and embedding API keys are likewise restricted to the verified
bundle. Preflight also does not import or launch Playwright code from an
external component; external browser capability must be declared by the host.

## Roleplay isolation

The research gateway refuses every `--mode roleplay` invocation with the
`ROLEPLAY_NETWORK` error code. Separately, its roleplay-environment helper sets
`ALEPH_ROLEPLAY_MODE=1` and `ALEPH_ROLEPLAY_NETWORK=0` for defense in depth.
Aleph prepares and validates a sealed packet boundary; the host must enforce a
distinct packet-only execution with the component root, evidence workspace,
HMAC key, browser/session variables, proxy variables, shell, filesystem,
network, and research tools denied. The filtered environment is defense in
depth and is not presented as a portable operating-system sandbox. If the host
cannot enforce and attest these denies, roleplay cannot satisfy verified
assurance.
