"""Multi-Actor Multi-Turn Strategic Roleplay Protocol for Aleph.

Implements simultaneous decision rounds, information isolation via actor packets,
additive intervention translation, scripted payoff adjudication, and bit-for-bit
deterministic replay.
"""

from __future__ import annotations

import copy
import datetime
import itertools
import math
from dataclasses import asdict
from typing import Any

from .engine import ComputationalModel, EngineConfig, config_payload, model_hash, run_deterministic
from .io import canonical_hash

ROLEPLAY_SPEC_SCHEMA_VERSION = "1.0.0"


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _verify_execution_identity(
    session: dict[str, Any], model: ComputationalModel, config: EngineConfig
) -> None:
    """Reject stale execution inputs before producing packets or mutating a round."""
    expected_model = session.get("model_hash")
    expected_config = session.get("config_hash")
    if not expected_model or not expected_config:
        raise ValueError("SESSION_IDENTITY_MISSING: initialize a new session with its model and config")
    if model_hash(model) != expected_model:
        raise ValueError("SESSION_MODEL_MISMATCH: model differs from the initialized session")
    if session.get("formula_version") != model.formula_version:
        raise ValueError("SESSION_FORMULA_MISMATCH: formula differs from the initialized session")
    if canonical_hash(config_payload(config)) != expected_config:
        raise ValueError("SESSION_CONFIG_MISMATCH: execution config differs from the initialized session")
    if canonical_hash(session.get("spec")) != session.get("spec_hash"):
        raise ValueError("SESSION_SPEC_MISMATCH: session specification changed after initialization")


