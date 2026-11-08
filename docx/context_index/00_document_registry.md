# Document Registry

## 2026-10-25：18.36 阶段 1–2 CPU 结果（门 2 未过）

- **新报告** `experiments/2026-10-25_decision_value_cpu_gate2_result.md`：六策略在登记预算（47 GiB/card）下选出**相同布局、预测差 0**；扫到 8 GiB/card 五策略仍同解；`strong_simple` 是唯一分歧，差 5.7 ms（Δ=100 ms 的 5.7%）。`joint` MILP 每个预算点都 `optimal`、无 fallback、与 `fastest_seq` 逐位相同。**按停止规则 1 整轮停在 CPU，不启动实机。**
- **机制**：worker-clock DAG 关键链。rank1 比 rank0 慢 2.75 ms，移动非关键链 extent 在交叉前不改变 makespan。压预算不能制造分歧——问题是目标函数对 extent 可分离（单 scenario + 同卡容量为唯一耦合）。后续切入点是**制造耦合**（分级保活成本、共享出口竞争），不是加大 solver。
- **修正**：模块总览第 7 节的 15.25 GiB → **14.25 GiB**（漏扣每卡 1 GiB 持久占用）。
- **新代码**：`codex_coding/src/.../decision_value/{case,execution}/`（候选冻结、合同、评分器、六策略）。已知取舍见报告第 7 节。
- **独立审计（两轮）**：复算审计 AGREE（有理数独立复算 0.1042007296 s，差 1.5e-7 为显示舍入）。结构审计三项，全部采纳：① 私加的 `admissible()` 已改写为合同级 `one_class_per_extent`（产品无此规则；严格来说，它只影响"同布局"这一集合层面的表述，去掉后分数仍是 0.104201 s）；② `balanced` 第一版是 `fastest_seq` 的别名，已重写为真正查 timeline 的瓶颈均衡，重写后结果不变；③ 产品 MILP 在容量宽松时会为同一 extent 放冗余副本（36/47 GiB 下 67 行），报告按去重后记账，未改产品代码。
- **第三轮结构审计（可行性清点）**：**方向 C（共享出口）在 CPU 上不可能评估**——`simple_model.py:19-21` 构建期硬性要求 `shared_resource_paths == {}`，登记模型这两个字段为空；共享并发因子实现虽在 `online_cost/model/predictor.py:151-172`，但只在 `model_cpu_r08/r09` 的合成 4-worker fixture 上启用过（factor 2.7778，拓扑不同）。**方向 D（目标 worker 兼任 source）在登记候选集里结构上不可能**——候选只放 `worker=rank` 自己的卡，`planning.py:40-41` 的 `"peer"` 分支永不激活。两者都要先重新登记候选域/新增测量。
- **MILP 排序能力缺陷（独立于本结论）**：`multi_joint` 的 `tc = max_w(Σ bytes/rate)` 把源侧与目侧当互不相干的加和，而 DAG 里共享 `src_w=2` 的 op 完全串行。用同一 `joint_scenarios` 选解再用 DAG 打分实测：hbm 仅 8/16/4 GiB/card（必须回落 base）时 MILP 目标比真实 makespan 低估 **48.8% / 46.8% / 48.3%**；登记 47 GiB（最优解全 HBM、0 次触碰 worker 2）只差 **5.0 µs**。→ MILP 只能当选解器，不能给"会用 base 的布局"排序，否则会系统性偏好多用 base。
- **可复现性已修**：去重提为合同级 `strategies.normalize`，由 `run_strategy` 统一调用；新增 `execution/sweep.py` 从已提交代码重跑全矩阵，与归档逐格比对最大绝对差 **4.251e-07**（六位小数显示舍入），门 2 判定逐点一致。`base_only` 已明确排除出极差（曾误纳入而得到 4.172 s 的假通过）。

## 2026-10-25：同预算决策价值设计 + 系统模块总览重写

