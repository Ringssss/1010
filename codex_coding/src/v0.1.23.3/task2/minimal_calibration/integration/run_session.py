"""Prepare existing worker commands or execute two owned-session block layouts."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from build_contract import ROOT, RESULTS, block_case
from fluidckpt.benchmarks.calibrated_recovery import bind_layout, execute_collection
from fluidckpt.benchmarks.frozen_layout import layout_ledger
from fluidckpt.benchmarks.topology_cases import interleave
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN, request
from fluidckpt.executor.chunks import DEFAULT_CHUNK, STAGING_BYTES
from fluidckpt.topology.resource_map import evidence_ref

TASK_BUDGET = 2 << 30


def launch_plan(spec):
    if spec["session"] != "minimal_calibration_r01" or len(spec["workers"]) != 2:
        raise ValueError("require the registered new two-worker session")
    result = []
    mesh = "node28:0-0,node30:0-0"
    for i, row in enumerate(spec["workers"]):
        node, physical, numa, bdf = (("node28", 0, 0, "0000:09:00.0"),
                                     ("node30", 4, 4, "0000:85:00.0"))[i]
        if (row["node"], row["physical_gpu"], row["numa"], row["bdf"]) != (node, physical, numa, bdf):
            raise ValueError("worker physical binding differs from registered candidate")
        root = row["stage_root"]
        env = dict(PYTHONPATH=root + "/main_lib", HIP_VISIBLE_DEVICES=str(physical),
                   CUDA_VISIBLE_DEVICES=str(physical),
                   OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", HSA_FORCE_FINE_GRAIN_PCIE="1",
                   NCCL_NET_PLUGIN="librccl-net-shca.so", NCCL_NET="IBext_v8",
                   NCCL_IB_HCA="shca_0", NCCL_SOCKET_IFNAME="ib0", NCCL_TOPO_FILE=row["owned_xml"],
                   NCCL_DEBUG="INFO", NCCL_DEBUG_SUBSYS="INIT,GRAPH,NET",
                   NCCL_TOPO_DUMP_FILE_RANK=str(i),
                   NCCL_TOPO_DUMP_FILE=root + f"/codex_coding/results/v0.1.23.3/task2/minimal_calibration/worker_session_r01/{node}.runtime.dump.xml")
        argv = ["python3", "-m", "fluidckpt.cluster.worker", "--node", node, "--gpu", "0",
                "--numa", str(numa), "--port-base", "34500", "--mesh", mesh,
                "--master", "12.11.3.28:29970", "--handoff", root + "/handoff_own_session",
                "--session", spec["session"], "--dram-budget", str(TASK_BUDGET),
                "--hbm-budget", str(TASK_BUDGET-STAGING_BYTES)]
        result.append(dict(node=node, argv=argv, env=env, unset_env=["ROCR_VISIBLE_DEVICES"], logical_gpu=0,
                           boot_id=row.get("boot_id"), container_id=row.get("container_id"),
                           container_started_at=row.get("container_started_at"),
                           physical_gpu=physical, bdf=bdf, numa=numa,
                           physical_budget_bytes=dict(dram=TASK_BUDGET, hbm=TASK_BUDGET),
                           staging_hbm_bytes=STAGING_BYTES,
                           endpoint=row["endpoint"], launch_by="parent exact-owner supervisor"))
    return dict(session=spec["session"], workers=result, new_service_framework=False,
                actual_BDF_confirmation="new qualified path and parent process evidence required",
                GPU_operations=0, remote_operations=0)


def owned_inventory(cluster, spec):
    workers, replicas = cluster.inventory()
    for i, (row, binding) in enumerate(zip(workers, spec["workers"])):
        if (row["worker"], row["session"], row["node"], row["gpu"], row["numa"]) != (
                i, spec["session"], binding["node"], 0, binding["numa"]):
            raise ValueError("endpoint does not belong to the new owned session")
        if row["budget_bytes"]["dram"] != TASK_BUDGET or row["budget_bytes"]["hbm"] != TASK_BUDGET-STAGING_BYTES:
            raise ValueError("worker budgets differ from registered physical budget")
        if row["transactions"]:
            raise ValueError("session has an in-flight transaction")
    return workers, replicas


def descriptor(source, rid, worker):
    return dict(key=rid, id=rid, mode="replica", worker=worker, tier="hbm", tp=source["tp"],
                identity=source["identity"], off=source["off"], n=source["n"],
                meta=source["meta"], index=source["index"])


def check_peak(workers, targets):
    ledger = []
    for row in workers:
        target_n = sum((t["n"]+4095)//4096*4096 for t in targets if t["worker"] == row["worker"])
        projected = row["used_bytes"]["hbm"] + target_n + row["staging_bytes"]
        allocator_peak = max(row["hbm_allocated_bytes"], row["hbm_reserved_bytes"]) + target_n
        if max(projected, allocator_peak) > TASK_BUDGET or target_n + (2 << 30) > row["headroom_bytes"]["hbm"]:
            raise ValueError("per-worker target/cache/staging peak or physical GPU guard insufficient")
        if row["used_bytes"]["dram"] > TASK_BUDGET:
            raise ValueError("per-worker retained source DRAM exceeds task budget")
        ledger.append(dict(worker=row["worker"], base_dram_bytes=row["used_bytes"]["dram"],
                           cache_hbm_bytes=row["used_bytes"]["hbm"], target_hbm_bytes=target_n,
                           staging_hbm_bytes=row["staging_bytes"], projected_hbm_bytes=projected,
                           conservative_allocator_peak_bytes=allocator_peak,
                           driver_context_peak_verified=False,
                           sampled_hbm_free_bytes=row["headroom_bytes"]["hbm"],
                           sampled_hbm_allocated_bytes=row["hbm_allocated_bytes"],
                           sampled_hbm_reserved_bytes=row["hbm_reserved_bytes"]))
    return ledger


def cleanup_owned(cluster, entries):
    errors = []
    try:
        workers, _ = cluster.inventory()
        live = {r["id"] for w in workers for r in w["replicas"] if r["state"] != "RELEASED"}
    except Exception as exc:
        live = None
        errors.append(dict(operation="cleanup_inventory", error=repr(exc)))
    for worker, rid in reversed(entries):
        if live is not None and rid not in live:
            continue
        try:
            request(cluster.endpoints[worker], "retire", dict(id=rid))
        except Exception as exc:
            errors.append(dict(worker=worker, id=rid, operation="retire", error=repr(exc)))
    return errors


def mesh_resource_budget(spec, workers, nbytes):
    """Include measured runtime increments before admitting any source allocation."""
    rows, passed = [], True
    for worker, binding in zip(workers, spec["workers"]):
        proofs = {}
        for name in ("baseline_guard_ref", "post_mesh_guard_ref"):
            ref = binding.get(name)
            if not ref or evidence_ref(ref["path"]) != ref:
                raise ValueError("mesh budget requires bound baseline and current guard sources")
            proofs[name] = json.loads(Path(ref["path"]).read_text())
            if proofs[name]["container"]["id"] != binding["container_id"] or proofs[name]["boot_id"] != binding["boot_id"]:
                raise ValueError("mesh budget guard belongs to another container/boot")
        before, current = proofs["baseline_guard_ref"], proofs["post_mesh_guard_ref"]
        stamp = datetime.fromisoformat(current["started_utc"].replace("Z", "+00:00")).timestamp()
        if not 0 <= datetime.now(timezone.utc).timestamp()-stamp <= 300:
            raise ValueError("post-mesh budget sample expired")
        before_dram = int(before["container_cgroup"]["files"]["memory.current"])
        current_dram = int(current["container_cgroup"]["files"]["memory.current"])
        before_hbm = int(before["selected_gpu_sysfs"]["mem_info_vram_used"])
        current_hbm = int(current["selected_gpu_sysfs"]["mem_info_vram_used"])
        # A digest may simultaneously hold a CPU tensor and its tobytes copy.
        additional_dram = (2*nbytes if worker["worker"] == 0 else 0) + 2*min(DEFAULT_CHUNK, nbytes)
        # Concentrated cache plus one target is the larger of the two HBM layouts.
        additional_hbm = (3 if worker["worker"] == 0 else 2)*nbytes
        dram_increment, hbm_increment = max(0, current_dram-before_dram), max(0, current_hbm-before_hbm)
        allowed = max(dram_increment+additional_dram, hbm_increment+additional_hbm) <= TASK_BUDGET
        passed = passed and allowed
        rows.append(dict(worker=worker["worker"], node=binding["node"],
            fixed_runtime_dram_increment_bytes=dram_increment, fixed_runtime_hbm_increment_bytes=hbm_increment,
            additional_application_dram_bound_bytes=additional_dram,
            additional_application_hbm_bound_bytes=additional_hbm,
            projected_dram_increment_bytes=dram_increment+additional_dram,
            projected_hbm_increment_bytes=hbm_increment+additional_hbm,
            task_budget_bytes=TASK_BUDGET, allowed=allowed,
            baseline_guard_ref=binding["baseline_guard_ref"], post_mesh_guard_ref=binding["post_mesh_guard_ref"],
            full_physical_peak_verified=False,
            scope="sampled runtime increment plus bounded application buffers; startup peak remains unknown"))
    return dict(passed=passed, resources=rows, full_physical_peak_verified=False)


def execute(spec, out, *, mesh_only=False):
    if spec.get("block_sizes_mib") != [16]:
        raise ValueError("this run authorizes only two 16MiB blocks; larger CPU candidates are not executable")
    for ref in spec.get("runtime_qualification_refs", []) + spec.get("current_guard_refs", []):
        if evidence_ref(ref["path"]) != ref:
            raise ValueError("session prerequisite source SHA changed")
    if not spec.get("runtime_qualification_refs") or not spec.get("current_guard_refs"):
        raise ValueError("new selected path qualification and current resource guards required")
    qualified = {}
    for ref in spec["runtime_qualification_refs"]:
        evidence = json.loads(Path(ref["path"]).read_text())
        child = evidence.get("child_result", {})
        qualified[child.get("requested_node")] = evidence
    for binding in spec["workers"]:
        evidence = qualified.get(binding["node"], {})
        child = evidence.get("child_result", {})
        logs = child.get("communication_logs", {})
        if (evidence.get("passed") is not True or evidence.get("xml_runtime_accepted") is not True
                or child.get("actual_bdf") != binding["bdf"] or child.get("requested_gpu") != binding["physical_gpu"]
                or child.get("selected_gpu_mapping", {}).get("passed") is not True
                or child.get("selected_net_mapping", {}).get("passed") is not True
                or logs.get("selected_HCA_ports") != [["shca_0", "1"]]
                or logs.get("actual_net_protocols") != ["IBext_v8"]):
            raise ValueError("qualification does not match this selected physical GPU/NIC path")
    guarded_ids = []
    for ref in spec["current_guard_refs"]:
        evidence = json.loads(Path(ref["path"]).read_text())
        stamp = datetime.fromisoformat(evidence["started_utc"].replace("Z", "+00:00")).timestamp()
        age = datetime.now(timezone.utc).timestamp()-stamp
        if not 0 <= age <= spec.get("guard_max_age_s", 300):
            raise ValueError("resource guard expired; preserve this result and use a fresh run")
        binding = next((w for w in spec["workers"] if w.get("container_id") == evidence.get("container", {}).get("id")), None)
        if (binding is None or evidence.get("boot_id") != binding.get("boot_id")
                or evidence.get("container", {}).get("State", {}).get("StartedAt") != binding.get("container_started_at")
                or not all(evidence.get("container_identity_checks", {}).get(k) is True for k in ("id", "started", "image", "running"))):
            raise ValueError("current guard does not bind this owned container/boot")
        guarded_ids.append(binding["container_id"])
    if sorted(guarded_ids) != sorted(w["container_id"] for w in spec["workers"]):
        raise ValueError("each new container requires exactly one current guard")
    profile = dict(rates=dict(local_dram=1, local_hbm=2, peer=1, network=1),
                   gpu_ingress_GBps=2, gpu_egress_GBps=2, dram_GBps=2,
                   network_node_GBps=2, serial_target_execution=True,
                   provenance="functional placeholders; manual layout only")
    cluster = Cluster([row["endpoint"] for row in spec["workers"]], profile, executor="serial")
    preparation = {}
    if not spec.get("mesh_initialized"):
        premesh = cluster.call_all("inventory", None)
        for i, row in enumerate(premesh):
            binding = spec["workers"][i]
            if (row["session"], row["node"], row["gpu"], row["numa"]) != (
                    spec["session"], binding["node"], 0, binding["numa"]):
                raise ValueError("pre-mesh endpoint does not belong to registered owned session")
            if row["worker"] is not None or row["replicas"] or row["transactions"]:
                raise ValueError("worker was already initialized or contains previous state")
        # Both workers must be initialized before either enters collective mesh creation.
        started = time.monotonic()
        cluster.call_all("mesh", [{}, {}])
        preparation["mesh_s"] = time.monotonic()-started
        started = time.monotonic()
        preparation["warm_result"] = cluster.call_all("warm", [dict(pairs=[[0, 1]]), dict(pairs=[[0, 1]])])
        preparation["pair_warm_s"] = time.monotonic()-started
    elif spec.get("pair_warmed") is not True:
        raise ValueError("initialized pair must be warmed before its fixed budget is assessed")
    workers, replicas = owned_inventory(cluster, spec)
    if replicas:
        raise ValueError("new session is not empty")
    result = dict(session=spec["session"], spec_ref=evidence_ref(spec["_source_path"]),
                  collection_kind="synthetic_blocks", model_weights_ready_measured=False,
                  automatic_planner_admitted=False, performance_claim_ready=False,
                  worker_runtime_path_verification="pending independent own communicator log/dump review",
                  fixed_preparation=preparation,
                  executor="serial", chunk_bytes=DEFAULT_CHUNK, cases=[])
    def save():
        (out / "execution.json").write_text(json.dumps(result, indent=2) + "\n")
    save()
    result["mesh_inventory"] = workers
    if mesh_only:
        result.update(status="mesh_ready_budget_pending", source_allocations=0,
                      next_step="parent captures current cgroup/GPU guard; then permits application or stops own workers")
        save()
        return result
    result["mesh_resource_budget"] = mesh_resource_budget(spec, workers, 16 << 20)
    if not result["mesh_resource_budget"]["passed"]:
        result.update(status="budget_rejected_before_synth", source_allocations=0)
        save()
        return result
    for mib in spec["block_sizes_mib"]:
        nbytes, owned = mib << 20, []
        prefix = spec["session"] + "/" + out.name + f"/{mib}MiB"
        row = dict(block_bytes=nbytes, layouts={}, source_preparation_s=None)
        result["cases"].append(row)
        try:
            prep_start = time.monotonic()
            for tp in (0, 1):
                snap, rid = prefix + f"/snap/{tp}", prefix + f"/base/{tp}"
                request(cluster.endpoints[0], "synth", dict(snap=snap, tp=tp, nbytes=nbytes, seed=100+tp))
                request(cluster.endpoints[0], "adopt", dict(snap=snap, tp=tp, id=rid))
                owned.append((0, rid))
            row["source_preparation_s"] = time.monotonic()-prep_start
            workers, replicas = owned_inventory(cluster, spec)
            base = sorted([r for r in replicas if r["id"].startswith(prefix + "/base/")], key=lambda r: r["tp"])
            if len(base) != 2 or any(b["n"] != nbytes for b in base):
                raise ValueError("complete synthetic source pair missing")
            for layout, locations in (("source_concentrated", (0, 0)), ("target_local_distributed", (0, 1))):
                layout_owned = []
                build_start = time.monotonic()
                try:
                    cache_targets = [descriptor(b, prefix+f"/{layout}/cache/{b['tp']}", w)
                                     for b, w in zip(base, locations)]
                    selected = [dict(t, node=workers[t["worker"]]["node"], gpu=workers[t["worker"]]["gpu"],
                                     numa=workers[t["worker"]]["numa"], preview_only=True, state="VALID")
                                for t in cache_targets]
                    frozen = dict(candidates=selected, budget=dict(tier_budgets=dict(hbm=2*nbytes),
                        location_budgets={f"{i}:hbm": 2*nbytes for i in (0, 1)},
                        base_dram_bytes=sum(b["allocated_bytes"] for b in base),
                        target_allocation_bytes=2*nbytes, staging_bytes=2*STAGING_BYTES))
                    frozen_ledger = layout_ledger(frozen, selected)
                    for target in cache_targets:
                        plan = cluster.plan_restore([target], "nearest", [base[target["tp"]]["id"]])
                        plan["chunk_bytes"] = DEFAULT_CHUNK
                        cluster.transfer(plan, verify_before_ready=True)
                        layout_owned.append((target["worker"], target["id"]))
                    cache_preparation_s = time.monotonic()-build_start
                    workers, replicas = owned_inventory(cluster, spec)
                    cache_ids = [t["id"] for t in cache_targets]
                    cache = [next(r for r in replicas if r["id"] == rid) for rid in cache_ids]
                    cache = bind_layout(frozen, selected, cache, workers, cache_ids)
                    targets = [descriptor(b, prefix+f"/{layout}/target/{b['tp']}", b["tp"]) for b in base]
                    ledger = check_peak(workers, targets)
                    plan = interleave(cluster.solve_restore(cache, targets, workers, "nearest"))
                    plan.update(executor="serial", chunk_bytes=DEFAULT_CHUNK,
                                scope="manual frozen cache locations; functional routing only; no calibrated prediction",
                                prediction_ready=False, profile_source="CPU functional placeholders")
                    for field in ("estimated_transfer_s", "bandwidth_lower_bound_s", "serial_target_times_s"):
                        plan.pop(field, None)
                    layout_owned.extend((t["worker"], t["id"]) for t in targets)
                    measured = execute_collection(cluster, plan, collection_kind="synthetic_blocks",
                        preparation_cost_s=cache_preparation_s, clock_domain=CONTROLLER_CLOCK_DOMAIN)
                    after, _ = owned_inventory(cluster, spec)
                    measured.update(physical_ledger=ledger, cache_build_logical_bytes=2*nbytes,
                                    cache_allocated_bytes=sum(r["allocated_bytes"] for r in cache),
                                    frozen_layout_ledger=frozen_ledger,
                                    after_inventory=after, source_preparation_s=row["source_preparation_s"])
                    row["layouts"][layout] = measured
                    if layout == "target_local_distributed":
                        row.update(strong_nearest_target_alias=layout, no_solver_delta=True,
                                   duplicate_execution_policy="do not rerun identical layout")
                    save()
                finally:
                    cleanup_errors = cleanup_owned(cluster, layout_owned)
                    row.setdefault("cleanup_errors", []).extend(cleanup_errors)
                    save()
                    if cleanup_errors and sys.exc_info()[0] is None:
                        raise RuntimeError("owned layout cleanup failed; see saved errors")
        except BaseException as exc:
            row["failure"] = repr(exc)
            raise
        finally:
            cleanup_errors = cleanup_owned(cluster, owned)
            row.setdefault("cleanup_errors", []).extend(cleanup_errors)
            try:
                after, replicas = owned_inventory(cluster, spec)
                row["after_cleanup"] = after
                row["owned_replicas_remaining"] = [r["id"] for r in replicas if r["id"].startswith(prefix)]
            except Exception as exc:
                row["postflight_error"] = repr(exc)
            save()
            if (row.get("owned_replicas_remaining") or row.get("postflight_error") or cleanup_errors) and sys.exc_info()[0] is None:
                raise ValueError("owned physical replica retirement not confirmed; see saved result")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--mesh-only", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    spec["_source_path"] = str(args.spec.resolve())
    plan = launch_plan(spec)
    out = args.out.resolve()
    if out.parent != RESULTS / "minimal_calibration" or not out.name.startswith("integration_"):
        parser.error("require new minimal_calibration/integration_* result directory")
    out.mkdir(parents=True, exist_ok=False)
    (out / "launch_plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    if args.execute:
        execute(spec, out, mesh_only=args.mesh_only)
    print(json.dumps(dict(out=str(out), executed=args.execute)))


if __name__ == "__main__":
    main()
