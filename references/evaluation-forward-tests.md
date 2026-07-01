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

## Package checks

Run:

```powershell
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python scripts\preflight.py --json
```

If forward-testing with subagents is allowed by the host runtime, pass only the skill path and one prompt. Do not pass expected answers.
