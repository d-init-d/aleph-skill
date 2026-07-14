---
name: aleph-skill
description: Evidence-grounded causal timeline simulation for AI CLI and IDE hosts that support Agent Skills or an Aleph adapter contract. Use for counterfactual history, prospective interventions, hybrid past-to-future projections, butterfly effects, calibrated scenario analysis, domain-pack modeling, public-role actor decisions with sealed research/roleplay separation, and auditable uncertainty.
---

# Aleph Skill 2.0.1

Model how a defined intervention propagates through a causal system. Treat Aleph as a simulation protocol, never an oracle. Keep these labels distinct:

- `fact`: directly supported observed-world evidence;
- `inference`: analyst interpretation of evidence;
- `assumption`: declared model premise;
- `simulation`: computed or roleplayed output;
- `counterfactual`: event known not to belong to observed history.

## Start every run

1. Define the change point, observation cutoff, simulation interval, domains, and geographies.
2. Select exactly one temporal mode from `references/temporal-modes.md`.
3. Assess all seven complexity dimensions in `references/adaptive-research-workflow.md`; do not ask the user to select a speed or source-count profile.
4. Initialize schema `2.0.0` artifacts in a user workspace outside this skill directory.
5. Resolve the installed skill directory to an absolute `ALEPH_SKILL_ROOT`; verify that it contains this `SKILL.md`, and never resolve helpers from the process working directory.
6. Run `<ALEPH_SKILL_ROOT>/scripts/preflight.py`. Use D Research only when its exact identity and compatible 3.x major pass discovery. Otherwise use the limited host-native fallback in `references/d-research-integration.md`.
7. Detect the host's actual tools/subagent capabilities. Never infer capability from an adapter file.
8. Read `references/artifact-contract.md` before modifying artifacts.

## Execute the simulation

1. Research the baseline, mechanisms, contradictions, actors, and measurable factors until evidence saturation. Checkpoint every wave. If a host limit interrupts the run, publish an honest unsaturated handoff that can be resumed. Follow `references/adaptive-research-workflow.md`.
2. When compatible D Research is available, import its signed ledger with `<ALEPH_SKILL_ROOT>/scripts/import_research_ledger.py` and preserve the source ledger and HMAC sidecar. Otherwise build the evidence map directly from opened host-native sources, retain explicit provenance, omit the D Research import receipt, and cap assurance at `limited`.
3. Build typed nodes and admitted causal edges. Read `references/node-builder.md` and `references/causal-edge-protocol.md`.
4. Compile and run the deterministic or Monte Carlo engine. Preserve config/model hashes, samples, invalid mass, traces, and replay material. Read `references/propagation-engine.md`.
5. Cluster distinct scenario branches. Use `relative_weight` unless a declared calibration policy and hindcast gate authorize `calibrated_probability`. Read `references/branch-management.md`.
6. Render the report using `references/reporting-contract.md`, validate, finalize atomically, then verify receipts.

## Seal material human decisions

Read `references/human-node-protocol.md` and `references/safety-and-privacy.md` whenever a person can materially change a branch.

1. Refuse minors, private-person profiling, doxxing, stalking, sensitive personal data, private motives, or manipulation before network access.
2. Use a dedicated research execution to create an evidence-backed public-role dossier. It must not roleplay.
3. Freeze the dossier and build a temporal packet with `<ALEPH_SKILL_ROOT>/scripts/actor_packet.py`. Admit only claims available to and accessible by the actor at the decision cutoff. Excluded claim content never enters the packet.
4. Use a distinct offline roleplay execution. It receives only the sealed packet and proposes at least two actions from the declared decision graph.
5. Reject roleplay that browses, calls tools, adds facts/evidence, invents private motives, or emits probability, confidence, or relative weight.
6. Let the main simulator adjudicate hypotheses against evidence. Only the adjudicator may assign `relative_weight`; it may assign `calibrated_probability` only after every calibration and validation gate passes.
7. Record hashed inputs/outputs, distinct execution/agent IDs, timestamps, policies, and an HMAC receipt chain in `human-track-ledger.jsonl`.

## Hard gates

- Every material fact, node, edge, and actor claim resolves to provenance or an explicit assumption.
- Every admitted edge has a mechanism, sign, effect parameter, lag, context, evidence confidence, and replayable transform.
- No post-cutoff claim is labeled fact or exposed to roleplay.
- Invalid/nonconvergent Monte Carlo mass is reported and cannot be silently renormalized.
- Branch probability is forbidden without calibration evidence; diagnostic score never grants an assurance tier.
- Material actor research and roleplay are separate, ordered, sealed, receipt-backed executions.
- Every artifact path remains workspace-relative; installers copy only the verified distribution manifest and never secrets or symlinks.
- Final output passes strict schema/semantic validation, replay, integrity, assurance, privacy, and report gates.

If a gate fails, repair it or publish an explicitly unsaturated partial result with the blocker. A partial handoff may declare `research_quality: limited`, but it has no final assurance tier. Never relabel a failure as verified.

## Resource router

- Full phases and stopping rules: `references/simulation-workflow.md`
- Exact artifacts and IDs: `references/artifact-contract.md`
- D Research ledger contract: `references/d-research-integration.md`
- Temporal semantics: `references/temporal-modes.md`
- Nodes and edges: `references/node-builder.md`, `references/causal-edge-protocol.md`
- Engine, uncertainty, and replay: `references/propagation-engine.md`
- Sealed actors: `references/human-node-protocol.md`, `references/safety-and-privacy.md`
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

```text
python "<ALEPH_SKILL_ROOT>/scripts/preflight.py" --json
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