def validate_session_spec(
    spec: dict[str, Any],
    model: ComputationalModel | None = None,
) -> tuple[bool, list[str]]:
    """Validate a roleplay session specification according to P6.A rules."""
    errors: list[str] = []

    if not isinstance(spec, dict):
        return False, ["Session spec must be an object"]

    if spec.get("schema_version") != ROLEPLAY_SPEC_SCHEMA_VERSION:
        errors.append(f"schema_version must be '{ROLEPLAY_SPEC_SCHEMA_VERSION}', got {spec.get('schema_version')}")

    if not spec.get("session_id") or not isinstance(spec.get("session_id"), str):
        errors.append("session_id must be a non-empty string")

    if not spec.get("simulation_id") or not isinstance(spec.get("simulation_id"), str):
        errors.append("simulation_id must be a non-empty string")

    actors = spec.get("actors")
    if not isinstance(actors, list) or len(actors) < 2:
        errors.append("actors must be an array of at least 2 actor IDs")
    else:
        for a in actors:
            if not isinstance(a, str) or not a.strip():
                errors.append(f"Invalid actor ID: {a}")

    decision_ticks = spec.get("decision_ticks")
    if not isinstance(decision_ticks, list) or len(decision_ticks) < 1:
        errors.append("decision_ticks must be an array of at least 1 non-negative integer")
    else:
        prev = -1
        for t in decision_ticks:
            if not isinstance(t, int) or isinstance(t, bool) or t < 0:
                errors.append(f"decision_tick must be non-negative integer, got {t}")
            elif t <= prev:
                errors.append(f"decision_ticks must be strictly ascending: {t} <= {prev}")
            prev = t

    allowed_actions = spec.get("allowed_actions")
    actor_action_map: dict[str, list[str]] = {}
    if not isinstance(allowed_actions, dict):
        errors.append("allowed_actions must be an object mapping actor_id to action list")
    elif isinstance(actors, list):
        for actor in actors:
            actions = allowed_actions.get(actor)
            if not isinstance(actions, list) or len(actions) < 2:
                errors.append(f"Actor '{actor}' must have at least 2 allowed actions (P6.A05)")
                continue
            has_noop = False
            act_ids: list[str] = []
            for act in actions:
                if not isinstance(act, dict):
                    errors.append(f"Action for actor '{actor}' must be an object")
                    continue
                aid = act.get("id")
                if not aid or not isinstance(aid, str):
                    errors.append(f"Action for actor '{actor}' missing id")
                    continue
                act_ids.append(aid)
                if act.get("noop") is True:
                    has_noop = True
                cost = act.get("cost", 0.0)
                if not isinstance(cost, (int, float)) or not math.isfinite(cost):
                    errors.append(f"Action '{aid}' cost must be finite number")
            if not has_noop:
                errors.append(f"Actor '{actor}' must have at least one explicit noop action with noop=true (P6.A05)")
            actor_action_map[actor] = act_ids

    # Validate action effects
    action_effects = spec.get("action_effects")
    if not isinstance(action_effects, dict):
        errors.append("action_effects must be an object mapping action IDs to effects")
    else:
        for key, eff_list in action_effects.items():
            if not isinstance(eff_list, list):
                errors.append(f"action_effects[{key}] must be an array of effect objects")
                continue
            for eff in eff_list:
                if not isinstance(eff, dict):
                    errors.append(f"Effect in {key} must be an object")
                    continue
                target = eff.get("target")
                if not target or not isinstance(target, str):
                    errors.append(f"Effect in {key} missing target")
                elif model and target not in model.variables:
                    errors.append(f"Effect target '{target}' not found in computational model")
                mag = eff.get("magnitude")
                if not isinstance(mag, (int, float)) or not math.isfinite(mag):
                    errors.append(f"Effect magnitude in {key} must be finite number")
                lag = eff.get("lag", 0)
                if not isinstance(lag, int) or isinstance(lag, bool) or lag < 0:
                    errors.append(f"Effect lag in {key} must be non-negative integer")
                if eff.get("op", "add") != "add":
                    errors.append(f"Effect op in {key} must be 'add' (additive intervention in v1)")

    # Validate payoffs
    payoffs = spec.get("payoffs")
    if not isinstance(payoffs, dict):
        errors.append("payoffs must be an object mapping actor_id to payoff configuration")
    elif isinstance(actors, list) and len(actor_action_map) == len(actors):
        # Generate all combination profiles across actors
        all_actor_actions = [actor_action_map[a] for a in actors]
        expected_profiles = list(itertools.product(*all_actor_actions))

        for actor in actors:
            p_conf = payoffs.get(actor)
            if not isinstance(p_conf, dict):
                errors.append(f"Missing payoff configuration for actor '{actor}'")
                continue
            state_weights = p_conf.get("state_weights")
            if not isinstance(state_weights, dict):
                errors.append(f"Actor '{actor}' payoff missing state_weights object")
            else:
                for n, w in state_weights.items():
                    if not isinstance(w, (int, float)) or not math.isfinite(w):
                        errors.append(f"state_weight for {n} in actor '{actor}' must be finite number")
                    if model and n not in model.variables:
                        errors.append(f"state_weight node '{n}' in actor '{actor}' not in model variables")
            util_table = p_conf.get("utility_table")
            if not isinstance(util_table, dict):
                errors.append(f"Actor '{actor}' payoff missing utility_table object")
            else:
                # P6.A09: Verify utility_table completeness for all profiles
                for profile in expected_profiles:
                    # Check multiple key conventions: "a1,a2" or "US:a1,China:a2"
                    key_plain = ",".join(profile)
                    key_tagged = ",".join(f"{act}:{action}" for act, action in zip(actors, profile, strict=True))
                    has_key = key_plain in util_table or key_tagged in util_table
                    if not has_key:
                        errors.append(
                            f"INCOMPLETE_UTILITY_TABLE: Actor '{actor}' missing utility for profile {key_plain} (P6.A09)"
                        )
                    else:
                        u_val = util_table.get(key_plain, util_table.get(key_tagged))
                        if not isinstance(u_val, (int, float)) or not math.isfinite(u_val):
                            errors.append(f"Utility value for profile {key_plain} must be finite number")

    return len(errors) == 0, errors


