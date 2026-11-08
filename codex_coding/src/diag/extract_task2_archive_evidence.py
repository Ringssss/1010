"""Extract reproducible archive evidence from immutable results; no model or GPU execution."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/"codex_coding/results/task2/archive_20260929"
DOC = ROOT/"code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md"
BEGIN = "<!-- TASK2_DETAILED_EVIDENCE_BEGIN -->"
END = "<!-- TASK2_DETAILED_EVIDENCE_END -->"


def main():
    hashes = {}
    def read(name):
        p = ROOT/name
        raw = p.read_bytes(); hashes[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    base = "codex_coding/results/task2/cluster/"
    raw = read(base+"placement01/summary.json")
    analysis = read(base+"placement01/placement_analysis.json")
    control = read(base+"control01/control_analysis.json")
    cpu = read(base+"profile01/profiling_analysis.json")
    bulk = read(base+"bulk_profile01/profiling_analysis.json")
    report = {"scope": "offline extraction; original summaries and analyses unchanged", "inputs_sha256": hashes,
              "timeline": [], "restores": [], "control": control["rows"], "cpu": cpu["profiles"],
              "bulk_trace_coverage": bulk["trace_byte_coverage"], "segment_differences": []}
    lines = [BEGIN, "", "## 附录 H：从原始结果自动摘录的可核对证据", "",
             "本附录由`codex_coding/src/diag/extract_task2_archive_evidence.py`离线生成，",
             "仅摘录已有结果，不重新求解、不启动GPU、不覆盖原summary/analysis。",
             "对应机器可读数据与输入SHA256见`codex_coding/results/task2/archive_20260929/detailed_evidence.json`。", "",
             "### H.1 placement01协调器时间线", "",
             "下表时间为协调器events.time转Asia/Shanghai，日期2026-09-28；不是跨主机monotonic相减。", "",
             "| 时间 | event | phase/path/说明 |", "| --- | --- | --- |"]
    for e in raw["events"]:
        if e["event"] in ("isolated_container_created", "preflight_passed", "mesh_ready", "model_captured",
                           "model_gpu_replicas_ready", "model_migrated_to_node28", "path_calibrated",
                           "placements_solved", "weights_transferred", "model_restore_passed", "optimized_layout_ready"):
            stamp = datetime.fromtimestamp(e["time"], ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S.%f")[:-3]
            note = e.get("phase", e.get("path", e.get("node", e.get("selected", ""))))
            report["timeline"].append({"local_time": stamp, **e})
            lines.append(f"| {stamp} | {e['event']} | {note} |")
    for sample in raw["restores"]:
        flow, plan = sample["flow"], sample["flow"]["plan"]
        lines += ["", f"### H.2.{len(report['restores'])+1} {sample['phase']}：实际操作及来源", "",
                  "JSON路径：summary.restores[].flow.plan.ops；每行是逻辑区间，n和off单位均为bytes。", "",
                  "| op | src→dst | replica_id | off | n | source_offset | kind |",
                  "| ---: | --- | --- | ---: | ---: | ---: | --- |"]
        sources = defaultdict(int)
        by_worker = {w["worker"]: w for w in flow["execution"]}
        for op in plan["ops"]:
            lines.append(f"| {op['op_id']} | {op['src_w']}→{op['dst_w']} | {op['replica_id']} | {op['off']} | {op['n']} | {op['source_offset']} | {op['kind']} |")
            sources[op["replica_id"]] += op["n"]
        assert dict(sources) == flow["source_bytes"]
        assert sum(sources.values()) == sample["verification"]["checked_bytes"] == 70329718112
        lines += ["", "| op | source角色耗时s | target/both角色耗时s |", "| ---: | ---: | ---: |"]
        for op in plan["ops"]:
            a = next(r for r in by_worker[op["src_w"]]["records"] if r["op_id"] == op["op_id"])
            b = next(r for r in by_worker[op["dst_w"]]["records"] if r["op_id"] == op["op_id"])
            lines.append(f"| {op['op_id']} | {a['elapsed_s']:.9f} | {b['elapsed_s']:.9f} |")
        network = sum(op["n"] for op in plan["ops"] if op["kind"] == "network")
        verification = sample["verification"]
        for v in verification["operations"].values():
            assert v["source_sha256"] == v["target_sha256"]
        first = next(iter(verification["operations"].values()))
        lines += ["", "源/目标两列可并行且起止点不同，不能相加；both为同一worker，重复显示不是两次拷贝。",
                  f"实际跨机总字节：{network:,}（{network/(1<<30):.8f} GiB）。",
                  f"全量校验：{verification['checked_bytes']:,} bytes；以下为其中一个op的原始记录：", "", "```json",
                  json.dumps(first, ensure_ascii=False, indent=2), "```", ""]
        report["restores"].append({"phase": sample["phase"], "ops": plan["ops"], "execution": flow["execution"],
             "source_bytes": dict(sources), "network_bytes": network, "verification_example": first})
    before, after = analysis["measured"]
    lines += ["### H.3 完整窗口变化的加法账本", "",
              "定义Δ=旧−新；正数表示该阶段缩短，负数表示该阶段变长。这是观察差值，不是随机化因果估计。", "",
              "| 阶段 | 旧s | 新s | Δ ms |", "| --- | ---: | ---: | ---: |"]
    total = 0
    for key, a in before["segments_s"].items():
        b = after["segments_s"][key]; delta = a-b; total += delta
        report["segment_differences"].append({"stage": key, "before_s": a, "after_s": b, "saved_s": delta})
        lines.append(f"| {key} | {a:.9f} | {b:.9f} | {delta*1000:.6f} |")
    assert abs(total-(before["load_s"]-after["load_s"])) < 1e-9
    lines += [f"| 合计 | {before['load_s']:.9f} | {after['load_s']:.9f} | {total*1000:.6f} |", "",
              "coordinator execute的106.294ms改善与目标时间线执行段的105.954ms改善口径略有不同，不再次叠加。", "",
              "### H.4 compact控制面原始字段", "",
              "| 指标 | legacy | compact |", "| --- | ---: | ---: |"]
    for key in ("plan_wall_s", "planner_request_bytes", "planner_response_bytes", "inventory_response_bytes",
                "prepare_request_bytes", "control_json_bytes", "control_rpc_calls", "epoch_snapshot_reused"):
        a, b = control["rows"]; lines.append(f"| {key} | {a[key]} | {b[key]} |")
    lines += ["", "### H.5 profile01线程与CPU调用证据", "",
              "wall、线程CPU和runqueue不是互斥相加项；runqueue只描述被采样的执行线程，不覆盖所有RCCL后台线程。", "",
              "| worker | execute wall s | thread CPU s | runqueue ms | 同步调用数 | 同步self s |",
              "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    for w in cpu["profiles"]:
        if w["worker"] < 4: continue
        sync = next(r for r in w["top_cpu_functions"] if "_cuda_synchronize" in r["function"])
        lines.append(f"| {w['worker']} | {w['execute_wall_s']:.9f} | {w['thread_cpu_s']:.9f} | {w['scheduler_runqueue_wait_s']*1000:.6f} | {sync['calls']} | {sync['self_s']:.9f} |")
    lines += ["", "### H.6 补测trace完整性与厂商标签纠错", "",
              "| worker | op | 应拷贝bytes | trace拷贝bytes | 通过 |", "| ---: | ---: | ---: | ---: | --- |"]
    for r in bulk["trace_byte_coverage"]:
        assert r["passed"]
        lines.append(f"| {r['worker']} | {r['op_id']} | {r['expected_bytes']} | {r['trace_copy_bytes']} | {r['passed']} |")
    trace = read(base+"bulk_profile01/profiles/worker_node29_0.json")
    wrong = next(e for e in trace["traceEvents"] if e.get("name") == "DeviceToHost"
                 and e.get("args", {}).get("destination memory kind") == "Device")
    correlated = next(e for e in trace["traceEvents"] if e.get("name") == "hipMemcpyAsync"
                      and e["args"].get("index") == wrong["args"]["index"])
    report["vendor_label_evidence"] = {"copy": wrong, "correlated_api": correlated}
    lines += ["", "以下为原trace字段摘录，非重构的示意日志：", "", "```json",
              json.dumps({"copy_name": wrong["name"], "copy_args": wrong["args"],
                          "correlated_api_args": correlated["args"]}, ensure_ascii=False, indent=2), "```", "",
              "### H.7 实际worker日志摘录", "",
              "来源：placement01/worker_node29_0.log；精确匹配新布局transaction及op，保留原行。", "", "```jsonl"]
    tx = raw["restores"][1]["flow"]["tx"]
    logpath = ROOT/(base+"placement01/worker_node29_0.log")
    log = logpath.read_bytes(); hashes[str(logpath.relative_to(ROOT))] = hashlib.sha256(log).hexdigest()
    selected = []
    for line in log.decode().splitlines():
        if line.startswith("{"):
            try: row = json.loads(line)
            except json.JSONDecodeError: continue
            if row.get("tx") == tx and row.get("ev") in ("cluster_op_begin", "cluster_op_end"):
                selected.append(line)
    assert len(selected) == 8
    lines += selected+["```", "", "### H.8 原始状态与证据级别", "",
             "- placement01：完整验收passed、无源码漂移、无清理错误。",
             "- profile01：模型/CPU记录passed；完整GPU trace缺失。",
             "- bulk_profile01：传输与trace覆盖通过，原始run严格指纹failed，保持诊断定位。",
             "- 本附录只读取原始结果，生成独立归档证据；没有将failed重写成passed。", "", END]
    OUT.mkdir(parents=True, exist_ok=True)
    report["extractor_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (OUT/"detailed_evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    section = "\n".join(lines)+"\n"
    (OUT/"detailed_evidence.md").write_text(section)
    current = DOC.read_text()
    if BEGIN in current:
        a, rest = current.split(BEGIN, 1); _, b = rest.split(END, 1)
        current = a+section.rstrip()+b
    else:
        current = current.rstrip()+"\n\n"+section
    DOC.write_text(current)
    print(json.dumps({"passed": True, "input_files": len(hashes), "appended_lines": len(section.splitlines()),
                      "document": str(DOC), "evidence": str(OUT/"detailed_evidence.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
