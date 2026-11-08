# TP4 小场景契约与离线对照

保持 `v0.1.23.3/task2`。本轮复用报告36的四目标 fixture，只建设容量与瓶颈均衡对照契约，不修改产品算法，不建立新 solver。输入与输出都是 synthetic；没有真实模型参数、实际 GPU/节点绑定或恢复实验。

`build_contract.py` 读取冻结的 `theory_validation/proofs_cpu_r01/validation.json`，沿用四个独立 source/target 角色，将 GiB 级理论区间改为每 rank 两个 4MiB 块。四 rank 的完整 DRAM 底本和目标 HBM 各32MiB；八个候选均为本 rank 的本地 HBM 块。逻辑 worker 0–3 为目标，4–7 为完整底本来源；所有 case 保持候选集合，零容量位置的候选仍在契约中，由预算检查拒绝。

| Case | 本地 cache 上限，单位4MiB | 总 cache 上限 | 目的 |
|---|---|---|---|
| C0 | `[2,2,2,2]` | 32MiB | 容量足够时最简单本地填充可覆盖全部目标 |
| C1 equal control | `[1,1,1,1]` | 16MiB | 保留理论多目标平台期，验证均衡对照可前进 |
| C1 uneven main | `[2,1,0,1]` | 16MiB | 固定不均衡预算，rank2不可缓存，允许无主指标增量 |
| C2 | 同 C1 uneven | 16MiB | 仅将 source4/5 独立出口约束合并为共享 U |

C2 的 node 标签合并是离线物理假设。source4/5 使用不同逻辑 GPU 与 NUMA 编号，没有搬动实际 worker。`c1_c2_impact_audit.json` 枚举全部 case 字段变化及 raw ResourceID 别名影响，并按稳定角色 ID 核对：仅两个出口共享关系变化，DRAM 仍独立，目标入口、每 worker 资源、network 类别及路径系数不变。一个共享 U 只计算一次容量，不能把两个来源各自的容量相加。

理论服务单位固定：一个4MiB块经 remote 消耗1个单位时间、本地消耗0.25；源网络出口容量为1块/单位时间，其他资源容量为100块/单位时间。原 planner 与 service scorer 的 `*_s` 字段沿用其 API 命名，此处均为归一化理论时间，不能视为实测秒数或写入硬件 profile。

固定对照包括原 `strong_simple` 的全部 control、最简单 `local_capacity_fill`、有限 `bottleneck_balanced` 和原 `joint_scenarios`。所有方法使用相同完整候选、底本、直接 unicast 路径与预算，由原 `resource_lp` 统一重评。均衡规则每次只试一个可行候选，以“全局资源成本、降序四 rank 串行服务时间向量”的字典序作严格改善，允许全局成本不变时继续均衡；ID确定破平局，不做删除、交换、bundle或枚举前瞻。规则冻结于输出 `baseline_rules`。

有限枚举最多256个 subset，只作同 route-policy、同 rounding 的参考，并按“主成本、最少 cache、ID”选 oracle。它不作为 baseline，也不声称求得真实有限执行最优。输出保留 cache—主成本 Pareto、base-only、每策略超过同等主成本最小 cache 的字节数；rank 次级改善不能冒称空间—主指标收益。

每个策略的队列由原 LP 计划及 `rank_interleaved` 顺序生成，附 source/target worker 前驱依赖。`service_cost.estimate_execution` 只重评孤立路径符号时长与 worker 顺序；disjoint pairs 共享 U 的动态时长没有被它建模。资源下界与有限队列时长分别输出，均不宣称真实 `T_ready`。

账本分开记底本32MiB、目标32MiB、每 case cache、暂存每 worker64MiB（八角色合计512MiB，已知保守预留而非已分配）和 runtime unknown。沿用原2GiB/node预算，不因8个逻辑角色扩配额；正式物理准入与全部 readiness 均为 false，GPU command 与实际模型 runtime binding 为 null。

运行入口使用已有 Python 环境；输出目录必须不存在，不覆盖旧 run：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/tp4_scenarios/contract/build_contract.py --out codex_coding/results/v0.1.23.3/task2/tp4_scenarios/contract_cpu_r03
```

`schema.json` 为本轮 TP4 schema，由已有 jsonschema 做 Draft2020-12 检查；另有少量覆盖、预算、角色一致与 C1/C2 唯一变量检查。没有套用或改写原 TP2 `topology_cases.validate_case`。

未来 remote-HBM 候选或一般位置赋值只有在实际路径、共享 incidence、同原语成本、runtime/暂存余量与真实模型绑定成立后另行登记。本轮没有这些机会，不能据此主张复杂联合规划有益。同解或强简单规则同成本就记录无增量，不追搜参数，不追加 GPU 对照。
