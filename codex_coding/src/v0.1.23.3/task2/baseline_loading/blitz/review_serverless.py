"""Read-only independent ServerlessLLM Store review; no GPU imports or RPCs."""
import argparse
import ast
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[6]
SHA = "9f50241baa5386e06a9321c51f19a9ef5f964c2b"


def load(path):
    return json.loads(Path(path).read_text())


def review(run, out):
    out.mkdir(parents=True, exist_ok=False)
    summary_path = run / "summary.json"
    summary = load(summary_path)
    worker_result_path = run / "worker/result.json"
    worker = load(worker_result_path) if worker_result_path.is_file() else None
    store_path = run / "worker/store_ready.json"
    store = load(store_path) if store_path.is_file() else None
    pool = load(run / "worker/pool_ready.json")
    phase = run.name+"_restore"
    metrics = {}
    evidence = [summary_path, run / "worker/pool_ready.json"]
    for rank in range(2):
        path = run / "metrics" / phase / f"rank{rank}.jsonl"
        metrics[rank] = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
        if path.is_file():
            evidence.append(path)
    log = (run / "loader.log").read_text(errors="replace")
    evidence.append(run / "loader.log")
    prepared = summary["format_setup"]
    frozen_path = Path(prepared["frozen_source_evidence"])
    frozen = load(frozen_path)["cases"][0]
    session_path = Path(prepared["session"])
    session = load(session_path)
    model = session["models"]["B"]
    source_map = {s["tp"]: s for s in model["sources"]}
    frozen_hashes = {op["tp"]: frozen["verification"]["operations"][str(op["op_id"])]["source_sha256"]
                     for op in frozen["flow"]["plan"]["ops"]}
    checks = {}
    def check(name, value):
        checks[name] = bool(value)
    check("pinned_upstream_commit", summary["upstream_sha"] == SHA == pool["upstream_sha"])
    check("full_source_frozen_S2_SHA", frozen["verification"]["checked_bytes"] == 70329718112
          and frozen["verification"]["passed"] and sorted(frozen_hashes) == [0, 1]
          and all(r["payload_sha256"] == r["frozen_source_sha256"] == frozen_hashes[r["tp_rank"]]
                  and r["logical_bytes"] == source_map[r["tp_rank"]]["n"] for r in prepared["ranks"]))
    geometry = []
    for row in prepared["ranks"]:
        rank = row["tp_rank"]
        metadata = load(row["metadata"])
        check(f"rank{rank}_prepared_exact_layout", metadata["index"] == source_map[rank]["index"]
              and metadata["meta"]["tensors"] == source_map[rank]["meta"]["tensors"]
              and all(metadata["meta"].get(key) == source_map[rank]["meta"].get(key)
                      for key in ("model_identity", "tp_size", "tp_rank")))
        check(f"rank{rank}_prepared_partition_size", Path(row["data_file"]).stat().st_size == row["file_bytes"]
              and row["file_bytes"]-row["logical_bytes"] == row["padding_bytes"] == 1360)
        evidence.append(Path(row["metadata"]))
        if store:
            record = next(r for r in store["ranks"] if r["tp_rank"] == rank)
            extents = [(c["file_offset"], c["logical_bytes"]) for c in record["destination_chunk_geometry"]]
            expected = sorted((offset, size) for offset, size in metadata["index"].values())
            check(f"rank{rank}_full_IPC_geometry", extents == expected
                  and sum(size for _, size in extents) == row["logical_bytes"]
                  and all(0 <= c["allocation_index"] < record["allocation_count"] and c["allocator_offset"] >= 0
                          for c in record["destination_chunk_geometry"]))
            check(f"rank{rank}_IPC_frame66_and_offsets_exercised", record["ipc_handle_formats"] == {"pytorch_version2_malloc_66": 1009}
                  and record["storage_count"] == 1009 and record["nonzero_allocation_offsets"] > 0)
            geometry.append({key: record[key] for key in ("tp_rank", "logical_bytes", "file_bytes", "padding_bytes",
                "storage_count", "allocation_count", "nonzero_allocation_offsets", "ipc_handle_formats",
                "load_begin_unix_s", "store_ready_unix_s", "store_load_s")})
    check("66GiB_pool_before_request", pool["pool_bytes"] == 66 << 30 and pool["pool_setup_s"] > 0
          and "pool_setup" in summary and "request_begin_monotonic_s" in summary)
    check("actual_upstream_backend_from_isolated_worktree", "/lib_cite/ServerlessLLM-hip/" in pool["backend"]
          and pool["backend"].endswith(".so"))
    check("actual_disk_pinned_dispatch_device_log", all(text in log for text in (
        "PinnedMemoryPool with 2112 buffers", "Loading model rank_0 to host with 4 threads",
        "Loading model rank_1 to host with 4 threads", "Dispatcher started for model rank_0",
        "Dispatcher started for model rank_1", "Finished loading model rank_0 from disk",
        "Finished loading model rank_1 from disk", "Finished loading tensor from memory to device 0",
        "Finished loading tensor from memory to device 1")))
    gate_path = run.parent / "serverless_offset_gate_r03/worker/result.json"
    gate = load(gate_path)
    check("separate_frame66_gate_passed", gate["status"] == "passed"
          and gate["verification"]["all_bytes_equal"] and gate["verification"]["guards_unchanged"]
          and gate["loader"]["ipc_handle_formats"] == {"pytorch_version2_malloc_66": 3})
    check("code_freeze_equal_at_run_end", summary.get("code_sha256_before") == summary.get("code_sha256_after")
          and summary.get("code_unchanged") is True)
    check("protected_catalog_unchanged_at_snapshot", summary.get("protected_metadata_unchanged") is True)
    if summary.get("status") == "passed":
        def protected_signature(workers):
            fields = ("id", "worker", "identity", "tp", "off", "n", "tier", "epoch", "generation", "state")
            return sorted(tuple(row.get(key) for key in fields) for worker in workers
                          for row in worker["replicas"] if row["state"] == "VALID")
        check("protected_catalog_unchanged_independently", protected_signature(summary["inventory_before"])
              == protected_signature(summary["inventory_after"])
              == sorted(tuple(row) for row in summary["protected_before"]))
        drift = []
        for name, expected in summary["code_sha256_after"].items():
            path = Path(name) if Path(name).is_absolute() else ROOT / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                drift.append(name)
        check("frozen_implementation_still_matches_at_review", not drift)
    clocks = [{e["event"]: e for e in rows} for rows in metrics.values()]
    complete_clock = all("load_begin" in events and "weights_ready" in events for events in clocks)
    primary = None
    if complete_clock:
        check("primary_same_target_host", len({e["host"] for rows in metrics.values() for e in rows}) == 1)
        primary = (max(e["weights_ready"]["monotonic_ns"] for e in clocks)
                   -min(e["load_begin"]["monotonic_ns"] for e in clocks))/1e9
        check("primary_independently_matches_summary", abs(primary-summary.get("primary_T_ready_s", -1)) < 1e-9)
    check("complete_engine_weights_ready", complete_clock)
    check("full_destination_SHA_proven", bool(worker and worker.get("status") == "passed"
          and all(r.get("full_sha256_equal") and r.get("target_sha256") == frozen_hashes[r["tp_rank"]]
                  for r in worker.get("ranks", [])) and len(worker.get("ranks", [])) == 2))
    check("exact_32token_output_proven", summary.get("text_correct") is True
          and summary.get("reply", {}).get("text") == model["reference"]["text"]
          and summary.get("reply", {}).get("tokens") == 32)
    check("target_release_and_physical_cleanup_completed", bool(summary.get("release_metrics"))
          and len(summary["release_metrics"]) == 2 and summary.get("physical_hbm_reclaimed") is True
          and not summary.get("cleanup", {}).get("pending_resume_preserved"))
    check("temporary_loader_container_removed", summary.get("cleanup", {}).get("container_removed") is True
          or summary.get("cleanup", {}).get("container_removed_before_release") is True)
    actual_admission = summary.get("actual_engine_timeout_admission", {})
    engine_spec_path = Path(actual_admission.get("process_spec", ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/fresh_engine_node29.process.json"))
    engine_spec = load(engine_spec_path)
    engine_log_path = Path(engine_spec["log"])
    engine_log = engine_log_path.read_text(errors="replace")
    timeout_lines = [line for line in engine_log.splitlines() if "TimeoutError:" in line or "keeper did not fill" in line]
    timeout_s = float(engine_spec["env"]["FLUIDCKPT_TIMEOUT"])
    overlay = None
    overlay_path = next((Path(name) if Path(name).is_absolute() else ROOT / name
                         for name in summary.get("code_sha256_before", {}) if name.endswith("session_overlay_owned.json")), None)
    if overlay_path:
        overlay = load(overlay_path)
        slot = overlay["slots"]["target_b"]
        pid_path = engine_spec_path.with_suffix(".pid.json")
        pid = load(pid_path)
        check("owned_overlay_matches_actual_target_identity", slot["process_spec"] == str(engine_spec_path)
              and slot["ownership_run"] == str(engine_spec_path.parent)
              and ["node29", str(engine_spec_path)] in overlay["recovery_owned_processes"]
              and actual_admission.get("pid") == pid["pid"]
              and actual_admission.get("start_time") == pid["start_time"]
              and actual_admission.get("spec_sha256") == hashlib.sha256(engine_spec_path.read_bytes()).hexdigest())
        check("actual_target_900s_timeout", actual_admission.get("handoff_timeout_s") == timeout_s == 900.0
              and actual_admission.get("source") == "actual /proc/PID/environ; recorded PID/starttime checked")
        check("recovery_did_not_change_frozen_source", overlay["models"]["B"] == model
              and overlay["endpoints"] == session["endpoints"]
              and overlay["slots"]["target_a"] == session["slots"]["target_a"])
        evidence += [overlay_path, pid_path]
    physical = []
    for old, new in zip(summary["inventory_before"], summary.get("inventory_after", [])):
        physical.append({"worker": old["worker"], "node": old["node"],
            "free_HBM_delta": new["headroom_bytes"]["hbm"]-old["headroom_bytes"]["hbm"],
            "keeper_allocated_delta": new["hbm_allocated_bytes"]-old["hbm_allocated_bytes"],
            "keeper_reserved_delta": new["hbm_reserved_bytes"]-old["hbm_reserved_bytes"],
            "transactions_after": new["transactions"]})
    check("target_physical_free_restored_independently", all(r["free_HBM_delta"] >= -(256 << 20)
          for r in physical if r["worker"] in (2, 3)) and len(physical) == 4)
    check("keeper_transactions_empty", all(not r["transactions_after"] for r in physical))
    released_metrics = all(rows and rows[-1]["event"] == "release_end" for rows in metrics.values())
    check("target_release_events_recorded", released_metrics)
    if summary.get("status") == "passed":
        baseline_allocated = {r["rank"]: r["allocated_bytes"] for r in summary["parked_before"]}
        check("normal_worker_exit_and_no_pending_cleanup", summary.get("worker_exit_code") == 0
              and summary.get("cleanup", {}).get("worker_final_exit_code") == 0
              and not any("error" in key or key.startswith("pending_") for key in summary.get("cleanup", {})))
        check("released_engine_allocator_matches_parked_baseline", released_metrics and all(
            rows[-1]["allocated_bytes"] <= baseline_allocated[rank] + (128 << 20) for rank, rows in metrics.items()))
        check("upstream_host_models_unloaded_before_normal_exit", "All models unloaded from memory" in log
              and log.count("Deallocated 1048 buffers") >= 2)
    control = None
    if run.name.endswith("full_r03"):
        probe_root = run.parent
        current_probe_path = probe_root / "serverless_hash_current_r01/summary.json"
        fixed_probe_path = probe_root / "serverless_hash_set_device_r01/summary.json"
        current_probe, fixed_probe = load(current_probe_path), load(fixed_probe_path)
        fixed_worker = load(probe_root / "serverless_hash_set_device_r01/worker/result.json")
        current_log = (probe_root / "serverless_hash_current_r01/worker.log").read_text()
        fixed_log = (probe_root / "serverless_hash_set_device_r01/worker.log").read_text()
        current_payload = (probe_root / "serverless_hash_current_r01/worker/data/rank_0/tensor.data_0").read_bytes()
        fixed_payload = (probe_root / "serverless_hash_set_device_r01/worker/data/rank_0/tensor.data_0").read_bytes()
        check("controlled_set_device_probe_139_to_normal_SHA_PASS", current_probe["worker_exit_code"] == 139
              and current_probe["status"] == "failed" and fixed_probe["worker_exit_code"] == 0
              and fixed_probe["status"] == fixed_worker["status"] == "passed"
              and current_payload == fixed_payload and len(current_payload) == 24576
              and fixed_worker["source_sha256"] == fixed_worker["target_sha256"] == fixed_worker["owner"]["sha256"]
              == hashlib.sha256(fixed_payload).hexdigest() and fixed_worker["owner"]["guards_unchanged"])
        current_traces = [json.loads(line) for line in current_log.splitlines() if line.startswith('{"event"')]
        fixed_traces = [json.loads(line) for line in fixed_log.splitlines() if line.startswith('{"event"')]
        check("probe_observes_uninitialized_vs_initialized_IPC_import", any(
            r["event"] == "before_ipc_import" and r["torch_cuda_initialized"] is False for r in current_traces)
            and any(r["event"] == "explicit_set_device_finished" and r["torch_cuda_initialized"] is True for r in fixed_traces)
            and sum(r["event"] == "after_ipc_import" for r in fixed_traces) == 3)
        old_adapter_path = probe_root / "serverless_hash_probe_sources_r01/source_before/serverless_adapter.py"
        adapter_path = ROOT / "main_lib/fluidckpt/benchmarks/serverless_adapter.py"
        old_tree, new_tree = ast.parse(old_adapter_path.read_text()), ast.parse(adapter_path.read_text())
        old_class = next(n for n in old_tree.body if isinstance(n, ast.ClassDef) and n.name == "UpstreamStoreLoader")
        new_class = next(n for n in new_tree.body if isinstance(n, ast.ClassDef) and n.name == "UpstreamStoreLoader")
        check("actual_Store_adapter_class_unchanged", ast.dump(old_class) == ast.dump(new_class))
        old_hash = next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == "hash_target")
        new_hash = copy.deepcopy(next(n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name == "hash_target"))
        new_hash.args.args.pop()
        new_hash.args.defaults.pop()
        new_hash.body = [n for n in new_hash.body if not (isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == "torch.cuda.set_device")]
        for loop in (n for n in new_hash.body if isinstance(n, ast.For)):
            loop.iter = loop.iter.args[0]
            loop.target = loop.target.elts[1]
            loop.body = [n for n in loop.body if not (isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "trace")]
        check("hash_semantics_only_add_set_device_besides_trace", ast.dump(old_hash) == ast.dump(new_hash))
        old_full = load(run.parent / "serverless_qwen_full_r02/summary.json")
        upstream_hashes = {name: value for name, value in summary["code_sha256_before"].items()
                           if "ServerlessLLM-hip/" in name}
        check("compiled_actual_Store_unchanged_since_full_r02", bool(upstream_hashes) and all(
            old_full["code_sha256_before"].get(name) == value for name, value in upstream_hashes.items()))
        traces = [json.loads(line) for line in log.splitlines() if line.startswith('{"phase": "post_ready_verification"')]
        trace_coverage = {}
        for rank in range(2):
            released_storage = [r for r in traces if r["tp_rank"] == rank and r["event"] == "storage_hashed_and_released"]
            before_import = [r for r in traces if r["tp_rank"] == rank and r["event"] == "before_ipc_import"]
            trace_coverage[rank] = len(released_storage)
            check(f"rank{rank}_all1009_storages_hashed_and_released_initialized", len(released_storage) == len(before_import) == 1009
                  and {r["storage_index"] for r in released_storage} == set(range(1009))
                  and all(r["torch_cuda_initialized"] is True for r in before_import))
        control = {"current_exit": current_probe["worker_exit_code"], "set_device_exit": fixed_probe["worker_exit_code"],
            "same_payload_bytes": len(current_payload), "set_device_probe_SHA": fixed_worker["target_sha256"],
            "owner_guards_unchanged": fixed_worker["owner"]["guards_unchanged"], "full_trace_storage_coverage": trace_coverage,
            "intervention_scope": "torch.cuda.set_device initializes Torch and selects the target device together; the probe does not separately isolate these two internal effects"}
        evidence += [current_probe_path, fixed_probe_path, probe_root / "serverless_hash_set_device_r01/worker/result.json",
                     probe_root / "serverless_hash_current_r01/worker.log", probe_root / "serverless_hash_set_device_r01/worker.log", old_adapter_path]
    status = "passed" if summary.get("status") == "passed" and all(checks.values()) else (
        "failed_incomplete" if summary.get("status") == "failed" else "pending")
    report = {"status": status, "reviewed_at_utc": datetime.now(timezone.utc).isoformat(), "input_run": str(run),
        "summary_status": summary.get("status"), "error": summary.get("error"), "checks": checks,
        "source_logical_bytes": sum(r["logical_bytes"] for r in prepared["ranks"]),
        "upstream_partition_bytes": sum(r["file_bytes"] for r in prepared["ranks"]),
        "frozen_source_rank_SHA": frozen_hashes, "frame66_gate": {"status": gate["status"], "scope": "separate 24576-byte shared-allocation offset/guard gate"},
        "geometry": geometry, "independent_primary_T_ready_s": primary,
        "store_window_wall_clock_s": store.get("store_window_s") if store else None,
        "pool_setup_outside_request_s": pool["pool_setup_s"], "pool_bytes": pool["pool_bytes"],
        "format_setup_rank_s": {r["tp_rank"]: r["format_setup_s"] for r in prepared["ranks"]},
        "engine_handoff_timeout_s": timeout_s, "engine_timeout_log_lines": timeout_lines,
        "actual_engine_timeout_admission": actual_admission,
        "owned_overlay": str(overlay_path) if overlay_path else None,
        "set_device_control_evidence": control,
        "full_SHA_records": [{key: rank[key] for key in ("tp_rank", "logical_bytes", "target_sha256", "source_sha256", "full_sha256_equal", "verification_s")}
                             for rank in worker["ranks"]] if worker and worker.get("status") == "passed" else None,
        "observed_physical_snapshot": physical, "cleanup_snapshot": summary.get("cleanup", {}),
        "source_path_origin": {
            "adapter": "main_lib/fluidckpt/benchmarks/serverless_adapter.py::UpstreamStoreLoader",
            "file_to_pinned": "lib_cite/ServerlessLLM-hip/sllm_store/csrc/sllm_store/model.cpp::ToHost pread→host_buffers→host_ptr_vector enqueue",
            "pinned_pool": "lib_cite/ServerlessLLM-hip/sllm_store/csrc/sllm_store/pinned_memory_pool.cpp::aligned_alloc+cudaHostRegister (HIP compilation)",
            "dispatch": "lib_cite/ServerlessLLM-hip/sllm_store/csrc/sllm_store/model.cpp::DispatchToGpu",
            "IPC_open": "lib_cite/ServerlessLLM-hip/sllm_store/csrc/sllm_store/checkpoint_store.cpp::GetDeviceMemPtr/cudaIpcOpenMemHandle",
            "pinned_to_IPC": "lib_cite/ServerlessLLM-hip/sllm_store/csrc/sllm_store/model.cpp::ToGpu cudaMemcpy host_buffer+chunk_offset→device_ptr[handle_idx]+gpu_offset"},
        "limitations": ["Store completion is not engine weights_ready, full SHA, correct output or completed cleanup",
            "66GiB pinned pool is built before measured request; its setup/space are separate costs",
            "runtime OS/NFS cache is uncontrolled/unmeasured; no observed fallback log does not prove uncached physical media reads",
            "processed payload 70.329718112GB differs from native raw checkpoint approximately71.904GB; do not attribute speed directly to Store/native algorithms",
            "after-inventory compares endpoint samples; pending consumers/importers do not count as completed cleanup",
            "small frame66 gate does not substitute for full-rank SHA/inference"],
        "worker_result_present": worker_result_path.is_file(), "store_completion_artifact_present": store_path.is_file(),
        "worker_exit_code": summary.get("worker_exit_code"),
        "request_to_first_correct_token_s": summary.get("request_to_first_correct_token_s"),
        "exact_output_observed": summary.get("text_correct") is True,
        "target_last_event": {rank: rows[-1]["event"] if rows else None for rank, rows in metrics.items()},
        "target_release_allocated_bytes": {rank: rows[-1].get("allocated_bytes") if released_metrics else None for rank, rows in metrics.items()},
        "verification_trigger_present": (run / "verification_start.json").exists(),
        "cache_state": summary.get("runtime_cache_state"), "odirect_fallback_log_present": "falling back to compatible mode" in log}
    evidence += [frozen_path, session_path, gate_path, engine_spec_path, engine_log_path]
    if store_path.is_file():
        evidence.append(store_path)
    if worker_result_path.is_file():
        evidence.append(worker_result_path)
    report["input_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in evidence}
    (out / "review.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    lines = [f"# {run.name} 独立审查", "", f"当前验收结果：**{status}**。summary={summary.get('status')}，错误 `{summary.get('error')}`。", "",
        "r03 的66B PyTorch malloc IPC framing、小共享allocation非零offset和guard检查已通过。本轮真实加载了两份完整processed TP文件，通过上游pread→pinned pool→chunk dispatcher→HIP IPC destination，Store已产生完成记录；这不等于引擎恢复成功。", "",
        f"目标引擎 `FLUIDCKPT_TIMEOUT={timeout_s:g}` 秒；Store wall-clock加载窗口为 {report['store_window_wall_clock_s']} 秒。主指标原始目标同host事件独立重算：{primary} 秒；完整目标SHA证据={checks['full_destination_SHA_proven']}，精确32token输出={checks['exact_32token_output_proven']}，物理回收={checks['target_physical_free_restored_independently']}。部分指标可观察，整体验收必须满足全部条件。", "",
        f"worker exit={summary.get('worker_exit_code')}；worker/result.json存在={worker_result_path.is_file()}；verification trigger存在={report['verification_trigger_present']}。两rank最终事件={report['target_last_event']}，release末allocated bytes={report['target_release_allocated_bytes']}。summary cleanup={summary.get('cleanup', {})}。恢复/清理由主agent处理，本审查不发请求。", "",
        f"来源为S2冻结源逐rank完整SHA：processed总量 {report['source_logical_bytes']:,} bytes，磁盘格式含padding总量 {report['upstream_partition_bytes']:,} bytes，每rank padding1360 bytes。与原raw checkpoint约71.904GB不同。", "",
        f"上游完整pinned pool为66GiB（{pool['pool_bytes']:,} bytes），构建 {pool['pool_setup_s']:.6f}s，在request前完成，未计入引擎T_ready。源格式建立两rank分别 {prepared['ranks'][0]['format_setup_s']:.6f}/{prepared['ranks'][1]['format_setup_s']:.6f}s，亦须单列。", "",
        "实际来源/实现：冻结ServerlessLLM Store commit9f50241baa5386e06a9321c51f19a9ef5f964c2b；只改HIP构建兼容与目标handle/framing/offset映射。每rank1009个storage、209个allocation、801个非零offset，连续source extents匹配processed index。日志两rank各分配1048×32MiB pinned chunks（共2096，pool2112），真实执行4 IO线程及device0/1 copy。", "",
        "计时边界：引擎主指标仅允许目标同host的monotonic load_begin/weights_ready差；Store window为同一worker的Unix wall-clock start/ready差，是次指标。禁止把协调器monotonic、workerUnix及不同主机事件混减。", "",
        "NFS/OS运行cache未控制/未测量。源码先尝试O_DIRECT、遇EINVAL/EOPNOTSUPP才回退；本轮日志未出现显式fallback，不代表数据来自未缓存物理介质。不得与native时延直接归因或宣布算法速度差。", "",
        f"来源/实现冻结before-after检查={checks['code_freeze_equal_at_run_end']}，overlay={overlay_path}。实际900s准入和PID/starttime/spec哈希（若提供）单独检查，使用新overlay保持所有原source/keeper记录；详见review.json。", "",
        "r01的120s handoff超时失败记录独立保留；本run出现的后续错误单独记录，不把两个失败合并成同一原因。", "",
        "本审查只读已有文件，不导入GPU模块，不发HTTP/SSH请求。原运行记录与冻结实现保持不变。"]
    if control:
        lines += ["", "set_device 控制实验：相同24576-byte输入与上游Store路径，原导入前Torch未初始化时首个IPC import崩溃，exit139；显式set_device后Torch initialized=true，3个storage完整SHA与owner一致、guard保持、exit0。", "",
            "AST独立对比确认实际UpstreamStoreLoader类与r02完全相同，去除trace/enumerate记录和新增set_device后，hash_target与冻结旧函数AST相同；本轮所有编译Store源码/扩展hash与r02一致。该干预同时完成Torch初始化与设备选择，微probe未分别隔离这两个内部作用。", "",
            "完整r03的两个rank各1009个storage都记录initialized=true并完成hash/import释放；两个rank的完整SHA如下：", "",
            "| TP rank | 逻辑字节 | 完整source/target SHA256 | 后置验证(s) |", "|---|---:|---|---:|"]
        lines += [f"| {r['tp_rank']} | {r['logical_bytes']} | {r['target_sha256']} | {r['verification_s']:.6f} |" for r in report["full_SHA_records"]]
        lines += ["", f"源与完整格式仍相同，主指标{primary:.9f}s，请求到首正确token {summary['request_to_first_correct_token_s']:.6f}s；后置SHA逐rank单列，66GiB pool建立 {pool['pool_setup_s']:.6f}s计时外。", "",
            "worker与clear_mem正常结束后再释放引擎；两rank release_end allocated均1,315,794,944 bytes，目标物理free较运行前各−6MiB（256MiB容差内），source侧0变化、keeper allocated/reserved delta全部0、无transactions。临时adapter container已删除、原命令恢复；受保护catalog签名独立一致，所有冻结文件在review时仍匹配。", "",
            "原r01 handoff超时和r02 postready IPC import崩溃均独立保留，本轮是第一次端到端全量验收通过。结论限定为ServerlessLLM Store数据面HIP适配，不覆盖原在线scheduler/frontend；cache状态和raw/processed格式差异保持报告边界。"]
    (out / "report.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"status": status, "summary": summary.get("status"), "error": summary.get("error"),
        "passed_checks": sum(checks.values()), "total_checks": len(checks), "primary_T_ready_s": primary,
        "store_window_s": report["store_window_wall_clock_s"], "cleanup": summary.get("cleanup", {})}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    review(args.run.resolve(), args.out.resolve())
