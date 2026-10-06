#!/usr/bin/env python3
"""CLI interface for Aleph Multi-Actor Multi-Turn Strategic Roleplay (P6).

Supports operations: init, packets, commit, advance, finalize, replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aleph.engine import ComputationalModel, EngineConfig, ModelEdge, Variable
from aleph.interactive_roleplay import (
    advance_roleplay_round,
    commit_actor_action,
    finalize_roleplay_session,
    generate_actor_packets,
    init_roleplay_session,
    replay_roleplay_session,
)
from aleph.io import canonical_hash, load_json_secure, write_json_atomic


def _load_object(path: str | Path) -> dict:
    data, issues = load_json_secure(Path(path))
    if issues or not isinstance(data, dict):
        raise ValueError(f"INVALID_ARTIFACT: {path}: expected a valid JSON object")
    return data


def _session_config(session: dict) -> EngineConfig:
    value = session.get("engine_config")
    if not isinstance(value, dict):
        raise ValueError("SESSION_CONFIG_MISSING: initialize a new session")
    return EngineConfig(**value)


def _load_model(path: str | Path) -> ComputationalModel:
    data = _load_object(path)
    variables = {}
    for k, v in data.get("variables", {}).items():
        if isinstance(v, dict):
            v_copy = dict(v)
            v_copy.setdefault("role", "endogenous")
            variables[k] = Variable(**v_copy)
        else:
            variables[k] = v
    edges = []
    for e in data.get("edges", []):
        if isinstance(e, dict):
            e_copy = dict(e)
            edges.append(ModelEdge(**e_copy))
        else:
            edges.append(e)
    interventions = data.get("interventions", [])
    formula_version = data.get("formula_version", "2.1")
    return ComputationalModel(
        variables=variables,
        edges=edges,
        interventions=interventions,
        formula_version=formula_version,
    )


def _resolve_model(args: argparse.Namespace) -> ComputationalModel:
    if getattr(args, "model", None):
        return _load_model(args.model)
    if getattr(args, "workspace", None):
        ws = Path(args.workspace)
        cand = ws / "simulation-model.json"
        if not cand.is_file():
            cand = ws / "model.json"
        if cand.is_file():
            return _load_model(cand)
    raise ValueError("Either --model or --workspace containing simulation-model.json must be provided")


def cmd_init(args: argparse.Namespace) -> int:
    spec = _load_object(args.spec)
    model = _resolve_model(args)
    config = EngineConfig(**_load_object(args.config)) if args.config else EngineConfig(seed=42)
    session = init_roleplay_session(spec, model, config)
    out_path = Path(args.out) if args.out else Path(args.spec).parent / f"session-{quote(session['session_id'], safe='')}.json"
    write_json_atomic(out_path, session)
    print(json.dumps({"ok": True, "session_id": session["session_id"], "session_path": str(out_path)}, indent=2))
    return 0


def cmd_packets(args: argparse.Namespace) -> int:
    session_path = Path(args.session)
    session = _load_object(session_path)
    model = _resolve_model(args)
    packets = generate_actor_packets(session, model, _session_config(session))
    write_json_atomic(session_path, session)

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for actor, packet in packets.items():
            packet_file = out_dir / f"packet-r{session['current_round']}-{quote(actor, safe='')}.json"
            write_json_atomic(packet_file, packet)

    print(json.dumps({
        "ok": True,
        "round_index": session["current_round"],
        "actors": list(packets.keys()),
        "packet_hashes": {a: p["packet_hash"] for a, p in packets.items()},
        "packets": packets if not args.out_dir else None,
    }, indent=2))
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    session_path = Path(args.session)
    session = _load_object(session_path)
    commit_record = commit_actor_action(
        session,
        args.actor,
        args.action,
        args.packet_hash,
        rationale=args.rationale or "",
    )
    write_json_atomic(session_path, session)
    print(json.dumps({"ok": True, "commit": commit_record}, indent=2))
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    session_path = Path(args.session)
    session = _load_object(session_path)
    if args.spec and canonical_hash(_load_object(args.spec)) != session.get("spec_hash"):
        raise ValueError("SESSION_SPEC_MISMATCH: --spec differs from the initialized session")
    model = _resolve_model(args)
    round_record = advance_roleplay_round(session, model, _session_config(session))
    write_json_atomic(session_path, session)
    print(json.dumps({
        "ok": True,
        "round_index": round_record["round_index"],
        "payoffs": round_record["payoffs"],
        "status": session["status"],
    }, indent=2))
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    session_path = Path(args.session)
    session = _load_object(session_path)
    res = finalize_roleplay_session(session)
    write_json_atomic(session_path, session)
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


def cmd_replay(args: argparse.Namespace) -> int:
    session_path = Path(args.session)
    session = _load_object(session_path)
    model = _resolve_model(args)
    res = replay_roleplay_session(session, model, _session_config(session))
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Aleph Multi-Actor Interactive Roleplay CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init")
    p_init.add_argument("--spec", required=True, help="Path to roleplay session spec JSON")
    p_init.add_argument("--model", help="Path to initial computational model JSON")
    p_init.add_argument("--workspace", help="Path to workspace directory containing simulation-model.json")
    p_init.add_argument("--config", help="EngineConfig JSON persisted with the session")
    p_init.add_argument("--out", help="Optional output path for session JSON")

    # packets / packet
    p_packets = subparsers.add_parser("packets", aliases=["packet"])
    p_packets.add_argument("--session", required=True, help="Path to session JSON")
    p_packets.add_argument("--model", help="Path to computational model JSON")
    p_packets.add_argument("--workspace", help="Path to workspace directory containing simulation-model.json")
    p_packets.add_argument("--out-dir", help="Optional directory to write actor packets")

    # commit
    p_commit = subparsers.add_parser("commit")
    p_commit.add_argument("--session", required=True, help="Path to session JSON")
    p_commit.add_argument("--actor", required=True, help="Actor ID")
    p_commit.add_argument("--action", required=True, help="Chosen action ID")
    p_commit.add_argument("--packet-hash", "--packet", dest="packet_hash", required=True, help="Packet hash issued for actor")
    p_commit.add_argument("--rationale", help="Reasoning for action")

    # advance
    p_adv = subparsers.add_parser("advance")
    p_adv.add_argument("--session", required=True, help="Path to session JSON")
    p_adv.add_argument("--model", help="Path to computational model JSON")
    p_adv.add_argument("--workspace", help="Path to workspace directory containing simulation-model.json")
    p_adv.add_argument("--spec", help="Optional spec path (overridden by session spec)")

    # finalize
    p_fin = subparsers.add_parser("finalize")
    p_fin.add_argument("--session", required=True, help="Path to session JSON")

    # replay
    p_rep = subparsers.add_parser("replay")
    p_rep.add_argument("--session", required=True, help="Path to session JSON")
    p_rep.add_argument("--model", help="Path to computational model JSON")
    p_rep.add_argument("--workspace", help="Path to workspace directory containing simulation-model.json")

    args = parser.parse_args()
    if args.command == "init":
        return cmd_init(args)
    if args.command in ("packets", "packet"):
        return cmd_packets(args)
    if args.command == "commit":
        return cmd_commit(args)
    if args.command == "advance":
        return cmd_advance(args)
    if args.command == "finalize":
        return cmd_finalize(args)
    if args.command == "replay":
        return cmd_replay(args)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        sys.exit(2)
