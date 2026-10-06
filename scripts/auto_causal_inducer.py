#!/usr/bin/env python3
"""CLI wrapper for Auto-Causal Induction pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add scripts directory to path for stdlib execution
sys.path.insert(0, str(Path(__file__).parent))

from aleph import EXIT_OK, EXIT_SEMANTIC, EXIT_USAGE
from aleph.auto_causal import apply_model, propose_model, review_model
from aleph.io import load_json_secure, write_json_atomic


def run_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-causal induction bridge from evidence ledgers to simulation models."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: propose-model (alias: propose)
    p_propose = subparsers.add_parser(
        "propose-model",
        aliases=["propose"],
        help="Generate causal proposal and gap report from evidence",
    )
    p_propose.add_argument("--ledger", help="Path to evidence CSV ledger")
    p_propose.add_argument("--evidence", help="Path to evidence JSON artifact")
    p_propose.add_argument("--proposal-id", help="Explicit proposal ID")
    p_propose.add_argument("--out", required=True, help="Output path for causal-proposal.json")

    # Subcommand: review-model (alias: review)
    p_review = subparsers.add_parser(
        "review-model",
        aliases=["review"],
        help="Generate review and admission decisions for proposal",
    )
    p_review.add_argument("--proposal", required=True, help="Path to causal-proposal.json")
    p_review.add_argument("--reviewer", default="deterministic:causal-screening", help="Reviewer identity")
    p_review.add_argument("--decisions", help="Path to optional manual decisions JSON overrides")
    p_review.add_argument("--out", required=True, help="Output path for causal-review.json")

    # Subcommand: apply-model (aliases: apply, admit)
    p_apply = subparsers.add_parser(
        "apply-model",
        aliases=["apply", "admit"],
        help="Apply admitted proposal & review into a workspace",
    )
    p_apply.add_argument("--proposal", required=True, help="Path to causal-proposal.json")
    p_apply.add_argument("--review", required=True, help="Path to causal-review.json")
    p_apply.add_argument("--workspace", required=True, help="Target workspace directory")
    p_apply.add_argument("--workspace-id", default="workspace:auto-causal-model", help="Simulation workspace ID")

    args = parser.parse_args()

    if args.command in ("propose-model", "propose"):
        src = args.ledger or args.evidence
        if not src:
            print("Error: Either --ledger or --evidence must be provided", file=sys.stderr)
            return EXIT_USAGE
        proposal = propose_model(src, proposal_id=args.proposal_id)
        write_json_atomic(Path(args.out), proposal)
        print(f"Proposal written to {args.out} (candidates: {len(proposal['candidate_edges'])} edges, gaps: {len(proposal['gaps'])})")
        return EXIT_OK

    if args.command in ("review-model", "review"):
        prop_data, issues = load_json_secure(Path(args.proposal))
        if issues or not isinstance(prop_data, dict):
            print(f"Error loading proposal from {args.proposal}", file=sys.stderr)
            return EXIT_SEMANTIC
        decisions_data = None
        if args.decisions:
            decisions_data, issues = load_json_secure(Path(args.decisions))
            if issues or not isinstance(decisions_data, dict):
                print("Error: decisions must be a valid JSON object", file=sys.stderr)
                return EXIT_SEMANTIC
        review = review_model(prop_data, reviewer=args.reviewer, decisions=decisions_data)
        write_json_atomic(Path(args.out), review)
        print(f"Review written to {args.out} (status: {review['overall_status']})")
        return EXIT_OK

    if args.command in ("apply-model", "apply", "admit"):
        prop_data, prop_issues = load_json_secure(Path(args.proposal))
        rev_data, review_issues = load_json_secure(Path(args.review))
        if prop_issues or review_issues or not isinstance(prop_data, dict) or not isinstance(rev_data, dict):
            print("Error loading proposal or review", file=sys.stderr)
            return EXIT_SEMANTIC
        try:
            res = apply_model(prop_data, rev_data, Path(args.workspace), workspace_id=args.workspace_id)
            print(json.dumps(res, indent=2))
            return EXIT_OK if res.get("ok") is True else EXIT_SEMANTIC
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Error applying model: {exc}", file=sys.stderr)
            return EXIT_SEMANTIC

    return EXIT_USAGE


def main() -> int:
    try:
        return run_cli()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return EXIT_SEMANTIC


if __name__ == "__main__":
    sys.exit(main())
