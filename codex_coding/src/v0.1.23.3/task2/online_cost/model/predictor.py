"""Finite, transferable aggregate-path hypotheses for full online validation."""
import copy
import math
from pathlib import Path
import statistics
import sys

from evaluate import digest, require, verify_ref

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.cluster.planning import validate_plan
from fluidckpt.cluster.service_cost import estimate_execution, fit_path, new_state
from fluidckpt.cluster.path_catalog import topology_id
from fluidckpt.executor.chunks import execution_chunk

GIB = 1 << 30


def physical(workers):
    return [{k: w[k] for k in ("worker", "node", "gpu", "numa")} for w in workers]


def plan_workload(plan):
    """Exclude source assignments and transaction keys; retain exact target work."""
    chunk = plan["chunk_bytes"]
    return {"targets": sorted([{k: t[k] for k in ("worker", "identity", "tp", "off", "n")}
                               for t in plan["targets"]], key=lambda t: (t["worker"], t["tp"], t["off"])),
            "ordered_extents": [{k: o[k] for k in ("dst_w", "identity", "tp", "off", "n")}
                                for o in plan["ops"]],
            "N": sum(o["n"] for o in plan["ops"]), "K": len(plan["ops"]),
            "C": sum((o["n"]+chunk-1)//chunk for o in plan["ops"])}


def build_model(context, workers, samples, shared_samples, source_refs):
    """Fit only preregistered calibration records; this never opens holdouts."""
    require(context["executor"] in ("serial", "double_buffer"), "executor")
    execution_chunk(context)
    require(context["control_mode"] and context["load_state"] and context["clock_domain"], "state/clock/config required")
    require(context["clock_scope"] == "single_controller", "one coordinator clock required")
    require(set(context["worker_BDF"]) == {str(w["worker"]) for w in workers} and all(context["worker_BDF"].values()), "current GPU BDF binding required")
    require(context["worker_physical"] == physical(workers), "calibration physical binding mismatch")
    require(source_refs, "calibration source refs required")
    for ref in source_refs:
        verify_ref(ref)
    require(set(samples) == set(context["path_context"]), "calibrate only/exactly preregistered required paths")
    costs, diagnostics = {}, {}
    sessions = set()
    for path, rows in samples.items():
        require(len(rows) == 2, "each required concrete path needs exactly two preregistered sizes")
        rows = sorted(rows, key=lambda r: r["nbytes"])
        require(all(type(r["nbytes"]) is int and r["nbytes"] > 0 and
                    math.isfinite(r["service_s"]) and r["service_s"] > 0 for r in rows), "invalid path calibration")
        require(max(r["nbytes"] for r in rows) >= GIB, "small-size historical fits cannot calibrate GiB paths")
        require(all(r["evidence_kind"] == "actual_path_calibration" and r["passed"] is True
                    and r["verified_bytes"] == r["nbytes"] for r in rows), "actual verified path calibration required")
        for row in rows:
            require(row["config_sha256"] == digest(context), "calibration configuration changed")
            require(row["session"], "missing calibration session")
            sessions.add(row["session"])
        c = fit_path(rows[0], rows[0], rows[1])
        # No cold setup inference: both records must have the registered state.
        c["first_pair_s"] = 0.0
        costs[path] = c
        diagnostics[path] = {"fit_bytes": [r["nbytes"] for r in rows],
                             "raw_intercept_s": rows[0]["service_s"] -
                                 (rows[1]["service_s"]-rows[0]["service_s"])/
                                 (rows[1]["nbytes"]-rows[0]["nbytes"])*rows[0]["nbytes"],
                             "interpretation": "Aggregate operation service; coefficients are not bare bandwidth/API latency."}
    qualifiers = {}
    for resource, rows in shared_samples.items():
        require(resource in context["shared_resource_paths"], "unregistered shared resource")
        require(1 <= len(rows) <= 2, "one shared point normally; at most two preregistered")
        require(all(r["evidence_kind"] == "actual_shared_calibration" and r["passed"] is True
                    and r["total_bytes"] >= GIB and r["overlap_confirmed"] is True
                    and r["resource_binding_confirmed"] is True for r in rows), "shared qualifier lacks actual overlap/resource binding")
        for row in rows:
            require(row["config_sha256"] == digest(context), "shared calibration configuration changed")
            require(sorted(row["paths"]) == sorted(context["shared_resource_paths"][resource]), "shared path set changed")
            require(type(row["flow_count"]) is int and row["flow_count"] == 2, "registered two-flow workpoint required")
            require(math.isfinite(row["service_s"]) and row["service_s"] > 0, "invalid shared duration")
            require(set(row["path_bytes"]) == set(row["paths"]) and sum(row["path_bytes"].values()) == row["total_bytes"], "shared per-flow byte counts")
            for path, count in row["path_bytes"].items():
                require(type(count) is int and costs[path]["fit_bytes"][0] <= count <= costs[path]["fit_bytes"][1], "shared per-flow bytes outside calibrated domain")
            sessions.add(row["session"])
        factors = []
        for row in rows:
            isolated = max(costs[path]["operation_s"] + count/1e9/costs[path]["warm_GBps"]
                           for path, count in row["path_bytes"].items())
            factors.append(row["service_s"]/isolated)
        qualifiers[resource] = {"concurrent_service_factor": statistics.median(factors),
                                "raw_factors": factors,
                                "calibration_total_bytes": [r["total_bytes"] for r in rows],
                                "flow_count": 2,
                                "interpretation": "Observed paired concurrent/isolated service ratio, only matching contiguous two-flow phases; not physical capacity or a strict bound."}
    base = {"schema": "online_path_model_v1", "context": copy.deepcopy(context),
            "topology_id": topology_id(workers), "paths": costs,
            "shared_qualifiers": qualifiers, "diagnostics": diagnostics,
            "source_refs": copy.deepcopy(source_refs), "calibration_sessions": sorted(sessions),
            "online_outer_s": None, "online_qualified": False, "physical_capacity_certified": False}
    return base


def transfer_service(plan, workers, model, state):
    """Reuse the product worker dependency scorer for both executors' aggregate costs."""
    validate_plan(plan)
    context = model["context"]
    require(physical(workers) == context["worker_physical"], "current physical mapping changed")
    require(plan.get("executor") == context["executor"] and plan.get("chunk_bytes") == context["chunk_bytes"], "execution/chunk changed")
    execution_chunk(plan)
    require(state.get("incarnations") == new_state(workers)["incarnations"], "state belongs to another incarnation")
    require(state.get("fresh_binding_verified") is True, "fresh source/current-incarnation binding missing")
    require(state.get("load_state") == context["load_state"], "execution state changed")
    require(state.get("config_sha256") == digest(context), "attested execution/configuration changed")
    require(state.get("worker_BDF") == context["worker_BDF"], "current GPU BDF binding changed")
    require(state.get("binding_source_refs"), "fresh physical/source binding evidence required")
    for ref in state["binding_source_refs"]:
        verify_ref(ref)
    require(plan_workload(plan) == context["full_model_plan_workload"], "target model/rank layout or N/K/C/extent order changed")
    paths = model["paths"]
    lineage = state["source_lineage"]
    used = set()
    for op in plan["ops"]:
        path = op["path"]
        require(path in paths, "unknown path")
        bounds = paths[path]["fit_bytes"]
        require(bounds[0] <= op["n"] <= bounds[1], "operation bytes outside GiB calibration interval")
        pcontext = context["path_context"][path]
        require([op["src_w"], op["dst_w"]] == pcontext["endpoints"], "path endpoint changed")
        require(op["kind"] == pcontext["operation_kind"], "path primitive changed")
        require(context["executor"] != "double_buffer" or pcontext["source_tier"] != "ssd", "SSD unsupported by tensor double-buffer path")
        require(op["replica_id"] in lineage, "unattested source")
        source = lineage[op["replica_id"]]
        require(all(op.get(k) == source.get(k) for k in ("identity", "generation", "source_epoch")), "source lineage changed")
        require(source["worker"] == op["src_w"] and source["source_epoch"] == next(w["epoch"] for w in workers if w["worker"] == op["src_w"]), "source incarnation mismatch")
        require(source["tier"] == pcontext["source_tier"] and source["numa"] == pcontext["source_numa"], "source tier/NUMA changed")
        if op["src_w"] != op["dst_w"]:
            pair = ":".join(map(str, sorted((op["src_w"], op["dst_w"]))))
            require(context["load_state"] == "warm" and pair in state.get("warm_pairs", []), "warm-only path model rejects unconfirmed/cold pairs")
        used.add(path)
    require(context["operation_count_interval"][0] <= len(plan["ops"]) <= context["operation_count_interval"][1], "operation count outside preregistered full-model domain")
    # Aggregate double-buffer operation timing already includes within-op overlap.
    # Its cross-operation source/target dependency graph is the same ordered graph.
    schedule_plan = dict(plan, executor="serial", estimated_transfer_s=plan.get("estimated_transfer_s"))
    schedule_model = {"schema_version": 1, "topology_id": model["topology_id"],
                      "executor": "serial", "chunk_bytes": context["chunk_bytes"],
                      "paths": paths, "request_s": 0.0, "dispatch_s": 0.0,
                      "scope": "Original worker-order DAG with actual-executor aggregate path costs."}
    isolated_dag = estimate_execution(schedule_plan, workers, schedule_model, state)
    adjusted_costs = copy.deepcopy(paths)
    shared = {}
    for resource, resource_paths in context["shared_resource_paths"].items():
        active = used.intersection(resource_paths)
        if len(active) < 2:
            continue
        qualifier = model["shared_qualifiers"].get(resource)
        require(qualifier is not None and sorted(active) == sorted(resource_paths), "uncalibrated shared combination")
        endpoints = [set(context["path_context"][p]["endpoints"]) for p in sorted(active)]
        require(not endpoints[0].intersection(endpoints[1]), "shared qualifier requires two disjoint worker pairs")
        phase = [[(op["off"], op["n"]) for op in plan["ops"] if op["path"] == path] for path in sorted(active)]
        require(phase[0] == phase[1] and phase[0], "shared paired-flow extent phases do not match")
        require(all(a+n == b for (a, n), (b, _) in zip(phase[0], phase[0][1:])), "shared qualifier only covers contiguous remote phases")
        factor = qualifier["concurrent_service_factor"]
        require(math.isfinite(factor) and factor > 0, "invalid empirical concurrent factor")
        for path in active:
            require(path not in {p for item in shared.values() for p in item["paths"]}, "overlapping shared qualifiers require further identification")
            adjusted_costs[path]["operation_s"] *= factor
            adjusted_costs[path]["warm_GBps"] /= factor
        shared[resource] = {"paths": sorted(active), "concurrent_service_factor": factor,
                            "phase_begin": phase[0][0][0], "phase_bytes_per_flow": sum(n for _, n in phase[0]),
                            "phase_ops_per_flow": len(phase[0])}
    schedule_model["paths"] = adjusted_costs
    dag = estimate_execution(schedule_plan, workers, schedule_model, state)
    return {"estimated_transfer_service_s": dag["estimated_worker_s"], "worker_dag_s": dag["estimated_worker_s"],
            "isolated_worker_dag_s": isolated_dag["estimated_worker_s"],
            "timeline": dag["timeline"], "shared_phase_qualifiers": shared,
            "shared_score_interpretation": "Empirical concurrent factor applied only to registered matched contiguous two-flow phases, then original worker DAG reused.",
            "physical_capacity_certified": False}


def calibrate_outer(model, calibration):
    """An actual full-model calibration restore supplies the once-only outer residual."""
    require(calibration["evidence_kind"] == "actual_model_online_calibration", "full-model calibration needed; no empty readiness sentinel")
    require(calibration["full_sha_passed"] is True and calibration["all_target_weights_usable"] is True, "calibration correctness")
    require(calibration["exact_reference_output_passed"] is True, "calibration output differs from the fixed reference")
    require(calibration["verification_position"] == "after_all_weights_ready", "calibration SHA must be outside online clock")
    require(calibration["resource_gate_passed"] is True and calibration["cleanup_passed"] is True, "calibration resource/request lifecycle failed")
    require(calibration["verified_bytes"] == sum(t["n"] for t in calibration["plan"]["targets"]), "calibration full weight SHA coverage")
    require(calibration["config_sha256"] == digest(model["context"]), "full-model calibration context changed")
    require(calibration["clock_domain"] == model["context"]["clock_domain"] and
            calibration["clock_scope"] == "single_controller", "full-model calibration clock mismatch")
    service = transfer_service(calibration["plan"], calibration["workers"], model, calibration["state"])
    if service["shared_phase_qualifiers"]:
        require(calibration.get("shared_phase_overlap_confirmed") is True, "full-model calibration requires actual paired-phase overlap evidence")
    begin, ready = calibration["load_begin_ns"], calibration["all_weights_ready_ns"]
    require(type(begin) is int and type(ready) is int and 0 <= begin < ready, "invalid full-model clock")
    actual = ready - begin
    residual = actual / 1e9 - service["estimated_transfer_service_s"]
    require(math.isfinite(residual) and residual >= 0, "negative outer residual; do not clamp or rescue")
    verify_ref(calibration["source_ref"])
    result = copy.deepcopy(model)
    result["online_outer_s"] = residual
    result["source_refs"].append(calibration["source_ref"])
    result["calibration_sessions"] = sorted(set(result["calibration_sessions"] + [calibration["session"]]))
    result["outer_interpretation"] = "One inclusive full-model online-minus-service residual, including control/materialize/IPC/handoff; not a per-term causal identification."
    result["outer_calibration_service"] = service
    return result


def predict(plan, workers, model, state):
    require(model["online_outer_s"] is not None, "complete online outer calibration not available")
    for ref in model["source_refs"]:
        verify_ref(ref)
    service = transfer_service(plan, workers, model, state)
    return dict(service, estimated_online_ns=round((service["estimated_transfer_service_s"] + model["online_outer_s"]) * 1e9),
                online_outer_s=model["online_outer_s"], online_qualified=False,
                scope="Preregistered transferable aggregate-path hypothesis; qualification requires every independent whole-model holdout within 10%.")
