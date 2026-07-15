# Forward-test prompts

Use these prompts to test whether the skill loads the right references, applies evidence gates, and keeps uncertainty explicit.

## Lehman bailout

Use $aleph-skill to simulate: What if Lehman Brothers was bailed out in September 2008? Build 3-5 timeline branches through 2016 with sourced mechanisms and confidence warnings. Use the locked bundled D Research component through Aleph's gateway; use the limited host-native fallback only after the gateway reports a capability blocker, and split material human decisions into research and roleplay tracks.

Expected behavior:

- Research the 2008 baseline and decision makers.
- Mark the bailout as counterfactual.
- Branch on credit freeze, moral hazard, political backlash, and regulatory reform.
- Do not claim a single definitive outcome.

## Oil shock

Use $aleph-skill to simulate an oil price +40% shock. Set both the observation cutoff and shock start to 2026-06-01. Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months. Use the locked bundled D Research component through Aleph's gateway; use the limited host-native fallback only after the gateway reports a capability blocker, and split material human decisions into research and roleplay tracks.

Expected behavior:

- Use factor/event/context nodes.
- Propagate oil to transport, production costs, inflation, rates, growth, and EM stress.
- Include context modifiers for geopolitical tension and energy dependence.
- Report sensitivity to pass-through assumptions.

## Waterloo

Use $aleph-skill to simulate: What if Napoleon won at Waterloo in 1815? Build a cautious alternate-history tree through 1870. Use the locked bundled D Research component through Aleph's gateway; use the limited host-native fallback only after the gateway reports a capability blocker, and split material human decisions into research and roleplay tracks.

Expected behavior:

- Warn that evidence is historical and counterfactual confidence is limited.
- Research major actors and institutions.
- Branch on British coalition recovery, French consolidation, Prussian reform, and German unification.
- Keep the timeline uncertain. Use normalized `relative_weight` as a branch ranking unless calibration and validation gates authorize `calibrated_probability`.

## Required D Research capability blocked

Use $aleph-skill to simulate a public-policy intervention. Verify the locked bundled D Research component through Aleph's gateway, but treat the required research route capability as blocked after preflight. Use only the host's lawful research tools, preserve explicit source provenance, keep public-role research separate from sealed roleplay, and checkpoint the run for a later execution if it cannot reach evidence saturation.

Expected behavior:

- Record the gateway capability blocker and build the standard evidence map without fabricating a D Research ledger, HMAC sidecar, or import receipt.
- Cap assurance at `limited`, even if every other applicable gate passes.
- Persist the next-wave frontier and return an honest unsaturated partial handoff with a `host_limit:` stop reason if the host interrupts the run.
