# Report

## Executive summary

This fixture evaluates a 25bp policy rate hike and its transmission to the output gap under open-economy conditions with explicit uncertainty.

## Methodology and scope

Deterministic qualitative model with relative_weight branches. Research quality is limited without D Research verification.

## Baseline and change point

Baseline policy rate is 4.0 percent. Change point is a 25bp hike on 2026-01-01.

## Evidence and source quality

Two user-provided fixture sources document the institutional mandate and macro series used for mechanisms.

## Causal architecture and propagation

Edge causal:rate-to-gap decreases the output gap with fixed lag and open-economy multiplier 1.1. Trace effect recomputed as -0.44.

## Scenario branches

Three scenario views: sustained tightening, external offset, and a stress view with null probability.

## Future monitoring and probability updates

Watch policy rate and output gap over P90D-P180D windows; disconfirm if credit accelerates after the hike.

## Human decision tracks

Governor public-role research and sealed roleplay used distinct agent references; adjudication emits relative_weight only.

## Sensitivity, contradictions, and limitations

Results are sensitive to openness multiplier and external demand. Not a calibrated forecast.

## Validation and audit

Schema 2.0 validation with formula replay and path containment.

## Source appendix

- evidence:policy-statute — institutional mandate excerpt
- evidence:macro-series — macro transmission notes

## Warnings and next steps

Uncalibrated relative weights only. Re-run with hindcasts before any probability claims.