def init_roleplay_session(
    spec: dict[str, Any],
    initial_model: ComputationalModel | dict[str, Any],
    config: EngineConfig | None = None,
) -> dict[str, Any]:
    """Initialize a multi-actor roleplay session state."""
    model_obj: ComputationalModel
    if isinstance(initial_model, dict):
        from .engine import ComputationalModel, ModelEdge, Variable
        variables = {k: Variable(**v) if isinstance(v, dict) else v for k, v in initial_model.get("variables", {}).items()}
        edges = [ModelEdge(**e) if isinstance(e, dict) else e for e in initial_model.get("edges", [])]
        interventions = copy.deepcopy(initial_model.get("interventions", []))
        model_obj = ComputationalModel(variables=variables, edges=edges, interventions=interventions,
                                       formula_version=initial_model.get("formula_version", "2.0.0"))
    else:
        model_obj = initial_model

    ok, errors = validate_session_spec(spec, model_obj)
    if not ok:
        raise ValueError("Invalid roleplay session spec:\n  " + "\n  ".join(errors))

    spec_copy = copy.deepcopy(spec)
    spec_hash = canonical_hash(spec_copy)
    now = _iso_now()
    cfg = config or EngineConfig(seed="42")
    m_hash = model_hash(model_obj) if model_obj else None
    c_hash = canonical_hash(config_payload(cfg))

    return {
        "schema_version": ROLEPLAY_SPEC_SCHEMA_VERSION,
        "session_id": spec["session_id"],
        "simulation_id": spec["simulation_id"],
        "spec_hash": spec_hash,
        "spec": spec_copy,
        "model_hash": m_hash,
        "config_hash": c_hash,
        "formula_version": model_obj.formula_version,
        "engine_config": asdict(cfg),
        "status": "initialized",
        "current_round": 0,
        "total_rounds": len(spec["decision_ticks"]),
        "decision_ticks": spec["decision_ticks"],
        "pending_commits": {},
        "rounds": [],
        "created_at": now,
        "updated_at": now,
    }


