# Aleph Skill Agent Instructions

Use this skill when the user asks for counterfactual history, causal timeline simulation, butterfly-effect analysis, scenario branching, or evidence-backed reconstruction from a point of change.

Core behavior:

1. Treat every result as uncertain. Prefer `relative_weight` until a domain/decision model is calibrated; never invent bare probability.
2. Separate fact, inference, simulation, assumption, and counterfactual content.
3. Resolve an absolute `ALEPH_SKILL_ROOT` from the loaded skill or verified adapter core. Never invoke helpers relative to the process working directory.
4. Discover D Research via explicit flag → `D_RESEARCH_SKILL` → capability file → conventional skill paths (never hardcoded developer paths). Import only `record_type=claim` via the `evidence` field.
5. If D Research is missing, ask once whether to install it. If declined, use host-native research tools to populate the evidence map with explicit source provenance; do not create a D Research ledger/import receipt, cap assurance at `limited`, and never expose research tools or evidence to roleplay.
6. Run privacy intake before research/roleplay. Refuse private persons, minors, unknown subjects, doxxing, stalking, and re-identification.
7. Build causal edges only with mechanism, evidence/assumption, lag, context modifiers, and effect parameters. Keep effect size separate from evidence confidence.
8. For material actors: freeze dossier → temporal knowledge packet → sealed roleplay → adjudicator. Distinct execution IDs and receipt chain required. Roleplay never emits probability or new evidence.
9. Record human tracks and receipts; prose claims of separation are insufficient.
10. Initialize schema `2.0.0` artifacts before research; migrate legacy `1.2.0` with `sim:migrate`. No protocol speed profiles, source caps, or elapsed-time caps. Checkpoint each research wave; if a host limit interrupts before saturation, persist `execution.research_control.next_wave_queue`, set `research_quality: limited`, and hand off an unsaturated partial result with unresolved gaps and no final assurance tier.
11. Validate with formula replay, finalize atomically, and score with assurance tiers (`experimental|limited|verified|calibrated`). Diagnostic score cannot override hard gates. `excellent` is legacy display only.

Useful commands:

- `python "<ALEPH_SKILL_ROOT>/scripts/preflight.py" --json`
- `python "<ALEPH_SKILL_ROOT>/scripts/init_simulation_workspace.py" --slug <slug> --change-point "..." --time <date> --horizon <duration> --observation-cutoff <date> --out-dir <user-workspace>`
- `python "<ALEPH_SKILL_ROOT>/scripts/migrate_workspace.py" --source <1.2-dir> --out <sibling-v2>`
- `python "<ALEPH_SKILL_ROOT>/scripts/validate_simulation_artifacts.py" --workspace <run-dir> --mode final --require-report`
- `python "<ALEPH_SKILL_ROOT>/scripts/run_simulation.py" --workspace <run-dir> --mode deterministic --seed <seed>`
- `python "<ALEPH_SKILL_ROOT>/scripts/evaluate_simulation_quality.py" --workspace <run-dir>`
- `python "<ALEPH_SKILL_ROOT>/scripts/validate_domain_packs.py"`
- `python "<ALEPH_SKILL_ROOT>/scripts/check_adapters.py"`

Output standards:

- Uncalibrated or experimental runs use normalized `relative_weight` only; it is a ranking, not probability. Use `calibrated_probability` only after the declared calibration and validation gates pass, and never mix the two modes.
- Include assumptions, likelihood mode, mechanisms, evidence quality, sensitivity, warnings, and next steps.
- Do not claim `verified`/`calibrated` unless hard gates and receipts pass.
