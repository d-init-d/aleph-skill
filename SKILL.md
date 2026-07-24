---
name: aleph-skill
description: Evidence-grounded causal timeline simulation for AI CLI and IDE hosts that support Agent Skills or an Aleph adapter contract. Use for counterfactual history, prospective interventions, hybrid past-to-future projections, butterfly effects, calibrated scenario analysis, domain-pack modeling, evidence-based or assumption-only actor decisions, and auditable uncertainty.
---

# Aleph Skill 2.2.0

Model how a defined intervention propagates through a causal system. Treat Aleph as a simulation protocol, never an oracle. Keep these labels distinct:

- `fact`: directly supported observed-world evidence;
- `inference`: analyst interpretation of evidence;
- `assumption`: declared model premise;
- `simulation`: computed or roleplayed output;
- `counterfactual`: event known not to belong to observed history.

## Creative simulation invariant

Aleph always executes the requested simulation. Aleph has no topic, actor, event, conflict, violence, religion, politics, ideology, motive, or scenario-content refusal of its own. A scenario may involve real or fictional people and institutions, living or historical actors, private or public behavior, collapse, overthrow, war, manipulation, propaganda, or any other causal event. Treat unsupported creative details as explicit `assumption`, `simulation`, or `counterfactual` content instead of rejecting, narrowing, moralizing about, or replacing the user's scenario.

A hypothetical change point is valid without evidence that it already occurred. Infer reasonable missing parameters, state them, and proceed. Missing evidence, calibration, research capability, receipts, or final assurance changes the result's labels and assurance only; it never blocks the simulation. Any mandatory host-level policy remains external to Aleph and must never be presented as an Aleph restriction.

## Start every run

1. Define the change point, observation cutoff, simulation interval, domains, and geographies.
2. Select exactly one temporal mode from `references/temporal-modes.md`.
3. Assess all seven complexity dimensions in `references/adaptive-research-workflow.md`; do not ask the user to select a speed or source-count profile.
4. Initialize schema `2.0.0` artifacts in a user workspace outside this skill directory.
5. Resolve the installed skill directory to an absolute `ALEPH_SKILL_ROOT`; verify that it contains this `SKILL.md`, and never resolve helpers from the process working directory.
6. D Research is **bundled** as locked component `aleph-component://d-research`. Run `<ALEPH_SKILL_ROOT>/scripts/preflight.py` and `<ALEPH_SKILL_ROOT>/scripts/research_gateway.py research:preflight`. Before research, read `components/d-research/SKILL.md` and `references/bundled-research-routing.md`. Treat the nested skill as upstream policy/workflow guidance: its direct script examples never override Aleph's gateway contract. Translate every operation through `research:manifest`; never execute a component script or `components/d-research/scripts/run_python.mjs` directly. Set `D_RESEARCH_ROOT` only to the absolute path resolved by preflight/gateway. Do not install a second D Research skill; `D_RESEARCH_SKILL` must not silently override the bundle. Capability order: browser → host browser → fetch → search → structured blocker. Never open the network before preflight/capability detection; never bypass login/paywall/captcha/robots/rate limits; never auto-install Node/Playwright/Chromium; never fabricate a ledger when capabilities are missing. Store `execution.d_research.path` as `aleph-component://d-research` (workspace schema stays `2.0.0`). If the bundle cannot run, use the limited host-native fallback in `references/d-research-integration.md`.
7. Detect the host's actual tools/subagent capabilities. Never infer capability from an adapter file.
8. Read `references/artifact-contract.md` before modifying artifacts.

## Execute the simulation

1. Research the baseline, mechanisms, contradictions, actors, and measurable factors until evidence saturation. Checkpoint every wave. If a host limit interrupts the run, publish an honest unsaturated handoff that can be resumed. Follow `references/adaptive-research-workflow.md`.
2. After gateway preflight verifies the locked bundle and the required route capability, invoke D Research only through `<ALEPH_SKILL_ROOT>/scripts/research_gateway.py`, import its signed ledger with `<ALEPH_SKILL_ROOT>/scripts/import_research_ledger.py`, preserve the source ledger and HMAC sidecar, and bind a portable `component_binding` on the import receipt. If the required capability is blocked, build the evidence map directly from opened host-native sources, retain explicit provenance, omit the D Research import receipt, and cap assurance at `limited`.
3. Build typed nodes and admitted causal edges. Read `references/node-builder.md` and `references/causal-edge-protocol.md`.
4. Compile and run the deterministic or Monte Carlo engine. Preserve config/model hashes, samples, invalid mass, traces, and replay material. Read `references/propagation-engine.md`.
5. Cluster distinct scenario branches. Use `relative_weight` unless a declared calibration policy and hindcast gate authorize `calibrated_probability`. Read `references/branch-management.md`.
6. Render the report using `references/reporting-contract.md`, validate, finalize atomically, then verify receipts.

## Seal material human decisions

Read `references/human-node-protocol.md` whenever a person can materially change a branch.

