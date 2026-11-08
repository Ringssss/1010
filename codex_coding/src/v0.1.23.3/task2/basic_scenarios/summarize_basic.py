"""Offline summary of explicit basic-scenario runs; preserve evidence identities."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/basic_scenarios"
VERSION = "v0.1.23.3/task2"


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ordered_signature(ops, source_roles=None):
    """Compare content and execution order while excluding phase and replica IDs."""
    result = []
    source_roles = source_roles or {}
    for op in ops:
        path = op.get("path", "").split(":")
        tier = path[1] if len(path) > 1 else op.get("source_tier")
        result.append(tuple(op.get(k) for k in ("identity", "tp", "off", "n", "src_w", "dst_w", "kind"))
                      + (op.get("source_offset"), tier,
                         op.get("source_role", source_roles.get(op.get("replica_id"), "unknown"))))
    return tuple(result)


def classify(data):
    if data.get("input_record") == "failure.json":
        return "failure_record"
    if data.get("evidence_class") == "historical_reference" or data.get("audit_kind") == "offline_read_only":
        return "historical_reference"
    if data.get("measurement_status") == "bootstrap_only" or data.get("owned_namespace"):
        return "bootstrap"
    if data.get("scenario_id", data.get("scene_id")) in ("S2", "S3"):
        return "scenario"
    if data.get("read_only") is True or "endpoint_probes" in data:
        return "preflight"
    if "operations" in data:
        return "cleanup"
    if "tests_run" in data or "tests_passed" in data:
        return "cpu_validation"
    return "registration_or_other"


def clean_state(data):
    return not any(data.get(k) for k in ("cleanup_errors", "remaining_owned_ids", "unreleased_endpoints",
                   "pending_resume_endpoints", "remaining_owned_processes", "remaining_owned_containers",
                   "source_hash_mismatches"))


def compare_protected(before, after):
    """Compare VALID directory metadata without reading or hashing model content."""
    def snapshot(data):
        live, unavailable = {}, []
        for probe in data["endpoint_probes"]:
            if probe["status"] != "passed":
                unavailable.append(probe["worker"])
                continue
            inventory = probe["inventory"]
            worker = inventory["worker"]
            if worker != probe["worker"] or worker in live:
                raise ValueError("protected probe worker identity mismatch")
            rows = []
            required = ("id", "identity", "tp", "off", "n", "tier", "generation", "state", "leases", "allocated_bytes", "epoch", "worker")
            optional = ("node", "gpu", "numa")
            for row in inventory["replicas"]:
                if row["state"] != "VALID":
                    continue
                if any(key not in row for key in required):
                    raise ValueError("protected VALID replica metadata incomplete")
                rows.append({key: row.get(key) for key in (*required, *optional)})
            if len({r["id"] for r in rows}) != len(rows):
                raise ValueError("protected VALID replica ID duplicated")
            live[worker] = {key: inventory.get(key) for key in (
                "worker", "node", "gpu", "numa", "epoch", "session", "pid", "budget_bytes", "used_bytes",
                "staging_bytes", "transactions", "transaction_phases")}
            live[worker]["valid_replicas"] = sorted(rows, key=lambda r: r["id"])
        return live, sorted(unavailable)
    first, first_unavailable = snapshot(before)
    last, last_unavailable = snapshot(after)
    mismatches = [worker for worker in sorted(set(first) | set(last)) if first.get(worker) != last.get(worker)]
    result = {"scope": "accessible original keepers: epoch/session and VALID replica directory metadata only; no content rehash",
              "content_rehashed": False, "hardware_headroom_excluded": True,
              "before_live_workers": sorted(first), "after_live_workers": sorted(last),
              "before_unavailable_workers": first_unavailable, "after_unavailable_workers": last_unavailable,
              "unavailable_set_unchanged": first_unavailable == last_unavailable,
              "mismatched_workers": mismatches, "metadata_unchanged": not mismatches and first_unavailable == last_unavailable,
              "before_valid_replica_count": sum(len(w["valid_replicas"]) for w in first.values()),
              "after_valid_replica_count": sum(len(w["valid_replicas"]) for w in last.values()),
              "before_valid_allocated_bytes": sum(r["allocated_bytes"] for w in first.values() for r in w["valid_replicas"]),
              "after_valid_allocated_bytes": sum(r["allocated_bytes"] for w in last.values() for r in w["valid_replicas"]),
              "workers": [{"worker": w, "node": first[w]["node"], "valid_replicas": len(first[w]["valid_replicas"]),
                           "metadata_unchanged": first[w] == last.get(w)} for w in sorted(first)]}
    return result


def extract_case(case, data, run_kind, run_id, index):
    flow = case.get("flow") or {}
    plan = flow.get("plan") or {}
    verification = case.get("verification") or flow.get("verification") or {}
    ops = plan.get("ops", [])
    expected = sum(o["n"] for o in ops) if ops else None
    verified = (verification.get("passed") is True and expected is not None
                and verification.get("checked_bytes") == expected)
    methods = data.get("methods", {})
    method = case.get("method_id", case.get("routing", "unspecified"))
    method_meta = methods.get(method, {})
    assessment = case.get("assessment") or case.get("pre_resume_admission") or {}
    source_tiers, source_workers, role_bytes = Counter(), Counter(), Counter()
    ledger = assessment.get("resource_ledger", {})
    roles = {r["allocation_id"]: r["role"] for r in ledger.get("allocations", [])}
    for op in ops:
        source_workers[str(op["src_w"])] += op["n"]
        parts = op.get("path", "").split(":")
        source_tiers[parts[1] if len(parts) > 1 else "unknown"] += op["n"]
        role_bytes[op.get("source_role", roles.get(op.get("replica_id"), "unknown"))] += op["n"]
    signature = ordered_signature(ops, roles)
    worker_nodes = {w["worker"]: w["node"] for sample in data.get("memory_samples", [])
                    for w in sample.get("workers", []) if "worker" in w and "node" in w}
    source_nodes = sorted({worker_nodes[o["src_w"]] for o in ops if o["src_w"] in worker_nodes})
    target_nodes = sorted({worker_nodes[o["dst_w"]] for o in ops if o["dst_w"] in worker_nodes})
    primary = case.get("primary_T_ready_s", (case.get("timing") or {}).get("load_s", case.get("ready_s")))
    runtime_case = run_kind == "scenario" and bool(ops) and primary is not None
    accepted = (runtime_case and data.get("status") == "passed" and case.get("status") == "passed"
                and case.get("correct") is True and verified and clean_state(data))
    return {
        "run": run_id, "case_index": index, "phase": case.get("phase"),
        "scenario_id": data.get("scenario_id", data.get("scene_id")),
        "method_id": method, "method_identity": case.get("adaptation_identity", method_meta.get("identity", data.get("adaptation_identity"))),
        "upstream": method_meta.get("upstream"), "status": case.get("status", "unknown"),
        "measurement_status": case.get("measurement_status", data.get("measurement_status")),
        "historical_reference": run_kind == "historical_reference", "runtime_observation": runtime_case,
        "accepted_correct_restore": accepted, "primary_T_ready_s": primary,
        "request_to_ready_ack_s": case.get("request_to_ready_ack_s"),
        "request_to_first_correct_token_s": case.get("request_to_first_correct_token_s"),
        "prepare_s": flow.get("prepare_s"), "execute_s": flow.get("execute_wall_s"),
        "publish_s": flow.get("verify_and_publish_s"), "online_planning_s": case.get("online_planning_s", plan.get("planning_s")),
        "post_ready_verification_s": case.get("post_ready_verification_s"),
        "source_bytes_by_replica": flow.get("source_bytes", {}), "source_bytes_by_worker": dict(source_workers),
        "source_bytes_by_tier": dict(source_tiers), "logical_bytes": expected,
        "source_bytes_by_role": dict(role_bytes),
        "runtime_scope": {"source_nodes": source_nodes, "target_nodes": target_nodes,
                          "target_slot": data.get("_target_slot"),
                          "locality": "local_host" if source_nodes and source_nodes == target_nodes else "cross_node" if source_nodes and target_nodes else "unknown",
                          "comparison_scope": "methods within this scenario only; S2/S3 use different target locations"},
        "sha_passed": verification.get("passed"), "sha_checked_bytes": verification.get("checked_bytes"),
        "full_sha_passed": verified, "text_correct": case.get("correct"), "generated_text": (case.get("reply") or {}).get("text"),
        "source_mode": assessment.get("source_mode"), "resource_ledger": ledger,
        "active_source_before_correct": (data.get("active_setup", {}).get("before_reply", {}).get("text") is not None)
            if data.get("active_setup") else None,
        "active_after_reply": case.get("active_after_reply"),
        "active_survival_evidence": (case.get("post_copy_assessment") or {}).get("active_source_evidence"),
        "foreground_evidence_scope": "source before/after correctness; no sustained interference measurement"
            if data.get("scenario_id", data.get("scene_id")) == "S3" else None,
        "ordered_operation_count": len(ops),
        "ordered_operation_sha256": hashlib.sha256(json.dumps(signature).encode()).hexdigest() if ops else None,
        "error": case.get("error"),
    }


def normalize_run(data, run_id):
    kind = classify(data)
    cases = data.get("cases", []) if kind == "scenario" else []
    observations = [extract_case(c, data, kind, run_id, i) for i, c in enumerate(cases)]
    groups = defaultdict(list)
    for row in observations:
        if row["ordered_operation_sha256"]:
            groups[row["ordered_operation_sha256"]].append(row)
    equivalences = []
    for signature, rows in groups.items():
        methods = sorted({row["method_id"] for row in rows})
        if len(methods) < 2:
            continue
        equivalences.append({"operation_sha256": signature, "methods": methods,
                             "phases": [row["phase"] for row in rows],
                             "conclusion": "same_ordered_decision_not_algorithm_gain",
                             "timing_scope": "independent restores may include different online planning costs; no routing speedup claim"})
    bootstrap = None
    if kind == "bootstrap":
        captures = data.get("capture_dram", {})
        target = data.get("target_fresh_restore", {})
        flow = target.get("flow", {})
        native = data.get("native_reference") or {}
        target_reply = target.get("reply") or {}
        bootstrap = {"native_text": native.get("text"), "target_text": target_reply.get("text"),
            "native_target_text_equal": bool(native) and bool(target_reply) and native.get("text") == target_reply.get("text"),
            "old_reference_matches": data.get("old_reference_matches"),
            "capture_checks": captures.get("verification"), "target_verification": flow.get("verification"),
            "native_load_and_reference_s": data.get("native_load_and_reference_s"),
            "capture_dram_s": data.get("capture_dram_s"), "target_fresh_restore_s": data.get("target_fresh_restore_s"),
            "target_timing": target.get("timing"), "baseline_performance_samples": 0,
            "profile": data.get("profile"), "scope": "environment bootstrap/capability gate only"}
    errors = data.get("errors", [])
    active_setup = data.get("active_setup") or {}
    owner_flow = data.get("active_owner_build") or {}
    active_costs = [{"method_id": row["method_id"], "phase": row["phase"],
                    "evidence": row["active_survival_evidence"]} for row in observations
                   if row["active_survival_evidence"]]
    return {"run": run_id, "kind": kind, "status": data.get("status", "unknown"),
        "input_record": data.get("input_record", "summary.json"),
        "measurement_status": data.get("measurement_status"), "development": data.get("development"),
        "experiment_id": data.get("experiment_id"), "gate_kind": data.get("gate_kind"),
        "adaptation_identity": data.get("adaptation_identity"), "error": data.get("error"),
        "errors": errors if isinstance(errors, list) else [],
        "test_errors": errors if isinstance(errors, int) else None, "test_failures": data.get("failures"),
        "tests_run": data.get("tests_run"), "tests_passed": data.get("tests_passed"),
        "service_classification": data.get("service_classification"),
        "retained_B": data.get("retained_B"), "partial_retained_B": data.get("partial_retained_B"),
        "cleanup_errors": data.get("cleanup_errors", []), "cleanup_complete": clean_state(data),
        "remaining_owned_ids": data.get("remaining_owned_ids", []), "unreleased_endpoints": data.get("unreleased_endpoints", []),
        "pending_resume_endpoints": data.get("pending_resume_endpoints", []),
        "remaining_owned_processes": data.get("remaining_owned_processes", []),
        "remaining_owned_containers": data.get("remaining_owned_containers", []),
        "retained_base_unchanged": data.get("retained_base_unchanged"),
        "protected_catalog_unchanged": data.get("protected_catalog_unchanged"),
        "cleanup_physical_ledger": data.get("cleanup_physical_ledger"), "source_hash_mismatches": data.get("source_hash_mismatches", []),
        "bootstrap": bootstrap, "cases": observations, "equivalent_decisions": equivalences,
        "active_setup": {"wall_s": active_setup.get("wall_s"),
                         "source_IPC_ready_s": (active_setup.get("timing") or {}).get("load_s"),
                         "owner_prepare_s": owner_flow.get("prepare_s"),
                         "owner_execute_s": owner_flow.get("execute_wall_s"),
                         "owner_verify_publish_s": owner_flow.get("verify_and_publish_s"),
                         "scope": "shared active-source establishment only; not an independent S1 baseline performance sample"}
            if active_setup else None,
        "active_cost_snapshots": active_costs,
        "active_cost_scope": "One source shared across methods. additional_lifetime_s is cumulative from active_started after initial correct source generation; HBM byte-seconds are cumulative from owner_started. No independent per-method cost subtraction or invented cleanup tail."
            if active_costs else None,
        "accepted_new_restore_count": sum(row["accepted_correct_restore"] for row in observations),
        "observed_new_restore_count": sum(row["runtime_observation"] for row in observations),
        "historical_new_samples": 0 if kind == "historical_reference" else None,
        "historical_evidence_summary": {key: data.get(key) for key in (
            "historical_hashes_unchanged", "historical_independent_observations", "e3_difference", "scenarios")}
            if kind == "historical_reference" else None}


def render(summary):
    lines = ["# 基础场景汇总", "", f"开发编号：{VERSION}。输入run保持原身份与状态。CPU验证、前检、历史引用和bootstrap不计入新增baseline性能样本。", "",
        "| run | 类型 | 状态 | 新实测/验收通过 | 失败或清理问题 |", "| --- | --- | --- | ---: | --- |"]
    for run in summary["runs"]:
        problem = run["error"] or "; ".join(map(str, run["errors"] + run["cleanup_errors"])) or "—"
        problem = problem.replace("|", "/").replace("\n", " ")[:240]
        lines.append(f'| {Path(run["run"]).name} | {run["kind"]} | {run["status"]} | {run["observed_new_restore_count"]}/{run["accepted_new_restore_count"]} | {problem} |')
    lines.extend(["", "| 场景/方法/run | 状态 | T_ready(s) | 协调确认(s) | prepare/execute(s) | planning/postverify(s) | 完整SHA/文本 |", "| --- | --- | ---: | ---: | --- | --- | --- |"])
    def number(value):
        return "—" if value is None else f"{value:.6f}"
    for run in summary["runs"]:
        for c in run["cases"]:
            lines.append(f'| {c["scenario_id"]}/{c["method_id"]}/{Path(run["run"]).name} | {c["status"]} | {number(c["primary_T_ready_s"])} | {number(c["request_to_ready_ack_s"])} | {number(c["prepare_s"])}/{number(c["execute_s"])} | {number(c["online_planning_s"])}/{number(c["post_ready_verification_s"])} | {c["full_sha_passed"]}/{c["text_correct"]} |')
    for run in summary["runs"]:
        if run["cases"]:
            scope = run["cases"][0]["runtime_scope"]
            lines.extend(["", f'{run["cases"][0]["scenario_id"]}：源{",".join(scope["source_nodes"]) or "unknown"}→目标{",".join(scope["target_nodes"]) or "unknown"}，slot={scope["target_slot"]}。'])
        for eq in run["equivalent_decisions"]:
            lines.extend(["", f'{Path(run["run"]).name}：{", ".join(eq["methods"])} 的有序操作相同；标记 same_ordered_decision_not_algorithm_gain。实测差值可以包含规划开销与单次波动，不写成算法增益。'])
    for run in summary["runs"]:
        if run["active_setup"]:
            setup = run["active_setup"]
            lines.extend(["", f'S3源建立{number(setup["wall_s"])}s，建立阶段IPC ready {number(setup["source_IPC_ready_s"])}s；这是共享源setup，不是独立S1 baseline。'])
    protected = summary.get("protected_metadata_check")
    if protected:
        lines.extend(["", f'原可访问keeper前后均为{len(protected["before_live_workers"])}个，VALID副本{protected["before_valid_replica_count"]}→{protected["after_valid_replica_count"]}，元数据一致={protected["metadata_unchanged"]}；原不可访问worker集合保持一致={protected["unavailable_set_unchanged"]}。该检查比较epoch/session及VALID id/identity/TP/区间/tier/generation/state/leases/allocated等目录字段，排除动态headroom，不是内容重新哈希。'])
    fixture = next((r for r in summary["runs"] if Path(r["run"]).name == "final_fixture_inspect_r01"), None)
    if fixture:
        b = fixture["retained_B"] or {}
        service = fixture["service_classification"] or {}
        lines.extend(["", f'最终新平台检查={fixture["status"]}，可访问keeper={service.get("live_keeper_count")}，完整Qwen DRAM来源逻辑字节={b.get("bytes")}。保留4keeper、完整65.5GiB DRAM及2个parked engine；S3新增HBM owner已清理，本汇总没有执行平台cleanup。'])
    lines.extend(["", "本轮只在同一场景内对照方法。S2是本地node27/target_a，S3是node27→node29/target_b；不能用两场景时延横向断言H2D优于D2D。同有序决策且execute近同支持功能平台通过，没有算法增量。", "",
        "S3源复制前后生成仅支持存活与正确性，不是持续前台干扰测量。同一源跨方法共享，存活时间从首次源生成后累计，HBM byte-seconds从owner建立起累计到各case后；不相减成每方法独立成本，不补造cleanup尾段。bootstrap的native/target生成、全SHA及建立时间另列于summary.json，不能并入baseline性能。失败与未决状态保留。", "",
        f'新增恢复观察{summary["observed_new_restore_count"]}个，其中内容、文本及run清理验收通过{summary["accepted_new_restore_count"]}个；历史引用新增样本0。', "",
        "来源字节、物理账本、方法身份与冻结版本见summary.json；报告所用JSON/事件输入的读取时指纹及原run版本清单见inputs_manifest.json。追加引擎日志不参与指纹，指纹不声明永久不可变。物理free恢复按注册容差解释，不声称精确归零。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if RESULT_ROOT.resolve() not in out.parents:
        raise ValueError("output must be a new run under the versioned basic_scenarios directory")
    if out.exists():
        raise FileExistsError("output run already exists; use a new run ID")
    dirs = [p.resolve() for p in args.run_dir]
    if len(set(dirs)) != len(dirs):
        raise ValueError("duplicate input run would double count observations")
    if any(out.is_relative_to(p) for p in dirs):
        raise ValueError("output must not be nested inside an input run")
    manifest, normalized, raw_inputs = [], [], {}
    for directory in dirs:
        def snapshot():
            return {str(p.relative_to(directory)): {"sha256": digest(p), "bytes": p.stat().st_size}
                    for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix in (".json", ".jsonl")}
        before = snapshot()
        record = "summary.json" if (directory / "summary.json").is_file() else "failure.json"
        data = json.loads((directory / record).read_text())
        data["input_record"] = record
        raw_inputs[directory.name] = data
        command_path = directory / "commands.txt.json"
        if command_path.is_file():
            argv = json.loads(command_path.read_text()).get("argv", [])
            if "--target-slot" in argv:
                data["_target_slot"] = argv[argv.index("--target-slot") + 1]
            elif classify(data) == "scenario":
                data["_target_slot"] = "target_b"
        after = snapshot()
        if before != after:
            raise RuntimeError(f"input changed during read: {directory}")
        manifest.append({"run": str(directory), "files": before, "before_equals_after": True,
            "input_record": record, "fingerprint_scope": "JSON/JSONL snapshot at analysis read; appendable engine logs excluded; no permanent immutability assertion",
            "declared_version": {k: data.get(k) for k in ("development", "task", "code_sha256",
                "session_sha256", "frozen_upstream_commits", "template_sha256", "format_contract")}})
        normalized.append(normalize_run(data, str(directory)))
    summary = {"status": "completed", "development": VERSION, "audit_kind": "offline_summary",
        "runs": normalized, "observed_new_restore_count": sum(r["observed_new_restore_count"] for r in normalized),
        "accepted_new_restore_count": sum(r["accepted_new_restore_count"] for r in normalized),
        "failed_runs": [r["run"] for r in normalized if r["status"] == "failed"],
        "bootstrap_baseline_performance_samples": 0, "historical_reference_new_samples": 0,
        "comparison_scope": "S2 local node27/target_a and S3 cross-node node27->node29/target_b are separate scenarios; no cross-scenario H2D-vs-D2D speed claim",
        "clock_policy": "reuse same-host recorded durations; never subtract cross-host timestamps",
        "commands": {"run_dirs": [str(p) for p in dirs], "out": str(out)}}
    if "runtime_preflight_r02" in raw_inputs and "protected_after_r01" in raw_inputs:
        summary["protected_metadata_check"] = compare_protected(raw_inputs["runtime_preflight_r02"], raw_inputs["protected_after_r01"])
    sources = {"development": VERSION, "runs": manifest, "summarizer_sha256": digest(Path(__file__)),
               "test_sha256": digest(Path(__file__).with_name("test_summary.py"))}
    out.mkdir(parents=True)
    for name, data in (("summary.json", summary), ("inputs_manifest.json", sources)):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (out / "report.md").write_text(render(summary))
    print(json.dumps({"status": "completed", "out": str(out), "accepted_new_restore_count": summary["accepted_new_restore_count"]}))


if __name__ == "__main__":
    main()
