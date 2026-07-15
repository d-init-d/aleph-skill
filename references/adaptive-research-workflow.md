# Adaptive research workflow

Use the locked bundled D Research component as the preferred evidence engine and this protocol as the causal-simulation outer loop. If gateway preflight reports a missing capability, use the limited host-native workflow in `references/d-research-integration.md`. Depth is determined by the event, not by a user-selected speed mode.

## 1. Frame the investigation

Record:

- the change point and observed-world contrast,
- observation cutoff and simulation end,
- temporal mode,
- domains and geographies,
- material actors and institutions,
- desired decision/report use,
- safety or source restrictions.

## 2. Assess complexity

Score each dimension from `0.0` to `1.0` and explain the score:

- temporal span,
- domain breadth,
- geographic breadth,
- actor density,
- causal depth,
- evidence uncertainty,
- stakes.

Set `overall_complexity` to approximately their mean. This score is not a speed setting or work cap. It determines decomposition breadth, number of research waves, subagent specialization, graph depth, sensitivity coverage, and the amount of evidence needed before saturation.

The validator applies proportional minimums for decomposition questions, critical paths, research waves, directly accessed primary/authoritative evidence, branch diversity, stabilization waves, and future monitoring conditions. These are floors, never ceilings; continue beyond them whenever critical uncertainty remains.

Reassess upward whenever research reveals new critical actors, domains, feedback loops, contradictions, or long-lag effects. Do not reduce complexity merely to finish faster.

## 3. Decompose the question

Create:

- root causal question,
- baseline-state questions,
- mechanism questions,
- actor/institution questions,
- threshold and feedback questions,
- branch-trigger questions,
- future-indicator questions for prospective/hybrid work,
- unknowns, risks, and critical evidence gaps.

Write subquestions and critical paths to the manifest before broad research.

## 4. Build a source map

Map likely primary and authoritative sources for each subquestion:

- official records, datasets, filings, archives, laws, transcripts, and statistics,
- academic papers and methodological reports,
- institutional analyses,
- high-quality contemporary reporting,
- public-role speeches, testimony, decisions, and memoirs for human actors,
- contradiction sources and alternative interpretations.

With bundled D Research, follow its browser-first/tool-priority rules through Aleph's gateway. In limited fallback mode, use only the host's declared lawful research capabilities and record the retrieval method for every source. Never bypass access controls.

## 5. Generate query fanout

For every critical subquestion, generate broad, exact, official, primary-source, filetype, dataset/API, contemporary, contradiction, and alternate-language queries when useful.

Do not conclude “not found” from one query family.

## 6. Execute research waves

Run research in waves and write findings immediately:

1. baseline and chronology,
2. causal mechanisms and measured analogues,
3. material actors and institutions,
4. contradictions and alternative explanations,
5. thresholds, feedback loops, and cross-domain spillovers,
6. prospective indicators and disconfirming evidence,
7. frontier expansion for remaining critical gaps.

Small, local, well-documented changes may saturate in one or two waves. Large, long-horizon, multi-domain, high-stakes, or weak-evidence changes should continue across as many waves and specialized subagents as necessary. The protocol imposes no source-count or elapsed-time ceiling.

After every wave, persist the research plan, completed subquestions, sources examined, atomic claims, contradictions, unresolved frontiers, complexity reassessment, and next-wave queue in the simulation workspace. Store resumable work items in `execution.research_control.next_wave_queue`. Update `research_waves_completed`, `sources_examined`, checkpoint flags, and `unresolved_critical_gaps` before dispatching more work. When D Research is available, preserve its plan and ledger too.

A host context, token, wall-clock, or tool budget may interrupt one execution even though it is not a protocol stopping rule. Before yielding, write a resumable checkpoint. If critical gaps remain, set `saturation_reached: false`, record a precise `stop_reason` beginning with `host_limit:`, keep unresolved gaps explicit, populate `execution.research_control.next_wave_queue`, and set `execution.research_quality: limited`. This is an unsaturated partial handoff, so it has no final assurance tier. A later execution resumes from the persisted frontier; it does not restart or relabel the partial result as complete. Only a later saturated, finalized fallback run may receive the `limited` assurance tier.

## 7. Maintain the evidence ledger

Every material claim needs source URL/path, source tier, retrieval status, date, excerpt/value, contradiction status, and confidence. Separate facts, inference, simulation, counterfactual assumptions, unknowns, and blocked sources.

Search snippets are discovery aids, not strong evidence. Prefer opened primary/authoritative sources in proportion to adaptive complexity.

## 8. Run human tracks

For each material actor, complete a dedicated public-role research dossier through the bundled D Research gateway or, after a capability blocker, the limited host-native fallback before dispatching the separate roleplay track. Freeze the dossier and simulated-time knowledge cutoff. Roleplay cannot browse, call research tools, inspect the evidence map, or add evidence.

## 9. Construct and challenge the causal graph

Build nodes and edges only after evidence is mapped. For every critical edge:

- specify transmission channel, lag, sign, strength, confidence, and contexts,
- search for contrary evidence and rival explanations,
- identify thresholds and feedback,
- calibrate against observed analogues where possible,
- record sensitivity and downstream branch triggers.

## 10. Expand unresolved frontiers

If a critical subquestion has missing evidence, only weak/secondary support, unresolved contradictions, or unstable human behavior, launch another targeted wave. Prioritize frontier nodes by downstream causal importance rather than curiosity.

## 11. Saturation gate

Set `saturation_reached: true` only when:

- every critical subquestion has sufficient evidence or an explicit non-critical limitation,
- primary/authoritative sources support the main mechanisms,
- contradiction searches no longer materially change claim confidence,
- additional sources no longer add material actors, mechanisms, thresholds, or branches,
- branch `relative_weight` rankings stabilize, or—only after every calibration and validation gate passes—`calibrated_probability` and sensitivity rankings stabilize,
- no unresolved critical evidence gap remains.

Do not treat run length as evidence saturation. Do not continue merely to accumulate duplicate sources after saturation. If the host interrupts before this gate passes, use the resumable partial handoff above rather than claiming completion.

## 12. Professional synthesis

Produce a decision-grade report with executive summary, methodology, temporal framing, baseline, evidence quality, causal architecture, propagation, declared likelihood mode, human decision tracks, sensitivity, contradictions, future indicators, audit results, limitations, and source appendix.
