"""Audit collected outer requests offline without changing any original evidence."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from outer_analysis import digest_json, service


ORDER = ["C0_prime", "C0_cal", "Hremote", "Hnear"]
DOMAIN = "post_one_fullrestore_samecontroller"
RANK_BYTES = 35164859056


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            result.update(chunk)
    return result.hexdigest()


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        with tarfile.open(self.path) as archive:
            members = archive.getmembers()
            if len(members) > 20000 or sum(m.size for m in members) > 512 << 20:
                raise ValueError("archive exceeds registered evidence bounds")
            for member in members:
                name = PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or ".." in name.parts
                        or member.name in self.data):
                    raise ValueError("unsafe or duplicate archive member")
                self.data[member.name] = archive.extractfile(member).read()

    def json(self, name):
        return json.loads(self.data[name])

    def ref(self):
        return {"path": str(self.path.resolve()), "sha256": file_digest(self.path)}


def sender_serial(raw, plan):
    remote = [op["op_id"] for op in plan["ops"] if op["src_w"] == 2]
    if not remote:
        return True
    rows = [w for w in raw["flow"]["execution"] if w["worker"] == 2]
    if len(rows) != 1:
        return False
    begins, intervals = {}, []
    for e in rows[0]["op_clock_events"]:
        if e["op_id"] not in remote:
            continue
        if e["ev"] == "cluster_op_begin":
            if e["op_id"] in begins:
                return False
            begins[e["op_id"]] = e
        elif e["ev"] == "cluster_op_end":
            begin = begins.get(e["op_id"])
            if begin is None or begin["clock_domain"] != e["clock_domain"]:
                return False
            intervals.append((e["op_id"], begin["monotonic_ns"], e["monotonic_ns"], e["clock_domain"]))
    return ([i[0] for i in intervals] == remote and len({i[3] for i in intervals}) == 1
            and all(i[1] < i[2] for i in intervals)
            and all(a[2] <= b[1] for a, b in zip(intervals, intervals[1:])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--old-run-dir", type=Path, required=True)
    parser.add_argument("--old-breakdown", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    runtime = Path(plan["handoff"]).parent
    runtime_scope = {"run_id":plan["run_id"], "nodes":{
        node:{key:body[key] for key in ("container_id", "container_started_at", "boot_id")}
        for node,body in plan["nodes"].items()}}
    archives = {node: Archive(args.run_dir / ("collection_" + node + ".tar")) for node in plan["nodes"]}
    required = ["phase_sequence/" + name for name in (
        "outer_registration.json", "model.json", "frozen_prediction.json", "protocol.json",
        "independent_validation.json", "sequence.json")]
    required += ["phase_sequence/" + name + suffix for name in ORDER for suffix in ("_record.json", "_raw.json")]
    missing = [name for name in required if name not in archives["node27"].data]
    if missing:
        available = {}
        for name in ORDER:
            member = "phase_sequence/" + name + "_record.json"
            if member in archives["node27"].data:
                record = archives["node27"].json(member)
                available[name] = {"actual_online_ns":record["all_weights_ready_ns"]-record["load_begin_ns"],
                                   "controller_pid":record["controller_pid"], "complete_request_index":record["complete_request_index"]}
        failures = {name: archives["node27"].json(name) for name in archives["node27"].data
                    if name.endswith("failure.json") or name.endswith("deadline.json")}
        result = {"passed":False, "functional_and_protocol_passed":False,
                  "all_scenario_error_gates_passed":None, "actual_model_online_qualified":False,
                  "cold_first_restore_prediction_qualified":None, "outer_domain":DOMAIN,
                  "runtime_scope":runtime_scope,
                  "missing_complete_protocol_artifacts":missing, "available_complete_requests":available,
                  "failures":failures, "archive_refs":[a.ref() for a in archives.values()],
                  "reviewer_GPU_SSH_Docker_actions":0}
        (args.out/"qualification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
        (args.out/"report.md").write_text("# 新 outer 校准实测独审\n\n采集原件未构成完整四请求协议，无法认证两留出整体10%。已有完成请求和原始failure/deadline分别保留于qualification.json；未将未执行请求记为0时延。\n")
        print(json.dumps(result,ensure_ascii=False))
        return
    old = Archive(args.old_run_dir / "collection_node27.tar")
    old_runtime = runtime.parent / "execution_runtime_online_simple_two_node_r02"
    old_checkpoint = old.json("phase_calibrate/checkpoint_path_model.json")
    old_breakdown = json.loads(args.old_breakdown.read_text())
    checks, cases, records = [], {}, {}
    def check(name, passed, category="protocol", detail=None):
        checks.append({"name": name, "passed": bool(passed), "category": category, "detail": detail})
    def raw_ref(ref):
        path = Path(ref["path"])
        for base, source in ((runtime, archives["node27"]), (old_runtime, old)):
            try:
                name = str(path.relative_to(base))
            except ValueError:
                continue
            return source.data.get(name)
        return path.read_bytes() if path.is_file() else None
    def ref_passed(ref):
        raw = raw_ref(ref)
        return raw is not None and hashlib.sha256(raw).hexdigest() == ref["sha256"]
    def load(name):
        return archives["node27"].json(name)
    registration = load("phase_sequence/outer_registration.json")
    model = load("phase_sequence/model.json")
    frozen = load("phase_sequence/frozen_prediction.json")
    protocol = load("phase_sequence/protocol.json")
    validation = load("phase_sequence/independent_validation.json")
    sequence = load("phase_sequence/sequence.json")
    bootstrap = load("phase_bootstrap/bootstrap.json")
    check("finite_same_controller_postprime_protocol", registration["outer_domain"] == DOMAIN and
          registration["complete_request_order"] == ORDER and registration["validation_order"] == ORDER[2:] and
          registration["max_complete_requests"] == 4 and sequence["complete_request_order"] == ORDER and
          sequence["complete_requests"] == 4 and sequence["controller_pid"] == registration["controller_pid"])
    check("original_preouter_checkpoint_SHA", ref_passed(registration["source_checkpoint_path_model_ref"]) and
          old_checkpoint["online_outer_s"] is None and len(old_checkpoint["source_refs"]) == 18)
    lineage = model["path_cost_inheritance"]
    check("all_twelve_points_completely_unchanged", model["point_costs"] == old_checkpoint["point_costs"] and
          lineage["point_costs_sha256"] == digest_json(old_checkpoint["point_costs"]) and
          sum(len(points) for points in model["point_costs"].values()) == 12)
    check("historical_raw_context_sessions_preserved", lineage["source_context"] == old_checkpoint["context"] and
          lineage["source_raw_refs"] == old_checkpoint["source_refs"] and
          lineage["source_calibration_sessions"] == old_checkpoint["calibration_sessions"] and
          all(ref_passed(ref) for ref in lineage["source_raw_refs"]))
    check("current_physical_scope_equals_original_except_clock", {k: v for k, v in model["context"].items() if k != "clock_domain"} ==
          {k: v for k, v in old_checkpoint["context"].items() if k != "clock_domain"} and
          model["context"]["clock_domain"] != old_checkpoint["context"]["clock_domain"])
    check("new_model_and_all_references_immutable", digest_json(model) == frozen["payload"]["model_sha256"] and
          all(ref_passed(ref) for ref in model["source_refs"]))
    check("frozen_both_predictions_payload_and_sources", digest_json(frozen["payload"]) == frozen["payload_sha256"] and
          set(frozen["payload"]["predictions"]) == set(ORDER[2:]) and
          all(ref_passed(ref) for ref in frozen["payload"]["calibration_refs"]) and
          ref_passed(frozen["payload"]["protocol_ref"]))
    check("bootstrap_full_capture_and_physical_park", bootstrap["status"] == "passed" and
          bootstrap["physical_park_passed"] and [m["n"] for m in bootstrap["manifests"]] == [RANK_BYTES]*2 and
          all(row[0]["verification"]["passed"] and row[0]["verification"]["checked_bytes"] == RANK_BYTES for row in bootstrap["capture"]))
    remote = [r for r in bootstrap["replicas"] if r["worker"] == 2]
    helper = next(w for w in bootstrap["workers"] if w["worker"] == 2)
    allocations = {a.get("id"): a for a in helper["host_allocations"] if a.get("id")}
    check("helper_two_complete_NUMA4_7_touched_registered", sorted((r["tp"], r["numa"], r["n"], r["tier"], r["state"]) for r in remote) ==
          [(0,4,RANK_BYTES,"dram","VALID"), (1,7,RANK_BYTES,"dram","VALID")] and all(
              allocations[r["id"]]["registered"] and allocations[r["id"]]["sampled_page_distribution"] == {str(r["numa"]):1.0} for r in remote))
    engine_identity, incarnation = None, None
    previous = None
    for index, name in enumerate(ORDER, 1):
        record = load("phase_sequence/" + name + "_record.json")
        raw = load("phase_sequence/" + name + "_raw.json")
        records[name] = record
        actual_ns = record["all_weights_ready_ns"] - record["load_begin_ns"]
        check(name + "_same_controller_sequence_raw_clock", record["controller_pid"] == registration["controller_pid"] and
              record["complete_request_index"] == index and record["prior_complete_restores"] == index-1 and
              record["state"]["prior_complete_restores"] == index-1 and record["session"] == plan["run_id"] and
              record["clock_domain"] == registration["controller_clock_domain"] and
              all(raw[k] == record[k] for k in ("load_begin_ns", "all_weights_ready_ns", "load_begin_wall_ns", "clock_domain")) and
              record["online_weights_ready_s"] == actual_ns/1e9 and all(ref_passed(ref) for ref in record["source_refs"]))
        check(name + "_registered_new_request", record["load_begin_wall_ns"] > registration["registered_wall_ns"] and
              record["evidence_kind"] == "actual_model_online" and record["outer_domain"] == DOMAIN)
        if previous is not None:
            check(name + "_after_previous_full_verify_park", previous["completed_wall_ns"] < record["load_begin_wall_ns"] and
                  previous["verification_end_ns"] < record["load_begin_ns"])
        previous = record
        check(name + "_70329718112B_66real_extents", sum(o["n"] for o in record["plan"]["ops"]) == 2*RANK_BYTES and
              len(record["plan"]["ops"]) == 66 and sum(t["n"] for t in record["plan"]["targets"]) == 2*RANK_BYTES)
        check(name + "_postready_fullSHA_golden", record["verification_begin_ns"] >= record["all_weights_ready_ns"] and
              record["verification"]["passed"] and record["verification"]["checked_bytes"] == 2*RANK_BYTES and
              len(record["verification"]["operations"]) == 66 and all(v["source_sha256"] == v["target_sha256"] and v["source_sha256"] for v in record["verification"]["operations"].values()) and
              record["exact_reference_output_passed"] and record["reply"]["text"] == bootstrap["reference"]["text"])
        check(name + "_physical_park_both90percent", record["physical_park"]["passed"] and
              len(record["physical_park"]["rows"]) == 2 and all(
                  r["weight_bytes"] == RANK_BYTES and 10*r["engine_allocated_drop_bytes"] >= 9*r["weight_bytes"] and
                  10*r["physical_free_gain_bytes"] >= 9*r["weight_bytes"] for r in record["physical_park"]["rows"]))
        check(name + "_live_parked_resource_and_cleanup", record["ready_resource_gate"]["passed"] and
              record["resource_gate"]["passed"] and record["cleanup_passed"] and record["engine_parked"])
        worker_epochs = {w["worker"]: w["epoch"] for w in record["workers"]}
        sources = record["state"]["source_lineage"]
        check(name + "_current_source_lineage_epoch_NUMA", all(
            op["replica_id"] in sources and sources[op["replica_id"]]["source_epoch"] == op["source_epoch"] == worker_epochs[op["src_w"]] and
            sources[op["replica_id"]]["worker"] == op["src_w"] and
            all(op.get(k) == sources[op["replica_id"]].get(k) for k in ("identity", "generation")) and
            sources[op["replica_id"]]["numa"] == model["context"]["path_context"][op["path"]]["source_numa"]
            for op in record["plan"]["ops"]))
        check(name + "_single_sender_actual_order", sender_serial(raw, record["plan"]))
        ranks = raw["rank_timing"]["ranks"]
        events, identity, rank_windows = {}, {}, {}
        for rank, timing in ranks.items():
            e = {row["event"]: row for row in timing["events"]}
            events[rank] = e
            identity[rank] = {"host": e["load_begin"]["host"], "pid": e["load_begin"]["pid"]}
            def duration(begin, end):
                return (e[end]["monotonic_ns"]-e[begin]["monotonic_ns"])/1e9
            rank_windows[rank] = {"load_to_layout_end": duration("load_begin", "model_layout_end"),
                "layout_end_to_export": duration("model_layout_end", "target_exported"),
                "load_to_export": duration("load_begin", "target_exported"),
                "export_to_accepted": duration("target_exported", "target_accepted"),
                "accepted_to_mapped": duration("target_accepted", "target_mapped")}
        check(name + "_engine_rank_identity_ready_within_ACK", set(ranks) == {"0","1"} and
              len({t["host"] for t in ranks.values()}) == 1 and all(
                record["load_begin_ns"] <= t["begin_ns"] == events[r]["load_begin"]["monotonic_ns"] <
                t["end_ns"] == events[r]["weights_ready"]["monotonic_ns"] <= record["all_weights_ready_ns"] and
                events[r]["load_begin"]["pid"] == events[r]["weights_ready"]["pid"] for r,t in ranks.items()))
        if engine_identity is None:
            engine_identity, incarnation = identity, record["incarnation_sha256"]
        check(name + "_same_prime_engine_and_worker", identity == engine_identity and record["incarnation_sha256"] == incarnation and
              digest_json(record["state"]["incarnations"]) == incarnation)
        times = [raw["load_begin_ns"], min(e["load_begin"]["monotonic_ns"] for e in events.values()),
                 max(e["load_begin"]["monotonic_ns"] for e in events.values()), max(e["target_exported"]["monotonic_ns"] for e in events.values()),
                 max(e["target_mapped"]["monotonic_ns"] for e in events.values()), min(e["target_execute_begin"]["monotonic_ns"] for e in events.values()),
                 max(e["target_copy_complete"]["monotonic_ns"] for e in events.values()), raw["all_weights_ready_ns"]]
        labels = ["request_to_first_rank","rank_arrival_skew","last_rank_to_last_export","last_export_to_last_map","last_map_to_first_execute","execute_span","last_copy_to_ACK"]
        partition = {label:(b-a)/1e9 for label,a,b in zip(labels,times,times[1:])}
        check(name + "_samehost_partition_reconstructs_online", all(a<=b for a,b in zip(times,times[1:])) and abs(sum(partition.values())-actual_ns/1e9)<1e-9)
        dag = service(model, record["plan"])
        max_worker = max(w["execute_s"] for w in raw["flow"]["execution"])
        values = {"actual_online_ns":actual_ns, "actual_online_s":actual_ns/1e9,
                  "predicted_worker_DAG_s":dag, "actual_max_worker_s":max_worker,
                  "actual_online_minus_max_worker_s":actual_ns/1e9-max_worker,
                  "online_minus_DAG_s":actual_ns/1e9-dag,
                  "samehost_partition_s":partition, "rank_windows_s":rank_windows,
                  "controller_pid":record["controller_pid"], "engine_identity":identity,
                  "request_index":index, "prepare_rpc_s":raw["flow"]["prepare_rpc_s"]}
        if name in ORDER[2:]:
            predicted = frozen["payload"]["predictions"][name]
            error = predicted["estimated_online_ns"]-actual_ns
            passed = 10*abs(error) <= actual_ns
            values.update(predicted_online_ns=predicted["estimated_online_ns"], signed_error_ns=error,
                          relative_error=error/actual_ns, ten_percent_passed=passed,
                          DAG_signed_error_s=dag-max_worker,
                          outer_signed_error_s=model["online_outer_s"]-(actual_ns/1e9-max_worker))
            check(name + "_frozen_before_and_exact_plan", record["load_begin_wall_ns"] > frozen["payload"]["freeze_wall_ns"] and
                  digest_json(record["plan"]) == predicted["plan_sha256"] and
                  round((dag+model["online_outer_s"])*1e9) == predicted["estimated_online_ns"])
            check(name + "_whole_online_10percent", passed, "accuracy")
        cases[name] = values
    current = model["current_outer_calibration"]
    check("exactly_one_C0_cal_residual_prime_excluded", abs(model["online_outer_s"]-cases["C0_cal"]["online_minus_DAG_s"]) < 1e-12 and
          current["prime_used_for_fitting"] is False and current["prime_online_ns"] == cases["C0_prime"]["actual_online_ns"] and
          current["calibration_online_ns"] == cases["C0_cal"]["actual_online_ns"] and
          current["engine_incarnations"] == engine_identity and
          Path(current["calibration_ref"]["path"]).name == "C0_cal_record.json")
    check("freeze_after_complete_cal_before_all_holdouts", records["C0_cal"]["completed_wall_ns"] < frozen["payload"]["freeze_wall_ns"] <
          records["Hremote"]["load_begin_wall_ns"] < records["Hnear"]["load_begin_wall_ns"] and
          frozen["payload"]["validation_order"] == ORDER[2:])
    check("no_new_path_samples_or_fifth_restore", validation["current_path_calibration_samples"] == 0 and
          validation["complete_requests"] == 4 and not any("path_cal_" in n or "path_warmup_" in n for n in archives["node27"].data))
    check("postprime_domain_does_not_certify_cold", model["outer_domain"] == validation["outer_domain"] == frozen["payload"]["outer_domain"] == DOMAIN and
          records["C0_prime"]["request_role"] == "prime" and validation["prime_used_for_fitting"] is False)
    guard = [json.loads(line) for line in archives["node30"].data["helper_memory_guard.jsonl"].splitlines()]
    check("helper_sampled_free_reserved_bounds_no_peak_claim", bool(guard) and all(
        s["free_bytes"] >= 2 << 30 and s["reserved_bytes"] <= 2 << 30 and s["interval_s"] == .5 and not s["peak_verified"] for s in guard))
    check("no_helper_ALARM", not any("ALARM" in n for n in archives["node30"].data))
    cleanup = load("phase_cleanup/cleanup.json")
    check("Catalog_final_empty_transactions_and_task_allocations", cleanup["passed"] and all(
        not any(w["used_bytes"].values()) and not w["transactions"] for w in cleanup["workers"]))
    for process in plan["processes"]:
        summary = archives[process["node"]].json(Path(process["out"]).name+"/supervisor.summary.json")
        check(process["key"]+"_final_exact_owned_closure", summary["passed"] and summary["final_owned_closure"]["passed"])
    host = json.loads((args.run_dir/"run_summary.json").read_text())
    collection = json.loads((args.run_dir/"collection_qualification.json").read_text())
    check("host_complete_and_both_nodes_quiet", host["passed"] and host["failure"] is None and host["cleanup_failure"] is None and
          collection["passed"] and all(v["pids_current"] == "1" and v["quiet"] for v in collection["nodes"].values()))
    functional = all(c["passed"] for c in checks if c["category"] != "accuracy")
    accurate = all(c["passed"] for c in checks if c["category"] == "accuracy")
    check("reported_numerical_qualification_matches_independent", validation["all_scenario_error_gates_passed"] == accurate and
          validation["actual_model_online_qualified"] == accurate)
    functional = functional and checks[-1]["passed"]
    result = {"passed":functional and accurate, "functional_and_protocol_passed":functional,
              "all_scenario_error_gates_passed":accurate, "actual_model_online_qualified":functional and accurate,
              "cold_first_restore_prediction_qualified":None, "outer_domain":DOMAIN, "runtime_scope":runtime_scope, "checks":checks,
              "measurements":cases, "online_outer_s":model["online_outer_s"],
              "helper_guard":{"samples":len(guard), "minimum_sampled_free_bytes":min(s["free_bytes"] for s in guard),
                "maximum_sampled_reserved_bytes":max(s["reserved_bytes"] for s in guard), "continuous_peak_verified":False},
              "archive_refs":[*(a.ref() for a in archives.values()),old.ref()],
              "old_breakdown_ref":{"path":str(args.old_breakdown.resolve()), "sha256":file_digest(args.old_breakdown)},
              "bound_plan_ref":{"path":str(args.plan.resolve()), "sha256":file_digest(args.plan)},
              "script_ref":{"path":str(Path(__file__).resolve()),"sha256":file_digest(__file__)},
              "model_retuned":False, "reviewer_GPU_SSH_Docker_actions":0, "exact_full_task_HBM_bytes":None}
    (args.out/"qualification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    comparison = {"old_outer_s":old_breakdown["cases"]["C0_mixed"]["C0_frozen_outer_s"],
                  "new_outer_s":model["online_outer_s"], "new_cases":cases,
                  "old_cases":old_breakdown["cases"],
                  "clock_method":"Compare durations from each same-node27 monotonic partition; no subtraction of clocks between runs or hosts.",
                  "cold_and_postprime_separate":True, "causal_first_call_claim":False,
                  "new_runtime_scope":runtime_scope,
                  "PID_interpretation":"PIDs establish continuity only within the new container/run; identical historical container PIDs do not imply reused processes."}
    (args.out/"breakdown.json").write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+"\n")
    lines = ["# 新 outer 校准实测独审", "", f"功能与协议：{'通过' if functional else '失败'}；两留出整体10%：{'全部通过' if accurate else '未全通过'}。未改模型或原件。", "",
             "| 请求 | 完整在线时间（s） | 冻结预测（s） | 相对误差 | 用途 |", "| --- | ---: | ---: | ---: | --- |"]
    for name,v in cases.items():
        estimate = f"{v['predicted_online_ns']/1e9:.9f}" if "predicted_online_ns" in v else "—"
        error = f"{v['relative_error']*100:+.3f}%" if "relative_error" in v else "—"
        role = "首次冷恢复，独立记录" if name == "C0_prime" else "唯一outer校准" if name == "C0_cal" else "留出"
        lines.append(f"| {name} | {v['actual_online_s']:.9f} | {estimate} | {error} | {role} |")
    lines += ["", f"旧outer={comparison['old_outer_s']:.9f}s；新outer={model['online_outer_s']:.9f}s。新参数只由一次C0_cal完整在线减固定原DAG得到。C0_prime时间和三项正确性单列，首次冷恢复预测资格仍未知；模型域仅为同一prime后的controller/engine/keeper incarnation。", "",
              "| 同node27窗口（ms） | 旧C0 | 新prime | 新cal | 新Hremote | 新Hnear |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    old_partition = old_breakdown["cases"]["C0_mixed"]["same_target_host_clock_partition_s"]
    for key,oldkey in [("request_to_first_rank","request__to__first_rank_load_begin"),("rank_arrival_skew","first_rank_load_begin__to__last_rank_load_begin"),("last_rank_to_last_export","last_rank_load_begin__to__last_target_exported"),("last_export_to_last_map","last_target_exported__to__last_target_mapped"),("last_map_to_first_execute","last_target_mapped__to__first_target_execute_begin"),("last_copy_to_ACK","last_target_copy_complete__to__ACK")]:
        lines.append("| "+key+" | "+f"{old_partition[oldkey]*1000:.3f}"+" | "+" | ".join(f"{cases[n]['samehost_partition_s'][key]*1000:.3f}" for n in ORDER)+" |")
    lines += ["", "窗口均由各自同node27单调时钟重建，比较时只比较时长，不跨主机或跨run相减时间戳。每rank内部窗口相互重叠，不能另求和当作唯一因果。完整窗口、搬运DAG及剩余误差见breakdown.json。", "",
              f"所有四次各恢复70,329,718,112B/66extent，完整SHA/固定输出/真实物理park独立核验；同controller PID={registration['controller_pid']}。helper共{len(guard)}个0.5s配置采样，最低free={min(s['free_bytes'] for s in guard)}B，最高reserved={max(s['reserved_bytes'] for s in guard)}B；连续峰值及完整driver/RCCL任务归属未知。精确源/NUMA/epoch、sender串行与最终Catalog/owned closure/两节点quiet详见qualification.json。", "",
              "新结论仅覆盖预登记两节点单sender后续恢复域；首恢复、复杂拓扑与规划器收益资格分别报告。复用旧路径成本作为跨incarnation迁移假设接受实测留出检验，未重测路径/拓扑或使用留出拟合。"]
    lines += ["", f"PID连续性仅在本次{plan['run_id']}及node27容器{plan['nodes']['node27']['container_id']}内成立；与历史容器相同的数字PID不代表复用旧进程。容器StartedAt/boot与bound plan SHA单列于qualification.json。"]
    (args.out/"report.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"passed":result["passed"],"functional_and_protocol_passed":functional,"check_count":len(checks),
                      "failed":[c["name"] for c in checks if not c["passed"]], "measurements":cases},ensure_ascii=False))


if __name__ == "__main__":
    main()
