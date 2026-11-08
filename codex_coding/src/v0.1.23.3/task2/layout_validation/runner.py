"""Four fixed cache layouts through the existing guarded serial controller."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[5]
INTEGRATION = ROOT / "codex_coding/src/v0.1.23.3/task2/minimal_calibration/integration"
sys.path.insert(0, str(INTEGRATION))
sys.path.insert(0, str(ROOT / "main_lib"))
module_spec = importlib.util.spec_from_file_location("layout_original_session", INTEGRATION / "run_session.py")
original = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(original)
from fluidckpt.benchmarks.calibrated_recovery import bind_layout, execute_collection
from fluidckpt.benchmarks.frozen_layout import layout_ledger
from fluidckpt.benchmarks.topology_cases import canonical_hash, interleave
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN, request
from fluidckpt.executor.chunks import DEFAULT_CHUNK, STAGING_BYTES
from fluidckpt.topology.resource_map import evidence_ref

BLOCK_BYTES = 16 << 20
DYNAMIC_MARGIN = 8 << 20
LAYOUTS = (("A", (0, 1)), ("B", (0, 0)), ("C", (1, 1)), ("D", (1, 0)))
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/layout_validation"
PROFILE = dict(rates=dict(local_dram=1, local_hbm=2, peer=1, network=1),
               gpu_ingress_GBps=2, gpu_egress_GBps=2, dram_GBps=2,
               network_node_GBps=2, serial_target_execution=True,
               provenance="functional placeholders; manual layout only")


def contract():
    layouts = {}
    for name, locations in LAYOUTS:
        ops = [dict(op_id=tp, tp=tp, src_w=src, dst_w=tp, n=BLOCK_BYTES,
                    kind="local_hbm" if src == tp else "network",
                    worker_roles={str(w): ("both" if src == tp else
                                  "source" if w == src else "target")
                                  for w in ({tp} if src == tp else {0, 1})})
               for tp, src in enumerate(locations)]
        layouts[name] = dict(label=dict(A="target_local", B="concentrated28", C="concentrated30", D="crossed")[name],
            cache_workers=list(locations), ops=ops,
            cache_bytes_by_worker={str(w): locations.count(w)*BLOCK_BYTES for w in (0, 1)},
            network_payload_bytes=sum(o["n"] for o in ops if o["kind"] == "network"))
    result = dict(schema_version="layout_validation.v1", development="v0.1.23.3/task2",
        block_bytes=BLOCK_BYTES, layouts=layouts, order=[name for name, _ in LAYOUTS],
        cache_hbm_bytes=2*BLOCK_BYTES, cache_location_cap_bytes=2*BLOCK_BYTES,
        base_dram_bytes=2*BLOCK_BYTES, base_worker=0, target_hbm_bytes=2*BLOCK_BYTES,
        staging_hbm_bytes_per_worker=STAGING_BYTES, chunk_bytes=DEFAULT_CHUNK,
        executor="serial", verification_position="before_replica_publish",
        dynamic_margin_bytes=DYNAMIC_MARGIN, physical_task_budget_bytes=original.TASK_BUDGET,
        weak_time_hypothesis=dict(A="usually shorter filled window", D="usually longer filled window",
                                  B_vs_C="unknown"),
        dependency_order=dict(A="independent local ops", B="local0 then forward1",
                              C="reverse0 then local1", D="reverse0 then forward1"),
        simultaneous_bidirectional=False, prediction_ready=False,
        absolute_time_prediction=None, model_weights_ready_measured=False,
        formal_performance_ready=False, no_solver_delta=True)
    result["contract_sha256"] = canonical_hash(result)
    return result


def source_refs():
    paths = [Path(__file__), INTEGRATION / "run_session.py", INTEGRATION / "build_contract.py"]
    paths += [ROOT / "main_lib/fluidckpt" / p for p in (
        "benchmarks/calibrated_recovery.py", "benchmarks/frozen_layout.py",
        "benchmarks/topology_cases.py", "cluster/control.py", "cluster/worker.py",
        "cluster/planning.py", "executor/chunks.py")]
    return [evidence_ref(p) for p in paths]


def budget_gate(spec, workers):
    budget = copy.deepcopy(original.mesh_resource_budget(spec, workers, BLOCK_BYTES))
    for row in budget["resources"]:
        # Both nodes may carry a 32MiB cache and a 16MiB target in this matrix.
        row["additional_application_hbm_bound_bytes"] = 3*BLOCK_BYTES
        row["dynamic_margin_bytes"] = DYNAMIC_MARGIN
        for tier in ("dram", "hbm"):
            projection = (row[f"fixed_runtime_{tier}_increment_bytes"]
                          + row[f"additional_application_{tier}_bound_bytes"] + DYNAMIC_MARGIN)
            row[f"projected_{tier}_increment_bytes"] = projection
        row["allowed"] = max(row["projected_dram_increment_bytes"],
                             row["projected_hbm_increment_bytes"]) <= original.TASK_BUDGET
    budget["passed"] = all(row["allowed"] for row in budget["resources"])
    budget["scope"] = "same preworker baseline; sampled runtime plus common application bounds and 8MiB margin"
    budget["per_layout_recheck_scope"] = "same postwarm source SHA/identity/freshness and static bound; no new physical sample"
    return budget


def assert_plan(plan, name, locations):
    expected = contract()["layouts"][name]["ops"]
    fields = ("op_id", "tp", "src_w", "dst_w", "n", "kind")
    actual = [{k: op[k] for k in fields} for op in plan["ops"]]
    if actual != [{k: op[k] for k in fields} for op in expected]:
        raise ValueError("actual ordered restore paths differ from frozen layout")
    if tuple(locations) != tuple(contract()["layouts"][name]["cache_workers"]):
        raise ValueError("cache placement differs from frozen contract")
    if plan["executor"] != "serial" or plan["chunk_bytes"] != DEFAULT_CHUNK:
        raise ValueError("executor/chunk changed")


def checked_inventory(cluster, spec):
    workers, replicas = original.owned_inventory(cluster, spec)
    if any(r.get("leases", 0) for w in workers for r in w["replicas"]):
        raise ValueError("owned catalog has an outstanding lease")
    return workers, replicas


def assert_execution(measured, name):
    expected = contract()["layouts"][name]["ops"]
    execution = measured["transfer"]["execution"]
    if sorted(w["worker"] for w in execution) != [0, 1]:
        raise ValueError("missing or duplicated execution worker")
    for worker in execution:
        wid = str(worker["worker"])
        records = [dict(op_id=r["op_id"], role=r["role"], bytes=r["bytes"], kind=r["kind"])
                   for r in worker["records"]]
        wanted = [dict(op_id=o["op_id"], role=o["worker_roles"][wid], bytes=o["n"], kind=o["kind"])
                  for o in expected if wid in o["worker_roles"]]
        if records != wanted:
            raise ValueError("actual worker roles/order/bytes differ from frozen contract")


def execute(spec, out, *, mesh_only=False):
    out = Path(out)
    result = dict(status="preflight_pending", cases=[], frozen_contract=contract(),
                  source_refs=source_refs(), spec_ref=evidence_ref(spec["_source_path"]),
                  source_allocations=0, performance_claim_ready=False,
                  model_weights_ready_measured=False)
    def save():
        (out / "execution.json").write_text(json.dumps(result, indent=2)+"\n")
    save()
    try:
        original.launch_plan(spec)  # Validate registered physical bindings, never emit obsolete commands.
        launch_ref = spec.get("actual_launch_plan_ref")
        if not launch_ref:
            raise ValueError("execution requires actual_launch_plan_ref for the parent's exact worker launch plan")
        if evidence_ref(launch_ref["path"]) != launch_ref:
            raise ValueError("actual worker launch plan SHA changed")
        result["actual_worker_launch_plan_ref"] = launch_ref
        # Reuse all prerequisite, identity, freshness, empty-session, mesh and warm guards.
        guarded = original.execute(spec, out, mesh_only=True)
        (out / "guarded_mesh.json").write_text(json.dumps(guarded, indent=2)+"\n")
        result.update(session=guarded["session"], fixed_preparation=guarded["fixed_preparation"],
                      mesh_inventory=guarded["mesh_inventory"], executor="serial", chunk_bytes=DEFAULT_CHUNK)
        save()
        if mesh_only:
            result["status"] = "mesh_ready_budget_pending"
            save()
            return result
        cluster = Cluster([w["endpoint"] for w in spec["workers"]], PROFILE, executor="serial")
        workers, _ = checked_inventory(cluster, spec)
        result["mesh_resource_budget"] = budget_gate(spec, workers)
        if not result["mesh_resource_budget"]["passed"]:
            result["status"] = "budget_rejected_before_synth"
            save()
            return result
        owned = []
        prefix = spec["session"]+"/"+out.name+"/16MiB"
        row = dict(block_bytes=BLOCK_BYTES, layouts={}, cleanup_errors=[])
        result["cases"].append(row)
        try:
            start = time.monotonic()
            for tp in (0, 1):
                snap, rid = prefix+f"/snap/{tp}", prefix+f"/base/{tp}"
                request(cluster.endpoints[0], "synth", dict(snap=snap, tp=tp, nbytes=BLOCK_BYTES, seed=100+tp))
                result["source_allocations"] += 1
                request(cluster.endpoints[0], "adopt", dict(snap=snap, tp=tp, id=rid))
                owned.append((0, rid))
            row["source_preparation_s"] = time.monotonic()-start
            workers, replicas = checked_inventory(cluster, spec)
            base = sorted([r for r in replicas if r["id"].startswith(prefix+"/base/")], key=lambda r:r["tp"])
            if len(base) != 2 or any(b["n"] != BLOCK_BYTES for b in base):
                raise ValueError("complete synthetic source pair missing")
            for name, locations in LAYOUTS:
                layout_owned, measured = [], {}
                row["layouts"][name] = measured
                try:
                    workers, _ = checked_inventory(cluster, spec)
                    measured["budget_gate"] = budget_gate(spec, workers)
                    if not measured["budget_gate"]["passed"]:
                        raise ValueError("layout budget rejected before cache allocation")
                    start = time.monotonic()
                    caches = [original.descriptor(b, prefix+f"/{name}/cache/{b['tp']}", w)
                              for b,w in zip(base, locations)]
                    selected = [dict(t, node=workers[t["worker"]]["node"], gpu=workers[t["worker"]]["gpu"],
                                     numa=workers[t["worker"]]["numa"], preview_only=True, state="VALID") for t in caches]
                    frozen = dict(candidates=selected, budget=dict(tier_budgets=dict(hbm=2*BLOCK_BYTES),
                        location_budgets={f"{i}:hbm":2*BLOCK_BYTES for i in (0,1)},
                        base_dram_bytes=sum(b["allocated_bytes"] for b in base),
                        target_allocation_bytes=2*BLOCK_BYTES, staging_bytes=2*STAGING_BYTES))
                    measured["frozen_layout_ledger"] = layout_ledger(frozen, selected)
                    for target in caches:
                        layout_owned.append((target["worker"], target["id"]))
                        plan = cluster.plan_restore([target], "nearest", [base[target["tp"]]["id"]])
                        plan["chunk_bytes"] = DEFAULT_CHUNK
                        cluster.transfer(plan, verify_before_ready=True)
                    cache_preparation_s = time.monotonic()-start
                    workers, replicas = checked_inventory(cluster, spec)
                    measured["cache_built_inventory"] = workers
                    cache_ids = [t["id"] for t in caches]
                    cache = [next(r for r in replicas if r["id"] == rid) for rid in cache_ids]
                    cache = bind_layout(frozen, selected, cache, workers, cache_ids)
                    targets = [original.descriptor(b, prefix+f"/{name}/target/{b['tp']}", b["tp"]) for b in base]
                    measured["physical_ledger"] = original.check_peak(workers, targets)
                    plan = interleave(cluster.solve_restore(cache, targets, workers, "nearest"))
                    plan.update(executor="serial", chunk_bytes=DEFAULT_CHUNK, prediction_ready=False,
                                profile_source="CPU functional placeholders", scope="manual fixed cache; no prediction")
                    for field in ("estimated_transfer_s", "bandwidth_lower_bound_s", "serial_target_times_s"):
                        plan.pop(field, None)
                    assert_plan(plan, name, locations)
                    measured["restore_plan"] = plan
                    layout_owned.extend((t["worker"],t["id"]) for t in targets)
                    measured.update(execute_collection(cluster, plan, collection_kind="synthetic_blocks",
                        preparation_cost_s=cache_preparation_s, clock_domain=CONTROLLER_CLOCK_DOMAIN))
                    assert_execution(measured, name)
                    after, _ = checked_inventory(cluster, spec)
                    measured.update(after_inventory=after, cache_build_logical_bytes=2*BLOCK_BYTES,
                                    cache_allocated_bytes=sum(r["allocated_bytes"] for r in cache),
                                    source_preparation_s=row["source_preparation_s"], status="passed")
                    save()
                finally:
                    errors = original.cleanup_owned(cluster, layout_owned)
                    measured["cleanup_errors"] = errors
                    after, replicas = checked_inventory(cluster, spec)
                    measured["after_cleanup_inventory"] = after
                    measured["owned_replicas_remaining"] = [r["id"] for r in replicas if r["id"].startswith(prefix+f"/{name}/")]
                    save()
                    if errors or measured["owned_replicas_remaining"]:
                        raise ValueError("owned layout retirement not confirmed")
        finally:
            row["cleanup_errors"].extend(original.cleanup_owned(cluster, owned))
            after, replicas = checked_inventory(cluster, spec)
            row["after_cleanup"] = after
            row["owned_replicas_remaining"] = [r["id"] for r in replicas if r["id"].startswith(prefix)]
            save()
            if row["cleanup_errors"] or row["owned_replicas_remaining"]:
                raise ValueError("owned base retirement not confirmed")
        result.update(status="passed", strong_nearest_target_alias="A", no_solver_delta=True)
        save()
        return result
    except BaseException as exc:
        result.update(status="failed", failure=repr(exc))
        save()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--mesh-only", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != RESULTS:
        parser.error("require a new layout_validation result directory")
    out.mkdir(parents=True, exist_ok=False)
    spec = json.loads(args.spec.read_text())
    spec["_source_path"] = str(args.spec.resolve())
    (out / "contract.json").write_text(json.dumps(contract(), indent=2)+"\n")
    (out / "source_refs.json").write_text(json.dumps(source_refs(), indent=2)+"\n")
    if args.execute:
        result = execute(spec, out, mesh_only=args.mesh_only)
        if result["status"] == "budget_rejected_before_synth":
            raise SystemExit(2)
    else:
        original.launch_plan(spec)
    print(json.dumps(dict(out=str(out), executed=args.execute)))


if __name__ == "__main__":
    main()
