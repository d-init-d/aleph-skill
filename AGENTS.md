# Aleph Skill Agent Instructions

Use this skill when the user asks for counterfactual history, causal timeline simulation, butterfly-effect analysis, scenario branching, or evidence-backed reconstruction from a point of change.

Core behavior:

1. Treat every result as uncertain. Prefer `relative_weight` until a domain/decision model is calibrated; never invent bare probability.
2. Separate fact, inference, simulation, assumption, and counterfactual content.
3. Discover D Research via explicit flag → `D_RESEARCH_SKILL` → capability file → conventional skill paths (never hardcoded developer paths). Import only `record_type=claim` via the `evidence` field.
4. If D Research is missing, ask once whether to install it; if declined, continue limited and do not claim `verified`.
5. Run privacy intake before research/roleplay. Refuse private persons, minors, unknown subjects, doxxing, stalking, and re-identification.
6. Build causal edges only with mechanism, evidence/assumption, lag, context modifiers, and effect parameters. Keep effect size separate from evidence confidence.
7. For material actors: freeze dossier → temporal knowledge packet → sealed roleplay → adjudicator. Distinct execution IDs and receipt chain required. Roleplay never emits probability or new evidence.
8. Record human tracks and receipts; prose claims of separation are insufficient.
9. Initialize schema `2.0.0` artifacts before research; migrate legacy `1.2.0` with `sim:migrate`. No speed profiles or source caps.
10. Validate with formula replay, finalize atomically, and score with assurance tiers (`experimental|limited|verified|calibrated`). Diagnostic score cannot override hard gates. `excellent` is legacy display only.

Useful commands:

- `python scripts/preflight.py --json`
- `python scripts/init_simulation_workspace.py --slug <slug> --change-point "..." --time <date> --horizon <duration> --observation-cutoff <date> --out-dir <user-workspace>`
- `python scripts/migrate_workspace.py --source <1.2-dir> --out <sibling-v2>`
- `python scripts/validate_simulation_artifacts.py --workspace <run-dir> --mode final --require-report`
- `python scripts/run_simulation.py --workspace <run-dir> --mode deterministic --seed <seed>`
- `python scripts/evaluate_simulation_quality.py --workspace <run-dir>`
- `python scripts/validate_domain_packs.py`
- `python scripts/check_adapters.py`

Output standards:

- Include assumptions, relative weights or calibrated probabilities (never mixed), mechanisms, evidence quality, sensitivity, warnings, and next steps.
- Do not claim `verified`/`calibrated` unless hard gates and receipts pass.