- [同预算布局决策价值计划](../plans/2026-10-25_same_budget_planner_decision_value_plan.md)：18.36 事前设计。三级命题分离、六策略同候选集、`T_ready_total = T_pre + T_plan + T_exec`、Δ 已锁定 `max(100ms, 5%)`、门 1–4 与停止规则。**未启动任何新实验。**
- [系统模块总览](../02_system_module_overview.md)：改写为 **V0 架构 + V0 五步工作流 + P1–P7 补丁表**。每个 patch 记插入点/原状/改造/论文目的/类型/状态/归口；文件路径降为附录。用于对齐工程思路，不写数值结论。
- 待确认的结构性问题：按当前登记配置每 rank 32.75GiB vs 48GiB/card 上限，HBM 不绑定、块级布局可能退化。未经 CPU 验证，由 18.36 阶段 1–2 判定。

## 2026-10-23：DSV4粒度的在线校准详细过程

- [详细过程专题](../../code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_online_cost_calibration_detailed_20261023.md)：逐函数、状态表、冻结来源链、四请求7段账、rank级等待、物理park、失败/资格分层、历史命令和下一步决策表。
- [补强后的恢复快照](../../code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_outer_calibration_recovery_detailed_20261023.txt)：增加专题必读和实际计时窗边界，首版快照保持。
- 实际主窗排除恢复前park证据/资源门/command发布，始于inventory之前；该限定在专题第5节和outer报告第4节同步。只补文档，不改变旧实测。

## 2026-10-23：上下文恢复归档

- [当前恢复说明](../../history-chat.txt)、[固定历史副本](../../code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_outer_calibration_recovery_20261023.txt)：按用户模板整理主张、规范、组件、历史进度、最新四恢复、适用边界、原件和必读顺序。
- [覆盖前旧history备份](../../code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_pre_archive_history_20261023.txt)：保留旧v0.1.23.2 GLM/并行交接，SHA与覆盖前一致；其运行状态属于历史。
- [完整过程文档](../../code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md)末尾10/12与10/23两节：实验全过程及归档操作。10/23无新实验和现场资源检查，最新数值仍来自下述10/12结果。

## 2026-10-12：控制/接管校准完成，两留出10%均通过

- [事前校准计划](../plans/2026-10-12_outer_online_calibration_plan.md)、[最终报告](../experiments/2026-10-12_outer_online_calibration_report.md)：首次与后续域、唯一outer参数、四完整请求、冻结结果、分段时间及退出证据。
- [73项实测独审](../../codex_coding/results/v0.1.23.3/task2/online_cost/outer_analysis_actual_r02/report.md)：路径表不变、remote−0.187%/near−6.036%；首次和复杂拓扑资格未知。
- 正式实验记录50.8、主计划18.35后续；当前应转向规划选择的主指标价值，不追加精度实验。

## 2026-10-12：简化两节点单发送者完整在线结果

- [简化计划](../plans/2026-10-12_simple_two_node_online_validation.md)、[实际报告](../experiments/2026-10-12_simple_two_node_online_report.md)：helper容量口径、r01拟合失败、r02操作服务表及完整C0/留出、主要误差和下一步。
- [r02独审](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/report.md)、[分段原件](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/breakdown.json)：60/61通过，唯一Hnear10%失败，搬运与控制外层分开分析。
- [r01失败独审](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r01/report.md)：12个正确样本不满足正斜率假设，原件与成功回收保持。

## 2026-10-12 18:52：IPC实证与完整Qwen物理暂停

- [在线ready报告第7节](../experiments/2026-10-12_online_ready_cost_validation_report.md)：32MiB受控复现、Torch版本与counter解释更正、真实70.33GB物理pause、资源阻塞和后续路线。
- [小IPC实测独审](../../codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r02/report.md)、[完整模型独审](../../codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r05/report.md)：24/31检查，分别确认小块保留差异与真实释放/DRAM保活；不提供预测误差资格。
- [退出修订审查](../../codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r06/report.md)：保持原guard失败，独立验证最终退出事实；未来修订CPU通过。

