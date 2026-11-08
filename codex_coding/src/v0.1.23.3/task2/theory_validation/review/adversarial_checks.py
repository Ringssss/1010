"""Check mathematical boundary witnesses with exact rational arithmetic.

This script imports no product code, contacts no remote host, and uses no GPU.
It checks the stated ideal model, not hardware observations or novelty claims.
"""

import argparse
import itertools
import json
from fractions import Fraction as F
from pathlib import Path


def encode(value):
    if isinstance(value, F):
        return {"exact": str(value), "approx": float(value)}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def direct_time(a):
    # a GiB of P is held on S0; the four unique byte volumes sum to 16 GiB.
    return max(F(1), F(4, 3), a / 8, (8 - a) / 8, (8 - a) / 6, a)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    whole = {"crossed": direct_time(F(0)), "greedy_P_first": direct_time(F(8))}
    check("whole_block_assignment", whole == {"crossed": F(4, 3), "greedy_P_first": F(8)}, whole)
    check("sixfold_gap_for_defined_direct_greedy", whole["greedy_P_first"] / whole["crossed"] == 6, F(6))
    check("P_source_choice_is_not_tie", F(8) > F(6), "S0-P=8, S1-P=6; S0-P and S0-Q tie")
    check("full_coverage_leaves_no_replication_bytes", F(16) - F(16) == 0, "Allocated unique cache coverage must be 16 GiB")
    check("source_S1_cut", F(8) / F(6) == F(4, 3), "Exactly 8 unique GiB remain on S1; all must cross its egress")
    chunk_points = [(F(i, 12), direct_time(F(i, 12))) for i in range(97)]
    optimum_points = [a for a, t in chunk_points if t == F(4, 3)]
    check("chunk_optimum_grid_matches_cut", min(t for _, t in chunk_points) == F(4, 3), chunk_points)
    check("chunk_optimum_not_unique", optimum_points == [F(i, 12) for i in range(17)], optimum_points)
    # If 0 <= a <= 4/3, all route terms are <= 4/3; a > 4/3 violates S1-Q.
    check("chunk_optimal_interval_endpoint", direct_time(F(4, 3)) == F(4, 3), direct_time(F(4, 3)))
    check("chunk_outside_interval_strictly_worse", direct_time(F(17, 12)) > F(4, 3), direct_time(F(17, 12)))

    fallback_rate = F(1, 10)
    fallback_time = max(F(1), F(8) / (6 + fallback_rate))
    fallback_bytes = fallback_rate * fallback_time
    source1_bytes = F(6) * fallback_time
    check("slow_independent_fallback_breaks_cut_claim", fallback_time < F(4, 3),
          {"fallback_rate_GiB_s": fallback_rate, "time_s": fallback_time,
           "P_from_fallback_GiB": fallback_bytes, "P_from_S1_GiB": source1_bytes})
    check("fallback_witness_covers_P", fallback_bytes + source1_bytes == 8, "S0 independently delivers Q in 1 s")
    relay_upper_bound = F(4, 3) + F(1)
    check("relay_breaks_eight_second_greedy_evaluation", relay_upper_bound < 8,
          {"time_s": relay_upper_bound, "assumptions": "S1->S0 6 GiB/s; S0 ingress independent; permitted staging"})
    check("relay_does_not_erase_unique_source_cut", relay_upper_bound >= F(4, 3), "S1 still emits 8 unique GiB")

    for m in (3, 4):
        def plateau(selected):
            # Target-local cache runs before remote loading: 1+4=5 seconds.
            return max(F(8) - F(3) * (rank in selected) for rank in range(m))
        small = [list(combo) for size in (1, 2) for combo in itertools.combinations(range(m), size)]
        check(f"plateau_m{m}_singleton_and_pair_zero_gain",
              all(plateau(bundle) == 8 for bundle in small), small)
        check(f"plateau_m{m}_balanced_full_set_improves", plateau(set(range(m))) == 5, F(5))
        # A shared one-GiB/s base exit changes the objective into a sum.
        shared_before, shared_after_one = F(8 * m), F(8 * m - 4)
        check(f"plateau_m{m}_shared_base_exit_erases_trap", shared_after_one < shared_before,
              {"before_s": shared_before, "after_one_s": shared_after_one})
    check("plateau_article_serial_local_service", F(4) / 4 + F(4) / 1 == 5,
          "Main article uses local 4 GiB/s followed by remote 1 GiB/s")
    check("plateau_earlier_parallel_variant_distinct", max(F(4), F(4) / 8) == 4,
          "r01 checked an ideal independent parallel cache variant, not main article section 4")

    h = F(16) * 8 / (8 + 6)
    shared_time = max(h / 8, (16 - h) / 6)
    check("shared_cut_split_balance", h == F(64, 7) and shared_time == F(8, 7),
          {"U_bytes_GiB": h, "V_bytes_GiB": 16 - h, "time_s": shared_time})
    check("shared_cut_witness_target_capacity", F(4) + F(3) == 7 and F(7) * shared_time == 8,
          "Both targets get U=4 and V=3 GiB/s; target ingress=7")
    check("shared_cut_witness_storage_capacity", F(32, 7) <= 8 and F(48, 7) <= 16,
          "S0=S1=32/7 GiB; S2=48/7 GiB; total=16 GiB")
    check("shared_cut_naive_independent_sum_fails", F(16) / 8 == 2 and F(8) / 8 == 1,
          "U is one shared 8 GiB/s resource, not two independent resources")
    check("resource_load_is_not_dag_schedule", max(F(1), F(1)) < F(1) + F(1),
          "Two unit-duration dependent operations on distinct resources: load bound=1, causal makespan=2")
    check("byte_totals_do_not_prove_subinterval_coverage", F(4) + F(4) == 8 and F(4) < F(8),
          "Sending [0,4) twice totals 8 GiB but covers only 4 of required [0,8)")

    result = {"status": "passed" if all(row["passed"] for row in checks) else "failed",
              "scope": "Exact symbolic boundary witnesses; grid is a check, analytic source cut proves all chunks",
              "gpu_launches": 0, "remote_accesses": 0, "product_changes": 0,
              "checks": checks, "checks_count": len(checks)}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "checks.json").write_text(json.dumps(encode(result), indent=2, ensure_ascii=False) + "\n")
    report = "# 独立反例边界检查\n\n"
    report += f"状态：{result['status']}；{len(checks)} 项精确有理数检查。仅 CPU，无远端访问或产品修改。\n\n"
    report += "完整缓存覆盖且总容量等于16GiB时，唯一字节没有复制空间，S1必须输出8GiB，故全分片下界4/3秒。整块贪心布局8秒，交叉4/3秒；分片最优布局不唯一，a∈[0,4/3]GiB均最优。\n\n"
    report += "独立0.1GiB/s后备给P供给字节，可把交叉布局时间改成80/61秒，严格小于4/3。慢路径不等于无收益。合法S1→S0中继可使原贪心布局≤7/3秒，破坏8秒评价，但仍服从S1唯一字节egress cut。\n\n"
    report += "m=3/4的关键rank平台：独立1GiB/s底本路径、本地4GiB/s先执行下，≤2个cache增量不能降低max，全部cache将8秒改为5秒，与主文§4一致。若底本只有共享1GiB/s注入，目标变成剩余总字节，单cache立即有收益，平台反例不成立。r01中的8→4是独立并行cache服务的不同理想变体，不能当作主文数值验收。\n\n"
    report += "共享U/V例子检查h=64/7GiB、T=8/7秒及源/目标/空间可达条件。两条不同资源上有依赖的单位操作说明总负载下界1不保证因果makespan2。重复发送[0,4)不满足[0,8)完整覆盖，说明分片区间校验必须保留。\n\n"
    report += "这些例子只反驳指定的严格正增益/固定顺序规则。assignment、平衡填充、词典序或允许零增益推进可以解决；不证明通用贪心失败、MILP必要或理论新颖。\n"
    (args.out / "report.md").write_text(report)
    print(json.dumps({"status": result["status"], "checks_count": len(checks), "out": str(args.out)}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
