# 有限候选决策检查：CPU 敏感性与停止条件

结论：没有发现已实测的 joint 增量。目标集中28、目标附近cache不足的最小场景在三个符号成本区间里，现有 `frozen_layout.strong_simple` 与 `multi_joint.joint_scenarios` 的统一有序服务成本都相同。签名差异来自等大小synthetic块的等价选择或顺序，不能据此安排策略性能对比。当前应冻结最小原语观察，成本仍为unknown；不继续扩场景寻找joint收益。

本轮CPU运行使用原 `restore_plan`、`joint_scenarios`、`strong_simple`、`service_cost.estimate_execution`。产品无改动，GPU/SSH/发信号次数为0。`contract.json`在CPU求解前写出，包含输入、候选、预算、停止条件、产品与脚本SHA；`inspection.json`保存每个合法layout、原ordered ops、依赖timeline和各方法选择，不只保存最终胜者。r01系统Python缺numpy而失败，原部分contract保留；r02使用已有sglang环境完成，不安装依赖。

## 冻结输入与公平性

固定28GPU0/BDF09与30GPU4/BDF85的两个worker身份坐标，但本CPU fixture的session/epoch是显式symbolic标记，不能用于实机控制。两块各16MiB synthetic，完整32MiB DRAM底本位于28；每块在每个worker各有一个合法HBM候选，共4个，允许重复缓存同块。cache总允许32MiB，每worker cache上限16MiB。这是预先规定的应用预算，不能称实际硬件已测空闲容量。完整底本32MiB、目标32MiB、每worker64MiB staging及未知runtime另计；物理2GiB准入和完整峰值须由父agent新检查。

受限case把两块目标都放28，因此28的16MiB cache只能容纳一块。它验证字节区间恢复，不能当作一个真实TP模型的两rank均映射同worker：现有topology case规范要求TP目标distinct，这里没有调用它来冒认模型资格。基线和joint使用相同底本、4候选、位置/总预算以及原LP objective；双方决策随后均用已有worker依赖scorer评估。

脚本中的 `diagnostic_ordered_marginal_enumerator` 是本轮诊断性边际枚举器，**不是现有strong_simple基线**。`finite_oracle`只枚举全部合法cache子集，并固定使用原fastest的整区间来源选择；它不是包括任意拆分、排列和固定开销的全局service最优解。这里声明的固定/dispatch/冷pair成本为0，不是拟合，也不是实际测量结论。

## 有界符号结果

所有毫秒值均为人为声明的敏感性输入，完全不作为实测率或profile。每个候选子集最多含2份cache，本轮仅两个case、各三个区间，没有参数搜索。

| case | 符号输入（ms/16MiB） | 原strong_simple有序成本 | 原joint有序成本 | 有限fastest枚举最小 | 解释 |
|---|---|---:|---:|---:|---|
| 原分散目标 | localHBM1/networkHBM3/DRAM40 | 1 | 1 | 1 | 双方A、签名相同，停止对比 |
| 原分散目标 | localHBM2/networkHBM2/DRAM40 | 2 | 4 | 2 | LP等价tie选D；已有scorer显式区分依赖 |
| 原分散目标 | localHBM3/networkHBM2/DRAM40 | 4 | 4 | 3 | 双方同选D，LP模型共同误排A/D |
| 两目标28、cap不足 | localHBM0.5/localDRAM1/remoteHBM3 | 1.5 | 1.5 | 1.5 | 只缓存一块28，另一块取DRAM |
| 两目标28、cap不足 | localHBM0.5/localDRAM2/remoteHBM2 | 2.5 | 2.5 | 2.5 | remote与DRAM等价，未认证收益 |
| 两目标28、cap不足 | localHBM0.5/localDRAM3/remoteHBM1 | 1.5 | 1.5 | 1.5 | 一块本地HBM、一块远端HBM |

后三行的缓存block身份或local/remote执行先后可能不同；两个相同大小块具有相同符号成本，统一有序目标相同。不能把不同SHA签名直接当成分歧收益，也不能据符号区间认定当前硬件属于其中哪个区间。

## 旧LP边界与现有评分器

原worker `_execute`按每worker相关op顺序运行，每chunk同步；不同worker不相交的local操作可独立推进。两个worker的所有network操作则占同一pair，D的reverse后forward必须相继完成。原 `planning.py` / `multi_joint.py` 的约束包含target服务和方向resource load，但缺少完整source/pair顺序依赖：在高resource cap符号条件下，A与D都可能给出max，两次network的真实依赖应给sum。

这里直接复用 `service_cost.estimate_execution`，由每个op的src/dst ready推进timeline，没有另建模拟器或求解器。报告34和 `layout_validation/review_final_r02/qualification.json` 的A/D逐worker roles、op次序证明的是现有执行依赖；未拿历史逐op elapsed当这张表的固定成本，也未由已测填充结果反算rate自证。

`managed.py`已有service rerank，仅比较bandwidth-MILP proposal、base-only和current。因此current=A时可拒绝更慢D；current为空时不会自动提出A。最小后续若实测确实触发误排，是复用该scorer评估现成simple proposal和有限候选，而不是先新建solver。当前没有实测证据授权声称该修正带来性能收益。

## 最小观察与停止

受限case真正需要区分的是28 localDRAM vs30 HBM→28的同primitive 16MiB服务，另需28 localHBM用于公共缓存路径。完整底本始终32MiB@28，不能为测30 DRAM另加不入账底本。可由父agent协调同原Cluster.transfer/Worker冷暖状态、单op观察及独立的localHBM+DRAM、localHBM+reverseHBM组合holdout；prepare/execute/filled/verify-publish各自保存。无需以正向HBM或微测64MiB的GBps代替这些路径。

真实路径成本、fresh预算、完整物理峰值和真实决策差异仍unknown。若同primitive观察给出strong_simple/joint同成本或同决策，停止策略GPU比较和候选扩张；若仅签名等价置换，同样停止。只有独立实测使有限已冻结候选出现有意义的有序服务差异，才交父agent决定必要的一次实机对照。符号依赖反例本身不触发GPU实验。

复现：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/decision_validation/case/inspect_finite.py --out codex_coding/results/v0.1.23.3/task2/decision_validation/case_cpu_NEW
```

输出目录必须新建，脚本拒绝覆盖已有run。r02实际运行exit0，6个符号配置完成；产品、新脚本和结果路径由独立review检查后再作最终资格结论。
