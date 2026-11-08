"""Analyze existing restore handoff evidence using only the Python standard library."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


CASES = (("C0", "C0_mixed", "phase_calibrate"),
         ("Hnear", "Hnear", "phase_validate"),
         ("Hremote", "Hremote", "phase_validate"))


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_json(value):
    return digest_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False).encode())


def service(model, plan):
    ready = {0: 0.0, 1: 0.0, 2: 0.0}
    for op in plan["ops"]:
        duration = model["point_costs"][op["path"]][str(op["n"])]["service_s"]
        end = max(ready[op["src_w"]], ready[op["dst_w"]]) + duration
        ready[op["src_w"]] = ready[op["dst_w"]] = end
    return max(ready.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--breakdown", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    archive_path = args.run_dir / "collection_node27.tar"
    member_refs, checks = [], []
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if len(members) > 20000 or sum(m.size for m in members) > 512 << 20:
            raise ValueError("archive exceeds evidence bounds")
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or ".." in name.parts or member.name in names:
                raise ValueError("unsafe or duplicate archive member")
            names.add(member.name)
        def read(name, lines=False):
            data = archive.extractfile(name).read()
            member_refs.append({"archive": str(archive_path.resolve()), "member": name,
                                "sha256": digest_bytes(data)})
            return [json.loads(line) for line in data.splitlines()] if lines else json.loads(data)
        model = read("phase_calibrate/model.json")
        checkpoint = read("phase_calibrate/checkpoint_path_model.json")
        checkpoint_ref = dict(member_refs[-1])
        frozen = read("phase_calibrate/frozen_prediction.json")
        bootstrap = read("phase_bootstrap/bootstrap.json")
        breakdown = json.loads(args.breakdown.read_bytes())
        check("frozen_payload_digest", digest_json(frozen["payload"]) == frozen["payload_sha256"])
        check("frozen_model_digest", digest_json(model) == frozen["payload"]["model_sha256"])
        check("registered_exact_extent_table", model["schema"] == "simple_exact_extent_model_v1")
        check("pre_outer_checkpoint_exact_points_unchanged", checkpoint["point_costs"] == model["point_costs"])
        check("pre_outer_checkpoint_excludes_C0_and_holdouts", checkpoint["online_outer_s"] is None and
              "outer_calibration_service" not in checkpoint and len(checkpoint["source_refs"]) == 18 and
              all("path_" in Path(ref["path"]).name for ref in checkpoint["source_refs"]))
        results = {}
        for label, case, phase in CASES:
            record = read(f"{phase}/{case}.json")
            raw = read(f"{phase}/{case}_raw.json")
            check(label + "_inclusive_clock_consistency", all(record[k] == raw[k] for k in
                  ("load_begin_ns", "all_weights_ready_ns", "load_begin_wall_ns", "clock_domain")))
            ranks, events = {}, {}
            for rank in (0, 1):
                rows = read(f"metrics/{record['session']}_{case}/rank{rank}.jsonl", lines=True)
                selected = [r for r in rows if r["event"] in (
                    "load_begin", "model_layout_end", "target_exported", "target_accepted",
                    "target_mapped", "target_execute_begin", "target_copy_complete", "weights_ready")]
                if len(selected) != 8 or len({r["event"] for r in selected}) != 8:
                    raise ValueError("missing or duplicate lifecycle landmarks")
                e = {r["event"]: r for r in selected}
                events[rank] = e
                check(label + f"_rank{rank}_one_target_host", len({r["host"] for r in selected}) == 1)
                def duration(begin, end):
                    return (e[end]["monotonic_ns"] - e[begin]["monotonic_ns"]) / 1e9
                ranks[rank] = {
                    "engine_pid": e["load_begin"]["pid"],
                    "keeper_pid": e["target_mapped"]["pid"],
                    "load_to_layout_end_s": duration("load_begin", "model_layout_end"),
                    "layout_end_to_export_s": duration("model_layout_end", "target_exported"),
                    "load_to_export_s": duration("load_begin", "target_exported"),
                    "export_to_accepted_s": duration("target_exported", "target_accepted"),
                    "accepted_to_mapped_s": duration("target_accepted", "target_mapped"),
                    "mapped_to_execute_s": duration("target_mapped", "target_execute_begin"),
                    "copy_complete_to_weights_ready_s": duration("target_copy_complete", "weights_ready")}
            stamps = [raw["load_begin_ns"],
                      min(e["load_begin"]["monotonic_ns"] for e in events.values()),
                      max(e["load_begin"]["monotonic_ns"] for e in events.values()),
                      max(e["target_exported"]["monotonic_ns"] for e in events.values()),
                      max(e["target_mapped"]["monotonic_ns"] for e in events.values()),
                      min(e["target_execute_begin"]["monotonic_ns"] for e in events.values()),
                      max(e["target_copy_complete"]["monotonic_ns"] for e in events.values()),
                      raw["all_weights_ready_ns"]]
            keys = ("request_to_first_rank", "rank_arrival_skew", "last_rank_to_last_export",
                    "last_export_to_last_map", "last_map_to_first_execute", "execute_span", "last_copy_to_ACK")
            partition = {k: (b-a)/1e9 for k, a, b in zip(keys, stamps, stamps[1:])}
            check(label + "_same_host_monotonic_partition", all(b >= a for a, b in zip(stamps, stamps[1:])) and
                  len({r["host"] for e in events.values() for r in e.values()}) == 1)
            online = (raw["all_weights_ready_ns"] - raw["load_begin_ns"])/1e9
            check(label + "_partition_reconstructs_metric", abs(sum(partition.values()) - online) < 1e-9)
            check(label + "_prior_review_metric_matches", abs(breakdown["cases"][case]["actual_online_s"] - online) < 1e-9)
            dag = service(model, record["plan"])
            max_worker = max(w["execute_s"] for w in raw["flow"]["execution"])
            check(label + "_full_real_weights_66_extents", len(record["plan"]["ops"]) == 66 and
                  sum(op["n"] for op in record["plan"]["ops"]) == 70329718112)
            check(label + "_three_correctness_checks", record["verification"]["passed"] and
                  record["verification"]["checked_bytes"] == 70329718112 and record["exact_reference_output_passed"] and
                  record["reply"]["text"] == bootstrap["reference"]["text"] and record["physical_park"]["passed"])
            results[label] = {"actual_online_s": online, "predicted_DAG_s": dag,
                "actual_max_worker_s": max_worker, "worker_signed_error_s": dag-max_worker,
                "actual_minus_max_worker_s": online-max_worker,
                "calibration_residual_s": online-dag,
                "same_host_partition_s": partition, "rank_windows_s": ranks,
                "controller_clock_domain": raw["flow"]["controller_timing"]["clock_domain"],
                "prepare_rpc_s": raw["flow"]["prepare_rpc_s"],
                "execute_wall_s": raw["flow"]["execute_wall_s"],
                "publish_s": raw["flow"]["verify_and_publish_s"]}
        check("C0_exact_residual_matches_frozen", abs(results["C0"]["calibration_residual_s"] - model["online_outer_s"]) < 1e-9)
    policy = {
        "schema": "outer_state_validation_proposal_v1", "development": "v0.1.23.3/task2",
        "goal": "paused_model_weights_ready", "new_GPU_samples_in_this_analysis": 0,
        "future_full_restore_count": 4, "repeats_per_case": 1,
        "future_order": ["C0_prime", "C0_outer_calibration", "freeze_both_holdouts", "Hremote", "Hnear"],
        "calibration_policy": "online_outer_s = C0_outer_calibration.actual_online_s - fixed_table_DAG(C0)",
        "path_table_source": {"archive": str(archive_path.resolve()), "member": "phase_calibrate/checkpoint_path_model.json",
                              "checkpoint_file_sha256": checkpoint_ref["sha256"],
                              "checkpoint_model_sha256": digest_json(checkpoint),
                              "point_costs_sha256": digest_json(checkpoint["point_costs"]),
                              "source_refs_preserved": checkpoint["source_refs"],
                              "bundle_files": 19, "raw_path_files": 18,
                              "historical_outer_and_holdout_files_in_bundle": 0},
        "transfer_hypothesis": "r02 exact path/size service costs transfer to a fresh same-hardware native incarnation",
        "prediction_scope": "subsequent full restores in the same primed engine/keeper/controller incarnation",
        "first_restore": "measure C0_prime inclusive cold latency and correctness separately; no holdout qualification claim",
        "controller_policy": "one controller process executes prime, C0 calibration, freeze, Hremote, Hnear",
        "metric": "controller begins before necessary inventory/plan; ends after both ranks weights_ready and full resume ACK",
        "checks_each_restore": ["all 70329718112 bytes SHA after ready", "exact reference output", "physical park both ranks >=90% allocated drop and card free gain"],
        "unchanged_gates": ["fresh admission", "source lineage NUMA epoch", "ready and parked resource gates", "leases transactions cleanup", "each holdout absolute relative error <=10%", "exact owned closure"],
        "prohibited_fit": ["holdout residual averaging", "per-layout outer constant", "future measured windows as prediction input", "new topology or bandwidth calibration", "fake or zero weights"],
        "stop_policy": "failure persists as raw evidence; no extra default repetition, fifth full restore or retuning"
    }
    def write(name, value):
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    provenance = {"source_archive": {"path": str(archive_path.resolve()), "sha256": digest_bytes(archive_path.read_bytes())},
                  "breakdown": {"path": str(args.breakdown.resolve()), "sha256": digest_bytes(args.breakdown.read_bytes())},
                  "members": member_refs, "script_sha256": digest_bytes(Path(__file__).read_bytes())}
    write("analysis.json", {"cases": results, "new_GPU_samples": 0, "model_retuned": False,
                           "interpretation": "Partitions add; per-rank windows overlap and must not be summed as critical-path causes."})
    write("measurement_proposal.json", policy)
    write("provenance.json", provenance)
    write("qualification.json", {"checks": checks, "passed": all(c["passed"] for c in checks),
                                 "analysis_only": True, "new_model_qualified": False})
    lines = ["# 已有两节点恢复外层开销分析", "", "仅分析 r02 原件；未启动 GPU、SSH、Docker，未改预测或历史结果。", "",
             "| 窗口（ms） | C0 首次恢复 | Hnear 后续 | Hremote 后续 |", "| --- | ---: | ---: | ---: |"]
    for k, title in (("request_to_first_rank", "请求→首 rank"), ("rank_arrival_skew", "rank 到达差"),
                     ("last_rank_to_last_export", "末 rank→末导出"), ("last_export_to_last_map", "末导出→末映射"),
                     ("last_map_to_first_execute", "末映射→首复制"), ("last_copy_to_ACK", "末复制→ACK")):
        lines.append("| " + title + " | " + " | ".join(f"{results[c]['same_host_partition_s'][k]*1000:.3f}" for c in results) + " |")
    lines.extend(["", "以上互不重叠窗口与复制跨度合起来精确重建在线主指标。逐 rank 窗口相互重叠，只用于定位，不额外相加。", "",
        "两 rank 的 load_begin→model_layout_end 为196.29–205.13ms，后续导出窗口39.73–43.24ms，总 load→export 为236.02–248.37ms；当前证据中基本稳定。前者包含布局/materialize相关工作，后者包含IPC句柄导出、同步与文件发布；已有事件不能继续拆出各自纯开销。", "",
        "C0 请求到首 rank、rank 到达差明显更大。Hnear keeper accepted→mapped 为147.21/164.45ms，C0为62.26/64.60ms，Hremote为60.32/61.72ms；prepare RPC壁钟相应为82.43/182.84/79.55ms。后续两次之间仍有约100ms映射差异，外层不是已识别的固定首调用常数。", "",
        "C0 calibrate controller为PID7613，Hnear/Hremote validate controller为PID9259；引擎rank PID259/260、keeper PID46/47相同。因此恢复序号、布局、时间/调度、controller进程同时变化；首次恢复状态迁移是强假设，尚未证明唯一首因。", "",
        "Hnear/Hremote的搬运DAG高估9.992/7.472ms，C0共同外层高估274.773/389.295ms；后者解释约96.49%/98.12%的带符号总误差。保留原Hnear10%失败与整体未认证结论。", "",
        "建议下一新会话只做四次完整恢复，每次一次：完整C0 prime→相同C0外层校准→同时冻结两留出→Hremote→Hnear。顺序反转用于打破旧Hnear第二次/Hremote第三次的固定关联；单会话不能独立证明顺序因果。默认不追加第二个warm C0或第五次恢复。", "",
        "六路径×两实际尺寸成本表直接复用r02冻结原件，来源版本/raw引用不变；新incarnation与旧成本表的迁移是事前声明的待验证假设。新source/epoch身份另核，不冒充同一incarnation或重写原校准来源。预测只替换一次新warm C0精确残差，保留原DAG；不平均留出、逐layout拟常数或把实测mapping窗口当oracle预测输入。", "",
        "prime仍是70,329,718,112B完整真实恢复，记录首次冷恢复主指标，SHA/固定输出/真实物理park三项全查。若prime单列setup成本，模型认证范围必须明确为同一已prime incarnation的后续恢复；首次恢复成本不被隐藏，也不因prime移出留出而声称冷启动准确率通过。", "",
        "prime、校准、freeze、两留出由一个controller进程贯穿；每次主时钟仍在必要inventory/plan之前开始，含materialize、IPC、GPU同步、TP barrier并在双rank weights_ready和完整resume ACK后结束。SHA和固定输出仍在主窗后，物理park和资源/lineage/NUMA/epoch/cleanup门保持。", "",
        "留出逐案10%门，任一失败保留原件并停止，不回填或继续试到通过。此分析不认证新成本模型。完整提案及冻结表来源见measurement_proposal.json；逐rank原始窗口与来源SHA见analysis.json/provenance.json。"])
    (args.out / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"passed": all(c["passed"] for c in checks), "checks": len(checks), "out": str(args.out)}, ensure_ascii=False))
    return 0 if all(c["passed"] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