## 2026-10-12：真实在线ready成本验证与IPC修复

- [当前计划](../plans/2026-10-12_online_ready_cost_validation_plan.md)：线上计时、整体10%、两节点真实模型校准/留出及资源边界，18.35/记录50。
- [执行报告](../experiments/2026-10-12_online_ready_cost_validation_report.md)：实机两次工作流、线程与IPC问题、当前阻塞、代码验证和下一步；没有预测精度实测资格。
- [核心CPU验证](../../codex_coding/results/v0.1.23.3/task2/online_cost/execution_core_cpu_r04/qualification.json)、[独立重验](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_core_final_cpu_r01/qualification.json)：各35项，不代表GPU释放或成本准确率。

## 2026-10-12：成本八请求实测与独立验证

- [完整成本报告](../experiments/2026-10-12_cost_ledger_calibration_report.md)：八点数据、worker分区、控制前段归因、经验模型与未过门，18.34/记录49。
- [最终解读](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/analysis_gpu4_final_r01/report.md)、[最终独审](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/review_gpu4_actual_C2_r02/report.md)：局部worker通过与整体未升级分别说明。
- [冻结预测](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C1_r01/frozen_prediction.json)、[留出验证](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C2_r01/independent_validation.json)：未回填、未放宽门槛、未追加样本。

## 2026-10-11：恢复成本账本与可辨识校准

- [正式计划](../plans/2026-10-11_cost_accounting_and_identification_plan.md)：成本归属、三层模型、最多八请求条件化矩阵、独立验证与停止规则；尚未执行硬件。
- [源码审计](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/ownership_r01/report.md)、[矩阵设计](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/identification_r01/report.md)、[理论审查](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/theory_r01/report.md)：三agent分工证据。
- [CPU精确秩结果](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/identification_r01/rank_checks.json)：秩4/八检查，不是实测性能；总计划18.33、记录48。

## 2026-10-06：完整场景表与软件顺序控制

- [完整表最终版](../../codex_coding/results/v0.1.23.3/task2/basic_scenarios/complete_table_r01/review_r02/report.md)、[主表CSV](../../codex_coding/results/v0.1.23.3/task2/basic_scenarios/complete_table_r01/review_r02/table.csv)：7行×11方法身份，13新观察/16历史/15别名分开；原版与未适配项保留，S5-P全未测。
- [正式报告第20节](../experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)：S1新平台IPC、S4局部缓存、S5正反顺序、内容/回收、CPU决策适配与下一步。
- S4 LP与fastest同操作，无算法增量；S5同布局/来源/字节交错缩短权重就绪，两配对40.69%/21.93%，不是placement或物理fabric结论。[独立控制复核](../../codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_control_review_r01/report.md)。

## 2026-10-06：基础场景第一轮已执行

- [正式报告第19节](../experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)：S1–S5契约、CPU验收、旧node27 OOM、新隔离平台、S2/S3四次实测与保留状态。
- [运行入口](../../codex_coding/src/v0.1.23.3/task2/basic_scenarios/README.md)、[机器汇总](../../codex_coding/results/v0.1.23.3/task2/basic_scenarios/analysis_r01/report.md)、[过程记录](../../code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md)。
- S2 host为1.504/1.331s，S3 active D2D为2.559/2.444s；各场景内同操作、execute近同，不作算法排名。原14keeper目录不变，新4keeper+完整DRAM+2parked engine保留，下一次重新检查。

## 2026-10-06：代码委派规则与Baseline源码落位

- [根AGENTS.md](../../AGENTS.md)：具体代码建设委派子agent，主agent规划、审查、验收；模型配置、目录与研究约束。
- `lib_cite/`：SGLang、Janus、BlitzScale、ServerlessLLM独立源码检出；README与baselines.lock.json记录来源和版本。取得源码不等于完成适配或复现。
- 本轮只整理源码与规则；“基础场景构建”尚未运行新实验。

