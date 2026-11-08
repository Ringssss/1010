"""Validate fixed symbolic proofs; allocate no payload and launch no GPU work."""
import argparse
from fractions import Fraction as F
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
GIB = 1 << 30


def exact(value):
    if isinstance(value, F):
        return str(value)
    if isinstance(value, dict):
        return {key: exact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [exact(item) for item in value]
    return value


def fixed_proofs():
    gains, k = [F(3), F(9), F(5), F(1)], 2
    subsets = list(itertools.combinations(range(len(gains)), k))
    chosen = tuple(sorted(sorted(range(len(gains)), key=lambda i: (-gains[i], i))[:k]))
    assert all(sum(gains[i] for i in chosen) >= sum(gains[i] for i in subset)
               for subset in subsets)
    # The one-dimensional parameter captures every direct fractional placement.
    placements = [{"P_at_S0_GiB": F(j, 6), "time_s": max(F(4, 3), F(j, 6))}
                  for j in range(49)]
    assert min(row["time_s"] for row in placements) == F(4, 3)
    assert max(F(8, 8), F(8, 1)) == 8
    assert max(F(8, 6), F(8, 8)) == F(4, 3)
    condition_count = 0
    for a, b, c, d in itertools.product(range(1, 9), repeat=4):
        if a >= b and min(a, d) < min(b, c):
            assert max(F(8, a), F(8, d)) > max(F(8, b), F(8, c))
            condition_count += 1
    # Each candidate locally removes three seconds; the maximum stays flat.
    plateau = [{"selected": list(subset), "time_s": max(8 - 3 * int(i in subset)
                                                        for i in range(4))}
               for count in range(5) for subset in itertools.combinations(range(4), count)]
    assert all(row["time_s"] == 8 for row in plateau if len(row["selected"]) < 4)
    assert plateau[-1]["time_s"] == 5
    shared = [{"H_payload_GiB": F(j, 7),
               "time_s": max(F(j, 56), (16 - F(j, 7)) / 6)} for j in range(113)]
    best = min(shared, key=lambda row: row["time_s"])
    assert best == {"H_payload_GiB": F(64, 7), "time_s": F(8, 7)}
    # A fallback or relay changes which cut is unavoidable.
    fallback_t = F(8, 7)
    assert 6 * fallback_t + fallback_t == 8
    assert 8 <= 8 * fallback_t
    return dict(
        provenance="fixed symbolic parameters, not hardware observations",
        separable=dict(gains=gains, cardinality=k, chosen=list(chosen), checked_subsets=len(subsets),
                       exchange_proof="Replace any selected lower gain with an omitted higher gain; equal sizes preserve feasibility and nonnegative fixed gains never reduce the sum."),
        scarce_slots=dict(rates_GiBps=[[8, 8], [6, 1]], source_caps_GiBps=[8, 6],
                          sequential_whole_block_s=8, fractional_optimum_s=F(4, 3),
                          optimal_x_interval_GiB=[F(0), F(4, 3)], grid=placements,
                          proof="Sixteen unique demanded GiB require sixteen cache GiB; each eight-GiB source must export eight GiB. S1 export cap six implies T >= 4/3; crossed placement attains it.",
                          scope="HBM-only, no compression or shared target bytes; direct-only routes for the greedy time, zero setup, concurrent flows, adequate target ingress. Relay does not lower the unavoidable S1-cut optimum but can rescue the greedy layout."),
        pair_swap=dict(notation="a=S0->P,b=S1->P,c=S0->Q,d=S1->Q; positive effective rates, independent direct pair execution",
                       condition="a>=b and min(a,d)<min(b,c)", checked_integer_rate_tuples=condition_count,
                       tie_rule="a=b requires the specified deterministic first-choice rule to prefer S0"),
        plateau=dict(target_count=4, demand_GiB_per_target=8, local_prefix_GiB_per_target=4,
                     remote_GiBps=1, local_GiBps=4, subsets=plateau,
                     proof="T(S)=max_i(8-3*x_i). Every proper subset leaves an uncached target at eight seconds. Singleton/pair strict-positive marginal additions stop at empty; all four reach five seconds.",
                     ledger=dict(base_dram_GiB=32, cache_hbm_cap_GiB=16, target_hbm_GiB=32,
                                 staging_runtime="separate, not allocated or modeled"),
                     strong_baseline_required="Add a bottleneck-balanced/all-tied-target bundle baseline before a joint-planner performance claim."),
        shared_cut=dict(H_source_slots_GiB=[8, 8], independent_S2_slot_GiB=16,
                        cache_total_cap_GiB=16, physical_cut_caps_GiBps=dict(U=8, V=6),
                        independent_source_only_prediction_s=1, both_in_H_actual_s=2,
                        best_whole_block_s=F(4, 3), fractional_optimum=best,
                        optimal_per_target_GiB=dict(H=F(32, 7), S2=F(24, 7)),
                        optimal_per_target_GiBps=dict(H=4, S2=3), grid=shared,
                        proof="For total H payload h, T >= max(h/8,(16-h)/6). Balance the two cuts: h=64/7, T=8/7. Two targets each receive H32/7 and S2 24/7 GiB at rates four and three.",
                        accounting="Both H sources use the same physical U constraint sum(flow)<=8; V is independent. Source/target/path constraints are additional bounds, never extra copies of U capacity.",
                        scope="Three logical source pools need not mean three physical nodes. No fallback or relay bypasses either cut, direct private paths are sufficient, ingress at least seven per target, zero setup and fluid streaming."),
        assumption_counterexamples=dict(
            independent_dram_fallback=dict(cross_layout_time_s=fallback_t, S1_to_P_GiB=F(48, 7),
                                           DRAM_to_P_GiB=F(8, 7), S0_to_Q_GiB=8,
                                           DRAM_GiBps=1, conclusion="An independent positive-rate DRAM fallback invalidates the every-source-exports-eight premise; merely saying DRAM is slow is insufficient."),
            relay=dict(greedy_layout_relay_time_s=F(4, 3), relay_route="S1->P->Q at six GiB/s, S0->P own bytes at eight GiB/s",
                       required="P ingress at least fourteen and separate relay egress at least six; fluid zero-latency forwarding",
                       conclusion="Greedy-layout time eight requires no such relay. The S1-cut lower bound still holds for the global optimum.")))


def existing_plateau():
    sys.path.insert(0, str(ROOT / "main_lib"))
    from fluidckpt.benchmarks.frozen_layout import evaluate, strong_simple
    from fluidckpt.cluster.multi_joint import joint_scenarios
    workers = [dict(worker=i, node=f"symbolic_node_{i}", gpu=0, numa=0,
                    capabilities=["local_dram", "local_hbm", "network"],
                    used_bytes=dict(hbm=0, dram=8 * GIB if i >= 4 else 0),
                    budget_bytes=dict(hbm=4 * GIB if i < 4 else 0, dram=8 * GIB))
               for i in range(8)]
    def replica(i, worker, n, tier, label):
        return dict(id=f"{label}/{i}", identity="fixed_tp4_symbolic", tp=i,
                    off=0, n=n, worker=worker, node=workers[worker]["node"],
                    gpu=0, numa=0, tier=tier, state="VALID", generation=1, leases=0)
    base = [replica(i, i + 4, 8 * GIB, "dram", "base") for i in range(4)]
    candidates = [replica(i, i, 4 * GIB, "hbm", "prefix_cache") for i in range(4)]
    targets = [dict(key=f"target/{i}", identity="fixed_tp4_symbolic", tp=i,
                    off=0, n=8 * GIB, worker=i, tier="hbm") for i in range(4)]
    scenarios = [dict(name="fixed_simultaneous_tp4_targets", weight=1, targets=targets)]
    gb = GIB / 1e9
    profile = dict(rates=dict(local_hbm=4 * gb, local_dram=4 * gb, network=gb),
                   gpu_ingress_GBps=100 * gb, gpu_egress_GBps=100 * gb,
                   dram_GBps=100 * gb, network_node_GBps=100 * gb,
                   network_pairs=[[i + 4, i] for i in range(4)], serial_target_execution=True,
                   provenance="SYMBOLIC: four independent source/target pairs, no shared bottleneck below four GiB/s")
    locations = {f"{i}:hbm": 4 * GIB if i < 4 else 0 for i in range(8)}
    case = dict(workers=workers, base=base, candidates=candidates, restore_scenarios=scenarios,
                objective="expected", budget=dict(tier_budgets={"hbm": 16 * GIB},
                location_budgets=locations, base_dram_bytes=32 * GIB,
                target_allocation_bytes=32 * GIB, staging_bytes=0))
    finite = []
    for count in range(5):
        for subset in itertools.combinations(candidates, count):
            score = evaluate(case, profile, list(subset))["objective_s"]
            expected = 5 if count == 4 else 8
            assert abs(score - expected) < 1e-8, (count, score)
            finite.append(dict(selected_ids=[c["id"] for c in subset], objective_s=score))
    simple = strong_simple(case, profile)
    joint = joint_scenarios(base, candidates, scenarios, workers, profile,
                            {"hbm": 16 * GIB}, locations)
    assert simple["selected"] == []
    assert all(row["selected"] == [] for row in simple["controls"].values())
    assert len(joint["selected"]) == 4 and joint["status"] == "optimal"
    assert abs(joint["rounded_plan_s"] - 5) < 1e-8
    refs = ["main_lib/fluidckpt/benchmarks/frozen_layout.py",
            "main_lib/fluidckpt/cluster/planning.py", "main_lib/fluidckpt/cluster/multi_joint.py"]
    return dict(fixture=case, profile=profile, finite_subsets=finite,
                source_refs=[dict(path=p, sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in refs],
                existing_strong_simple=dict(selected_ids=[], winner=simple["winner"],
                    objective_s=evaluate(case, profile, simple["selected"])["objective_s"],
                    controls={name: dict(selected_ids=[c["id"] for c in row["selected"]],
                                         objective_s=row["evaluation"]["objective_s"])
                              for name, row in simple["controls"].items()}),
                existing_joint=dict(selected_ids=[c["id"] for c in joint["selected"]],
                                    status=joint["status"], objective_s=joint["objective_s"],
                                    rounded_plan_s=joint["rounded_plan_s"]),
                simple_bottleneck_balanced_reference=dict(selected_ids=[c["id"] for c in candidates], objective_s=5),
                qualification="Existing planner fixture verified under its target-additive bandwidth model. No source-ready/pair/order/setup model or real TP runtime was verified.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--existing-planners", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = dict(development="v0.1.23.3/task2", gpu_launches=0, remote_operations=0,
                  product_changes=0, measured_rates=False, proofs=fixed_proofs(),
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if args.existing_planners:
        result["existing_planner_plateau"] = existing_plateau()
    (args.out / "validation.json").write_text(json.dumps(exact(result), indent=2) + "\n")
    (args.out / "report.md").write_text(
        "# 固定理论例子的 CPU 验证\n\n"
        "本轮参数为理论符号值，无硬件测量、GPU、远端操作或产品改动。\n\n"
        "- 等大、固定独立收益、基数约束：top-k 的交换论证与六个 subset 一致。\n"
        "- 稀缺位置：特定顺序 whole-block 策略 8s，联合 assignment 及分片最优 4/3s。分片最优不唯一。\n"
        "- TP4 plateau：所有 proper subset 都为 8s，四个 prefix 全选为 5s；单点/两点严格正收益贪心停在空集。\n"
        "- 共享 cut：H 两源共享一个 8GiB/s 出口，另一独立池 6GiB/s；whole-block 最优 4/3s，流体分片最优 8/7s。\n"
        "- 独立 DRAM fallback 能破坏稀缺位置下界；快速 relay 能拯救原贪心布局，假设须明确。\n\n"
        + ("已直接调用原 strong_simple 和 joint_scenarios：前者三种 control 均选空、8s；后者选四个候选、5s。有限枚举 16 个 subset 复核。简单 bottleneck-balanced 策略同为 5s，所以不证明 MILP 必需。\n\n" if args.existing_planners else "本次未调用产品 planner。\n\n")
        + "具体输入、源码 SHA、枚举与结果见 validation.json。理论时延不可写入正式硬件 profile；真实 source/pair 依赖、setup、DRAM 条件与资源账本仍须独立验证。\n")
    print(json.dumps(dict(out=str(args.out), existing_planners=args.existing_planners, status="passed")))


if __name__ == "__main__":
    main()
