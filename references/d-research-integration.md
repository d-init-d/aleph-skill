# D Research integration (bundled component)

Aleph 2.1+ ships D Research as a locked internal component. Hosts load only `aleph-skill`. Nested `components/d-research/` is an internal resource, not a second installable skill.

## Discovery order (2.1)

1. Verified bundled component `aleph-component://d-research` (default; always preferred).
2. Explicit external path only when both `--external-d-research` and `--allow-external` are supplied (dev/legacy).
3. Conventional external roots only in explicit external mode when the bundle is absent.

`D_RESEARCH_SKILL` **must not** silently override the bundled component. Preflight records refused env overrides under `tried[]`.

Accept only identity-compatible D Research 3.x (`SKILL.md` name `d-research`, recognized package name, major `3`, `scripts/evidence_ledger.py` present). Bundled installs are verified via `component-lock.json` (per-file SHA-256, tree digest, entrypoints). One-byte tamper, missing file, or extra file is a hard fail (`COMPONENT_TAMPER` / `COMPONENT_FILE_MISSING` / `COMPONENT_EXTRA_FILE`).

Record discovery/import state as a closed contract:

| `execution.d_research.status` | Required coupling |
|---|---|
| `unknown` | Draft only; `invoked: false` and research quality `unknown` or `limited`. |
| `unavailable` | `invoked: false`, no import receipt, and `research_quality: limited`. |
| `incompatible` | `invoked: false`; hard failure rather than silent fallback or final output. |
| `available` | Bundled URI or explicit external path, `package_major: 3`, truthful invocation flag. |
| `imported` / `verified` | `invoked: true`, portable `component_binding` on the import receipt, `ledger_ref`, and `artifact_paths.research_import_receipt`. |

For bundled runs store `execution.d_research.path` as `aleph-component://d-research`. Workspace `schema_version` and `formula_version` remain `2.0.0`.

## Gateway

All research subprocesses use `scripts/research_gateway.py`:

- allowlisted locked scripts only;
- `shell=False`;
- absolute locked script paths with the child working directory set to an external user workspace;
- filtered environment (`D_RESEARCH_*` allowlist; HMAC only when needed);
- timeouts and output limits;
- JSON result with `component_binding`, capabilities, selected route, fallback chain, blockers, stdout/stderr digests.

Use `research:manifest` as the machine-readable route inventory. Stable routes cover ledger, plan, browser, API/search, citations, archives, Wikidata, social snapshots, PDF/OCR, translation, semantic retrieval, multi-format extraction, data cleanup, scoring, reports, quality evaluation, acceptance, and package checks. `scripts/run_python.mjs` is deliberately not dispatchable.

## Capability ladder

1. Playwright + Node + browser binary
2. Host browser (runtime-declared only)
3. Fetch / read URL
4. Web search (snippet-only; never strong evidence alone)
5. Structured blocker — never fabricate claims or ledgers

Do not auto-install dependencies or browser binaries.

## Limited host-native fallback

When the bundle cannot run a needed capability, continue only with host-lawful tools or publish a partial blocker. Fallback does not emit a D Research CSV, HMAC sidecar, or import receipt, and cannot support `verified` / `calibrated` assurance.

Execute the same decomposed questions, source fanout, contradiction searches, and saturation checks with those host-native tools. For every material atomic claim, write the standard `evidence-map.csv` fields: stable evidence ID, claim, opened URL or workspace-relative source path, source type/tier, publication and retrieval dates, access method/status, quote or measured value, evidence confidence, contradiction status, and provenance notes. Preserve allowed raw captures or structured research notes under the simulation workspace and hash them when the artifact contract requires it. Search snippets remain discovery aids and never become strong evidence by themselves.

The fallback does not emit a D Research CSV, HMAC sidecar, preserved D Research ledger, or research import receipt, and it must not populate `artifact_paths.research_import_receipt`. It can support an honest `limited` result only; it cannot support `verified` or `calibrated` assurance. Public-role research still runs in a dedicated research execution, and neither its tools nor its evidence are exposed to the sealed roleplay execution. The same seal applies to host-native fallback research.

## Ledger contract

Accept exact ordered D Research CSV headers: legacy 14, social 19, provenance 22, record-type 23. Verify sidecar `d-research-skill/hmac-sha256/v1 <digest>` with `D_RESEARCH_LEDGER_KEY`. Canonical CSV of Aleph must stay byte-equivalent with the pinned helper; drift is `D_RESEARCH_CANONICAL_DRIFT` hard fail.

Import only `record_type=claim` as evidence; keep `process` and `blocker` in the audit stream.

## Portable receipt binding

Import receipts keep `schema_version: "2.0.0"` and `receipt_type: "d-research-import"`, plus:

```json
{
  "component_binding": {
    "source_kind": "bundled",
    "component_uri": "aleph-component://d-research",
    "component_id": "d-research",
    "package_name": "d-research-skill-tools",
    "package_version": "<pinned>",
    "package_major": 3,
    "upstream_tag": "<tag>",
    "upstream_tag_object": "<40-hex>",
    "upstream_commit": "<40-hex>",
    "component_lock_sha256": "sha256:<digest>",
    "component_tree_sha256": "sha256:<digest>",
    "entrypoint": "scripts/evidence_ledger.py",
    "entrypoint_sha256": "sha256:<digest>"
  }
}
```

Bundled receipts must not embed absolute component install paths. Quality re-verifies lock, tree, and helper digests from the current Aleph root; it does not trust self-asserted `status: verified`.

## Roleplay isolation

Roleplay never calls the research gateway. Aleph emits and validates only the sealed packet/output/receipt chain; the host must create a distinct execution with browser, network, shell, filesystem, and research tools denied. Environment filtering is defense in depth, not an operating-system sandbox. A host that cannot attest and enforce the deny policies must stop or remain below verified assurance.

## External compatibility

External D Research requires both explicit CLI flags and always remains compatibility-limited at runtime. Workspace migration rewrites an old absolute path only when every file in the external snapshot is byte-equivalent to the component lock; a helper-only digest match is insufficient. Incompatible explicit candidates hard-fail without silent fallback.
