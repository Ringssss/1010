# TP4 C0/C1/C2 契约 CPU 结果

最终 run 为 `contract_cpu_r03`，固定四个 synthetic 输入，无 GPU、SSH、payload 分配、产品改动或新 solver。每 rank 两个4MiB块；目标/完整底本各32MiB。复用原 `strong_simple`、`joint_scenarios`、`resource_lp` 与 `service_cost.estimate_execution`，新增有限均衡对照和契约，不声明实机性能资格。

表中时间是归一化符号单位，括号为 cache MiB：

| 场景 | base-only | 本地填充 | 原 strong_simple | 瓶颈均衡 | 原 joint | 固定路由有限参考最优 |
|---|---|---|---|---|---|---|
| C0，全部本地可缓存 | 2 (0) | 0.5 (32) | 2 (0) | 0.5 (32) | 0.5 (32) | 0.5 (32) |
| C1，等预算平台期 control | 2 (0) | 1.25 (16) | 2 (0) | 1.25 (16) | 1.25 (16) | 1.25 (16) |
| C1，不均衡 `[2,1,0,1]` | 2 (0) | 2 (16) | 2 (0) | 2 (16) | 2 (0) | 2 (0) |
| C2，source4/5 共享 U | 4 (0) | 2 (16) | 2 (8) | 2 (16) | 2 (8) | 2 (8) |

C0 和 C1 control 复现原 strict-positive control 的平台期停止，简单本地填充和均衡均与 joint 同成本，不支持 MILP 必要性。不均衡 C1 因 rank2 无本地缓存，2 已是下界；base-only 支配用16MiB缓存但不改善主指标的填充/均衡方案。C2 的原 strong_simple 与 joint 都只需8MiB缓存达到2；其余两种规则使用额外8MiB仅改善次级 rank 向量。没有联合主指标增量，不改参数追加有利场景。

有限可行布局数依次256、81、36、36。原路由每个布局均有完整覆盖、无 fallback，预算检查通过；参考不是 baseline，也不搜索执行顺序或声明实际有限执行最优。完整 cache—主成本 Pareto 位于 `comparison.json`：C0 为 `(0MiB,2)/(16MiB,1.25)/(32MiB,0.5)`，C1 control 为 `(0,2)/(16,1.25)`，C1 uneven 只有 `(0,2)`，C2 为 `(0,4)/(4,3)/(8,2)`。

`contract.json` 保留四目标、八候选、底本、外部预算、所有角色和路径/ResourceID/incidence。`c1_c2_impact_audit.json` 实际比较字段及归一化资源：source4/5 的 node 标签仅表达共享出口假设；source 使用不同逻辑 GPU/NUMA，8个 `(node,gpu)` 角色唯一。DRAM raw ID 改名但稳定角色独立，目标网络入口、每 worker 资源、路径类别、速率、候选及预算不变；只有 source export0/1 合并 U，容量不重复相加。

每个算法输出覆盖有效的 rank-interleaved 抽象队列、source/target worker 前驱与原服务评分时间线。服务评分没有对 disjoint pairs 的共享 U 动态时长建模，因此 C2 的 base-only 服务分值2与资源下界4分开保留，不能将前者当作合法有限共享执行时长。路径物理容量、实际并发服务、真实 epoch/内容校验、模型权重 runtime binding 和实际主计时均未验证。

底本32MiB、目标32MiB、缓存0–32MiB、暂存每 worker64MiB合计512MiB分别计账；runtime 与完整物理峰值未知。暂存为未分配的保守预留，8个逻辑 worker 不意味着原2GiB/node预算可部署。全部功能/性能/物理准入 readiness=false，实际模型 binding/GPU command=null。

本轮 schema 使用已有 jsonschema Draft2020-12；额外核 TP4 身份/coverage、角色一致、候选/预算、C1/C2 唯一变量、源 SHA 和预期有限布局数。没有调用或绕改原 TP2 `validate_case`。最终源与 schema 快照见 `source_snapshot/`，命令与 rc0 见 `execution_metadata.json`。

`contract_cpu_r01` 是首轮中间结果，r02补空间判据后修正逻辑 GPU 冲突；两者原件保留，只新增 `superseded.json` 标注，不能用当前源 SHA 认证它们。最终资格以 r03 及独立 review 为准。本轮只包含本地缓存候选；remote-HBM 赋值和真实 TP4 模型实验仍待物理路径、成本、共享 incidence 与 runtime 预算关口，不据本轮结果继续策略 GPU 比较。
