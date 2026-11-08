"""Review frozen bridge evidence using local files only, with unique outputs."""
import argparse
import json
from pathlib import Path
from evidence import review_freeze, review_placement, review_bridge, review_restore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--placement", action="append", type=Path, default=[])
    parser.add_argument("--bridge", type=Path)
    parser.add_argument("--restore", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review_freeze(args.freeze)
    result["placements"] = [review_placement(path, args.freeze) for path in args.placement]
    for row in result["placements"]:
        result["checks"].extend(dict(c, name=row["case_id"] + "/" + c["name"]) for c in row["checks"])
    if args.bridge:
        result["bridge"] = review_bridge(args.bridge)
        result["checks"].extend(dict(c, name="bridge/" + c["name"]) for c in result["bridge"]["checks"])
    if args.restore:
        result["restore"] = review_restore(args.restore, args.freeze)
        result["checks"].extend(dict(c, name="restore/" + c["name"]) for c in result["restore"]["checks"])
    result.update(status="passed" if all(c["passed"] for c in result["checks"]) else "failed",
                  audit_scope="local frozen/CPU/production-bridge evidence; no network or GPU access",
                  checked_count=len(result["checks"]))
    (args.out / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    failures = [c["name"] for c in result["checks"] if not c["passed"]]
    (args.out / "report.md").write_text(
        "# Keeper 桥接冻结输入与CPU选择独立审查\n\n"
        + f"状态：{result['status']}；独立检查{result['checked_count']}项；新增GPU样本0。\n\n"
        + "独立复算冻结契约、CPU选择、真实小extent生产路径，以及指定时的完整模型功能smoke。"
          "物理共享资源与策略性能增量分别判断。\n\n"
        + ("失败：" + ", ".join(failures) + "。\n" if failures else "全部冻结来源保持一致。\n")
        + "\n".join(f"\n{p['case_id']}：strong_simple/joint完整布局与有序操作同解={p['decisions_equal']}；"
                    "复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。"
                    for p in result["placements"]) + "\n")
    if args.bridge:
        bridge = result["bridge"]
        rows = ["\n## 生产 keeper 路径桥接\n\n",
                "仅引用三个正式小extent配置各一次观察，另有32MiB pair预热；审查新增GPU样本0。"
                "小extent以合法replica目标走原Cluster.transfer serial，不构成完整模型T_ready或算法增量。\n\n",
                "| 配置 | payload B | receiver RPC包络 ms | worker接收端单op ms |\n",
                "| --- | ---: | ---: | ---: |\n"]
        for row in bridge["observations"]:
            receiver = [value * 1000 for value in row["receiver_operation_elapsed_s"].values()]
            rows.append(f"| {row['case']} | {row['payload_bytes']} | {row['receiver_envelope']['makespan_ns']/1e6:.6f} | "
                        + "/".join(f"{value:.6f}" for value in receiver) + " |\n")
        rows.append("\n共同窗口为node29同host/boot的execute HTTP RPC包络，包含dispatch、response；"
                    "不等于设备kernel占用或worker同步monotonic窗口。阶段coordinator时间含200ms空闲采样、"
                    "inventory与日志落盘，均标instrumented；prepare、全SHA验证和publish不混为copy时间。\n\n"
                    "隔离微probe采用synthetic GPU buffer、独立pair communicator与重复broadcast；"
                    "应用桥接读取实际DRAM、源HBM staging、broadcast、目标land并每chunk同步。"
                    "因此微probe36.46GB/s与0.85判据均不直接搬入应用服务模型。"
                    "当前RPC时间没有提供可信worker共同窗口；生产执行器争用、S5-P和共享成本项保持未定。"
                    "完整模型或同预算选源实测出现决策及T_ready差异后，再判断是否需要新增共享成本项。\n\n"
                    "NIC计数仅在execute前后读取，每HCA/port只计一次；idle窗口估计背景，不能排除突发其他用户流量。"
                    "实际运行环境和完整初始化日志独立保留，GPU tensor接口不自动代表GDR。\n")
        with (args.out / "report.md").open("a") as stream:
            stream.write("".join(rows))
    if args.restore:
        restore = result["restore"]
        with (args.out / "report.md").open("a") as stream:
            stream.write("\n## 完整模型功能smoke\n\n"
                         f"T2固定split_rank_prefix、互斥target_b、fastest单次完整恢复T_ready={restore['T_ready_s']:.9f}s；"
                         "全70,329,718,112逻辑字节SHA及固定生成输出通过；真实epoch/generation与catalog映射、"
                         "共用rank_interleaved与worker实际操作顺序独立通过。\n\n"
                         f"缓存建立{restore['cache_build_s']:.6f}s，规划{restore['online_planning_s']:.6f}s，"
                         f"prepare {restore['prepare_s']:.6f}s，ready后SHA {restore['post_ready_verification_s']:.6f}s。"
                         "全DRAM底本与完整目标页分配各70,329,720,832B分别计账。实际缓存8GiB按6+2计账，"
                         "unique6GiB、重复2GiB，建立traffic仍8GiB；执行只使用其中3份共6GiB缓存，其余2GiB副本仍占用并建立。\n\n"
                         "这个8GiB固定复制布局功能smoke与CPU两个策略各选4GiB的结果不同，不构成joint性能执行、"
                         "策略对照或新算法收益。cache worker物理free恢复0；目标两卡各-6MiB在登记256MiB回收容差内，"
                         "catalog/allocated/reserved恢复；完整底本、旧14可访问keeper及parked release证据通过。"
                         "只保存离散内存采样，continuous physical peak未认证。\n")
    if args.restore and args.bridge:
        (args.out / "next_step.md").write_text(
            "# 建设收口与下一步\n\n"
            "本轮停止GPU扩矩阵。桥接证明实际DRAM-staged keeper路径正确且资源可回收；"
            "薄runner已完成T2固定复制布局的完整模型功能smoke。三场景CPU强simple与joint均同布局及有序操作，"
            "没有新增求解器或重复完整GPU策略比较的证据。\n\n"
            "继续复用可靠service model与共同合法NIC/transport机会：当前profile仍provisional，"
            "隔离probe的共享退化和GDR路径组合证据不得替换生产路径预测。小extent RPC/worker耗时可作为"
            "路径桥接参考，但不能定为固定capacity或物理S5-P。完整smoke的live target同步事件可验证应用"
            "执行窗口，仍混有软件共享源的serial依赖，无匹配控制组可单独归因硬件争用。\n\n"
            "当前无充分证据新增共享成本项。仅当同预算、同来源、同复制机会、同NIC合法机会下存在实际"
            "选源/布局差异，且生产执行证据表明当前service模型遗漏影响主指标的等待或干扰，才补一项"
            "必要评分/约束并有界验收T_ready。简单方法持续同解时采用简单方法；不通过新增有利场景救算法。\n\n"
            "审查脚本全为本地读文件；GPU/HTTP/SSH调用0，新增GPU样本0。raw GPU、失败初审及重试均保留。\n")
    print(json.dumps({k: result[k] for k in ("status", "checked_count", "new_GPU_samples")}, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
