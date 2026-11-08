"""Extend the prior complete table with distinct baseline identities and failures.

This offline reader never runs a model, estimates missing timings, or computes
speed ratios between different initial states, formats, cache states, or budgets.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading"
PREVIOUS = ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios/complete_table_r01/review_r02/summary.json"
SCAN_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios"
DEFAULT_SUPPORT = [SCAN_ROOT / name / "summary.json" for name in (
    "baseline_loading_final_fixture_r01", "baseline_loading_protected_after_r01", "stage2_protected_after_r01")]
IDENTITIES = {
    "sglang_vendor_native": {"name": "海光 vendor SGLang 原生 checkpoint", "identity": "vendor_native_checkpoint",
        "initial_target": "fresh engine", "source_tier": "NFS 原生 safetensors / Linux file cache observed",
        "input_format": "原生 checkpoint → 原生处理后 TP2", "scope": "installed vendor loader; 非冻结 upstream 原版 daemon"},
    "blitz_scale": {"name": "BlitzScale 分发策略 HIP 适配", "identity": "policy_adaptation_common_executor",
        "initial_target": "既有 eager 引擎 parked", "source_tier": "真实远端 active HBM TP2；完整 DRAM fixture 保留",
        "input_format": "已处理 TP2 packed storages", "scope": "冻结上游选源/TP-chain 策略；既有 HIP/RCCL 执行器，非原版 CUDA/RDMA/live-serving"},
    "serverless_store_hip": {"name": "ServerlessLLM Store 数据面 HIP 适配", "identity": "upstream_store_dataplane_hip",
        "initial_target": "既有 eager 引擎 parked；新 Store pinned pool 已初始化",
        "source_tier": "NFS 已处理分区 → 上游 pinned host pool → target HBM",
        "input_format": "已处理 TP2 tensor.data 分区；offline 转换单列",
        "scope": "真实上游 Store 数据面；非原版整系统/选址调度/frontend"},
}


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            result.update(block)
    return result.hexdigest()


class Inputs:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.manifest = {}
        self.missing = []

    def path(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.root / path

    def label(self, path):
        path = self.path(path).resolve()
        return str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)

    def read(self, path, lines=False, optional=False):
        path = self.path(path)
        if not path.is_file():
            self.missing.append(self.label(path))
            if optional:
                return None
            raise FileNotFoundError(path)
        before = sha(path)
        raw = path.read_text()
        data = [json.loads(line) for line in raw.splitlines() if line] if lines else json.loads(raw)
        if before != sha(path):
            raise RuntimeError(f"input changed while reading: {path}")
        self.manifest[self.label(path)] = {"sha256": before, "bytes": path.stat().st_size}
        return data

    def verify(self):
        for path, record in self.manifest.items():
            if sha(self.path(path)) != record["sha256"]:
                raise RuntimeError(f"input changed during export: {path}")

    def record_file(self, path):
        path = self.path(path)
        self.manifest[self.label(path)] = {"sha256": sha(path), "bytes": path.stat().st_size}


def clock_proof(timing, primary):
    ranks = timing.get("ranks", {})
    proof = {"passed": False, "expected_rank_count": 2, "rank_count": len(ranks)}
    if set(ranks) != {"0", "1"} or not isinstance(primary, (int, float)):
        return dict(proof, reason="missing_complete_TP2_rank_window")
    starts, ends, hosts = [], [], set()
    for detail in ranks.values():
        events = detail.get("events", [])
        begin = [e for e in events if e.get("event") == "load_begin"]
        end = [e for e in events if e.get("event") == "weights_ready"]
        if len(begin) != 1 or len(end) != 1 or any(e.get("event") == "load_failed" for e in events):
            return dict(proof, reason="invalid_rank_events")
        if end[0]["monotonic_ns"] < begin[0]["monotonic_ns"]:
            return dict(proof, reason="negative_rank_window")
        starts.append(begin[0]["monotonic_ns"])
        ends.append(end[0]["monotonic_ns"])
        hosts.update(e.get("host") for e in events)
    actual = (max(ends) - min(starts)) / 1e9
    passed = len(hosts) == 1 and None not in hosts and actual >= 0 and abs(actual - primary) < 1e-9
    return dict(proof, passed=passed, hosts=sorted(str(h) for h in hosts), recomputed_T_ready_s=actual)


def cleanup_proof(run, kind):
    bad = ("cleanup_errors", "remaining_owned_ids", "unreleased_endpoints", "pending_resume_endpoints",
           "remaining_owned_processes", "remaining_owned_containers", "owned_remaining", "source_hash_mismatches")
    if any(run.get(key) for key in bad):
        return False
    if kind == "sglang_vendor_native":
        return (run.get("process_cleanup", {}).get("returncode") == 0 and run.get("container_cleanup", {}).get("returncode") == 0
                and run.get("retained_base_unchanged") is True)
    if kind == "blitz_scale":
        return run.get("retained_base_unchanged") is True and run.get("protected_catalog_unchanged") is True
    cleanup = run.get("cleanup", {})
    return (not any(value for key, value in cleanup.items() if key.endswith("error") or key == "pending_resume_preserved")
            and bool(cleanup.get("container_removed") or cleanup.get("container_removed_before_release"))
            and run.get("protected_metadata_unchanged") is True and run.get("physical_hbm_reclaimed") is True)


def native_sha(inputs, source, case):
    directory = inputs.path(source).parent
    expected = inputs.read(directory / "expected.json", optional=True)
    checked = 0
    details = []
    if expected is None:
        return {"passed": False, "checked_bytes": 0, "reason": "missing_expected"}
    for rank in range(2):
        actual = inputs.read(directory / f"fingerprint_rank{rank}.json", optional=True)
        frozen = expected.get("ranks", {}).get(str(rank))
        if actual is None or frozen is None:
            return {"passed": False, "checked_bytes": checked, "reason": "missing_rank_fingerprint"}
        count = actual.get("checked_bytes", 0)
        valid = (actual.get("passed") is True and actual.get("layout_passed") is True and actual.get("layout") == frozen.get("layout")
                 and actual.get("content_sha256") == frozen.get("content_sha256") and bool(actual.get("content_sha256"))
                 and count == frozen.get("bytes") == sum(row["bytes"] for row in actual.get("storages", [])))
        details.append({"rank": rank, "passed": valid, "checked_bytes": count, "sha256": actual.get("content_sha256")})
        checked += count
    return {"passed": all(row["passed"] for row in details) and checked == 70329718112, "checked_bytes": checked, "ranks": details}


def transfer_sha(case):
    verification = case.get("verification") or case.get("flow", {}).get("verification", {})
    ops = case.get("flow", {}).get("plan", {}).get("ops", [])
    rows = verification.get("operations", {})
    checked = sum(op["n"] for op in ops)
    valid = bool(ops) and len(rows) == len(ops) and verification.get("passed") is True and verification.get("checked_bytes") == checked
    for op in ops:
        row = rows.get(str(op["op_id"]), {})
        valid = valid and row.get("n") == op["n"] and bool(row.get("source_sha256")) and row.get("source_sha256") == row.get("target_sha256")
    return {"passed": bool(valid) and checked == 70329718112, "checked_bytes": checked, "operations": rows}


def store_sha(run):
    worker = run.get("worker_result", {})
    rows = worker.get("ranks", [])
    checked = sum(row.get("logical_bytes", 0) for row in rows)
    frozen = run.get("frozen_rank_sha256", {})
    passed = (worker.get("status") == "passed" and sorted(row.get("tp_rank", -1) for row in rows) == [0, 1]
              and all(row.get("full_sha256_equal") is True and bool(row.get("source_sha256"))
                      and row.get("source_sha256") == row.get("target_sha256") == frozen.get(str(row.get("tp_rank"))) for row in rows)
              and checked == 70329718112)
    return {"passed": passed, "checked_bytes": checked, "ranks": [{key: row.get(key) for key in
            ("tp_rank", "logical_bytes", "source_sha256", "target_sha256", "full_sha256_equal")} for row in rows]}


def cache_evidence(inputs, source, case, kind, run):
    if kind != "sglang_vendor_native":
        runtime = run.get("worker_result", {}).get("runtime_cache_state", run.get("pool_setup", {}).get("runtime_cache_state"))
        return {"label": "active_HBM" if kind == "blitz_scale" else runtime or "prepared_newly_written_runtime_file_residency_unmeasured",
                "runtime_file_residency_measured": False, "prepared_label": run.get("format_setup", {}).get("cache_state"),
                "reported_runtime_cache_state": runtime,
                "scope": "HBM实际源" if kind == "blitz_scale" else "准备阶段写入状态；加载前Linux/NFS cache不能从该描述推断"}
    directory = inputs.path(source).parent
    values = []
    for rank in range(2):
        for moment in ("before", "after"):
            row = inputs.read(directory / f"cache_rank{rank}_{moment}.json", optional=True)
            if row:
                values.append(dict(rank=rank, moment=moment, **{key: row.get(key) for key in
                    ("resident_fraction", "sampled_pages", "resident_pages", "checkpoint_bytes", "wall_time")}))
    return {"label": case.get("cache_state"), "runtime_file_residency_measured": bool(values), "samples": values,
            "scope": "Linux mincore抽样；NFS/storage cache未认证；不是显式pinned-host baseline"}


def performance_row(inputs, source, run, case, kind, index=0):
    primary = case.get("primary_T_ready_s", case.get("timing", {}).get("load_s"))
    timing = clock_proof(case.get("timing", {}), primary)
    verification = native_sha(inputs, source, case) if kind == "sglang_vendor_native" else transfer_sha(case) if kind == "blitz_scale" else store_sha(run)
    correct = case.get("correct") is True if kind != "serverless_store_hip" else run.get("text_correct") is True
    cleaned = cleanup_proof(run, kind)
    status_passed = run.get("status") == "passed" and case.get("status", run.get("status")) == "passed"
    accepted = status_passed and timing["passed"] and verification["passed"] and correct and cleaned
    if kind == "serverless_store_hip":
        accepted = accepted and run.get("worker_exit_code") == 0 and run.get("code_unchanged") is True
    setup = {"source_active_owner_setup_s": run.get("active_setup", {}).get("wall_s"),
             "store_pool_setup_s": run.get("pool_setup", {}).get("pool_setup_s"),
             "format_setup_s": sum(row.get("format_setup_s", 0) for row in run.get("format_setup", {}).get("ranks", [])) or None,
             "fresh_start_to_model_info_s": case.get("fresh_start_to_model_info_s"),
             "owner_build_s": run.get("active_owner_build", {}).get("coordinator_wall_s"),
             "owner_copy_s": run.get("active_owner_build", {}).get("execute_wall_s")}
    phase = case.get("phase", str(index))
    return {"sample_id": f"{inputs.label(source)}#{phase}", "source_file": inputs.label(source), "run_id": run.get("run_id", inputs.path(source).parent.name),
            "scenario_id": run.get("scenario_id", run.get("scene_id", "S2" if kind != "blitz_scale" else "S3")),
            "method_id": kind, "identity": IDENTITIES[kind], "run_status": run.get("status"),
            "measurement_status": "new_measurement" if accepted else "observed_unaccepted" if primary is not None else run.get("status", "unmeasured"),
            "accepted": accepted, "new_sample_count": int(accepted), "observed_sample_count": int(primary is not None),
            "metrics": {"T_ready_s": primary if accepted else None, "observed_T_ready_s": primary,
                "request_to_ready_ack_s": case.get("request_to_ready_ack_s"),
                "request_to_first_correct_token_s": case.get("request_to_first_correct_token_s", run.get("request_to_first_correct_token_s")),
                "post_ready_verification_s": case.get("post_ready_verification_s", sum(row.get("verification_s", 0) for row in run.get("worker_result", {}).get("ranks", [])) or None),
                "execute_s": case.get("flow", {}).get("execute_wall_s"), "prepare_s": case.get("flow", {}).get("prepare_s"),
                "online_planning_s": case.get("online_planning_s"), "store_window_s": run.get("worker_result", {}).get("store_window_s")},
            "logical_bytes": verification["checked_bytes"], "proof": {"clock": timing, "verification": verification, "text_correct": correct, "cleanup_complete": cleaned},
            "cache": cache_evidence(inputs, source, case, kind, run), "setup": setup,
            "metric_scopes": {"T_ready_s": "same-host weights load window; excludes post-ready full SHA",
                "request_to_first_correct_token_s": "fresh process start request to first correct token; includes independent post-ready full SHA" if kind == "sglang_vendor_native" else "restore request to first correct token",
                "fresh_start_to_model_info_s": "fresh process start request to get_model_info acknowledgment; separately recorded startup boundary" if kind == "sglang_vendor_native" else None},
            "format_contract": run.get("format_contract"), "session_sha256": run.get("session_sha256"),
            "source_budget": {"protected_fixture_dram_logical_bytes": run.get("protocol", {}).get("fixture_dram_bytes", 70329718112),
                "fixture_required_by_restore_path": False, "fixture_used_for_owner_setup": kind == "blitz_scale",
                "fixture_scope": "受保护测试底本；native/Store不从它读取；Blitz仅在owner建立时读取，恢复使用额外active HBM；底本不视为热恢复路径算法必需驻留",
                "configured_store_pinned_pool_bytes": run.get("worker_result", {}).get("pool_bytes", run.get("pool_setup", {}).get("pool_bytes")),
                "extra_active_owner_hbm_bytes": 70329718112 if kind == "blitz_scale" else 0,
                "native_file_cache_dram_bytes": None, "native_cache_scope": "mincore抽样驻留率不是全量DRAM占用或峰值",
                "target_weight_hbm_logical_bytes": 70329718112,
                "scope": "已配置路径资源与测试fixture分开；不推断算法最小驻留、runtime/KV/workspace或完整物理峰值"}}


def kind_for(run, path):
    name = Path(path).parent.name
    if name.startswith("native_") and "cpu" not in name:
        return "sglang_vendor_native"
    if name.startswith("blitz_") and "cpu" not in name:
        return "blitz_scale"
    if name.startswith("serverless_"):
        return "serverless_store_hip"
    return None


def attempt(inputs, path):
    run = inputs.read(path)
    kind = kind_for(run, path)
    functional = "not a Store performance sample" in run.get("scope", "")
    diagnostic = Path(path).parent.name.startswith("serverless_hash_")
    support = run.get("mode") == "gate" or "format" in Path(path).parent.name or "cpu" in Path(path).parent.name or functional or diagnostic
    rows = []
    if kind and not support:
        if kind == "serverless_store_hip" and run.get("mode") == "full" and run.get("primary_T_ready_s") is not None:
            rows.append(performance_row(inputs, path, run, run, kind))
        else:
            for index, case in enumerate(run.get("cases", [])):
                rows.append(performance_row(inputs, path, run, case, kind, index))
    category = ("diagnostic_non_performance" if diagnostic else "functional_setup" if functional else "capability_gate" if run.get("mode") == "gate" else "format_setup" if "format" in Path(path).parent.name else
                "performance" if rows else "performance_pending" if run.get("mode") == "full" and run.get("status") == "running" else
                "failed_without_ready_measurement" if run.get("mode") == "full" and run.get("status") == "failed" else
                "unmeasured_full" if run.get("mode") == "full" else
                "failed_before_measurement" if run.get("status") == "failed" and kind else "CPU_or_registration")
    return {"run_id": run.get("run_id", Path(path).parent.name), "source_file": inputs.label(path), "status": run.get("status", "unknown"),
            "kind": category,
            "method_id": kind, "identity": IDENTITIES.get(kind), "error": run.get("error") or
                (f"worker_exit_code={run['worker_exit_code']}" if run.get("status") == "failed" and run.get("worker_exit_code") is not None else None), "measurement_count": len(rows),
            "cleanup": run.get("cleanup", {key: run.get(key) for key in ("cleanup_errors", "owned_remaining", "unreleased_endpoints", "pending_resume_endpoints")}),
            "physical_hbm_reclaimed": run.get("physical_hbm_reclaimed"), "retained_base_unchanged": run.get("retained_base_unchanged", run.get("protected_metadata_unchanged")),
            "functional_setup_s": run.get("functional_setup_s") if functional else None, "session_overlay": run.get("session_overlay") if functional else None,
            "functional_intermediate_proof": {"full_sha": run.get("functional_restore", {}).get("verification"), "text_correct": run.get("text_correct"),
                "owned_engine_stop": run.get("owned_engine_stop"), "transaction_cleanup_error": run.get("transaction_cleanup_error")} if functional else None,
            "diagnostic_result": run.get("worker_result") if diagnostic else None,
            "gate_bytes": run.get("worker_result", {}).get("total_logical_bytes") if run.get("mode") == "gate" else None}, rows


def build(inputs, paths, previous=PREVIOUS, failure_records=None, supporting_paths=()):
    old = inputs.read(previous)
    if old.get("audit_kind") is None or not isinstance(old.get("observations"), list):
        raise ValueError("previous complete_table evidence format is required")
    attempts, measurements = [], []
    for path in sorted(set(inputs.path(p).resolve() for p in paths)):
        entry, rows = attempt(inputs, path)
        attempts.append(entry)
        measurements.extend(rows)
    if failure_records:
        recorded = inputs.read(failure_records)
        known_ids = {row["run_id"] for row in attempts}
        for row in recorded:
            if row["run_id"] in known_ids:
                continue
            attempts.append(dict(row, source_file=inputs.label(failure_records), identity=IDENTITIES.get(row.get("method_id")),
                                 measurement_count=0, from_declared_archived_failure=True))
            if row.get("evidence") and inputs.path(row["evidence"]).is_file():
                inputs.record_file(row["evidence"])
    references = []
    for row in old["observations"]:
        reference = copy.deepcopy(row)
        reference["original_measurement_status"] = reference.get("measurement_status")
        reference.update(measurement_status="prior_reference", new_sample_count=0,
                         inherited_acceptance=True, original_complete_table_source=inputs.label(previous))
        references.append(reference)
    supplements = recovery_evidence(inputs, paths)
    settled = {row["run_id"] for row in supplements if row["cleanup_verified"]}
    pending = [row["run_id"] for row in attempts if row["status"] == "running"]
    recovery = [row["run_id"] for row in attempts if (row.get("cleanup") or {}).get("pending_resume_preserved") and row["run_id"] not in settled]
    accepted = [row for row in measurements if row["accepted"]]
    fixture = latest_fixture(inputs, attempts, measurements)
    support = supporting_evidence(inputs, supporting_paths)
    return {"schema_version": 1, "status": "snapshot_with_pending" if pending or recovery else "evidence_summary_with_gaps", "development": "v0.1.23.3", "task": "task2",
            "identities": IDENTITIES, "measurements": measurements, "attempts": attempts, "prior_references": references,
            "recovery_supplements": supplements, "latest_fixture": fixture,
            "supporting_evidence": support,
            "previous_complete_table": {"source_file": inputs.label(previous), "counts": old.get("counts"), "original_system_status": old.get("original_system_status"),
                "columns": old.get("columns"), "matrix": old.get("matrix"), "preserved_without_reclassification": True},
            "counts": {"accepted_new_performance_samples": len({row["sample_id"] for row in accepted}),
                "unaccepted_observations": sum(not row["accepted"] for row in measurements),
                "prior_reference_rows": len(references), "prior_unique_samples": len({r["sample_id"] for r in references if not r.get("alias_of")}),
                "failed_attempts": sum(row["status"] == "failed" for row in attempts), "pending_performance_runs": pending, "pending_recovery_runs": recovery,
                "passed_capability_gates": sum(row["status"] == "passed" and row["kind"] == "capability_gate" for row in attempts)},
            "missing_inputs": inputs.missing, "algorithm_speedups": [], "comparisons": [],
            "rules": {"scope": "扩展既有complete_table，旧S1/S2/S3/S4/S5证据全部历史引用，不增加本轮样本",
                "acceptance": "terminal passed + full TP2 clock + full content SHA + exact output + cleanup",
                "no_speed_ratio": "启动/初始来源层级/输入格式/cache/空间预算不同；本汇总不导出速度比或算法增益",
                "gates": "小IPC gate与CPU/registration/格式准备不算Qwen性能样本",
                "remaining_unmeasured": "原版系统列/Janus原版/S5物理共享/e7/e8与原表缺口继续保持；新身份不填旧原版列"}}


def latest_fixture(inputs, attempts, measurements):
    recovery = [row for row in attempts if row["kind"] == "functional_setup" and row["status"] == "passed" and row.get("session_overlay")]
    if not recovery:
        return {"status": "no_new_overlay_recorded", "scope": "本汇总不以缺少overlay推断原现场进程状态"}
    row = recovery[-1]
    overlay_path = inputs.path(row["session_overlay"])
    owned = overlay_path.with_name("session_overlay_owned.json")
    if owned.is_file():
        overlay_path = owned
    overlay = inputs.read(overlay_path, optional=True)
    if overlay is None:
        return {"status": "missing_overlay", "source_file": row["source_file"]}
    slot = overlay["slots"]["target_b"]
    full = [case for case in measurements if case["method_id"] == "serverless_store_hip"]
    final = full[-1] if full else None
    return {"status": "parked_after_verified_full_cleanup" if final and final["accepted"] else "registered_parked_overlay_not_live_state_inference",
            "session_overlay": inputs.label(overlay_path), "recovery_source_file": row["source_file"],
            "parent_session": overlay.get("recovery_parent_session"), "target_slot": {key: slot.get(key) for key in
                ("endpoint", "workers", "command", "metrics", "parked", "ownership_run", "process_spec")},
            "latest_full_source_file": final["source_file"] if final else None,
            "scope": "新overlay单列；原session与失败summary不覆盖；实时状态仅在最后完整run通过release/cleanup时据其终态确认"}


def supporting_evidence(inputs, paths):
    values, result = {}, {"records": [], "protected_comparison": None}
    for path in paths:
        data = inputs.read(path, optional=True)
        if data is None:
            continue
        name = inputs.path(path).parent.name
        values[name] = data
        result["records"].append({"source_file": inputs.label(path), "status": data.get("status"), "read_only": data.get("read_only"),
            "service_classification": data.get("service_classification"), "retained_B": data.get("retained_B"),
            "scope": "只读现场/保护证据，非新增性能样本；旧平台known不可达不以整体failed误判新增损坏"})
    before, after = values.get("stage2_protected_after_r01"), values.get("baseline_loading_protected_after_r01")
    if before and after:
        sys.path.insert(0, str(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios"))
        from summarize_basic import compare_protected
        inputs.record_file(ROOT / "codex_coding/src/v0.1.23.3/task2/basic_scenarios/summarize_basic.py")
        result["protected_comparison"] = compare_protected(before, after)
    return result


def recovery_evidence(inputs, paths):
    """Bind separate post-failure cleanup logs without rewriting original attempts."""
    directories = {inputs.path(path).parent.parent for path in paths}
    result = []
    for root in directories:
        for directory in sorted(root.glob("*_cleanup")):
            identity_path, removal_path = directory / "identity_before.txt", directory / "remove.txt"
            if not identity_path.is_file() or not removal_path.is_file():
                continue
            inputs.record_file(identity_path)
            inputs.record_file(removal_path)
            content, removed = identity_path.read_text(), removal_path.read_text().strip()
            try:
                labels, _ = json.JSONDecoder().raw_decode(content.lstrip())
            except json.JSONDecodeError:
                result.append({"run_id": directory.name, "cleanup_verified": False, "reason": "invalid_ownership_log", "source_file": inputs.label(identity_path)})
                continue
            run_id = labels.get("fluidckpt.serverless_run")
            source = root / str(run_id) / "summary.json"
            run = inputs.read(source, optional=True)
            matched = run is not None and bool(removed) and removed == run.get("container")
            workers_present = "fluidckpt.cluster.worker" in content
            producer_absent = content.count("COMMAND") >= 2 and "sglang_fluid_launch.py" not in content
            result.append({"run_id": run_id, "source_file": inputs.label(directory), "cleanup_verified": matched and workers_present and producer_absent,
                "ownership_label": labels.get("fluidckpt.serverless_run"), "adapter_remove_stdout": removed,
                "adapter_name_matches_failed_run": matched, "fixture_process_listing_has_keepers": workers_present,
                "fixture_process_listing_has_no_engine": producer_absent, "historical_attempt_unchanged": True,
                "scope": "后续operator清理记录：所有目标exporter退出后删除仅自有adapter；旧pending为历史状态，底本keeper保留；未推造原run的release ACK"})
    return result


def escape(value):
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def display_time(value):
    return f"{value:.9f}" if isinstance(value, (int, float)) else "—"


def markdown(data):
    text = ["# Baseline loading 完整证据扩展", "", "本表保留原 complete_table 的事实和原版未测列；以下三身份有不同初始来源、输入格式与启动条件。仅作逐观察描述，不计算速度比或算法增益。", "",
        f"本轮已验收性能样本 {data['counts']['accepted_new_performance_samples']}；失败尝试 {data['counts']['failed_attempts']}；既有引用行 {data['counts']['prior_reference_rows']}（本轮新增计数为0）。", "",
        "## 本轮性能观察", "", "| 身份 / run | 终态 / 验收 | 初始目标与来源 | 格式 / cache | T_ready(s) | 首正确token(s) | setup(s) | 全SHA / 输出 / cleanup |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- |"]
    for row in data["measurements"]:
        identity = row["identity"]
        proof = row["proof"]
        setup = "; ".join(f"{key}={display_time(value)}" for key, value in row["setup"].items() if value is not None)
        clock = row["metrics"]["T_ready_s"] if row["accepted"] else row["metrics"]["observed_T_ready_s"]
        label = display_time(clock) + ("（未验收观察）" if not row["accepted"] and clock is not None else "")
        text.append("| " + " | ".join(escape(x) for x in (identity["name"] + "/" + row["run_id"], row["run_status"] + ("/通过" if row["accepted"] else "/未验收"),
            identity["initial_target"] + "; " + identity["source_tier"], identity["input_format"] + "; " + str(row["cache"]["label"]), label,
            display_time(row["metrics"]["request_to_first_correct_token_s"]), setup,
            f"{proof['verification']['passed']} / {proof['text_correct']} / {proof['cleanup_complete']}")) + " |")
    text += ["", "## 路径资源与受保护fixture分别计账", "", "| 身份 / run | 路径配置的额外active HBM(GiB) | Store pinned pool(GiB) | 另保留fixture DRAM(GiB) | 计账边界 |",
             "| --- | ---: | ---: | ---: | --- |"]
    for row in data["measurements"]:
        budget = row["source_budget"]
        pool = budget["configured_store_pinned_pool_bytes"]
        text.append("| " + " | ".join(escape(x) for x in (row["identity"]["name"] + "/" + row["run_id"],
            budget["extra_active_owner_hbm_bytes"] / (1 << 30), pool / (1 << 30) if pool is not None else "—",
            budget["protected_fixture_dram_logical_bytes"] / (1 << 30), budget["fixture_scope"])) + " |")
    text += ["", "native文件cache的DRAM字节/峰值未测；各目标完整权重HBM与KV/runtime另保留。上表是路径实用配置与测试fixture计账，不是算法最低驻留证明。", ""]
    for row in data["measurements"]:
        if row["method_id"] == "sglang_vendor_native":
            text += ["", "native启动请求→首正确token **" + display_time(row["metrics"]["request_to_first_correct_token_s"]) + " s** 包含ready后的独立全量SHA验证（" + display_time(row["metrics"]["post_ready_verification_s"]) + " s）；fresh启动请求→get_model_info ACK **" + display_time(row["setup"]["fresh_start_to_model_info_s"]) + " s** 是单列的启动边界。正式T_ready不含ready后SHA。", ""]
    text += ["", "Blitz owner_build_s是完整建立的coordinator窗口，owner_copy_s仅为其中copy阶段；两者不相加，总active source setup另外记录。源owner建立在恢复窗口外；Store转换和pinned pool建立单列。验证开销见JSON/CSV。", "",
             "## 每次尝试、gate与失败", "", "| run | 身份 | 类别 | 状态 | 错误 | 原始证据 |", "| --- | --- | --- | --- | --- | --- |"]
    for row in data["attempts"]:
        text.append("| " + " | ".join(escape(x) for x in (row["run_id"], (row["identity"] or {}).get("name"), row["kind"], row["status"], row["error"], row["source_file"])) + " |")
    if data.get("recovery_supplements"):
        text += ["", "## 失败后的独立清理 / 当前现场", "", "原尝试中的pending字段保留为当时状态，以下是后续补充，既不抹掉失败也不把已清理资源继续标为pending。", "",
                 "| 原run | 后续清理确认 | exporter进程列表无engine | keeper保留 | 补充来源 |", "| --- | --- | --- | --- | --- |"]
        for row in data["recovery_supplements"]:
            text.append("| " + " | ".join(escape(row.get(key)) for key in ("run_id", "cleanup_verified", "fixture_process_listing_has_no_engine", "fixture_process_listing_has_keepers", "source_file")) + " |")
    if data.get("latest_fixture", {}).get("session_overlay"):
        fixture = data["latest_fixture"]
        text += ["", "最新登记现场：`" + fixture["session_overlay"] + "`，状态 `" + fixture["status"] + "`。原session和失败记录保留；新target command/process ownership由overlay登记，functional recovery不增加Store性能样本。", ""]
    support = data.get("supporting_evidence", {})
    if support.get("records"):
        text += ["", "## 最终fixture与原protected集合", "", "| 支持证据 | 原扫描状态 | 现场服务计数 |", "| --- | --- | --- |"]
        for row in support["records"]:
            text.append("| " + " | ".join(escape(value) for value in (row["source_file"], row["status"], json.dumps(row.get("service_classification"), ensure_ascii=False))) + " |")
        comparison = support.get("protected_comparison")
        if comparison:
            text += ["", f"原protected集合metadata unchanged={comparison['metadata_unchanged']}；可达keeper {len(comparison['after_live_workers'])}，VALID replicas {comparison['after_valid_replica_count']}；不可达集合 unchanged={comparison['unavailable_set_unchanged']}，原已知不可达 {comparison['after_unavailable_workers']}。这里只比较目录/epoch/session，未重新hash旧模型内容。", ""]
    text += ["", "## 既有S1–S5历史引用", "", "以下保留先前验收/别名/未测状态，全部为本汇总的旧证据引用，不构成本轮新增实测。小IPC hit沿用owner全SHA门槛，未冒称每hit重新全hash。", "",
             "| 场景 | 方法 / run | 原状态 | 旧验收 | T_ready(s) | SHA / 输出 / cleanup | 原始来源 |", "| --- | --- | --- | --- | ---: | --- | --- |"]
    for row in data["prior_references"]:
        proof = row.get("proof", {})
        text.append("| " + " | ".join(escape(x) for x in (row.get("matrix_row", row["scenario_id"]), str(row["method_id"]) + "/" + str(row["run_id"]),
            row["original_measurement_status"], row.get("accepted"), display_time(row.get("metrics", {}).get("T_ready_s")),
            f"{proof.get('full_sha_passed')} / {proof.get('text_correct')} / {proof.get('cleanup_complete')}", row.get("source_file"))) + " |")
    matrix = data["previous_complete_table"].get("matrix") or []
    if matrix:
        methods = list(matrix[0].get("cells", {}))
        text += ["", "## 原完整表状态矩阵（全部历史引用）", "", "| 场景 | " + " | ".join(methods) + " |", "| --- | " + " | ".join("---" for _ in methods) + " |"]
        for row in matrix:
            text.append("| " + escape(row.get("row_id")) + " | " + " | ".join("引用/" + escape(row.get("cells", {}).get(method, {}).get("status", "missing")) for method in methods) + " |")
    text += ["", "## 身份与剩余缺口", ""]
    text += [f"- {item['name']}：{item['scope']}。" for item in IDENTITIES.values()]
    text += ["- 原版系统未测、S5-P物理共享、e7/e8与既有表其他缺口保留；新身份数据不填入旧原版列。",
             "- Store cache仅有prepared新写入证据时，加载前驻留未测；native的mincore也是抽样而非全页/NFS server cache证明。",
             "- 尚未终态passed的JSON只显示pending/未验收；不能用小gate时间或source建立时间代替完整模型恢复窗口。", ""]
    return "\n".join(text)


def write_outputs(data, inputs, out):
    out = Path(out).resolve()
    if BASE.resolve() not in out.parents:
        raise ValueError("output must be below versioned baseline_loading result root")
    inputs.verify()
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("summary.json", data), ("inputs_manifest.json", inputs.manifest)):
        (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    (out / "report.md").write_text(markdown(data))
    columns = ["evidence_class", "sample_id", "run_id", "scenario_id", "method_id", "identity", "measurement_status", "accepted", "T_ready_s", "observed_T_ready_s",
               "first_correct_token_s", "post_ready_verification_s", "initial_target", "source_tier", "input_format", "cache_state", "resources_json", "setup_json", "proof_json", "source_file", "new_sample_count"]
    with (out / "measurements.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in data["measurements"] + data["prior_references"]:
            old = row.get("measurement_status") == "prior_reference"
            identity = row.get("identity") or {}
            if not isinstance(identity, dict):
                identity = {"identity": identity}
            metrics = row.get("metrics", {})
            writer.writerow({"evidence_class": "prior_reference" if old else "current", "sample_id": row["sample_id"], "run_id": row["run_id"],
                "scenario_id": row["scenario_id"], "method_id": row["method_id"], "identity": identity.get("identity"), "measurement_status": row["measurement_status"],
                "accepted": row["accepted"], "T_ready_s": metrics.get("T_ready_s"), "observed_T_ready_s": metrics.get("observed_T_ready_s"),
                "first_correct_token_s": metrics.get("request_to_first_correct_token_s"), "post_ready_verification_s": metrics.get("post_ready_verification_s"),
                "initial_target": identity.get("initial_target"), "source_tier": identity.get("source_tier"), "input_format": identity.get("input_format"),
                "cache_state": row.get("cache", {}).get("label"), "setup_json": json.dumps(row.get("setup"), ensure_ascii=False),
                "resources_json": json.dumps(row.get("source_budget", row.get("budget")), ensure_ascii=False),
                "proof_json": json.dumps(row.get("proof"), ensure_ascii=False), "source_file": row["source_file"], "new_sample_count": row.get("new_sample_count", 0)})
    with (out / "attempts.csv").open("w", newline="") as stream:
        columns = ["run_id", "method_id", "kind", "status", "error", "measurement_count", "gate_bytes", "source_file"]
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data["attempts"])
    with (out / "prior_matrix.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["row_id", "scenario_id", "method_id", "evidence_class", "original_status", "cell_json"])
        writer.writeheader()
        for row in data["previous_complete_table"].get("matrix") or []:
            for method, cell in row.get("cells", {}).items():
                writer.writerow({"row_id": row["row_id"], "scenario_id": row.get("scenario_id"), "method_id": method,
                    "evidence_class": "prior_reference", "original_status": cell.get("status"), "cell_json": json.dumps(cell, ensure_ascii=False)})
    with (out / "recovery.csv").open("w", newline="") as stream:
        columns = ["run_id", "cleanup_verified", "adapter_name_matches_failed_run", "fixture_process_listing_has_no_engine",
                   "fixture_process_listing_has_keepers", "historical_attempt_unchanged", "source_file", "scope"]
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data.get("recovery_supplements", []))
    inputs.verify()


def discover(root):
    paths = []
    for directory in sorted(Path(root).iterdir()):
        if not directory.is_dir() or not directory.name.startswith(("native_", "blitz_", "serverless_")):
            continue
        for name in ("summary.json", "prepared_data.json", "result.json"):
            path = directory / name
            if path.is_file():
                paths.append(path)
                break
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, action="append", default=[])
    p.add_argument("--attempts-root", type=Path, default=BASE)
    p.add_argument("--previous-table", type=Path, default=PREVIOUS)
    p.add_argument("--failure-records", type=Path, default=BASE / "summary_cpu_r01/known_failures.json")
    p.add_argument("--support", type=Path, action="append")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    inputs = Inputs()
    data = build(inputs, a.input or discover(a.attempts_root), a.previous_table,
                 a.failure_records if a.failure_records.is_file() else None, a.support if a.support is not None else DEFAULT_SUPPORT)
    write_outputs(data, inputs, a.out)
    print(json.dumps({"status": data["status"], "counts": data["counts"], "out": str(a.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
