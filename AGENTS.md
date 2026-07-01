# Aleph Timeline Simulator Agent Instructions

Use this skill when the user asks for counterfactual history, causal timeline simulation, butterfly-effect analysis, scenario branching, Aleph causal KB work, or evidence-backed reconstruction from a point of change.

Core behavior:

1. Treat every result as probabilistic. Do not present a single future as definitive.
2. Separate fact, inference, simulation, and counterfactual content.
3. Use D Research for source discovery, evidence ledgers, contradiction checks, and public-role actor research when available.
4. Use the Aleph repo only through local paths, environment variables, or explicit GitHub access. Do not vendor Aleph into this skill.
5. Refuse or narrow requests that require private personal data, doxxing, stalking, minors, private accounts, access-control bypass, captcha evasion, or paywall bypass.
6. Build causal edges only when the mechanism, evidence, lag, context modifiers, strength, and confidence are explicit.
7. For person nodes, use public-role dossiers and institutional constraints. Treat roleplay as hypothesis generation, never evidence.
8. Always run validation scripts when creating artifacts.

Useful commands:

- `python scripts/preflight.py --aleph <path> --d-research <path>`
- `python scripts/init_simulation_workspace.py --slug <slug> --change-point "<description>"`
- `python scripts/validate_skill_package.py .`
- `python scripts/validate_simulation_artifacts.py --workspace <run-dir>`
- `python scripts/render_simulation_report.py --workspace <run-dir> --out <report.md>`

Output standards:

- Include assumptions, branch probabilities, mechanism highlights, evidence quality, sensitivity points, warnings, and next steps.
- Do not claim completeness unless all relevant gates passed.
