"""Read-only historical evidence audit; no GPU imports or remote operations."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


RUNS = {
    "e1": "e1/e1_c02_qwen_ipc_r01",
    "e3": "e3/e3_c02_qwen_r01",
    "e4": "e4/e4_c01_qwen_r01",
    "e5": "e5/e5_c01_qwen_decision_r01",
    "e6": "e6/e6_c01_qwen_curve_r01",
}
GIB = 1 << 30
ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path("codex_coding/results/v0.1.23.3/task2")
PAPER = RESULTS / "paper_e1_e8"
EXTRA_SOURCES = (
    "AGENTS.md", "docx/next_step.txt", "docx/debug_philosophy.md",
    "docx/articles/2026-10-06_baselines_delta_and_research_roadmap.md",
    "docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md",
    "docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md",
    "main_lib/fluidckpt/cluster/worker.py",
    "main_lib/fluidckpt/cluster/control.py",
)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def historical_manifest(root):
    return {str(p.relative_to(root)): {"sha256": digest(p), "bytes": p.stat().st_size}
            for rel in RUNS.values() for p in sorted((root / PAPER / rel).rglob("*"))
            if p.is_file()}


def read_json(path):
    return json.loads(path.read_text())


def rank_phases(rows):
    """Only subtract Linux monotonic clocks on the same named host."""
    events = {r["event"]: r for r in rows if r["event"] not in ("release_begin", "release_end")}
    pairs = {
        "load_to_layout_s": ("load_begin", "model_layout_end"),
        "layout_to_export_s": ("model_layout_end", "target_exported"),
        "load_to_execute_s": ("load_begin", "target_execute_begin"),
        "execute_to_copy_s": ("target_execute_begin", "target_copy_complete"),
        "copy_to_publish_s": ("target_copy_complete", "target_ready_publish"),
        "publish_to_ready_s": ("target_ready_publish", "weights_ready"),
        "load_to_ready_s": ("load_begin", "weights_ready"),
    }
    out = {"event_order": list(events), "durations": {}}
    for key, (a, b) in pairs.items():
        if a not in events or b not in events:
            continue
        if events[a]["host"] != events[b]["host"]:
            raise ValueError(f"uncalibrated cross-host timing: {a}/{b}")
        delta = (events[b]["monotonic_ns"] - events[a]["monotonic_ns"]) / 1e9
        if delta < 0:
            raise ValueError("negative local duration")
        out["durations"][key] = delta
    return out


def source_tier(op):
    # Transfer kind "network" does not identify the source memory tier.
    tier = op["path"].split(":")[1]
    if tier not in ("hbm", "dram", "ssd"):
        raise ValueError(f"unknown source tier: {op['path']}")
    return tier


def audit_case(case, run_dir):
    flow = case["flow"]
    ops = flow["plan"]["ops"]
    by_id = {op["op_id"]: op for op in ops}
    if len(by_id) != len(ops):
        raise ValueError("duplicate plan operation ID")
    tier_bytes, source_bytes, pair_bytes = Counter(), Counter(), Counter()
    for op in ops:
        tier = source_tier(op)
        tier_bytes[tier] += op["n"]
        source_bytes[str(op["src_w"])] += op["n"]
        pair_bytes[f'{op["src_w"]}->{op["dst_w"]}/{tier}'] += op["n"]
    detailed, workers, dependency_edges = [], [], set()
    target_records = Counter()
    seen_workers = set()
    for row in flow["execution"]:
        w = row["worker"]
        if w in seen_workers:
            raise ValueError("duplicate execution worker")
        seen_workers.add(w)
        records = row["records"]
        expected = [o["op_id"] for o in ops if w in (o["src_w"], o["dst_w"])]
        if [r["op_id"] for r in records] != expected:
            raise ValueError(f"worker {w}: missing or reordered records")
        groups = []
        local_sum = 0.0
        roles = Counter()
        for seq, record in enumerate(records):
            op = by_id[record["op_id"]]
            role = "both" if op["src_w"] == w == op["dst_w"] else "source" if op["src_w"] == w else "target"
            if record["role"] != role or record["bytes"] != op["n"] or record["kind"] != op["kind"]:
                raise ValueError(f"worker {w}: record disagrees with plan")
            if role in ("target", "both"):
                target_records[op["op_id"]] += record["bytes"]
            roles[role] += 1
            local_sum += record["elapsed_s"]
            key = (role, op["src_w"], op["dst_w"], op["kind"], source_tier(op))
            if not groups or groups[-1]["key"] != list(key):
                groups.append({"key": list(key), "first_op_id": op["op_id"], "last_op_id": op["op_id"],
                               "operations": 0, "bytes": 0, "elapsed_sum_s": 0.0})
            group = groups[-1]
            group["last_op_id"] = op["op_id"]
            group["operations"] += 1
            group["bytes"] += record["bytes"]
            group["elapsed_sum_s"] += record["elapsed_s"]
            detailed.append({"worker": w, "local_sequence": seq, **record,
                             "src_w": op["src_w"], "dst_w": op["dst_w"], "tp": op["tp"],
                             "off": op["off"], "source_tier": source_tier(op), "path": op["path"],
                             "resources": op.get("resources", [])})
            if seq:
                dependency_edges.add((records[seq - 1]["op_id"], record["op_id"]))
        workers.append({"worker": w, "roles": dict(roles), "operations": len(records),
                        "participation_bytes": sum(r["bytes"] for r in records),
                        "execute_s": row["execute_s"], "elapsed_sum_s": local_sum,
                        "groups": groups, "staging_bytes": row.get("staging_bytes"),
                        "chunk_bytes": row.get("chunk_bytes")})
    if target_records != Counter({o["op_id"]: o["n"] for o in ops}):
        raise ValueError("target byte coverage differs from plan")
    if any(w not in seen_workers for o in ops for w in (o["src_w"], o["dst_w"])):
        raise ValueError("missing participant worker")
    ranks = {}
    starts, ends, hosts = [], [], set()
    for path in sorted((run_dir / "metrics" / case["phase"]).glob("rank*.jsonl")):
        rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
        ranks[path.stem] = rank_phases(rows)
        for event in rows:
            if event["event"] == "load_begin":
                starts.append(event["monotonic_ns"]); hosts.add(event["host"])
            elif event["event"] == "weights_ready":
                ends.append(event["monotonic_ns"]); hosts.add(event["host"])
    if len(hosts) != 1 or len(starts) != 2 or len(ends) != 2:
        raise ValueError("T_ready requires two complete ranks on a single host")
    ready_s = (max(ends) - min(starts)) / 1e9
    if abs(ready_s - case["timing"]["load_s"]) > 1e-9:
        raise ValueError("raw rank timing does not match historical summary")
    cross_destination_edges = [
        {"before_op": a, "after_op": b, "shared_workers": sorted(set((by_id[a]["src_w"], by_id[a]["dst_w"])) & set((by_id[b]["src_w"], by_id[b]["dst_w"]))),
         "before_pair": [by_id[a]["src_w"], by_id[a]["dst_w"]], "after_pair": [by_id[b]["src_w"], by_id[b]["dst_w"]]}
        for a, b in sorted(dependency_edges) if by_id[a]["dst_w"] != by_id[b]["dst_w"]]
    return {
        "phase": case["phase"], "label": case["label"], "scenario": case["scenario"],
        "status": case["status"], "correct": case["correct"], "ready_s": ready_s,
        "prepare_s": flow["prepare_s"], "execute_s": flow["execute_wall_s"],
        "publish_s": flow["verify_and_publish_s"], "executor": flow["executor"],
        "logical_bytes": sum(o["n"] for o in ops), "tier_bytes": dict(tier_bytes),
        "source_worker_bytes": dict(source_bytes), "pair_tier_bytes": dict(pair_bytes),
        "logical_aggregate_GBps": sum(o["n"] for o in ops) / flow["execute_wall_s"] / 1e9,
        "worker_records_match_ordered_plan": True, "workers": workers, "rank_phases": ranks,
        "dependency_edges": [list(e) for e in sorted(dependency_edges)],
        "cross_destination_dependencies": cross_destination_edges,
    }, detailed


def scenario_mapping(data):
    def entry(s, name, state, sources, conclusion, gap):
        return {"scenario": s, "name": name, "evidence_class": "historical_reference",
                "coverage": state, "source_runs": [RUNS[x] for x in sources],
                "new_samples": 0, "system_identity": "original historical FluidCKPT / HIP IPC identities retained",
                "conclusion": conclusion, "minimum_gap": gap}
    return [
        entry("S1", "目标HBM完整权重", "covered_mechanism", ["e1"],
              "完整Qwen HIP IPC两循环已通过，是历史硬件适配机制证据。", "原版SGLang daemon未验收；引用不能记为新样本。"),
        entry("S2", "CPU保活恢复", "covered_common_executor", ["e6", "e4", "e5"],
              "e6 0GiB额外HBM用完整DRAM底本恢复；e4强简单与joint同决策，e5无新增恢复。", "此为Fluid共同执行器历史host恢复，不改名Janus warm，也未比较Janus原版。"),
        entry("S3", "活跃复制", "unmeasured", [],
              "e1独立keeper owner与e3局部HBM缓存不构成活跃服务实例D2D复制。", "固定活跃源、服务干扰与源保护协议后才有功能/性能证据。"),
        entry("S4", "零散空间", "partial_related", ["e3", "e6"],
              "已有8GiB分散块及0/8/16GiB HBM曲线；完整65.5GiB DRAM底本始终保留计费。", "未测聚合DRAM不足/物理位置容量不均导致完整副本不可行；不支持广义多层容量紧张主张。"),
        entry("S5", "共享资源", "partial_verified_software_dependency", ["e3", "e4", "e5"],
              "同一worker跨目标串行参与的有序依赖已验证；e3远端来源负载不同。", "软件依赖和时间关联可核对；物理出口争用、运行顺序或冷/热效应尚未分离。"),
    ]


def build_summary(root):
    data = {k: read_json(root / PAPER / v / "summary.json") for k, v in RUNS.items()}
    if any(data[k]["status"] != "passed" for k in ("e1", "e3", "e4", "e6")):
        raise ValueError("historical run status changed")
    e3, records = [], {}
    for case in data["e3"]["cases"]:
        audit, detail = audit_case(case, root / PAPER / RUNS["e3"])
        e3.append(audit); records[case["phase"]] = detail
    by = {(c["label"], c["scenario"]): c for c in e3}
    means = {layout: sum(by[layout, target]["ready_s"] for target in ("target_a", "target_b")) / 2
             for layout in ("concentrated", "distributed")}
    delta = means["concentrated"] - means["distributed"]
    e4_aliases = data["e4"].get("route_aliases", [])
    e6_aliases = data["e6"].get("route_aliases", [])
    summary = {
        "status": "passed", "development": "v0.1.23.3/task2", "audit_kind": "offline_read_only",
        "evidence_class": "historical_reference", "new_gpu_samples": 0, "new_samples": 0,
        "historical_independent_observations": {"e1": len(data["e1"]["hits"]), "e3": len(e3),
            "e4": len(data["e4"]["cases"]), "e5": data["e5"]["new_gpu_restore_count"], "e6": len(data["e6"]["cases"])},
        "historical_aliases": {"e4": len(e4_aliases), "e6": len(e6_aliases)},
        "baseline_identity": "historical HIP IPC adaptation and FluidCKPT common executor; no Janus measurements",
        "scenarios": scenario_mapping(data), "e3_cases": e3,
        "e3_difference": {"equal_weight_means_s": means, "saved_s": delta,
            "saved_percent": delta / means["concentrated"] * 100,
            "target_deltas": {t: {"ready_saved_s": by["concentrated", t]["ready_s"] - by["distributed", t]["ready_s"],
                                  "execute_saved_s": by["concentrated", t]["execute_s"] - by["distributed", t]["execute_s"]}
                              for t in ("target_a", "target_b")},
            "observed_order": [c["label"] + "/" + c["scenario"] for c in e3],
            "interpretation": "23.9%是等权两目标单次观察；A命中HBM量改变，B同字节来源重排并改变worker依赖，未建立硬件争用因果。"},
        "minimum_shared_constraint": {
            "constraint": "每个worker按全局有序op列表串行完成其参与操作；共享worker的跨pair操作不能同时推进。",
            "verified_by": "全部计划参与集合、操作次序、角色和目标字节均逐项匹配execution.records；rank本机copy/publish阶段另列。",
            "model_implication": "候选取数评分至少应识别参与worker的累积负载及跨目标先后依赖；仅独立pair带宽/target串行不能描述共享source gate。",
            "minimum_followup": "先冻结B的总HBM/DRAM字节、合法来源和chunk/executor，仅改变source0承担第二目标HBM块的布局；反序一次区分顺序效应，必要时第二次。新增实机由主agent预登记调度。",
            "stop_rule": "反序不保留差异或强简单策略同决策时保留负结果并采用简单方案；不新增自动复制/通用排程器。",
            "unresolved": ["local elapsed包含collective等待、传输、落地与同步，不能拆成独立链路带宽。",
                           "旧record无每op开始/结束绝对戳，不能恢复跨主机重叠或精确硬件出口利用率。",
                           "单次固定顺序仍与布局变化混杂，物理NIC/PCIe/NUMA争用及热状态未独立控制。"]},
        "clock_policy": "仅同一host monotonic_ns求差；worker本地elapsed只局部汇总；不减未校准跨主机wall/monotonic钟。",
        "e4_e5_decision": {"e4_independent": len(data["e4"]["cases"]), "e4_aliases": len(e4_aliases),
                           "e5_status": data["e5"]["status"], "e5_decision": data["e5"]["decision"],
                           "new_gpu_restore_count": data["e5"]["new_gpu_restore_count"]},
        "e6_ready_points": [{"label": c["label"], "target": c["scenario"], "ready_s": c["timing"]["load_s"]}
                            for c in data["e6"]["cases"]],
    }
    return summary, records


def report(summary):
    lines = ["# 基础场景历史证据离线审计", "", "开发编号：v0.1.23.3/task2。证据类型为 historical_reference；新增GPU恢复与性能样本均为0。原run、方法身份和失败边界保留，历史结果不命名为Janus。", "",
             "## S1–S5覆盖", "", "| 场景 | 状态 | 已有证据 | 最小缺口 |", "| --- | --- | --- | --- |"]
    for s in summary["scenarios"]:
        lines.append(f'| {s["scenario"]} {s["name"]} | {s["coverage"]} | {s["conclusion"]} | {s["minimum_gap"]} |')
    d = summary["e3_difference"]
    lines.extend(["", "## e3来源与指标", "",
        f'集中/分散等权均值分别为{d["equal_weight_means_s"]["concentrated"]:.9f}/{d["equal_weight_means_s"]["distributed"]:.9f}s，差值{d["saved_s"]:.9f}s（{d["saved_percent"]:.6f}%）。这是四个历史单次配置的描述性差值。', "",
        "| 布局/目标 | T_ready(s) | execute(s) | 命中HBM(GiB) | DRAM(GiB) | 来源worker字节(GiB) |", "| --- | ---: | ---: | ---: | ---: | --- |"])
    for c in summary["e3_cases"]:
        src = ", ".join(f'{w}: {n/GIB:.6f}' for w,n in c["source_worker_bytes"].items())
        lines.append(f'| {c["label"]}/{c["scenario"]} | {c["ready_s"]:.9f} | {c["execute_s"]:.9f} | {c["tier_bytes"].get("hbm",0)/GIB:.6f} | {c["tier_bytes"].get("dram",0)/GIB:.6f} | {src} |')
    lines.extend(["", "A集中/分散命中4/8GiB HBM，因此A不是同来源流量的纯拓扑对照。B两组均为8GiB HBM+57.499654GiB DRAM；source0/source1从36.749827/28.749827变成32.749827/32.749827GiB。B变化集中在execute。逻辑总字节/execute仅代表整组逻辑吞吐。", "", "## worker依赖与rank局部阶段", ""])
    for c in summary["e3_cases"]:
        lines.extend([f'### {c["label"]}/{c["scenario"]}', "", "| worker | 角色/操作数 | execute(s) | 局部elapsed总和(s) | 连续操作组（src→dst/kind/op范围/秒） |", "| --- | --- | ---: | ---: | --- |"])
        for w in c["workers"]:
            if not w["operations"]:
                continue
            groups = "; ".join(f'{g["key"][1]}→{g["key"][2]}/{g["key"][3]}:{g["key"][4]}/{g["first_op_id"]}–{g["last_op_id"]}/{g["elapsed_sum_s"]:.6f}' for g in w["groups"])
            lines.append(f'| {w["worker"]} | {w["roles"]} | {w["execute_s"]:.6f} | {w["elapsed_sum_s"]:.6f} | {groups} |')
        lines.extend(["", "| rank | 本机execute→copy(s) | copy→publish(s) | publish→ready(s) |", "| --- | ---: | ---: | ---: |"])
        for rank, phase in c["rank_phases"].items():
            p=phase["durations"]
            lines.append(f'| {rank} | {p["execute_to_copy_s"]:.6f} | {p["copy_to_publish_s"]:.6f} | {p["publish_to_ready_s"]:.6f} |')
        lines.append("")
    lines.extend(["## 最小已验证约束及后续界限", "", summary["minimum_shared_constraint"]["constraint"], "",
                  "集中B中source0先完成对target8的操作，再进入target9的HBM；target9之后才能继续自身DRAM操作。这是来源共享导致的执行先后依赖，不需要假设物理网络争用。worker0/1与target8/9的局部elapsed和copy完成/发布等待形成可核对证据链。所有操作记录与计划次序、角色、字节逐项匹配。", "",
                  summary["minimum_shared_constraint"]["model_implication"], "",
                  summary["minimum_shared_constraint"]["minimum_followup"], "",
                  summary["minimum_shared_constraint"]["stop_rule"], "",
                  "未决原因：" + " ".join(summary["minimum_shared_constraint"]["unresolved"]), "",
                  summary["clock_policy"], "", "## 复用与完整性", "",
                  "e4为2个独立实测+6别名；e5为decision_only/unchanged，0新增GPU恢复；e6为8个独立实测+4别名。引用、别名和本次离线审计均不增加性能样本数。S3未测，S4仅局部覆盖。", "",
                  "sources.json保存五个历史run的全部文件SHA256/长度及审计前后相等检查。原结果没有覆写。e3_worker_records.json保留逐worker操作次序、角色、局部elapsed及计划来源；summary.json保留完整派生证据。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    out = args.out.resolve()
    allowed = (root / RESULTS / "basic_scenarios").resolve()
    if not out.is_relative_to(allowed) or out == allowed:
        raise ValueError("output must be a new run under basic_scenarios")
    if out.exists():
        raise FileExistsError("run directory already exists; choose a new run ID")
    before = historical_manifest(root)
    summary, records = build_summary(root)
    after = historical_manifest(root)
    if before != after:
        raise RuntimeError("historical inputs changed during audit")
    summary["historical_hashes_unchanged"] = True
    sources = {"historical_results": before, "historical_files": len(before), "before_equals_after": True,
               "supporting_sources": {p: digest(root / p) for p in EXTRA_SOURCES},
               "audit_script_sha256": digest(Path(__file__)), "historical_runs": RUNS}
    out.mkdir(parents=True)
    for name, value in (("summary.json", summary), ("sources.json", sources), ("e3_worker_records.json", records)):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    (out / "report.md").write_text(report(summary))
    print(json.dumps({"status": "passed", "out": str(out), "historical_files": len(before),
                      "new_gpu_samples": 0, "saved_percent": summary["e3_difference"]["saved_percent"]}))


if __name__ == "__main__":
    main()
