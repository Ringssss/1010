"""Finite-layout comparison and controller-clock execution using existing components."""
import copy
import time

from .frozen_layout import bind_real_cache, decision_signature, layout_ledger
from .topology_cases import canonical_hash, interleave, validate_case
from fluidckpt.cluster.planning import restore_plan, validate_plan
from fluidckpt.executor.chunks import execution_chunk
from fluidckpt.topology.resource_map import finite_workpoint_evidence, planner_readiness, planning_input, validate_resource_map


def comparison_protocol(case, layouts, functional_profile, *, chunk_bytes, load_state):
    """Register two manual layouts; provisional routing never claims prediction."""
    validate_case(case)
    if len(layouts) != 2 or len(set(layouts)) != 2:
        raise ValueError("exactly two distinct named layouts required")
    if type(chunk_bytes) is not int or chunk_bytes <= 0 or not load_state:
        raise ValueError("explicit positive executor chunk and load state required")
    execution_chunk(dict(executor="serial", chunk_bytes=chunk_bytes))
    candidates = {row["id"]: row for row in case["candidates"]}
    rows = {}
    for name, ids in layouts.items():
        selected = [candidates[rid] for rid in ids]
        ledger = layout_ledger(case, selected)
        ledger.update(peak_dram_bytes=ledger["base_dram_bytes"],
                      peak_hbm_bytes=ledger["allocated_hbm_bytes"] + ledger["target_allocation_bytes"]
                                     + ledger["staging_bytes"],
                      physical_peak_verified=False)
        staging = case["budget"].get("staging_by_location", {})
        if sum(staging.values()) != ledger["staging_bytes"]:
            raise ValueError("each physical staging allocation must have a location")
        target_bytes = {f"{w['worker']}:hbm": max(
            sum((t["n"]+4095)//4096*4096 for t in scenario["targets"] if t["worker"] == w["worker"])
            for scenario in case["restore_scenarios"]) for w in case["workers"]}
        ledger["peak_hbm_by_location"] = {key: ledger["location_bytes"][key] + target_bytes[key] + staging.get(key, 0)
                                          for key in ledger["location_bytes"]}
        ledger["base_dram_by_location"] = {f"{w['worker']}:dram": sum(
            b.get("allocated_bytes", (b["n"]+4095)//4096*4096) for b in case["base"] if b["worker"] == w["worker"])
            for w in case["workers"]}
        plans = {}
        for scenario in case["restore_scenarios"]:
            plan = interleave(restore_plan(case["base"] + selected, scenario["targets"],
                                          case["workers"], functional_profile, "nearest",
                                          quantum=chunk_bytes))
            plan.update(executor="serial", chunk_bytes=chunk_bytes)
            # This profile only provides functional route ordering before calibration.
            for field in ("estimated_transfer_s", "bandwidth_lower_bound_s", "serial_target_times_s"):
                plan.pop(field, None)
            plan.update(scope="functional provisional route ordering; no measured-rate prediction",
                        profile_source="CPU_functional_placeholders", prediction_ready=False)
            plans[scenario["name"]] = plan
        rows[name] = dict(selected=copy.deepcopy(selected), ledger=ledger, plans=plans,
                          decision_signature=decision_signature(selected, dict(plans=plans)))
    if len({row["ledger"]["allocated_hbm_bytes"] for row in rows.values()}) != 1:
        raise ValueError("manual layouts must charge the same physical replica bytes")
    signatures = [row["decision_signature"] for row in rows.values()]
    protocol = dict(schema_version="fluidckpt.calibrated_recovery_protocol.v1",
                    development="v0.1.23.3/task2", case_sha256=canonical_hash(case),
                    physical_budget=copy.deepcopy(case["budget"]), layouts=rows,
                    format_contract=copy.deepcopy(case["format_contract"]),
                    objective=case["objective"], targets=copy.deepcopy(case["restore_scenarios"]),
                    chunk_bytes=chunk_bytes, load_state=load_state, executor="serial",
                    schedule="rank_interleaved", verification_position="before_replica_publish",
                    metric=dict(begin="controller immediately before loading dispatch",
                                filled_end="controller observed every synchronized target execute complete",
                                published_end="controller observed every verified target publish complete",
                                clock="one controller monotonic domain"),
                    preparation_cost="measured independently before loading",
                    prediction_ready=False, performance_ready=False,
                    measurement_status="CPU_contract_only", new_GPU_samples=0,
                    decisions_equal=signatures[0] == signatures[1])
    if "target_local_distributed" in rows:
        protocol.update(strong_nearest_target_decision_signature=rows["target_local_distributed"]["decision_signature"],
                        strong_nearest_target_alias="target_local_distributed", no_solver_delta=True,
                        duplicate_execution_policy="do not rerun identical strong-nearest-target layout")
    if "physical_binding_by_worker" in case:
        protocol["physical_binding_by_worker"] = copy.deepcopy(case["physical_binding_by_worker"])
    protocol["protocol_sha256"] = canonical_hash(protocol)
    return protocol


def admitted_workpoint(mapping, request, *, now=None, max_age_s=300):
    """Expose evidence only for the exact requested combination, without LP caps."""
    result = planning_input(mapping, request, now=now, max_age_s=max_age_s)
    result["solver_capacity_profile"] = None
    result["scope"] = "finite admitted workload; no interpolation or independent path capacity inference"
    return result


def observed_workpoint_score(mapping, request, *, now=None, max_age_s=300):
    """Score only the measured exact workload under its observed load conditions."""
    check = validate_resource_map(mapping)
    if not check["passed"]:
        raise ValueError("invalid physical/evidence map")
    evidence = finite_workpoint_evidence(mapping, request, now=now, max_age_s=max_age_s, require_profile=False)
    if len(evidence) != 1:
        raise ValueError("one unambiguous exact measured workpoint is required")
    row = evidence[0]
    timing = row["timing"]
    if timing.get("clock_scope") != "single_controller" or type(timing.get("elapsed_ns")) is not int or timing["elapsed_ns"] <= 0:
        raise ValueError("joint observed score requires a single-controller duration")
    return dict(observed_collection_window_s=timing["elapsed_ns"]/1e9,
                load_scope=row.get("load_scope"), measurement_id=row["id"], source_refs=copy.deepcopy(row["source_refs"]),
                workpoint=copy.deepcopy(request["workpoint"]),
                planner_readiness=planner_readiness(mapping, request, now=now, max_age_s=max_age_s),
                formal_profile_admitted=False, performance_ready=False,
                model_weights_ready_measured=False, solver_capacity_profile=None,
                scope="same finite block workload under measured load; no model or layout interpolation")


def bind_layout(case, selected, actual_catalog, workers, owner_ids):
    return bind_real_cache(case, selected, actual_catalog, workers, owner_ids)


def execute_collection(controller, plan, *, collection_kind, preparation_cost_s,
                       clock_domain, readiness_binding=None, clock_ns=time.monotonic_ns):
    """Measure all published targets, then verify; remote clocks are never subtracted."""
    validate_plan(plan)
    if collection_kind not in ("synthetic_blocks", "packed_weight_snapshot", "model_weights"):
        raise ValueError("unknown collection kind")
    if (not clock_domain or type(preparation_cost_s) not in (int, float)
            or not 0 <= preparation_cost_s < float("inf")):
        raise ValueError("explicit controller domain and finite preparation cost required")
    if collection_kind == "model_weights" and readiness_binding is None:
        raise ValueError("actual engine weight binding is required for model readiness")
    begin = clock_ns()
    replica_targets = any(t.get("mode") == "replica" for t in plan["targets"])
    transfer = controller.transfer(plan, verify_before_ready=replica_targets, retain=True)
    tx = transfer["tx"]
    try:
        binding = readiness_binding(transfer) if readiness_binding is not None else None
        if collection_kind == "model_weights" and (not isinstance(binding, dict)
                or binding.get("all_target_weights_usable") is not True
                or not binding.get("source_refs")):
            raise ValueError("engine did not confirm every target weight binding")
        ready = clock_ns()
        if ready < begin:
            raise ValueError("controller clock moved backwards")
        verification = transfer.get("verification") if replica_targets else controller.verify(tx)
        if verification.get("passed") is not True or verification.get("checked_bytes") != sum(t["n"] for t in plan["targets"]):
            raise ValueError("full collection verification failed")
        timing = transfer.get("controller_timing", {})
        if timing and timing.get("clock_domain") != clock_domain:
            raise ValueError("transfer belongs to another controller clock domain")
        start_ns = timing.get("load_begin_ns", begin)
        filled_ns = timing.get("all_targets_filled_ns")
        published_ns = timing.get("all_targets_published_ns", ready)
        if not start_ns <= (filled_ns if filled_ns is not None else published_ns) <= published_ns <= ready:
            raise ValueError("invalid controller phase timestamp order")
        metric_ready_ns = ready if collection_kind == "model_weights" else published_ns
        return dict(collection_kind=collection_kind, tx=tx, load_begin_ns=start_ns,
                    all_targets_ready_ns=metric_ready_ns, weights_collection_ready_s=(metric_ready_ns-start_ns)/1e9,
                    block_buffers_filled_s=(filled_ns-start_ns)/1e9 if filled_ns is not None else None,
                    published_collection_ready_s=(published_ns-start_ns)/1e9,
                    model_weights_ready_s=(metric_ready_ns-start_ns)/1e9 if collection_kind == "model_weights" else None,
                    clock_domain=clock_domain,
                    verification_position="before_replica_publish" if replica_targets else "after_weights_ready",
                    verification=verification, preparation_cost_s=preparation_cost_s,
                    all_targets_filled_s=transfer.get("prepare_s", 0) + transfer.get("execute_wall_s", 0),
                    model_weights_ready_measured=collection_kind == "model_weights",
                    engine_binding=binding, transfer=transfer,
                    metric_scope="all targets published; engine binding included when model_weights",
                    performance_claim_ready=False)
    finally:
        controller.finish(tx)