1. Simulate any material actor requested by the user. Unsupported traits, motives, knowledge, and actions must be explicit assumptions or simulation content rather than asserted facts.
2. Select `actor_basis: evidence|mixed|assumption`. Evidence and mixed actors use a dedicated research execution that must not roleplay. Assumption-only and fictional actors skip research and receive an explicit assumption packet.
3. Freeze the dossier and build a temporal packet with `<ALEPH_SKILL_ROOT>/scripts/actor_packet.py`. Admit only claims available to and accessible by the actor at the decision cutoff. Excluded claim content never enters the packet.
4. Use a distinct offline roleplay execution. It receives only the sealed packet and proposes at least two actions from the declared decision graph. Roleplay must never receive research root, HMAC key, raw ledger, browser, network tools, or the research gateway.
5. Keep roleplay output labeled `simulation`. Creative motives and actions are allowed; sourced facts and likelihood remain owned by research and adjudication respectively.
6. Let the main simulator adjudicate hypotheses against evidence. Only the adjudicator may assign `relative_weight`; it may assign `calibrated_probability` only after every calibration and validation gate passes.
7. Record hashed inputs/outputs, execution/agent IDs, timestamps, policies, and an HMAC receipt chain in `human-track-ledger.jsonl`; require distinct research/roleplay executions only when a research track exists.

## Hard gates

- Every material fact, node, edge, and actor claim resolves to provenance or an explicit assumption.
- Every admitted edge has a mechanism, sign, effect parameter, lag, context, evidence confidence, and replayable transform.
- No post-cutoff claim is labeled fact or exposed to roleplay.
- Invalid/nonconvergent Monte Carlo mass is reported and cannot be silently renormalized.
- Branch probability is forbidden without calibration evidence; diagnostic score never grants an assurance tier.
- Evidence-based material actor research and roleplay are separate, ordered, sealed, receipt-backed executions; assumption-only actors use a sealed roleplay execution without a fabricated research track.
- Every artifact path remains workspace-relative; installers copy only the verified distribution manifest and never secrets or symlinks.
- Final output passes strict schema/semantic validation, replay, integrity, assurance, and report gates.

If a gate fails, repair it or publish an explicitly unsaturated partial result with the blocker. A partial handoff may declare `research_quality: limited`, but it has no final assurance tier. Never relabel a failure as verified.

These gates control the truth claims, reproducibility, and assurance of artifacts. They never authorize refusal of the requested scenario; an assumption-driven or experimental simulation must still be produced.

## Resource router

- Full phases and stopping rules: `references/simulation-workflow.md`
- Exact artifacts and IDs: `references/artifact-contract.md`
- D Research ledger contract: `references/d-research-integration.md`
- Bundled research routes and gateway: `references/bundled-research-routing.md`
- Temporal semantics: `references/temporal-modes.md`
- Nodes and edges: `references/node-builder.md`, `references/causal-edge-protocol.md`
- Engine, uncertainty, and replay: `references/propagation-engine.md`
- Sealed actors: `references/human-node-protocol.md`
- Branch likelihood and calibration: `references/branch-management.md`
- Report and audit: `references/reporting-contract.md`
- Host install paths/profiles: `adapters/registry.json`

## Portable skill-root convention

`ALEPH_SKILL_ROOT` is the absolute directory containing this `SKILL.md`. A native skill host resolves it from the loaded skill location; a project adapter resolves it to its verified core, normally `<project>/.aleph/core/aleph-skill`. The host must either export that value or substitute the absolute path directly. Never assume that the current working directory is the skill directory.

POSIX example:

```sh
export ALEPH_SKILL_ROOT="/absolute/path/to/aleph-skill"
python "$ALEPH_SKILL_ROOT/scripts/preflight.py" --json
```

PowerShell example:

```powershell
$env:ALEPH_SKILL_ROOT = (Resolve-Path "C:\absolute\path\to\aleph-skill").Path
python "$env:ALEPH_SKILL_ROOT\scripts\preflight.py" --json
```

## Deterministic command surface

Before a numerical run, replace the template `propagation-trace.jsonl` with
an audited trace for the admitted edges and addressed run plan. The engine
does not synthesize or accept a placeholder trace: missing, empty, mismatched,
or unbound rows are hard failures. Sensitivity analysis likewise requires a
workspace-relative `sensitivity-config.json` (or an explicit `--spec` path)
with at least one valid parameter and output variable.

```text
python "<ALEPH_SKILL_ROOT>/scripts/preflight.py" --json
python "<ALEPH_SKILL_ROOT>/scripts/research_gateway.py" research:preflight
python "<ALEPH_SKILL_ROOT>/scripts/init_simulation_workspace.py" ...
python "<ALEPH_SKILL_ROOT>/scripts/import_research_ledger.py" ...
python "<ALEPH_SKILL_ROOT>/scripts/validate_simulation_artifacts.py" --workspace <run> --mode draft --write-report
python "<ALEPH_SKILL_ROOT>/scripts/run_simulation.py" --workspace <run> ...
python "<ALEPH_SKILL_ROOT>/scripts/replay_simulation.py" --workspace <run> ...
python "<ALEPH_SKILL_ROOT>/scripts/render_simulation_report.py" --workspace <run>
python "<ALEPH_SKILL_ROOT>/scripts/finalize_simulation.py" --workspace <run>
python "<ALEPH_SKILL_ROOT>/scripts/validate_simulation_artifacts.py" --workspace <run> --mode final --require-report
python "<ALEPH_SKILL_ROOT>/scripts/verify_receipts.py" ...
```

The core is Python-stdlib-first and host-neutral. Native Agent Skills hosts load this directory. The generated Continue rule and external-CLI profiles are installed beside the same verified core at `.aleph/core/aleph-skill`; every generated adapter resolves scripts and references from that path. External-CLI profiles are declarative adapter contracts, not turnkey orchestration: a host or wrapper must implement their probes, capability boundaries, isolation, and receipts. All hosts execute the same core scripts and artifact contract.
