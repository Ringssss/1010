# 最小决策验证独立范围审查

日期：2026-10-10。开发编号：v0.1.23.3/task2。本审查仅本地读代码、既有原始证据与 CPU 反例计算；未访问远端、未启动 GPU、未改产品或正式文档。

## 范围裁决

当前两 worker 的原 serial 执行器可以检验有限预算下的布局、取源和串行操作成本。它不能证明多个恢复流同时争用共享网络时的联合规划收益。Worker._execute 对每个参与 worker 按共同 op 顺序逐项执行，每 chunk synchronize；两 worker 的全部跨节点操作都使用同一 pair，正反方向依次执行。double_buffer 也不是多个 pair 的并发争用证明。

报告34的 A/B/C/D 已分别验证两 local 独立、local 后正向、反向后 local、反向后正向的关系。A 在总32MiB、各位置32MiB上限和固定目标条件下可全量就近，已经是强 baseline；这种条件没有需要新增 solver 才能兑现的增量。原 layout_validation/review_final_r02 对同预算、路径、SHA、lease和退出共116条件通过，同时明确 solver_decision_increment_proven=false、performance_ready=false。

同 GPU 上的两块各16MiB目标、两位置 cache 上限各16MiB的候选场景，可以减少原 A 的自由度。该场景是合成块恢复，不是 TP2 模型可用验证；真实 TP2 的两个 rank 不能因薄 runner 允许两目标同 worker 而宣称已测。两块对称、目标都在28时，每次恢复操作都经过 worker0，字节服务成本可相加。若双方选择相同缓存字节与来源，只因 rank/ID 的 tie-break 不同而换块，不能认定科研决策增量。

## 已有组件与具体缺口

- planning.restore_plan 已有 nearest/fastest/balanced/resource_lp；multi_joint.joint_scenarios 已有有限候选、expected/worst、互斥未来场景，不能说这些能力未实现。
- service_cost.estimate_execution 已有 src/dst ready 的依赖图、每 op 固定成本、first-pair 和 request/dispatch 成本。此次不需要另建 DAG scorer。
- frozen_layout.strong_simple 已提供相同 candidate copies 下的 singleton/paired-rank marginal greedy、fastest/resource_lp 及现有 joint_greedy；优先复用。
- managed.py 的服务评分只重排 bandwidth_milp/base_only/current 三种布局。它既不引入现成 strong_simple proposal，也不重做 resource_lp 的路由；不能把 service_reranked 称为全局服务成本最优。
- resource_lp 的 resource max 与 target serial 约束是下界/近似评分。它未计每 op 固定成本，未完整计 source/pair 顺序依赖。若单流有效速率被同时用作路径和共享容量，不会自动获得真实共享瓶颈能力。

CPU 反例见 dependency_audit.json：显式合成 local=10GB/s、remote=1GB/s、其他容量100GB/s、每块16MiB且所有固定成本0。A 下界等于依赖成本；B/C 下界漏掉 local 与 network 的前后顺序；D 的依赖成本恰好为模型下界2倍。四条件通过。这里的值用于验证代码模型边界，不是硬件预测或实测。

## 强 baseline 与公平性

小 case 的主对照为现成 strong_simple 的最佳合法控制，同一 base、candidate copies、所有合法远端来源、复制机会、page rounding、位置/全局上限、executor、顺序、chunk、warm状态和计时边界。nearest/fastest保留为机制消融。若候选少，列举全部合法子集仅作为审计 oracle，不增加产品 solver。

缓存预算是上限：任何方法均可少占缓存、不选远端副本；不得要求 baseline 用满32MiB而让 joint 少用。缓存副本建立时间和暂存另列；完整底本、全部目标分配与 runtime 同账。最终比较也需双方使用相同取源策略或交叉比较布局×路由，否则把缓存位置和 LP 路由能力混为一个收益。

## 最少测量与预登记

只测待执行计划实际需要的 directed source-tier→target 路径。两目标在28且底本在28的候选最小集合通常是 localDRAM28→28、localHBM28→28、remoteHBM30→28；若计划确实使用 remoteDRAM 或目标30，才增补该路径。不得由旧64MiB DRAM探针或本次待验证布局反推 HBM 路径率。

每实际路径要区分 byte slope 与 fixed operation cost：两种 warm 字节数的单 op，加一个同总字节、多 op 的观察；若完全固定尺寸只需预测该点，可直接冻结独立相同尺寸路径值，明确不外推斜率。冷 pair 若恢复协议先 warm，则无需额外估冷成本；若声称冷恢复，必须观测 first-pair，不能由缓存建立已热路径冒认冷态。request/dispatch 与控制 prepare/verify/publish 单列，不能把两端 elapsed 相加或跨时钟相减。

拟合数据与验证数据分开：先冻结 calibrations、模型SHA、每方案 cache/route/op 顺序/数量、依赖 timeline、predicted execute、controller filled，以及明确误差容忍与可区分阈值。再执行未参与拟合的字节数或组合。既有 ABCD 可用于历史边界核对，不能重新拟合后把同一 ABCD 称为 holdout。当前一轮每配置一次，只有关键反例/漂移不清才第二次；不默认多轮统计。

## 可证伪 case 与停止条件

1. 固定目标、全量本地缓存可行：若 planner 没有比 A 更快的合法机制，则记无增量；不启动复杂建设。
2. 2×16MiB、cap16/16、两目标28：强greedy与joint若仅交换对称rank或选同路由字节，记同成本，无须 GPU 重复“算法对照”。若 LP 拆分 localDRAM/remoteHBM，预先记录增加的 op 数与 fixed cost，验证服务成本会不会抵消字节模型收益。
3. 在同预算下 joint 的模型优布局，若 dependency scorer 或 holdout执行不优于strong_simple，记录负结果或路由/候选集成缺口；不得更换指标或不断补有利场景救方法。
4. 校准不能在独立组合上区分候选，或控制/共享负载漂移高于预测差异，则暂停性能排名。冻结预算失败、SHA/路径/状态失败立即停止，保留原 run。
5. 没有分离 pair 同时执行与实际共享瓶颈的证据，physical_shared_contention_gain=false；synthetic filled不提升为完整模型T_ready。

## 验证记录

成功命令：`python3 codex_coding/src/v0.1.23.3/task2/decision_validation/review/dependency_audit.py --output codex_coding/results/v0.1.23.3/task2/decision_validation/review_scope_r01/dependency_audit.json`。四项模型边界检查通过，原产品源码SHA保存在JSON。第一次尝试调用 `python` 返回 command not found/127，随后使用已安装 python3；无GPU或远端失败。

本轮 measure_refresh_r01/summary.json 已为两容器身份、pids=1、noOOM、存储和两端口空闲给出只读 passed；该事实只是后续启动准入条件，不是校准样本或性能证明。后续独立验收需绑定启动前冻结预测和原 execution，禁止从待验收结果再拟合其自身。
