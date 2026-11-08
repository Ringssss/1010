"""Score a replica selection with the archived exact-extent model.

Routing is delegated to the product planner (`restore_plan`), the operation order
is normalised to the registered rank-interleaved sequence, and the aggregate
service comes from the frozen `simple_exact_extent_model_v1` scorer. Only the
source selection may vary: the target workload fingerprint must stay identical.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../task2/decision_value/execution
TASK2 = HERE.parents[1]                          # .../task2
ROOT = HERE.parents[5]                           # repository root
sys.path.insert(0, str(ROOT / "main_lib"))
sys.path.insert(0, str(TASK2 / "online_cost/model"))
sys.path.insert(0, str(TASK2 / "online_cost/execution"))
sys.path.insert(0, str(HERE.parent / "case"))

from fluidckpt.benchmarks.topology_cases import allocated, interleave
from fluidckpt.cluster.planning import restore_plan, validate_plan
from fluidckpt.cluster.service_cost import new_state
from predictor import plan_workload
from simple_model import transfer_service

from registered_fixture import (GiB, ROLES, TAIL, WARM_PAIRS, base_replicas,
                               budgets, candidate_replicas, extents, load,
                               ranks, workers)

ONLINE_OUTER_S = 0.4235804238204  # registered common residual; identical for all strategies


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def profile_from_points(model):
    """Derive a routing profile from the registered service points only.

    Rates are 1 GiB / observed service, so the product planner ranks sources in
    exactly the order the measured model will charge them. No new numbers are
    introduced and nothing is fitted.
    """
    points = model["point_costs"]
    # 1 GiB of payload over the observed service at 1 GiB; ranking only.
    ordered = {path: GiB / 1e9 / rows[str(GiB)]["service_s"] for path, rows in points.items()}
    return {
        "rates": {"local_dram": ordered["0:dram:4->0"], "local_hbm": ordered["0:hbm:4->0"],
                  "network": ordered["2:dram:4->0"], "peer": ordered["0:dram:4->0"],
                  "local_ssd": 0.5},
        "gpu_ingress_GBps": 1e6, "gpu_egress_GBps": 1e6, "dram_GBps": 1e6,
        "network_node_GBps": 1e6, "ssd_GBps": 0.5, "store_network_GBps": 1.0,
        "directed_path_rates": ordered,
        "verified_paths": sorted(points),
        "network_pairs": [[2, 0], [2, 1]],
        "serial_target_execution": True,
    }


def make_state(model, workers_, evidence_path):
    ref = {"path": str(evidence_path), "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest()}
    context = model["context"]
    lineage = {}
    for row in base_replicas(model) + candidate_replicas(model):
        lineage[row["id"]] = dict(worker=row["worker"], tier=row["tier"], numa=row["numa"],
                                  identity=row["identity"], generation=row["generation"],
                                  source_epoch=row["epoch"])
    state = dict(new_state(workers_), fresh_binding_verified=True,
                 load_state=context["load_state"], config_sha256=digest(context),
                 warm_pairs=list(WARM_PAIRS), source_lineage=lineage,
                 worker_BDF=dict(context["worker_BDF"]), binding_source_refs=[ref])
    return state


def targets_of(model):
    return [dict(key=f"rank{tp}", worker=tp, identity=identity, tp=tp, off=0, n=n)
            for tp, identity, n in ranks(model)]


def build_plan(model, selected, workers_, profile, routing="nearest"):
    """Native plan over base + selection, normalised to the registered op order."""
    plan = restore_plan(base_replicas(model) + list(selected), targets_of(model),
                        workers_, profile, routing)
    if plan["strategy"] != routing:
        raise RuntimeError("routing fell back to %r; cannot score as %r"
                           % (plan["strategy"], routing))
    # `restore_plan` returns routing only; the registered scorer needs the
    # execution contract that the frozen model was calibrated against.
    plan["executor"] = model["context"]["executor"]
    plan["chunk_bytes"] = model["context"]["chunk_bytes"]
    plan = interleave(plan)
    validate_plan(plan)
    return plan


def score(model, plan, workers_, state):
    """Aggregate service from the frozen scorer, plus the common outer residual."""
    if plan_workload(plan) != model["context"]["full_model_plan_workload"]:
        raise RuntimeError("target workload fingerprint changed; selection is not layout-only")
    result = transfer_service(plan, workers_, model, state)
    service = result["estimated_transfer_service_s"]
    return dict(service_s=service, ready_total_s=service + ONLINE_OUTER_S,
                timeline=result["timeline"], worker_dag_s=result["worker_dag_s"])


def rank_end_s(model, selected, workers_, profile, state):
    """Per-destination completion time from the frozen timeline.

    `worker_dag_s` is only the scalar makespan, so the per-rank chain length is
    recovered the way the DAG builds it: each op's path ends in `->dst`, and a
    destination is done when its last op ends.
    """
    ends = {w["worker"]: 0.0 for w in workers_}
    if not selected:
        return ends
    plan = build_plan(model, selected, workers_, profile)
    for row in score(model, plan, workers_, state)["timeline"]:
        dst = row["path"].rsplit("->", 1)[-1]
        try:
            ends[int(dst)] = max(ends.get(int(dst), 0.0), float(row["end_s"]))
        except (TypeError, ValueError):
            continue
    return ends


def ledger(selected, location_budgets, tier_budgets):
    """Capacity accounting identical to the frozen-layout contract."""
    if len({c["id"] for c in selected}) != len(selected):
        raise ValueError("duplicate allocation ID")
    locations = {key: sum(allocated(c) for c in selected if f"{c['worker']}:{c['tier']}" == key)
                 for key in location_budgets}
    total = sum(locations.values())
    if any(n > location_budgets[key] for key, n in locations.items()):
        raise ValueError("selection exceeds frozen location caps")
    if total > tier_budgets["dram"] + tier_budgets["hbm"]:
        raise ValueError("selection exceeds frozen tier caps")
    return dict(allocated_bytes=total, location_bytes=locations,
                copies=len(selected), logical_bytes=sum(c["n"] for c in selected))


def feasible(selected, location_budgets, tier_budgets):
    try:
        ledger(selected, location_budgets, tier_budgets)
    except ValueError:
        return False
    return True


def evaluate_selection(model, selected, workers_, profile, state, loc_budgets, tier_budgets,
                       routing="nearest"):
    book = ledger(selected, loc_budgets, tier_budgets)
    plan = build_plan(model, selected, workers_, profile, routing)
    scored = score(model, plan, workers_, state)
    scored.update(ledger=book, plan=plan, selected=[c["id"] for c in selected])
    return scored
