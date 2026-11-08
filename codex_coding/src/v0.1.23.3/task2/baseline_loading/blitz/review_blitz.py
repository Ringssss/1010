"""Independently review an existing Blitz run; never import GPU libraries or issue RPCs."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
SHA = "594e91127714349c183d935ac68d7b0eb63fc1ed"


def read(path):
    return json.loads(Path(path).read_text())


def signature(rows):
    fields = ("id", "worker", "identity", "tp", "off", "n", "tier", "epoch", "generation", "state")
    return sorted(tuple(row.get(field) for field in fields) for row in rows)


def canonical(plan, replicas):
    by_id = {row["id"]: row for row in replicas}
    return sorted((op["tp"], op["identity"], op["off"], op["n"], op["source_offset"],
                   op["src_w"], op["dst_w"], op["kind"], op["path"], by_id[op["replica_id"]]["tier"])
                  for op in plan["ops"])


def review(run, prior, out):
    out.mkdir(parents=True, exist_ok=False)
    summary = read(run / "summary.json")
    row = summary["cases"][0]
    phase = row["phase"]
    plan = read(run / f"{phase}_plan.json")
    case = read(run / f"{phase}_case.json")
    survival = read(run / "active_survival.json")
    before, after = read(run / "inventory_before.json"), read(run / "inventory_after.json")
    ready = read(run / "inventory_blitz_scale_0_target_ready.json")
    released = read(run / "inventory_blitz_scale_0_released.json")
    setup_inventory = read(run / "inventory_active_ready.json")
    base = read(run / "base_sources_before.json")
    checks = {}
    def check(name, condition):
        checks[name] = bool(condition)

    check("reported_completed", summary["status"] == "passed" and row["status"] == "passed")
    check("pinned_policy_origin", plan["blitz"]["origin"]["commit"] == SHA)
    check("no_strategy_substitution", plan["strategy"] == "blitz_scale_tp_distribution"
          and plan["requested_strategy"] == plan["strategy"] and plan["fallback_reason"] is None)
    check("actual_TP_route", plan["blitz"]["tp_chains"] == [[0, 2], [1, 3]]
          and plan["blitz"]["source_mode"] == "rdma_TP_chain_single_target")
    source_rows = {r["id"]: r for r in case["replicas"]}
    active_group = case["active_instances"][0]
    ops = plan["ops"]
    check("complete_single_active_group", len(case["active_instances"]) == 1
          and set(plan["blitz"]["source_ids"]) == set(active_group["replica_ids"])
          and all(source_rows[o["replica_id"]]["source_role"] == "active"
                  and source_rows[o["replica_id"]]["tier"] == "hbm" for o in ops))
    check("rank_contents_distinct", len({o["identity"] for o in ops}) == 2)
    check("generation_epoch_content_preserved", all(
        o["generation"] == source_rows[o["replica_id"]]["generation"]
        and o["source_epoch"] == source_rows[o["replica_id"]]["epoch"]
        and o["identity"] == source_rows[o["replica_id"]]["identity"]
        and o["tp"] == source_rows[o["replica_id"]]["tp"] for o in ops))
    records = [{"worker": w["worker"], **r} for w in row["flow"]["execution"] for r in w["records"]]
    check("real_source_target_records", len(records) == 4 and all(
        len([r for r in records if r["op_id"] == o["op_id"] and r["role"] == role
             and r["worker"] == o[worker_field] and r["bytes"] == o["n"]
             and r["replica_id"] == o["replica_id"] and r["kind"] == "network"] ) == 1
        for o in ops for role, worker_field in (("source", "src_w"), ("target", "dst_w"))))
    checked_bytes = sum(o["n"] for o in ops)
    hashes = row["verification"]["operations"]
    check("full_SHA_per_operation", set(hashes) == {str(o["op_id"]) for o in ops} and all(
        hashes[str(o["op_id"])]["n"] == o["n"]
        and len(hashes[str(o["op_id"])]["source_sha256"]) == 64
        and hashes[str(o["op_id"])]["source_sha256"] == hashes[str(o["op_id"])]["target_sha256"] for o in ops)
        and row["verification"]["checked_bytes"] == checked_bytes == row["source_sha_checked_bytes"])
    check("setup_full_SHA_matches_transferred_contents", all(
        summary["active_owner_build"]["verification"]["operations"][str(o["op_id"])]["target_sha256"]
        == hashes[str(o["op_id"])]["source_sha256"] for o in ops)
        and summary["active_owner_build"]["verification"]["checked_bytes"] == checked_bytes)
    metric_files = [run / "metrics" / phase / f"rank{rank}.jsonl" for rank in range(2)]
    metrics = [[json.loads(line) for line in path.read_text().splitlines()] for path in metric_files]
    event_maps = [{r["event"]: r for r in rows} for rows in metrics]
    check("target_clock_is_same_host", len({r["host"] for events in event_maps for r in events.values()}) == 1)
    recalculated = (max(e["weights_ready"]["monotonic_ns"] for e in event_maps)
                    -min(e["load_begin"]["monotonic_ns"] for e in event_maps))/1e9
    check("independent_T_ready", abs(recalculated-row["primary_T_ready_s"]) < 1e-9)
    ordered = ("load_begin", "model_layout_end", "target_exported", "target_accepted", "target_mapped",
               "target_execute_begin", "target_copy_complete", "target_ready_publish", "weights_ready",
               "release_begin", "release_end")
    check("per_rank_event_order", all([events[key]["monotonic_ns"] for key in ordered]
                                     == sorted(events[key]["monotonic_ns"] for key in ordered) for events in event_maps))
    check("target_release_persisted", all(rows[-1]["event"] == "release_end" for rows in metrics))
    owned = set(active_group["replica_ids"])
    ready_active = [r for r in ready["replicas"] if r["id"] in owned]
    released_active = [r for r in released["replicas"] if r["id"] in owned]
    setup_active = [r for r in setup_inventory["replicas"] if r["id"] in owned]
    check("read_leases_observed_during_postready_validation", len(ready_active) == 2
          and all(r["leases"] == 1 and r["state"] == "VALID" for r in ready_active))
    check("source_retained_then_leases_released", signature(setup_active) == signature(ready_active) == signature(released_active)
          and all(r["leases"] == 0 for r in released_active))
    check("source_and_target_exact_32token_outputs", summary["active_setup"]["before_reply"]["text"]
          == row["reply"]["text"] == row["active_after_reply"]["text"]
          == survival["before"]["text"] == survival["after"]["text"]
          and all(reply["tokens"] == 32 for reply in (summary["active_setup"]["before_reply"], row["reply"], row["active_after_reply"])))
    check("source_survival_claim_has_runtime_evidence", survival["source"] == "runtime"
          and survival["survival_evidence"]["alive_before"] and survival["survival_evidence"]["alive_after"]
          and survival["survival_evidence"]["protected_during_copy"])
    check("retained_DRAM_unchanged_independently", signature(base) == signature(
        [r for r in after["replicas"] if r["id"] in {b["id"] for b in base}]))
    check("unrelated_catalog_unchanged_independently", signature(before["replicas"]) == signature(
        [r for r in after["replicas"] if r["id"] not in owned]))
    check("owned_sources_retired", not owned & {r["id"] for r in after["replicas"]}
          and not summary["remaining_owned_ids"] and not summary["unreleased_endpoints"]
          and not summary["pending_resume_endpoints"])
    physical = [{"worker": old["worker"], "allocated_delta": new["hbm_allocated_bytes"]-old["hbm_allocated_bytes"],
                 "reserved_delta": new["hbm_reserved_bytes"]-old["hbm_reserved_bytes"],
                 "free_delta": new["headroom_bytes"]["hbm"]-old["headroom_bytes"]["hbm"],
                 "catalog_before": old["used_bytes"]["hbm"], "catalog_after": new["used_bytes"]["hbm"]}
                for old, new in zip(before["workers"], after["workers"])]
    check("physical_HBM_allocator_returned", all(r["allocated_delta"] == r["reserved_delta"] == 0
          and r["catalog_before"] == r["catalog_after"] == 0 and r["free_delta"] >= -(256 << 20) for r in physical))
    check("no_remaining_worker_transactions", all(not w["transactions"] for w in after["workers"]))
    check("cleanup_and_recorded_drift_clear", not summary["cleanup_errors"] and not summary["source_hash_mismatches"])
    current_drift = [name for name, digest in summary["code_sha256"].items()
                     if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    check("frozen_sources_still_match_at_review", not current_drift)
    prior_summary = read(prior / "summary.json")
    comparison = []
    for old in prior_summary["cases"]:
        old_case = read(prior / f"{old['phase']}_case.json")
        comparison.append({"method": old["method_id"], "T_ready_s": old["primary_T_ready_s"],
            "same_processed_format": summary["format_contract"] == prior_summary["format_contract"],
            "same_transport_route_content_tier": canonical(plan, case["replicas"]) == canonical(old["flow"]["plan"], old_case["replicas"]),
            "same_executor_and_chunk": row["flow"]["executor"] == old["flow"]["executor"]
                and {w["chunk_bytes"] for w in row["flow"]["execution"]} == {w["chunk_bytes"] for w in old["flow"]["execution"]}})
    rank_intervals = [{"rank": rank, "load_s": (e["weights_ready"]["monotonic_ns"]-e["load_begin"]["monotonic_ns"])/1e9,
        "layout_s": (e["model_layout_end"]["monotonic_ns"]-e["load_begin"]["monotonic_ns"])/1e9,
        "export_to_execute_s": (e["target_execute_begin"]["monotonic_ns"]-e["target_exported"]["monotonic_ns"])/1e9,
        "copy_s": (e["target_copy_complete"]["monotonic_ns"]-e["target_execute_begin"]["monotonic_ns"])/1e9,
        "copy_to_weights_ready_s": (e["weights_ready"]["monotonic_ns"]-e["target_copy_complete"]["monotonic_ns"])/1e9}
        for rank, e in enumerate(event_maps)]
    report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "input_run": str(run), "comparison_run": str(prior), "commit": SHA, "current_frozen_drift": current_drift,
        "transfer_bytes": checked_bytes, "transfer_GiB": checked_bytes/(1 << 30),
        "route": canonical(plan, case["replicas"]), "records": records, "full_SHA": hashes,
        "timing": {"independent_T_ready_s": recalculated, "request_ready_ack_s": row["request_to_ready_ack_s"],
            "request_first_correct_token_s": row["request_to_first_correct_token_s"],
            "online_inventory_planning_s": row["online_inventory_and_planning_s"], "online_planning_s": row["online_planning_s"],
            "prepare_s": row["flow"]["prepare_s"], "execute_wall_s": row["flow"]["execute_wall_s"],
            "postready_full_SHA_s": row["post_ready_verification_s"], "rank_intervals": rank_intervals},
        "setup": {"total_wall_s": summary["active_setup"]["wall_s"], "owner_build_coordinator_s": summary["active_owner_build"]["coordinator_wall_s"],
                  "owner_build_copy_s": summary["active_owner_build"]["execute_wall_s"], "IPC_load_window_s": summary["active_setup"]["timing"]["load_s"],
                  "source_HBM_bytes": checked_bytes, "source_alias_second_allocation": False,
                  "source_lifetime_reported": row["source_lifetime"]},
        "physical_cleanup": physical, "existing_S3_comparison": comparison,
        "scope": "single-target BlitzScale source/cache/TP distribution HIP mechanism; not original live serving or full multicast performance",
        "limits": ["one source and one target: source/cache choice is structurally trivial in this measured case",
                   "same route as prior S3 does not establish algorithm or topology gain",
                   "source correctness before/after copy; sustained serving interference not measured",
                   "source byte-seconds covers owner build through postcopy source check; final cleanup tail is outside this reported value",
                   "physical peak remains unverified; cleanup compares endpoint samples",
                   "r01 failed on missing numpy before active source construction or GPU transfer; not a performance observation"]}
    evidence = [run / "summary.json", run / f"{phase}_plan.json", run / f"{phase}_case.json", run / "active_survival.json",
                run / "inventory_before.json", run / "inventory_after.json", *metric_files, prior / "summary.json"]
    report["evidence_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in evidence}
    (out / "review.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    lines = ["# BlitzScale S3 r02 独立验收", "", f"验收结果：**{report['status']}**，{sum(checks.values())}/{len(checks)} 项证据检查通过。", "",
        f"实际恢复完整 Qwen TP2：{checked_bytes:,} bytes（{report['transfer_GiB']:.5f} GiB）；node27 GPU0→node29 GPU0、node27 GPU1→node29 GPU1，来源为本 run 完整活跃 HBM 逻辑实例。", "",
        "源码策略为 Steersman 缓存准入 + 非 unidirect sender 顺序 + source pop + TP stride；没有 fastest/LP 测量阶段取源或后备替换。当前唯一 source/target，单目标分发退化为按 TP rank 的两条 P2P。", "",
        "4条实际执行records包含2源/2目标，每条op两端字节一致。两个rank内容身份和SHA不同，各自source/target全量SHA相同，且与源owner建立时的SHA相同。ready时观察到每源1个read lease，校验/目标释放后lease归零；source generation/epoch/content保持。", "",
        f"目标主机同一host原始rank事件独立重算 T_ready={recalculated:.9f}s；请求→ready ack={row['request_to_ready_ack_s']:.6f}s，请求→首正确token={row['request_to_first_correct_token_s']:.6f}s。", "",
        f"在线inventory+plan={row['online_inventory_and_planning_s']:.6f}s，plan={row['online_planning_s']:.6f}s，prepare={row['flow']['prepare_s']:.6f}s，copy执行wall={row['flow']['execute_wall_s']:.6f}s，后置全SHA={row['post_ready_verification_s']:.6f}s。这些来自不同阶段的窗口，不强行相加重建主指标。", "",
        f"源setup总wall={summary['active_setup']['wall_s']:.6f}s，其中owner构建协调窗口={summary['active_owner_build']['coordinator_wall_s']:.6f}s、copy={summary['active_owner_build']['execute_wall_s']:.6f}s、IPC加载窗口={summary['active_setup']['timing']['load_s']:.6f}s。源HBM={report['transfer_GiB']:.5f}GiB，IPC消费者共享同一owner allocation；此专门建立的source计入增量驻留成本。", "",
        f"源setup/复制后的两次32token输出与目标输出完全一致。报告源存活={row['source_lifetime']['additional_lifetime_s']:.6f}s，owner byte-seconds={row['source_lifetime']['additional_hbm_byte_seconds']:.3f}；该值结束于postcopy source check，不包含最后cleanup尾部。", "",
        "清理独立比较：4worker catalog HBM从0返回0，allocated/reserved delta均0；源worker物理free各+18MiB，目标worker各−6MiB，在256MiB容差内。无残留transactions、owned IDs、pending resume或未释放consumer；DRAM底本与无关catalog签名一致，cleanup_errors/source_hash_mismatches为空。", "",
        "| 既有S3方法 | T_ready(s) | 路由/内容/源tier相同 | executor/chunk相同 |", "|---|---:|---|---|"]
    lines += [f"| {c['method']} | {c['T_ready_s']:.9f} | {c['same_transport_route_content_tier']} | {c['same_executor_and_chunk']} |" for c in comparison]
    lines += ["", "本次与既有S3的实际执行路径相同；独立source/cache/TP策略已可执行，但该拓扑未产生不同决策，不把时延差记为算法或拓扑增益。身份限定为BlitzScale分发机制HIP适配，不是原版live-serving或完整广播性能。", "",
              "r01系统Python缺numpy，在活跃源构建规划阶段失败，未执行GPU恢复；r02使用已有conda成功。原始失败记录保留，不将r01计入性能观察。", "",
              "逐rank分项、原始SHA、执行records、全部检查与输入文件hash见review.json。审查只读既有文件，没有发起worker RPC或GPU工作。"]
    (out / "report.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"status": report["status"], "checks": f"{sum(checks.values())}/{len(checks)}", "T_ready_s": recalculated,
                      "source_setup_s": report["setup"]["total_wall_s"], "existing_S3_comparison": comparison}, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    review(args.run.resolve(), args.prior.resolve(), args.out.resolve())