## 2026-10-06：Baseline、论文差异与后续建设（暂定方案）

- [专题文章](../articles/2026-10-06_baselines_delta_and_research_roadmap.md)：SGLang加载/IPC、Janus warm/D2D、BlitzScale、ServerlessLLM及强简单策略的比较角色、复现状态、固定源码、可复用组件、论文表述、公平协议与e1–e8建议顺序。
- 核心重点为预算受限的块驻留与共享资源下的恢复决策；“已有工作都未考虑”及“已优于Janus”均非当前结论。Janus已有NUMA与跨服务器能力；BlitzScale已有长期参数池。
- 新文章不改变开发编号、原实验次数或主指标；无新GPU实验，下一步先审计既有证据和对照协议，正式执行继续进入原计划/报告。

## 2026-10-06：多节点权重与通信拓扑文献知识库

- [长期参考文章](../articles/2026-10-06_weight_placement_and_topology_literature.md)：ServerlessLLM、BlitzScale、FaaScale/λScale、SpotServe、SCCL、TACCL、TE-CCL、ForestColl、Helix九条工作线；问题/假设/实现入口、容量与时序约束、复杂拓扑及e3–e6映射。
- 重点纠偏：BlitzScale已有长期参数池和布放，Helix是token流，ForestColl吞吐最优有条件，TE-CCL奖励目标不直接等于严格makespan；计划负载下界、placement最优下界和测量估计分开。
- 下一步建设先读该文第7–10节并形成方法对应表；本文不代表作者系统复现或新GPU实验，不授权新增组件。

## 2026-10-06：e6后论文范围审查（最新规划入口）

- [计划v1.4第17节](../plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)：逐项盘点e1–e8证据缺口、三个组件已实现/必补/条件新增/暂缓，统一建议顺序与停止门槛。
- 核心区别：e3人工布局价值、e6资源换时间已经有探索证据；优于强简单系统的决策/能力增量仍未成立。先复用已有执行记录审查，不自动新建优化器或并行执行器。
- GLM多节点大模型正确性关口保留且有界；e7对应前台主张、e8对应动态主张。子agent最新使用gpt-6.1-sol/high，主agent监督。本次只读审查与文档更新，无新增实验/产品实现。

## 2026-10-06：Qwen实验推进至e6；GLM前置缺口保留

