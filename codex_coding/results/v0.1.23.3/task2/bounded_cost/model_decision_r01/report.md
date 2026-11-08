# 有界路径模型的决策含义

本轮三层组合检验冻结模型从隔离路径迁移到依赖请求的误差。模型已提供粗路径量级；同内容、同目标、同预算的布局收益需要另外建立合法对照合同。以下数值只读 `../model_frozen_r01/frozen_model.json`，模型 SHA `e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`，系数保持不变。

## 8MiB 路径量级与误差敏感度

| 路径 | 8MiB 服务预测(ms) | 在三层组合服务中占比 |
|---|---:|---:|
| localHBM w4→w4 | 0.280796 | 2.07% |
| remoteDRAM w2→w4 | 1.194229 | 8.81% |
| SSD w4→w5 | 12.087742 | 89.12% |
| 三路径依赖组合 | 13.562767 | 100% |

remote−local 为 0.913433ms，SSD−remote 为 10.893513ms。这是不同已校准路径的量级差，当前三个源对应不同内容身份，SSD 的目标 w5 也不同于另外两条路径的 w4，因此这些差值尚未构成同一恢复任务的替代布局收益。

local 的四点原始服务包络为 0.219493–0.333784ms，宽度 0.114291ms，均值为 0.280796ms。小于约 0.1ms 的局部候选差异与已有本地波动在同一尺度，现有数据不能提供稳定排序保证；包络宽度不是统计显著性阈值。约 0.913ms 和 10.894ms 的路径差量超过这一本地包络，但远端/SSD 的跨 session 误差与组合等待效应还需本轮实测约束，不能只按 local 包络认证这些差量。

共有 worker request residual 0.050530ms、controller residual 2.674446ms 各加一次，得到 mixed execute 16.287744ms；完整 filled 粗基准再加训练控制中位数 18.874934ms，得到 35.162678ms。相同公共项不会改变模型里的数学排序，但会稀释相对误差：相同 0.913ms 差量相对约 13.563ms 服务为 6.7%，相对 35.163ms 完整 filled 为 2.6%。真实控制段的请求间变化另会扰动观察排序，当前没有其统计区间；完整窗误差小必须与服务/execute 误差分列。published 保留实测，正式预测为 null。

## 哪些比较当前有定义

模型域为每 op 4–16MiB、每路径每请求最多一次、最多三个 op、serial、32MiB chunk、cached、实际 warm24/45。新 session 须通过 fresh 物理/配置/source lineage 绑定；未知路径、cold、重复路径、1MiB×4、配置或身份改变均拒绝。

| 比较问题 | 当前可用性与含义 |
|---|---|
| local8MiB 与 mixed3×8MiB 的预测/实测 | 各自可验证模型迁移误差；两请求字节、目标及来源不同，不能作为布局胜负对照。 |
| 单条已知 warm 路径 4–16MiB 的服务量级 | 可用于确定值得关注的粗成本段；local 恒定项的尺寸排序为平局，remote/SSD 有正有效斜率。 |
| 相同三 op 的排列 | 所有 op 都占用 w4，ready 递推总服务恒为三项之和，模型给全部排列同分。 |
| 同内容/目标、同预算的两个合法布局 | 须先构造并检查完整 coverage、source identity、暂停库存、峰值与合法 routes；仅当两计划均落入模型域才有 CPU 评分定义。 |
| 当前三路径数值直接认证 placement/joint 收益 | 缺同一任务的替代来源、目标和预算对照；本轮保持路径模型检验定位。 |
| 共享 HCA/PCI 并发容量、全候选布局或冷 pair | 当前模型没有对应校准；本轮所有 op 共享 w4 的串行依赖也没有产生必要的同时资源竞争对照。 |

当前模型只能用路径、字节与 worker 依赖区分合法 plans。等字节、等路径计数且都经 w4 的候选会得到相同分数；即使交换内容放置或更换 op 顺序，模型也没有产生独立联合优化增量的参数。保持这个平局能帮助选择有真实决策差量的合同。

## 独立新会话的实际分段

有效会话 `execution_actual_r03` 完成两条正式请求，训练和查询 AB 均为零。首 r01 在 mesh 前 inventory 顺序检查失败；r02 在 bootstrap 校验字典键格式处失败，两者均未执行 holdout。r03 只修正执行器调用顺序/数据格式，正式模型与系数未改。两预测在首正式请求前冻结，来源参见 `../execution_actual_r03/measure/prediction_freeze.json`。完整运行资格由独审分工核查；下表直接读取该会话 `validation.json` 原件。

