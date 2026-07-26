# Aleph capability baseline freeze (Phase A0)

Frozen from the released state of `main` before the v2.4.0 upgrade work.

- Baseline commit: `891a8f3c67b042b665c3f8f8503fa82763f65952` (tag `v2.3.1`, published)
- Prior release in line: `v2.3.0` @ `38653d0a3616498c5e717f7c76d785dce5032e6c` (tag kept immutable, unpublished)
- Package/validator version: `2.3.1`; workspace schema `2.0.0`; formula `2.0.0` + `2.1.0`
- Bundled component: D Research `3.3.0` (lock: 209 files, tree
  `sha256:6f34464f149dab246f51c082aaa886688133f80db4d5589526b3625329490bfa`,
  upstream commit `f097505c…`, 531 excluded of 740 tracked)
- Toolchain: Python 3.11.15, Node v24.14.0, Windows 11

Note on scope: the original upgrade plan called this phase "freeze v2.2.0".
Between plan authoring and execution, `v2.3.0`/`v2.3.1` were released on
`main` (D Research 3.3.0 re-vendor + exact 37-column ledger interop). The
baseline therefore freezes the *current released state* `v2.3.1`, which is a
strict superset of `v2.2.0`.

## Baseline gate results (pristine v2.3.1 tree, this environment)

| Gate | Result |
|---|---|
| `python -m unittest discover -s tests` | **366/366 OK** (after removing untracked `__pycache__` pollution left by a prior review session inside `components/d-research/`) |
| `python scripts/check_adapters.py` | PASS — 11 adapters checked, 0 issues |
| `python scripts/validate_domain_packs.py` | PASS — 7/7 packs `ok`, all semantically valid, declared maturity `experimental` (as designed) |
| `python scripts/lock_bundled_component.py --upstream-repo <d-research clone>` | PASS — tag/tree/archive/recipe/byte parity verified (740 tracked / 531 excluded / 209 snapshot) |
| `python scripts/preflight.py --json` | PASS — bundled component found, ready, compatible |
| `python scripts/release_gate.py --json` | PASS after `validate_skill_package.py --generate-manifest` accounted for the two new baseline files (the initial run correctly flagged the stale manifest — the fail-closed path works) |

## Frozen artifacts

- `capability-baseline.json` — machine-checkable public surface (gateway routes,
  CLI scripts, aleph modules, adapters, packs, schemas, references, templates,
  ledger header widths `[14, 19, 22, 23, 37]` and record types, formula versions,
  readable workspace schemas). Enforced as a superset on every suite run by
  `tests/test_capability_baseline.py`; regenerating the snapshot is an explicit
  maintainer operation (`python -m tests.test_capability_baseline --capture`).
- `schema-2.0-fixture-hashes.json` — SHA-256 of the 10 valid schema-2.0 fixtures;
  these exact bytes must keep validating under every future validator.