def generate_actor_packets(
    session: dict[str, Any],
    model: ComputationalModel,
    config: EngineConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """Generate isolated decision packets for the current round (P6.B)."""
    if session.get("status") not in ("initialized", "active"):
        raise ValueError(f"Session is {session.get('status')}; cannot generate packets")

    round_idx = session["current_round"]
    if round_idx >= session["total_rounds"]:
        raise ValueError("All session rounds already executed; call finalize_roleplay_session")

    spec = session["spec"]
    decision_tick = spec["decision_ticks"][round_idx]
    cfg = config or EngineConfig(seed="42")
    _verify_execution_identity(session, model, cfg)

    # Replay simulation from tick 0 to decision_tick using all committed interventions so far
    all_past_interventions: list[dict[str, Any]] = []
    for r in session.get("rounds", []):
        all_past_interventions.extend(r.get("interventions", []))

    run_model = copy.deepcopy(model)
    run_model.interventions = copy.deepcopy(model.interventions) + all_past_interventions
    sim_res = run_deterministic(run_model, config=cfg, ticks=decision_tick)
    if not sim_res.get("ok"):
        issue_codes = [i.get("code") for i in sim_res.get("issues", [])]
        raise ValueError(
            f"Simulation engine failed during packet generation at tick {decision_tick}: {issue_codes}"
        )
    current_state = sim_res["payload"]["final_state"]

    packets: dict[str, dict[str, Any]] = {}
    visibility_spec = spec.get("visibility", {})

    past_rounds_summary = [
        {
            "round_index": r["round_index"],
            "decision_tick": r["decision_tick"],
            "committed_actions": r["committed_actions"],
            "payoffs": r["payoffs"],
        }
        for r in session.get("rounds", [])
    ]

    for actor in spec["actors"]:
        # Information isolation (P6.B03): filter state by visibility if declared
        actor_vis = visibility_spec.get(actor)
        visible_nodes = actor_vis.get("visible_nodes") if isinstance(actor_vis, dict) else None
        if visible_nodes is not None:
            filtered_state = {k: v for k, v in current_state.items() if k in visible_nodes}
        else:
            filtered_state = copy.deepcopy(current_state)

        packet_payload = {
            "schema_version": ROLEPLAY_SPEC_SCHEMA_VERSION,
            "session_id": session["session_id"],
            "actor_id": actor,
            "round_index": round_idx,
            "decision_tick": decision_tick,
            "allowed_actions": spec["allowed_actions"][actor],
            "observed_state": filtered_state,
            "past_rounds": copy.deepcopy(past_rounds_summary),
            "spec_hash": session["spec_hash"],
        }
        # Cryptographic hash binding
        packet_hash = canonical_hash(packet_payload)
        packet_payload["packet_hash"] = packet_hash
        packets[actor] = packet_payload

    session.setdefault("issued_packets", {})
    round_str = str(round_idx)
    session["issued_packets"][round_str] = {
        actor: p["packet_hash"] for actor, p in packets.items()
    }

    session["updated_at"] = _iso_now()
    return packets


def commit_actor_action(
    session: dict[str, Any],
    actor_id: str,
    action_id: str,
    packet_hash: str,
    *,
    rationale: str = "",
    considered_options: list[str] | None = None,
) -> dict[str, Any]:
    """Commit an actor's decision for the current round with validation (P6.B08-P6.B11)."""
    if session.get("status") not in ("initialized", "active"):
        raise ValueError(f"Session is {session.get('status')}; cannot commit actions")

    spec = session["spec"]
    if actor_id not in spec["actors"]:
        raise ValueError(f"Unknown actor '{actor_id}'")

    allowed_ids = {a["id"] for a in spec["allowed_actions"][actor_id]}
    if action_id not in allowed_ids:
        raise ValueError(f"Action '{action_id}' is not an allowed action for actor '{actor_id}'")

    # Verify packet_hash binding against issued packet (P6.B09)
    round_idx = session["current_round"]
    round_str = str(round_idx)
    issued_dict = session.get("issued_packets", {})
    issued_packets = issued_dict.get(round_str) if round_str in issued_dict else issued_dict.get(round_idx, {})
    expected_hash = issued_packets.get(actor_id)
    if not expected_hash or packet_hash != expected_hash:
        raise ValueError(
            f"Invalid or unissued packet_hash for actor '{actor_id}' in round {round_idx}: "
            f"expected '{expected_hash}', got '{packet_hash}'"
        )

    # Check for duplicate conflicting commit in same round (P6.C07)
    existing = session["pending_commits"].get(actor_id)
    if existing and existing.get("action_id") != action_id:
        raise ValueError(
            f"Actor '{actor_id}' already committed '{existing.get('action_id')}' in round {session['current_round']}. "
            "Modifying requires creating a variant session."
        )

    commit_record = {
        "actor_id": actor_id,
        "action_id": action_id,
        "packet_hash": packet_hash,
        "rationale": rationale,
        "considered_options": considered_options or [],
        "committed_at": _iso_now(),
    }
    session["pending_commits"][actor_id] = commit_record
    session["status"] = "active"
    session["updated_at"] = _iso_now()
    return commit_record


def _derive_round_interventions(spec: dict[str, Any], profile: dict[str, str], round_index: int, tick: int) -> list[dict[str, Any]]:
    deltas: dict[tuple[str, int], float] = {}
    for actor, action in profile.items():
        effects = spec["action_effects"].get(f"{actor}:{action}") or spec["action_effects"].get(action) or []
        for effect in effects:
            key = (effect["target"], effect.get("lag", 0))
            deltas[key] = deltas.get(key, 0.0) + float(effect["magnitude"])
    return [{"id": f"int_r{round_index}_{target}_lag{lag}", "target": target,
             "op": "add", "value": magnitude, "start_tick": tick + lag,
             "end_tick": None, "release_policy": "retain"}
            for (target, lag), magnitude in sorted(deltas.items())]


def advance_roleplay_round(
    session: dict[str, Any],
    model: ComputationalModel,
    config: EngineConfig | None = None,
) -> dict[str, Any]:
    """Adjudicate committed actions, run simulation to next tick, compute payoffs (P6.B10, P6.C05)."""
    if session.get("status") != "active":
        raise ValueError("Session is not active; cannot advance round")

    spec = session["spec"]
    actors = spec["actors"]
    round_idx = session["current_round"]

    # Verify all actors have committed (P6.B11)
    missing = [a for a in actors if a not in session["pending_commits"]]
    if missing:
        raise ValueError(f"Cannot advance round {round_idx}: missing commits for actors: {', '.join(missing)}")

    decision_tick = spec["decision_ticks"][round_idx]
    # Determine tick to evaluate round payoff: either next decision tick or decision_tick + 10
    if round_idx + 1 < len(spec["decision_ticks"]):
        eval_tick = spec["decision_ticks"][round_idx + 1]
    else:
        eval_tick = decision_tick + 10

    committed_profile = {a: session["pending_commits"][a]["action_id"] for a in actors}

    # Verify commit actor identity against actor key slot (FX03.07/FX03.08)
    for a in actors:
        commit_rec = session["pending_commits"][a]
        if commit_rec.get("actor_id") != a:
            raise ValueError(
                f"Pending commit actor_id mismatch for slot '{a}': got '{commit_rec.get('actor_id')}'"
            )

    # Verify packet hash validity and action allowlist for each commit before executing round (P6.B09, R03)
    round_str = str(round_idx)
    issued_dict = session.get("issued_packets", {})
    issued_for_round = issued_dict.get(round_str) if round_str in issued_dict else issued_dict.get(round_idx, {})
    for a in actors:
        commit_rec = session["pending_commits"][a]
        expected_pkt_hash = issued_for_round.get(a)
        if not expected_pkt_hash or commit_rec.get("packet_hash") != expected_pkt_hash:
            raise ValueError(
                f"Pending commit for actor '{a}' has invalid or tampered packet_hash in round {round_idx}: "
                f"expected '{expected_pkt_hash}', got '{commit_rec.get('packet_hash')}'"
            )
        chosen_id = commit_rec.get("action_id")
        allowed_list = spec.get("allowed_actions", {}).get(a, [])
        valid_ids = {act["id"] for act in allowed_list}
        if chosen_id not in valid_ids:
            raise ValueError(
                f"Action '{chosen_id}' is not in allowed actions for actor '{a}' in round {round_idx}. Valid: {sorted(valid_ids)}"
            )

    round_interventions = _derive_round_interventions(spec, committed_profile, round_idx, decision_tick)

    # Combine all interventions from past rounds + current round
    past_interventions: list[dict[str, Any]] = []
    for r in session.get("rounds", []):
        past_interventions.extend(r.get("interventions", []))
    all_interventions = past_interventions + round_interventions

    # Run deterministic simulation from tick 0 to eval_tick
    cfg = config or EngineConfig(seed="42")
    _verify_execution_identity(session, model, cfg)

    run_model = copy.deepcopy(model)
    run_model.interventions = copy.deepcopy(model.interventions) + all_interventions
    sim_res = run_deterministic(run_model, config=cfg, ticks=eval_tick)
    if not sim_res.get("ok"):
        issue_codes = [i.get("code") for i in sim_res.get("issues", [])]
        raise ValueError(
            f"Simulation engine nonconvergence or failure during round {round_idx} eval: {issue_codes}"
        )
    round_state = sim_res["payload"]["final_state"]

    # Calculate payoffs (P6.A08, P6.C05)
    # payoff = state_weights + utility - cost
    profile_plain = ",".join(committed_profile[a] for a in actors)
    profile_tagged = ",".join(f"{a}:{committed_profile[a]}" for a in actors)

    actor_costs = {}
    for a in actors:
        chosen_id = committed_profile[a]
        for act in spec["allowed_actions"][a]:
            if act["id"] == chosen_id:
                actor_costs[a] = float(act.get("cost", 0.0))
                break

    round_payoffs: dict[str, float] = {}
    for a in actors:
        p_conf = spec["payoffs"][a]
        state_component = sum(
            round_state.get(node, 0.0) * float(weight)
            for node, weight in p_conf["state_weights"].items()
        )
        util_table = p_conf["utility_table"]
        util_val = float(util_table.get(profile_plain, util_table.get(profile_tagged, 0.0)))
        cost_val = actor_costs.get(a, 0.0)
        round_payoffs[a] = round(state_component + util_val - cost_val, 6)

    round_record = {
        "round_index": round_idx,
        "decision_tick": decision_tick,
        "eval_tick": eval_tick,
        "committed_actions": copy.deepcopy(committed_profile),
        "commits": copy.deepcopy(session["pending_commits"]),
        "interventions": round_interventions,
        "state_snapshot": round_state,
        "payoffs": round_payoffs,
    }
    round_record["round_hash"] = canonical_hash(round_record)

    session["rounds"].append(round_record)
    session["pending_commits"] = {}
    session["current_round"] += 1

    if session["current_round"] >= session["total_rounds"]:
        session["status"] = "completed"

    session["updated_at"] = _iso_now()
    return round_record


def finalize_roleplay_session(session: dict[str, Any]) -> dict[str, Any]:
    """Finalize a completed roleplay session and generate final receipts (P6.C09)."""
    if session.get("status") == "finalized" and "receipt" in session and isinstance(session["receipt"], dict):
        return {"ok": True, "receipt": session["receipt"]}

    if session.get("current_round") < session.get("total_rounds", 1):
        return {
            "ok": False,
            "status": "partial",
            "blocker": "session_incomplete",
            "completed_rounds": session.get("current_round", 0),
            "total_rounds": session.get("total_rounds", 0),
        }

    actors = session["spec"]["actors"]
    cumulative_payoffs: dict[str, float] = {a: 0.0 for a in actors}
    for r in session.get("rounds", []):
        for a, p in r.get("payoffs", {}).items():
            cumulative_payoffs[a] = round(cumulative_payoffs[a] + p, 6)

    session["status"] = "finalized"
    session["cumulative_payoffs"] = cumulative_payoffs
    session["updated_at"] = _iso_now()
    session_hash = canonical_hash({k: v for k, v in session.items() if k not in ("receipt", "session_hash")})
    session["session_hash"] = session_hash

    receipt = {
        "schema_version": ROLEPLAY_SPEC_SCHEMA_VERSION,
        "session_id": session["session_id"],
        "simulation_id": session["simulation_id"],
        "status": "finalized",
        "rounds_executed": len(session["rounds"]),
        "cumulative_payoffs": cumulative_payoffs,
        "session_hash": session_hash,
        "finalized_at": _iso_now(),
    }
    session["receipt"] = receipt
    return {"ok": True, "receipt": receipt}


def replay_roleplay_session(
    session: dict[str, Any],
    initial_model: ComputationalModel,
    config: EngineConfig | None = None,
) -> dict[str, Any]:
    """Replay simulation and recalculate payoffs from logged interventions (P6.C08)."""
    spec = session["spec"]
    actors = spec["actors"]
    cfg = config or EngineConfig(seed="42")

    try:
        _verify_execution_identity(session, initial_model, cfg)
    except ValueError as exc:
        return {"ok": False, "match": False, "error": f"REPLAY_{exc}"}

    accumulated_interventions: list[dict[str, Any]] = []
    replayed_rounds: list[dict[str, Any]] = []

    for index, r in enumerate(session.get("rounds", [])):
        if index >= len(spec["decision_ticks"]):
            return {"ok": False, "match": False, "error": "REPLAY_ROUND_SEQUENCE_MISMATCH"}
        tick = spec["decision_ticks"][index]
        eval_tick = spec["decision_ticks"][index + 1] if index + 1 < len(spec["decision_ticks"]) else tick + 10
        if r.get("round_index") != index or r.get("decision_tick") != tick or r.get("eval_tick") != eval_tick:
            return {"ok": False, "match": False, "error": "REPLAY_ROUND_SEQUENCE_MISMATCH"}
        profile = r.get("committed_actions", {})
        commits = r.get("commits", {})
        issued = session.get("issued_packets", {}).get(str(index), {})
        if set(profile) != set(actors) or set(commits) != set(actors):
            return {"ok": False, "match": False, "error": "REPLAY_COMMIT_MISMATCH"}
        for actor in actors:
            commit = commits[actor]
            if (commit.get("actor_id") != actor or commit.get("action_id") != profile[actor]
                    or profile[actor] not in {a["id"] for a in spec["allowed_actions"][actor]}
                    or not issued.get(actor) or commit.get("packet_hash") != issued[actor]):
                return {"ok": False, "match": False, "error": "REPLAY_COMMIT_MISMATCH"}
        derived = _derive_round_interventions(spec, profile, index, tick)
        if derived != r.get("interventions"):
            return {"ok": False, "match": False, "error": "REPLAY_INTERVENTION_MISMATCH"}
        accumulated_interventions.extend(derived)

        run_model = copy.deepcopy(initial_model)
        run_model.interventions = copy.deepcopy(initial_model.interventions) + accumulated_interventions
        sim_res = run_deterministic(run_model, config=cfg, ticks=eval_tick)
        if not sim_res.get("ok"):
            return {
                "ok": False,
                "match": False,
                "error": f"REPLAY_SIMULATION_FAILED at round {r.get('round_index')}: {[i.get('code') for i in sim_res.get('issues', [])]}",
            }
        replayed_state = sim_res["payload"]["final_state"]

        committed_profile = r["committed_actions"]
        profile_plain = ",".join(committed_profile[a] for a in actors)
        profile_tagged = ",".join(f"{a}:{committed_profile[a]}" for a in actors)

        actor_costs = {}
        for a in actors:
            chosen_id = committed_profile[a]
            for act in spec["allowed_actions"][a]:
                if act["id"] == chosen_id:
                    actor_costs[a] = float(act.get("cost", 0.0))
                    break

        replayed_payoffs: dict[str, float] = {}
        for a in actors:
            p_conf = spec["payoffs"][a]
            state_val = sum(
                replayed_state.get(node, 0.0) * float(weight)
                for node, weight in p_conf["state_weights"].items()
            )
            util_table = p_conf["utility_table"]
            util_val = float(util_table.get(profile_plain, util_table.get(profile_tagged, 0.0)))
            cost_val = actor_costs.get(a, 0.0)
            replayed_payoffs[a] = round(state_val + util_val - cost_val, 6)

        # Bit-for-bit check
        if replayed_payoffs != r["payoffs"]:
            return {
                "ok": False,
                "match": False,
                "error": f"REPLAY_PAYOFF_MISMATCH at round {r['round_index']}: expected {r['payoffs']}, got {replayed_payoffs}",
            }

        if replayed_state != r.get("state_snapshot"):
            return {
                "ok": False,
                "match": False,
                "error": f"REPLAY_STATE_MISMATCH at round {r.get('round_index')}: replayed state diverged from recorded state_snapshot",
                "details": replayed_rounds + [{
                    "round_index": r.get("round_index"),
                    "state_match": False,
                    "payoffs": r.get("payoffs", {}),
                }],
            }

        # Verify round_hash integrity
        expected_round_hash = canonical_hash({k: v for k, v in r.items() if k != "round_hash"})
        if r.get("round_hash") != expected_round_hash:
            return {
                "ok": False,
                "match": False,
                "error": f"REPLAY_ROUND_HASH_MISMATCH at round {r.get('round_index')}: expected {expected_round_hash}, got {r.get('round_hash')}",
            }

        replayed_rounds.append({
            "round_index": r["round_index"],
            "state_match": replayed_state == r["state_snapshot"],
            "payoffs": replayed_payoffs,
        })

    # Verify final receipt integrity if present (R03, S02)
    if session.get("status") == "finalized":
        if "receipt" not in session or not isinstance(session["receipt"], dict):
            return {
                "ok": False,
                "match": False,
                "error": "REPLAY_RECEIPT_MISSING: finalized session requires valid receipt",
            }

    actual_sess_hash = canonical_hash({k: v for k, v in session.items() if k not in ("receipt", "session_hash")})
    if session.get("session_hash") and session.get("session_hash") != actual_sess_hash:
        return {
            "ok": False,
            "match": False,
            "error": f"REPLAY_SESSION_HASH_MISMATCH: expected session_hash '{actual_sess_hash}', got '{session.get('session_hash')}'",
        }

    if "receipt" in session and isinstance(session["receipt"], dict):
        rec = session["receipt"]
        cumulative = {a: 0.0 for a in actors}
        for record in session.get("rounds", []):
            for actor, payoff in record["payoffs"].items():
                cumulative[actor] = round(cumulative[actor] + payoff, 6)
        if (rec.get("status") != "finalized" or rec.get("rounds_executed") != len(replayed_rounds)
                or rec.get("cumulative_payoffs") != cumulative or session.get("cumulative_payoffs") != cumulative
                or session.get("current_round") != len(replayed_rounds)):
            return {"ok": False, "match": False, "error": "REPLAY_RECEIPT_TAMPERED: summary differs"}
        if rec.get("session_hash") != actual_sess_hash:
            return {
                "ok": False,
                "match": False,
                "error": f"REPLAY_RECEIPT_TAMPERED: expected session_hash '{actual_sess_hash}', got '{rec.get('session_hash')}'",
            }
        if rec.get("simulation_id") != session.get("simulation_id"):
            return {
                "ok": False,
                "match": False,
                "error": f"REPLAY_RECEIPT_TAMPERED: simulation_id mismatch '{rec.get('simulation_id')}' != '{session.get('simulation_id')}'",
            }
        if rec.get("session_id") != session.get("session_id"):
            return {
                "ok": False,
                "match": False,
                "error": "REPLAY_RECEIPT_TAMPERED: session_id mismatch",
            }

    return {
        "ok": True,
        "match": True,
        "replayed_rounds_count": len(replayed_rounds),
        "details": replayed_rounds,
    }
