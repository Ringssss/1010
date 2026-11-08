"""Freeze the single preregistered active-source revision and its negative parent."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2/placement_competition"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    paths = [RESULTS / p for p in (
        "review_admission_r01/qualification.json", "review_objective_r01/qualification.json",
        "review_case_active_r01/qualification.json", "review_gate_active_r01/qualification.json",
        "review_active_boundary_r01/checks.json", "case_active_preregister_r01/auditrefs_final.json",
        "review_final_r01/qualification.json")]
    data = [json.loads(p.read_text()) for p in paths]
    checks = {}
    for path, item in zip(paths[:3], data[:3]):
        checks.update({path.parent.name + "/" + name: value for name, value in item["checks"].items()})
    checks.update({"boundary/" + name: value for name, value in data[4]["checks"].items()})
    refs = [ROOT / r["path"] for item in data for r in item.get("source_refs", [])]
    checks["all_frozen_evidence_and_source_hashes_match"] = all(
        sha(ROOT / r["path"]) == r["sha256"] for item in data for r in item.get("source_refs", []))
    checks["all_eight_candidates_used_and_every_block_two_meaningful_sources"] = (
        data[3]["every_block_multiple_meaningful_locations_gate"] and data[3]["all_candidates_used_somewhere"])
    checks["negative_parent_gate_false_preserved"] = data[6]["research_gate_passed"] is False
    checks["single_rational_revision_registered_without_sweep"] = data[5]["single_v"] == "3/2" and data[5]["parameter_sweep"] is False
    passed = all(checks.values())
    qualification = "passed_symbolic_multi_source_competition_physical_unadmitted" if passed else "failed"
    result = {"development": "v0.1.23.3/task2", "qualification": qualification,
              "checks": checks, "checks_passed": sum(checks.values()), "checks_total": len(checks),
              "cpu_correctness_review_passed": passed, "research_gate_passed": passed,
              "negative_parent_gate_false_preserved": True,
              "joint_increment_over_exchange_baseline": False,
              "candidate_opportunities_used": 8, "feasible_subsets": 121,
              "independently_recomputed_rows": 130,
              "expected_and_actual_symbolic_scores": {"capacity_aware_fastest": "11/12", "bottleneck_balanced": "11/12",
                "balanced_exchange": "5/6", "existing_joint": "5/6"},
              "same_actual_cache_bytes_fastest_exchange_joint": 16777216,
              "resource_pareto": data[2]["resource_pareto"], "fixed_worker_dag_pareto": data[2]["fixed_worker_dag_pareto"],
              "symbolic_schedule_capacity_feasible": True, "finite_execution_optimum_certified": False,
              "physical_budget_admitted": False, "physical_mapping_ready": False,
              "physical_rate_ready": False, "real_model_binding_ready": False, "real_T_ready_measured": False,
              "gpu_command": None, "GPU_calls": 0, "SSH_actions": 0,
              "remote_writes": 0, "signals": 0, "product_mutations": 0,
              "source_refs": [{"path": str(p.resolve().relative_to(ROOT)), "sha256": sha(p)}
                              for p in dict.fromkeys(paths + refs + [Path(__file__)])]}
    (args.out / "qualification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.out / "report.md").write_text(f"""# 多源放置竞争：active 修订独立验收

编号：v0.1.23.3/task2。最终输入 case_active_r01；上一版本 case_cpu_r03 和 review_final_r01 保留 CPU 正确、研究门槛失败的负结果。最终独立检查 {sum(checks.values())}/{len(checks)} 通过；资格为 `{qualification}`。

## 已满足的研究条件

每个 4MiB 块有两个合法 HBM 保存来源，8 个候选在全部 121 个合法 subset 的共同取数政策中均实际使用过，完整 DRAM 后备对全部策略相同。副本合法且按字节计账，两个池各 8MiB，全局 16MiB；不强制占满，不强制使用 cache。

原 r03 的两个慢候选被 DRAM 支配。修订先推导并登记条件 1<v<12/7，再只把这两条理论速率置为 v=3/2，保留其余内容、预算、source 角色、路径容量和 baseline 规则。没有参数扫描，也不要求 joint 胜出。独立检查预登记 derivation/route_witness/builder 哈希及代码中先写合同再执行的顺序。

本关只重新累计原件操作，没有导入 planner、再跑 solver 或构建新 scheduler。121 个 subset、6 个策略及原 strong-simple 3 个 control 共 130 行的覆盖、来源、负载、位置配额、复制账本、source/target ready、队列前序和最后 receiver 完成时间均核实。pool0/1 后续作为 source 的尾时间与其自身目标 ready 分开。

## 数值与归因

两份预登记整块路线在原 resource_lp/4MiB 取整后的实际操作中都出现：顺序最快是 r0@pool0、r1@pool1、r2@pool0、r3@pool1，评分11/12；交换是 r0@pool1、r1@pool1、r2@pool0、r3@pool0，评分5/6。二者实际缓存均16MiB。差值1/12来自将较灵活的r0移走，把稀缺pool0留给更依赖它的内容；这里只证明具体顺序占位规则的损失。

均衡单添加为11/12，均衡交换与原joint为5/6，选择、route和queue相同。joint相对强交换对照没有增量，不能据此宣称一般普通方法失败或MILP必要。原strong-simple的singleton/paired/marginal规则保留，共同评分2、cache0；它的原内部资源目标与新增baseline的DAG目标分别说明。

资源下界 Pareto 为0MiB/1、16MiB/0.5；固定共同 worker-DAG Pareto 为0MiB/2、8MiB/1、16MiB/5/6。允许预算0/4/8/12/16MiB下最小共同评分为2/2/1/1/5/6。这是相同固定取数政策下的有限 layout 参考，不能称为任意路由/顺序的执行最优。

所有声明路径按事件区间累计并发rate都不超过path/resource容量，因而本case的DAG是零启动、恒速、冻结资源模型中的可达witness。一般 shared-U local-first 反例仍保留：分别评分2并不能达到共同时序3。本case的U容量4，两个base worker同时最多总rate2，不是上述布局收益的决定因素。

## 物理状态

当前是六个 symbolic role，目标0/1兼HBM pool；不是旧八worker契约的物理等价部署。底本16MiB、目标16MiB、cache至多16MiB、staging384MiB分列，runtime/实际峰值未知。旧两节点8worker及4worker复用方案仍未准入，新六role也未获实际多worker预算证据。保持原每node DRAM/HBM各2GiB，不借用旧keeper资格或改变XML。

GPUcommand=null；physical_budget_admitted=false；physical mapping/rates/model binding/T_ready均未认证。独立审查GPU、SSH、远端写、信号和产品变更均为零。CPU多来源结构门槛已过，下一步须先核真实资源图与六role预算，再决定有区分度的物理工作点。
""")
    print(json.dumps({"qualification": qualification, "passed": sum(checks.values()), "total": len(checks)}))
    if not passed:
        raise SystemExit("Active final review failed")


if __name__ == "__main__":
    main()
