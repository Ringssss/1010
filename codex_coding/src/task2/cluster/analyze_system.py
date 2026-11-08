"""Audit eight infrastructure gates and summarize one-pass cluster model restores."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
STAGES = {
    "1_resource_directory": "统一资源目录", "2_automatic_placement": "自动保活放置",
    "3_lifecycle": "保活生命周期", "4_gpu_source": "显存来源接入",
    "5_two_node_model": "双机恢复闭环", "6_multisource_fixture": "多源协同恢复",
    "7_joint_placement": "存放恢复联合规划", "8_three_node_model": "三机系统验收",
}


def main(args):
    directory = Path(args.run_dir)
    raw = (directory / "summary.json").read_bytes()
    data = json.loads(raw)
    workers = {w["worker"]: w for w in data.get("stages", {}).get("1_resource_directory", {}).get("workers", [])}
    stages = [{"id": k, "name": name, "passed": data.get("stages", {}).get(k, {}).get("passed", False)} for k, name in STAGES.items()]
    report = {"status": data["status"], "development": data["development"], "error": data.get("error"),
              "stages": stages, "restores": [], "cleanup_errors": data.get("cleanup_errors"),
              "summary_sha256": hashlib.sha256(raw).hexdigest(),
              "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "recorded_code_files": len(data["code_sha256"]),
              "source_hash_mismatches": [name for name, sha in data["code_sha256"].items()
                                         if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha],
              "limitations": ["one observation per model scenario; no statistical speedup claim",
                              "fixed TP2 tensor layout; no reshaping across TP sizes",
                              "bandwidth profile is provisional; objective values are planning estimates",
                              "collective communication failure may require restarting the isolated worker session",
                              "no native SGLang keepalive or SSD comparison in this run"]}
    for sample in data.get("restores", []):
        flow = sample["flow"]
        plan = flow["plan"]
        expected, actual, by_node = {}, {}, {}
        for op in plan["ops"]:
            rid = op["replica_id"]
            expected[rid] = expected.get(rid, 0)+op["n"]
            node = workers[op["src_w"]]["node"]
            by_node[node] = by_node.get(node, 0)+op["n"]
        for worker in flow["execution"]:
            for row in worker["records"]:
                if row["role"] in ("source", "both"):
                    rid = row["replica_id"]
                    actual[rid] = actual.get(rid, 0)+row["bytes"]
        verified = sample["verification"]
        passed = (sample["passed"] and verified["passed"] and expected == actual == flow["source_bytes"]
                  and verified["checked_bytes"] == sum(t["n"] for t in plan["targets"]))
        if sample["phase"] == "three_node_restore":
            passed = passed and {"node27", "node28"} <= set(by_node) and sample["node"] == "node29"
        report["restores"].append({"phase": sample["phase"], "target_node": sample["node"],
            "strategy": sample["strategy"], "passed": passed,
            "load_begin_to_all_weights_ready_s": sample["timing"]["load_s"],
            "planning_s": plan["planning_s"], "prepare_s": flow["prepare_s"],
            "execute_wall_s": flow["execute_wall_s"], "source_bytes": actual, "source_bytes_by_node": by_node,
            "verified_bytes": verified["checked_bytes"], "generated_replies": len(sample["replies"])})
    report["retained_logical_budget_GiB"] = {
        tier: sum(w["used_bytes"][tier] for w in data.get("final_inventory", []))/2**30
        for tier in ("dram", "hbm")}
    report["model_joint_decisions"] = data.get("model_joint_placement", {}).get("placement")
    report["passed"] = (data["status"] == "passed" and all(s["passed"] for s in stages)
                        and len(report["restores"]) == 2 and all(s["passed"] for s in report["restores"])
                        and not report["source_hash_mismatches"] and data.get("cleanup_errors") == [])
    (directory / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", required=True)
    main(parser.parse_args())
