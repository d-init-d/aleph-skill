# D Research 3.x integration

Aleph uses D Research for lawful public evidence collection. Integration is optional, but verified research requires exact D Research identity and a compatible major.

## Discovery

Check in this order:

1. explicit `--d-research <path>`;
2. `D_RESEARCH_SKILL`;
3. a host capability file;
4. conventional user skill locations for Codex, Agent Skills, Claude Code, OpenCode, and Grok.

Accept only a directory whose `SKILL.md` frontmatter name is exactly `d-research`, whose package identity is recognized, whose major is `3`, and which contains `scripts/evidence_ledger.py`. An explicit incompatible candidate is a hard preflight failure. Do not silently select another installation or hardcode a developer path.

## Ledger contract

Accept the exact ordered D Research CSV headers:

- legacy 14 columns;
- social 19 columns;
- provenance 22 columns;
- record-type 23 columns.

Verify the sidecar format `d-research-skill/hmac-sha256/v1 <digest>` using `D_RESEARCH_LEDGER_KEY`. The digest covers D Research canonical CSV bytes: ordered headers, trimmed values, RFC 4180 quoting, UTF-8, and LF line endings. If a sidecar exists, a missing key, malformed signature, or mismatch is a hard failure.

Preserve the raw ledger bytes before transformation, the verified sidecar, raw SHA-256, canonical SHA-256, every source field, and a hash of every raw row. Import only `record_type=claim` as evidence; keep `process` and `blocker` rows in the audit stream. The importer refuses any source, evidence, raw-preservation, receipt, or sidecar paths that alias one another.

Every successful import emits a separate import-receipt JSON artifact that binds the discovered D Research package identity, mapping contract, raw and canonical ledger digests, evidence-map digest, preserved-ledger reference, sidecar reference, and HMAC-verification result. Store that artifact under `artifact_paths.research_import_receipt` in the simulation manifest.

`verified` assurance does not trust `d_research.status` or arbitrary ledger files. During quality evaluation Aleph reloads the import receipt, verifies its own hash and all referenced digests, rediscovers the compatible D Research package, and repeats the ledger import contract with `D_RESEARCH_LEDGER_KEY`. A missing key, missing receipt, self-asserted status, changed artifact, or unverifiable sidecar can never support `verified` output.

| D Research | Aleph evidence map |
|---|---|
| `claim_id` | stable `evidence_id` |
| `claim` | atomic claim |
| `source_url` | source |
| `source_type` | source type and conservative tier |
| `date_published` | date |
| `date_accessed` | retrieved_at |
| `access_method` | access method and retrieval status |
| `evidence`, `quote_or_anchor` | quote_or_value |
| `contradiction` | contradiction_status |
| `confidence` | preserved label plus explicit evidence-confidence mapping; never event probability |

Blocked/process rows never support causal claims. Search snippets remain provisional. D Research only feeds the Human Research track; the Roleplay track has no browser or ledger access.
