# 多源放置竞争独立审查口径

开发编号保持 `v0.1.23.3/task2`。本关交付有意义的多位置竞争与结构条件；不要求 joint 胜出。仅 CPU 审查，不刷新远端、不运行 GPU。

## 固定准入状态

既有 TP4 feasibility_r03 的两节点八 worker、三节点八 worker和两节点四 worker 复用方案均未通过任务预算准入。原任务额度保持每节点 DRAM/HBM 各 2GiB。单 worker 线性投影不是物理占用下界；四 worker source/target 复用还改变操作依赖。原 keeper 的 session/mesh/热加载源码资格不能从磁盘或旧 catalog 推断。本关 `physical_budget_admitted=false`、`gpu_command=null`。

## 内容与公平性

- 每个不同内容块明确模型身份、TP/区间、目标；完整 DRAM 底本不随策略变化。
- 每块至少两个不同合法 HBM 来源位置，不能将多个目标的本地缓存重新命名为多源放置。
- 所有策略共享完整候选、每位置/全局预算、后备来源、允许重复副本及相同取数政策。零配额候选保留并由预算拒绝。
- 恢复目标、cache、底本、staging、runtime 和建立/切换峰值分别记账；逻辑额外 cache 字节不能称作物理峰值。

## 决策与独立计算

原 strong-simple 的 singleton/paired 控制保留，另包含不用 oracle 的容量分配/交换或瓶颈均衡对照。说明具体规则；有限反例只能证明这些规则的失败，不能证明普通方法或 MILP 的必要性。

先登记路径速率、共享资源关联、场景和预算，再计算结果。速率为理论参数且物理容量未知；不循环调参数寻找 joint 胜出。同成本/同占用、候选 ID 置换及非关键 rank 改善不算主指标增量。

独立审查按输出操作逐条核覆盖、已选来源、资源 incidence 与容量，直接累计 path/resource 负载和 target/source 服务需求；按操作顺序复算 `start=max(source_ready,target_ready)` 的 worker DAG。有限 layout 表只作为固定既有取数政策的参考，审查完整可行 subset 个数、最小分数所需 cache 和 Pareto，不另跑重复 solver 矩阵。

共享硬件下资源下界与 worker DAG 两个评分分列。它们的 max 不自动成为同一 schedule 的可达时间；只有提供共同合法时序才可认定可达。部署、实测速率、真实 TP 模型绑定和 T_ready 各自保持未知。

## 冻结与负结果

最终审查绑定输入合同、比较结果、源码和 objective 证据的 SHA。生成结果使用新 run_id、禁止原件覆盖。失败与修订各留原件；joint 与强对照同解时保留同解并停止无依据的 GPU 对比。