误差统一为 `(预测−实测)/实测`。

| 请求/口径 | 预测(ms) | 实测(ms) | 相对误差 |
|---|---:|---:|---:|
| local8MiB both-record 服务 | 0.280796 | 0.268654 | +4.52% |
| local8MiB worker execute | 0.331326 | 0.311994 | +6.20% |
| local8MiB controller execute | 3.005772 | 3.347495 | −10.21% |
| local8MiB 完整 filled | 21.880706 | 23.309653 | −6.13% |
| local8MiB 完整 published | null | 46.963841 | null |
| mixed3×8MiB 服务关键路径 | 13.562767 | null | null |
| mixed3×8MiB 最大参与 worker execute | 13.613298 | 14.133330 | −3.68% |
| mixed3×8MiB controller execute | 16.287744 | 17.285672 | −5.77% |
| mixed3×8MiB 完整 filled | 35.162678 | 37.902339 | −7.23% |
| mixed3×8MiB 完整 published | null | 88.079182 | null |

mixed 的最大参与 worker 和最大 target worker 都是 w4，故该 worker 口径在本请求相同。local 实测 0.268654ms 落在旧四点包络中；模型的限定本地固定项在这个独立尺寸上误差为 0.012142ms。相应 controller execute 低估 0.341723ms，主要是共有 controller residual 预测 2.674446ms 低于实际 controller−maxworker 的 3.035501ms，而非 local 服务固定项失准。

mixed 的 worker 低估 0.520032ms，controller residual 实际为 3.152342ms，比冻结项多 0.477896ms，两项恰合 controller execute 低估 0.997928ms。完整 filled 的实际控制段 `filled−execute` 为 20.616667ms，较粗训练控制项多 1.741733ms，形成 full filled 总低估 2.739661ms。由这些分段可直接检查误差来自 worker、controller residual 或额外控制窗，不能用某个完整窗百分比覆盖其他口径。

mixed 的原记录为：w4 local 0.265984ms、DRAM target 0.732100ms、SSD source 12.914913ms；w2 DRAM source 1.131250ms；w5 SSD target 13.885566ms。SSD source 与 target 窗会重叠，target 还包含等候；两端时延不能相加。w4 本身三个顺序 record 时长和为 13.912997ms，worker execute 外剩余 0.220333ms；它只是一组同 worker 窗口的账目，不是分离出来的纯请求固定项。混合纯服务没有无等待的独立观测，实测与误差保持 null；13.562767ms 是模型量。

## 下一步建设决策

这次独立验证支持按已有三路径进行粗成本筛选：local 新尺寸服务误差约 0.012ms，mixed worker 和 controller execute 误差分别约 0.520/0.998ms。单条新样本给出了实际偏差尺度，没有形成误差上界或候选布局排序认证。特别是粗 remote−local 差量 0.913433ms 与本次 execute 偏差 0.997928ms 同一尺度，依赖这类亚毫秒差异的布局选择应先定位决定差量的路径/控制段。粗 SSD−remote 差量约 10.894ms 更适合下一步合同筛选；其合法同内容/目标路由与暂停库存仍需先闭合。

下一步用现有组件建立一个同内容、同目标、同暂停资源上限的两布局 CPU 合同，明确相同 processed 字节覆盖、每块真实可用来源、worker/routes、HBM/DRAM 保活占用、SSD 来源以及 transition/runtime 峰值。两 plan 均通过已有合法性与有界域检查后再评分，首先检查差量是否主要来自已校准的粗 SSD 服务项；未知 path 直接作为合同缺口，不扩全路径矩阵。若两 layout 等路径/等字节且都经 w4，保留模型平局。基线采用已有就近/均衡交换策略，暂不增加 solver。

若 CPU 合同只能产生与已观测约 0.5–1ms 偏差相当的细差，先只读定位决定差量的单段。当前原件指出 SSD source 是 w4 长段，DRAM 两端的窗口也不同；应结合执行原语和依赖识别要核的具体段，保持等待与服务的区别。必要新测量由主另行协调，本报告没有追加实验；冻结系数、原失败、原请求和完整计时窗均保持。

本轮最终状态为粗路径量级与一次独立预测误差已有证据；排序证据限于上述模型代数，同预算 layout 收益与共享容量尚无实机对照。`decision_ready=false` 保持。

## 只读核算与证据

`evidence_refs.json` 保存冻结模型和 r03 关键原件 SHA；`arithmetic.json` 保存各请求 worker/execute/full 的绝对与相对误差、controller residual 和控制段差量。各值直接由原 JSON 计算，没有写回任何模型或实验原件。
