"""Bind saved execution, actual runtime XML/logs, observed resources and exact exit."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/layout_validation"
HELPER = ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py"
HELPER_SHA = "d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if digest(HELPER) != HELPER_SHA:
        raise ValueError("frozen local XML/log validation helper changed")
    spec = importlib.util.spec_from_file_location("frozen_path_checks", HELPER)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    if args.out.resolve().parent != RESULTS or not args.out.name.startswith("review_"):
        raise ValueError("require a fresh review output")
    args.out.mkdir(parents=True, exist_ok=False)
    refs = {}
    def register(path):
        path = Path(path).resolve()
        refs[str(path)] = dict(sha256=digest(path), bytes=path.stat().st_size)
        return path
    def read(path):
        return json.loads(register(path).read_text())
    register(Path(__file__))
    register(HELPER)
    plan_path = RESULTS / "operations_plan_r01/launch_plan.json"
    plan = read(plan_path)
    plan_sha = digest(plan_path)
    execution = read(RESULTS / "execution_r01/execution.json")
    prior = read(RESULTS / "review_execution_r01/qualification.json")
    checks = dict(prior["checks"])
    nodes, comm_ids = {}, []
    def check(name, value):
        checks[name] = bool(value)
    controller = read(RESULTS / "operations_control_r01/summary.json")
    outer = read(RESULTS / "operations_run_r01/summary.json")
    check("controller_application_and_workers_rc0", controller.get("application_passed") is True
          and controller.get("application_status") == "passed" and outer["controller_process"]["returncode"] == 0
          and all(w["returncode"] == 0 for w in outer["workers"].values()))
    check("actual_launch_plan_bound", execution["actual_worker_launch_plan_ref"] == dict(path=str(plan_path.resolve()), sha256=plan_sha))
    collection = read(args.collection / "collection_result.json")
    check("recollection_only_no_new_GPU_or_signals", collection.get("GPU_launches") == 0
          and collection.get("signals") == 0 and collection.get("status") == "collection_completed_pending_independent_review"
          and collection["original_outer_summary_ref"]["sha256"] == digest(RESULTS / "operations_run_r01/summary.json"))
    actual_source = register(args.collection / "executed_control.source.py")
    check("executed_control_source_f315_preserved", digest(actual_source) == collection["original_controller_ref"]["sha256"]
          == "f3154366e462c480aa41d513663a8302b18fa83ef13d25e7c871478dcd848ab6")
    old_calibration_refs = read(ROOT / "codex_coding/results/v0.1.23.3/task2/minimal_calibration/review_final_r01/evidence_refs.json")
    old_failure = ROOT / "codex_coding/results/v0.1.23.3/task2/minimal_calibration/review_remote64_r01/summary.json"
    register(old_failure)
    check("historical_remote64_budget_failure_preserved", digest(old_failure) == old_calibration_refs[str(old_failure.relative_to(ROOT))]["sha256"])
    failed_collect = RESULTS / "operations_collect_r01/collect_node28"
    register(failed_collect / "worker.tar")
    check("original_partial_collection_kept", read(failed_collect / "command.json")["returncode"] == 0
          and outer["status"] == "prelaunch" and not (RESULTS / "operations_collect_r01/collect_node30").exists())
    for ref in execution["source_refs"]:
        check("source_SHA/"+Path(ref["path"]).name, digest(register(ref["path"])) == ref["sha256"])
    old_source = read(RESULTS / "review_preflight_r01/evidence_refs.json")
    check("original_worker_controller_unchanged", all(digest(register(ROOT / key)) == old_source[key]["sha256"]
          for key in ("main_lib/fluidckpt/cluster/worker.py", "main_lib/fluidckpt/cluster/control.py")))
    for rank, worker in enumerate(plan["workers"]):
        node = worker["node"]
        raw = args.collection / ("collect_"+node) / "raw"
        register(args.collection / ("collect_"+node) / "worker.tar")
        owned = raw / ("integration_worker_"+node+"_layout_r01")
        owner = read(owned / "worker.owner.json")
        summary = read(owned / "supervisor.summary.json")
        stop = read(owned / "STOP.json")
        exact = read(args.collection / ("exact_owner_"+node) / "stdout.json")
        baseline = read(RESULTS / "operations_baseline_r01" / ("baseline_"+node) / "stdout.json")
        after = read(args.collection / ("after_stop_"+node) / "stdout.json")
        before_meta = read(RESULTS / "operations_run_r01" / ("deployed_"+node) / "stdout.json")
        after_meta = read(args.collection / ("metadata_"+node) / "stdout.json")
        log = register(owned / "worker.log").read_bytes()
        dump = register(raw / "integration_layout_worker_session_r01" / (node+".runtime.dump.xml")).read_bytes()
        input_raw = register(worker["env"]["NCCL_TOPO_FILE"]).read_bytes()
        view, parse = gate.dump_parse_view(dump)
        (args.out / (node+".topology.parse_view.xml")).write_bytes(view)
        (args.out / (node+".topology.parse_view.json")).write_text(json.dumps(parse, indent=2)+"\n")
        communication = gate.log_evidence(log, owner["pid"], rank, worker["env"]["NCCL_TOPO_FILE"],
                                         "cross", 2, 0, worker["bdf"], worker["physical_gpu"], "IBext_v8")
        gpu = gate.dump_check(input_raw, view, worker["bdf"], rank, 0, communication)
        net = gate.net_dump_check(input_raw, view, communication)
        (args.out / (node+".runtime_path.json")).write_text(json.dumps(dict(communication=communication, GPU=gpu, NET=net), indent=2)+"\n")
        check(node+"/actual_IB_GPU_NIC_path", communication["passed"] and gpu["passed"] and net["passed"])
        comm_ids.append(communication["device_binding"].get("comm_id"))
        check(node+"/matching_STOP_owner_exact_exit", stop["plan_sha256"] == summary["plan_sha256"] == owner["plan_sha256"] == plan_sha
              and summary["stop_reason"] == "owned_stop_marker" and summary["cleanup"]["passed"] is True
              and summary["cleanup"]["before"]["pid"] == owner["pid"] and summary["cleanup"]["before"]["start_time"] == owner["start_time"]
              and summary["cleanup"]["after"]["state"] == "absent" and not summary["cleanup"]["pending_group_members"]
              and exact["passed"] is True and exact["owner"] == owner and exact["owner_sha256"] == digest(owned / "worker.owner.json")
              and exact["current"]["state"] == "absent" and not exact["group_members"]
              and exact["boot_id"] == worker["boot_id"] == owner["boot_id"] and exact["pid_namespace"] == owner["pid_namespace"])
        check(node+"/container_identity_preserved", all(body["container"]["id"] == worker["container_id"]
              and body["boot_id"] == worker["boot_id"] and all(body["container_identity_checks"].values()) for body in (baseline, after)))
        check(node+"/old_owners_catalogs_preserved", all(r["owner_matches"] for body in (baseline, after) for r in body["keeper_process_discovery"])
              and {r["endpoint"]:r["catalog_sha256"] for r in baseline["keepers"]} == {r["endpoint"]:r["catalog_sha256"] for r in after["keepers"]})
        check(node+"/quiet_no_OOM", after["container_cgroup"]["files"]["pids.current"] == "1"
              and all(line in after["container_cgroup"]["files"]["memory.events"].splitlines() for line in ("oom 0", "oom_kill 0")))
        parse_hash = lambda body: {l.split(maxsplit=1)[1]:l.split(maxsplit=1)[0] for l in body["hash_stdout"].splitlines()}
        before_hash, after_hash = parse_hash(before_meta), parse_hash(after_meta)
        check(node+"/original_XML_mapping_input_plan_SHA", before_hash == after_hash
              and after_hash.get(str(plan_path.resolve())) == plan_sha
              and after_hash.get(worker["env"]["NCCL_TOPO_FILE"]) == hashlib.sha256(input_raw).hexdigest())
        check(node+"/remote_assets_SHA", baseline["source_assets"] == after["source_assets"]
              and all(r["sha256"] == r["expected_sha256"] for r in after["source_assets"].values()))
        baseline_mem = int(baseline["container_cgroup"]["files"]["memory.current"])
        observed = max(int(s["memory.current"]) for s in summary["cgroup_samples"] if s["memory.current"] is not None)
        increment = observed-baseline_mem
        check(node+"/observed_DRAM_within2GiB", 0 <= increment <= 2 << 30)
        check(node+"/kernel_peak_boundary", summary["physical_peak_verified"] is False
              and all(s.get("memory.peak") is None for s in summary["cgroup_samples"]))
        projected = next(r["projected_dram_increment_bytes"] for r in execution["mesh_resource_budget"]["resources"] if r["worker"] == rank)
        nodes[node] = dict(pid=owner["pid"], start_ticks=owner["start_time"],
            physical_gpu=worker["physical_gpu"], BDF=worker["bdf"], NUMA=worker["numa"],
            boot_id=owner["boot_id"], container_id=worker["container_id"],
            preworker_baseline_bytes=baseline_mem, observed_max_bytes=observed,
            observed_DRAM_increment_bytes=increment, observed_budget_margin_bytes=(2 << 30)-increment,
            gate_projection_including8MiB_margin_bytes=projected, observed_minus_gate_projection_bytes=increment-projected,
            lifetime_samples=len(summary["cgroup_samples"]), sample_period_s=0.25,
            actual_transport="IBext_v8", HCA="shca_0:1", gdr=0, exact_exit="absent_and_group_empty")
    check("same_actual_pair_communicator", len(set(comm_ids)) == 1 and bool(comm_ids[0]))
    result = dict(schema_version="fluidckpt.layout_validation.independent_final.v1", checks=checks,
        independent_checks_passed=all(checks.values()), failures=[k for k,v in checks.items() if not v],
        qualified_scope="one original serial-executor selected pair session; two16MiB HBM blocks; four fixed same-budget ABCD layouts",
        nodes=nodes, layouts=prior["layouts"], time_hypotheses=prior["time_hypotheses"],
        forward_reverse_paths_qualified=all(checks.values()), performance_ready=False,
        formal_capacity_profile_admitted=False, model_weights_ready_measured=False,
        full_physical_peak_verified=False, solver_decision_increment_proven=False,
        runtime_internal_host_page_NUMA="unknown; gdr0/XML supplies no residency proof",
        collection_failure="original parent rc1 after GPU/controller success: local tar.extractall(filter=data) unsupported; fresh collection only, no GPU repeat",
        source_controller_claim="f3154366 frozen actual source retained unchanged; compatibility script used for collection only")
    (args.out / "qualification.json").write_text(json.dumps(result, indent=2)+"\n")
    (args.out / "evidence_refs.json").write_text(json.dumps(refs, indent=2)+"\n")
    print(json.dumps(dict(out=str(args.out), passed=result["independent_checks_passed"], checks=len(checks), failures=result["failures"], nodes=nodes)))
    raise SystemExit(0 if result["independent_checks_passed"] else 1)


if __name__ == "__main__":
    main()
