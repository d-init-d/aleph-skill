# Evaluation and forward tests

Use these scenarios to evaluate skill behavior after changes.

## Test 1: Lehman bailout

Prompt:

```text
Use $aleph-skill to simulate: What if Lehman Brothers was bailed out in September 2008? Build 3-5 timeline branches through 2016 with sourced mechanisms and confidence warnings. Use D Research for evidence and split material human decisions into research and roleplay tracks.
```

Pass conditions:

- Marks bailout as counterfactual.
- Researches Fed, Treasury, Lehman, credit markets, political backlash, and reform context.
- Produces at least three branches.
- Avoids claiming a single true alternate history.
- Separates public-role actor research from any roleplay hypothesis for material decision makers.
- Reports weak edges such as moral hazard timing.
- Records two distinct human-track executions per material actor when a subagent tool is exposed.
- Passes final validation, replay, integrity, privacy, and assurance gates. A diagnostic score cannot override those gates.

## Test 2: 2026 oil shock

Prompt:

```text
Use $aleph-skill to simulate an oil price +40% shock starting June 2026. Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months. Use D Research for evidence and split material human decisions into research and roleplay tracks.
```

Pass conditions:

- Uses measurable factor nodes.
- Includes context modifiers such as geopolitical tension and energy import dependence.
- Shows propagation through transport costs, inflation, rates, growth, and EM stress.
- Separates central-bank/government decision research from any roleplay hypothesis.
- Reports sensitivity to pass-through and policy reaction assumptions.
- Uses directly opened primary/authoritative evidence rather than relying on search snippets.
- Infers `prospective_intervention` when the shock begins at the observation cutoff and projects only conditional future branches.
- Gives each branch leading indicators and disconfirming conditions.

## Test 3: Waterloo

Prompt:

```text
Use $aleph-skill to simulate: What if Napoleon won at Waterloo in 1815? Build a cautious alternate-history tree through 1870. Use D Research for evidence and split material human decisions into research and roleplay tracks.
```

Pass conditions:

- Explicitly warns about historical counterfactual uncertainty.
- Builds actor/institution nodes.
- Branches on coalition recovery, French consolidation, Prussian reform, and German unification.
- Avoids presentist hindsight in actor knowledge.
- Keeps commander/statesman roleplay as simulation-only and separate from sourced historical research.
- Resolves every evidence/node/edge/actor/branch/trace ID reference.

## Test 4: present intervention

Prompt:

```text
Use $aleph-skill to simulate a 200-basis-point policy-rate cut effective at the current observation cutoff. Project the next 36 months across inflation, credit, housing, labor markets, exchange rates, and political response. Use D Research, investigate material decision makers in separate research and roleplay tracks, and produce a professional report.
```

Pass conditions:

- Automatically selects `prospective_intervention`; it does not ask for an execution profile.
- Labels every post-cutoff node as inference or simulation, never fact or observed baseline.
- Research depth expands with cross-domain complexity and stops only after evidence saturation is documented.
- Produces at least three probability-normalized branches with observable leading indicators and disconfirming conditions.
- Identifies what evidence would update each branch probability.

## Test 5: hybrid past-to-future projection

Prompt:

```text
Use $aleph-skill to simulate a constitutional divergence beginning in 2010, reconstruct its alternate path to the present observation cutoff, then project the resulting system through 2035. Use D Research and produce a professional report with explicit temporal-knowledge controls.
```

Pass conditions:

- Automatically selects `hybrid_projection`.
- Separates shared baseline, observed baseline, and simulated-branch nodes.
- Prevents actors from using information unavailable at each modeled decision time.
- Distinguishes reconstructed counterfactual history from prospective uncertainty after the observation cutoff.
- Carries uncertainty forward rather than resetting confidence at the present boundary.

## Package checks

Run:

```powershell
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python -m unittest discover -s tests -v
python scripts\preflight.py --json
```

If forward-testing with subagents is allowed by the host runtime, pass only the skill path and one prompt. Do not pass expected answers.