- [正式报告第18节](../experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md#18-2026-10-06-授权推进至-e6执行增量)：Qwen IPC双循环回收通过、e3同预算平均23.9%单次改善、e4同决策、e5零新增GPU恢复、e6固定DRAM/0–16GiB HBM曲线。
- [计划v1.3](../plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)同步执行状态；[e6图](../../codex_coding/results/v0.1.23.3/task2/paper_e1_e8/figures/e6_curve_r01/e6_budget_curve.png)与[机器汇总](../../codex_coding/results/v0.1.23.3/task2/paper_e1_e8/analysis/through_e6_r02/report.md)可查。
- GLM两次fresh全SHA通过但首次输出与历史不一致，后一次同引擎第二次输出匹配；根因未定，修复版GLM IPC gate未执行。不能把Qwen完成范围扩大到GLM。
- 自建引擎/新监控已清理，原22keeper和底本保留；e7/e8未执行。旧条目的“待验”“服务中”按日期解释，不作为当前状态。

## 2026-10-06：e1–e8 计划 v1.2，共享基建与 IPC 验收

- [当前计划](../plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)：第4/5节给出IPC补验→GLM关口→e3–e5→e6/e7→条件性e8；第15节列已有组件、IPC启发与固定上游版本；第16节明确测量同步、子agent委派和现场入口。
- 同一篇论文复用GLM/Qwen/IPC成果；Qwen主迭代、GLM关键验收，保留开发编号v0.1.23.3/task2。新安排尚未执行，实验次数和历史结论不变。
- 现场更正：旧GLM服务已按用户要求停止，CPU owners/保活worker/Qwen保留；下次实验重新preflight，不复用旧engine句柄。详情见计划16.3节。

## 2026-10-06：论文范围管理长期指南

- [范围管理与最小建设指南](../01_paper_scope_management_guide.md)：范围蔓延定义、范围基线、三类建设、准入检查、停止规则与 e1–e8 阶段决策。
- 用于每次思考下一步建设；e3/e5/e7 后形成阶段性组件建设判断，e8 条件性执行。该指南不代表新实验已运行，不增加日常实现的机械审批流程。


## 2026-10-04：v0.1.23.3/task2 论文缺口实验规范

- [e1–e8 完整计划](../plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)：论文问题、固定范围、资源与指标口径、各实验对照及推进判据。
- [配套实验总记录](../experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)：运行前登记、逐次报告模板、数据契约、e1–e8 状态表与历史背景入口。
- 状态（2026-10-06）：e1/e2 两个历史重分析 run 完成，e3 两次尝试中一次 preflight 失败、一次四布局 GPU 先导通过；完整模型布局收益待验，e4–e8 未执行。详细数据与组件建设判断见报告第 7–9、17 节。
- 这是当前会话明确的 `.3`/权重可用时延系列；下方 `.2`/生命周期与首正确 token 资料保留原范围，不视为互相覆盖。


## 2026-10-04：GLM三节点实施进行中

- [执行过程](../../code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_glm_three_node_execution_20261004.md)：默认FP8 GEMM故障独立复现、Triton对照、全局同步开关导致的通信阻塞观察；原生生成尚在运行。
- [HLL与恢复规划](../articles/2026-10-04_hll_and_recall_planning.md)：可合并需求统计、精确目录与恢复瓶颈块假设，非已测性能结论。

## 2026-10-03：三节点 GLM 后续配置核查

- [完成状态及后续配置](../plans/2026-10-03_three_node_glm_followup.md)：三节点授权，node27+29 TP16候选、node28本地SSD；GLM原生推理未验证，已有四节点结果与候选配置分开记录。
- 证据：`codex_coding/results/three_node_glm_audit_20261003/evidence.json`。本轮没有新GPU实验。

## 2026-10-03：生命周期真实验收已完成（优先入口）

- [最终实测报告](../articles/2026-10-03_lifecycle_results.md)：固定配置final03，A→B 4.77→1.27s、B→A16.56→7.24s、远端A14.62→4.70s；全部到首个正确token。
- [实施、命令和失败历史](../../code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_lifecycle_execution_20261003.md)：存储alias、warm engine、RoPE cache、NUMA容量、SSD和MILP的实现与验收。
- [分析JSON](../../codex_coding/results/lifecycle/final03/analysis.json)：全量校验、B后台请求、迁移成本、无源码漂移和清理错误。
- 已完成并清理；不要继续旧full/final进程。此前设计文档中的待实施状态由此次固定TP2双模型验收更新，论文级扩展仍待验证。

## 2026-10-03：四节点生命周期与官方 daemon 对照设计

- [完整设计](../plans/2026-10-03_fluidckpt_lifecycle_design.md)：当前用户要求以同预算 A→B→A 端到端为目标；核实官方 LMSYS Weight Cache Daemon、FAST/OSDI 前作、双向 deadline 契约与七组实验。
- [过程与命令](../../code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_lifecycle_design_audit_20261003.md)：只读审计、历史命令溯源和当前 F 确定性问题。
- [证据](../../codex_coding/results/design_audit_20261003/evidence.json)：历史1.84s跨节点恢复、0.341s自写attach、125s启动、源码漂移。此次没有新GPU跑分；下方旧单窗口目标不能覆盖最新用户目标。

## 2026-09-29晚间：managed04优先入口

- 过程文档`v0.1.23.2-task2_system_profiling_joint_placement.md`附录I：联合控制层真实验收、NUMA/端口失败链、预算及性能数据。
- `codex_coding/results/task2/cluster/managed04/managed_analysis.json`：加载2.03455→1.72208s，搬运1.37985→1.22130s；42项CPU测试和两次真实恢复通过。
- `history-chat.txt`顶部续作更新：优先于下方历史交接；单目标首交付完成，扩展边界保留。
- `code_building/history_chat/v0.1-init-project/v0.1.23.2-task2_managed04_recovery.txt`：最新恢复文件副本。

## 2026-09-29 最新入口（v0.1.23.2 / task2）

过程文档已按用户指定v0.1.19.10信息深度第二次增强，新增附录A～H；自动摘录来源清单在`codex_coding/results/task2/archive_20260929/detailed_evidence.json`。这次仅丰富归档，不新增产品实现或GPU实验。

| 路径 | 角色 | 状态与优先级 |
| --- | --- | --- |
| `history-chat.txt` | recovery_handoff | 必读，完整现场及下一轮阅读顺序 |
| `docx/next_step.txt` | stage_entry | 必读，已写入用户固定编号例外和一至两次验证约束 |
| `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md` | process | 必读，完整实验链和失败记录 |
| `docx/articles/2026-09-29_task2_joint_placement_findings.md` | article | 必读，联合问题、tradeoff与证据边界 |
| `docx/plans/2026-09-29_task2_joint_planning_control_plan.md` | plan | 必读，七步控制层待实施，非已完成结果 |
| `docx/plans/2026-09-28_task2_numa_flow_loading_plan.md` | historical_plan | 第一阶段历史设计，相关闭环已完成 |
| `docx/plans/2026-09-28_task2_stage2_replica_multipath_plan.md` | historical_plan | 第二阶段历史设计；旧重复次数已被用户后续要求覆盖 |
| `codex_coding/results/task2/cluster/placement01/placement_analysis.json` | evidence | 最新成功的同预算模型恢复对比 |
| `codex_coding/results/task2/cluster/profile01/profiling_analysis.json` | evidence | CPU/阶段可用，模型GPU trace不完整 |
| `codex_coding/results/task2/cluster/bulk_profile01/profiling_analysis.json` | diagnostic | 有GPU活动/字节证据，原始run因分析脚本指纹保持failed |

下方为2026-09-27历史注册表；其“当前等待/被GLM阻塞”的状态描述已由上述入口及最新stage_summary覆盖，文件保留以便溯源。

本表登记 `v0.1-init-project` 阶段中具有恢复价值或溯源价值的文档。过程文档与结果文件才是证据源，
本表只作查找辅助。

## Active Recovery Documents

| Path | Role | Topic | Priority | Key Conclusions |
| --- | --- | --- | --- | --- |
| `docx/next_step.txt` | stage_entry | 当前阶段代号、建设原则、归档约束 | high | 定义 `v0.1-init-project`；17 条建设原则（含 core dump 防护、main_lib 分层）；6 项压缩前归档清单 |
| `history-chat.txt` | recovery_handoff | 上下文压缩恢复块 | high | 可直接粘贴的交接棒；本轮核心主题、关键数据、已排除方向 |
| `docx/00_docx_storage_and_lookup_guide.md` | guide | `docx/` 存放与查找指南 | high | 定义哪些进 `docx/`、`articles/`、`context_index/`，哪些进 `main_lib/`、`code_building/`、`codex_coding/` |
| `docx/context_index/01_stage_summary.md` | context_index | 当前阶段总结 | high | 当前阶段为「平台缺陷阻塞下的规范建设」；下一步由磁盘/规范化收尾转入保活实验 |
| `docx/context_index/02_active_threads.md` | context_index | 活跃线程 | high | 三条线程：多节点保活（active，被平台缺陷阻塞）、SGLang 保活对比（active）、keep2 悬案（已定性 ENOSPC） |
| `docx/context_index/03_current_required_process_docs.md` | context_index | 最小必读过程文档集 | high | 恢复当前现场所需的最小过程文档集合 |
| `docx/context_index/04_insight_ledger.md` | context_index | 机制洞察活账本 | high | I1–I12：传输、平台、内存三层的关键机制规律 |
| `docx/debug_philosophy.md` | guide | debug 方法论 | medium | 12 条原则 + 「改前确认→写预期→改代码→对照→匹配」5 步循环 |

## Current Articles（技术专题）

| Path | Role | Topic | Priority | Key Conclusions |
| --- | --- | --- | --- | --- |
| `docx/articles/2026-09-27_glm53_startup_vmfault_forensics.md` | article | GLM-5.3 启动崩溃取证 | **high** | 崩溃在 SGLang 自家 NSA path（`nsa_indexer.py:438` → `lightop_attention.fuse_layernorm_rotary_embedding`），gfx936 上无法绕过，与 FluidCKPT 无关 |
| `docx/articles/2026-09-27_multinode_transport_findings.md` | article | 跨节点传输能力与限制 | **high** | 成对单流 25.7 GB/s，N 流聚合 205.8 GB/s；跨节点 send/recv 不可用，只能 collectives |
| `docx/articles/2026-09-27_node27_resource_profile.md` | article | node27 单节点资源画像 | high | NUMA/PCIe 拓扑、H2D 46 GB/s、D2H 47 GB/s、本地 NVMe 2.5 GB/s、NFS 0.27 GB/s |
| `docx/articles/2026-09-27_glm53_model_freeze.md` | article | 模型冻结事实 | high | V9_links_hswio_numa_rw_conflict_tied 冻结；V10 被否决（保留拟合文件） |

## Current Cites（立项依据）

| Path | Role | Topic | Priority | Key Conclusions |
| --- | --- | --- | --- | --- |
| `docx/cites/2026-09-27_fluidckpt_scope_and_positioning.md` | cites | 项目范围与定位 | **high** | 回答「想解决什么问题、为什么收敛到 POC」；目标为全面胜过 SGLang 自身保活 |

## Current Scripts and Results

| Path | Role | Topic | Priority |
| --- | --- | --- | --- |
| `main_lib/fluidckpt/` | core_library | FluidCKPT 核心库（6 子包，2768 行） | high |
| `codex_coding/src/serving/glm_e2e.py` | driver | 端到端驱动（capture/spread/save/drop/restore） | high |
| `codex_coding/src/serving/glm_keepalive.py` | driver | 一遍式 7 步保活驱动 | high |
| `codex_coding/results/bw/` | results | 跨节点带宽测量 | high |
| `codex_coding/results/model/` | results | 资源模型拟合与冻结 | high |
| `codex_coding/results/logs/base2_*.log` | evidence | **纯 SGLang 复现崩溃的决定性证据** | **high** |
| `codex_coding/results/logs/core_inventory/core_list.txt` | evidence | core dump 清单归档 | medium |

## Deferred / Not Included On Purpose

- `dllm_wh` 继承的三本账与 `context_index/` 原文 → `.spec_backup/*.dllm_wh.bak`
  （保留为来源参考，不作为当前项目内容）
- 原始 `logs/` 全量（40+ 文件）→ 只精选 8 份进 `codex_coding/results/logs/`，
  其余留在 `/home/zhujianian/fluidckpt/logs/`（`*.log` 被 `.gitignore` 忽略）
- `__pycache__/`、`.pytest_cache/` → 缓存，不迁

## Notes

- 本仓库于 2026-09-27（`v0.1.23.1`）从 `/home/zhujianian/fluidckpt` 迁移至
  `/home/zhujianian/zhujianian/fluidckpt_zjn`。看到旧路径即为迁移前历史记录。
- 新增重要文档时同步：本表 + `code_building/key_files_index.md` + `code_building/progress_diff_summary.md`。
