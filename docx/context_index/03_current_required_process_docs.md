# Current Required Process Docs

## 2026-10-23 最新必读：逐函数与原始时间账

新增必读code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_online_cost_calibration_detailed_20261023.md。按用户DSV4参考展开，包含12节：时间线、控制变量、函数与代码状态、模型/freeze链、计时生命周期、实测多层账、假说矩阵、失败和资格、资源清理、命令、下一步接口及恢复入口。旧basic_scenarios继续保存总时间线，history已经指向新专题；本轮没有新增测量。

## 2026-10-23 当前优先：归档恢复与已达标的在线模型

按history-chat.txt“这一轮”顺序阅读AGENTS、next_step、debug哲学、outer校准报告/计划、basic_scenarios过程末尾、论文范围/组件/baseline、总计划18.35和总记录50.7/50.8。再看online_cost/outer_analysis_actual_r02/{report.md,qualification.json,breakdown.json}，不要从旧成本账本“尚未通过”继续跑。最新10/12两留出0.19%/6.04%、73项通过，首次与复杂域仍分开。

过程文档是code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md；固定恢复快照为code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_outer_calibration_recovery_20261023.txt。旧history保存在同目录pre_archive_history_20261023.txt。10/23只归档与本地核验，未新测现场。

## 2026-10-12 当前优先：成本八请求结果与分层资格

先读docx/experiments/2026-10-12_cost_ledger_calibration_report.md、总记录49、总计划18.34；再看codex_coding/results/v0.1.23.3/task2/cost_ledger/analysis_gpu4_final_r01/report.md、model_gpu4_C1_r01/frozen_prediction.json、model_gpu4_C2_r01/independent_validation.json、review_gpu4_actual_C2_r02/report.md。需要原始成本时读execution_gpu4_actual_C1_r01/measure和execution_gpu4_actual_C2_r01/measure。C1/C2均已完成，GPU0旧失败是资源拒绝0测量；旧first_op B仍已跳过，不重开。开发过程本轮接在basic_scenarios过程文档末尾，无history覆盖。

## 2026-10-11 当前优先：恢复成本归属合同与最小辨识设计

先读next_step最新块和docx/plans/2026-10-11_cost_accounting_and_identification_plan.md，再看cost_contract_design下ownership_r01/report.md、identification_r01/report.md与rank_checks.json、theory_r01/report.md；最后对照计划18.33/记录48及当前basic_scenarios过程增量。新硬件矩阵未跑，只完成源码审计和CPU数学必要条件检查。用户要求成本先于规划，覆盖旧回layout建议；A/B结论不改，history不覆盖。

## 2026-10-11 当前优先：首操作状态A结果与停止边界

先读next_step最新块、报告47/计划18.32，再读codex_coding/results/v0.1.23.3/task2/first_op_validation/analysis_result_A_r01/report.md、review_actual_A_r01/report.md和final_qualification.json、execution_result_A_r01及execution_closure_A_r01。独立A8完成、状态unidentified/S5us，B按预登记未开，不是应自动补完的缺项。旧model保持，model_frozen_r01文件frozen=false/预测0，同集拟合不可报独立准确率。

源在version/src/first_op_validation/{execution,analysis,review}，analysis_frozen_r01保留A前不可变规则。prepared_A_r02/actual_A_r01是唯一实际包/run，早期prepared_A_r01只本地旧包别stage。功能/时钟/SHA/资源/回收通过，首差仍存在但thread/request收费规则未识别；不把持久线程视为自动免首项。所有新线程/对象/进程结束，旧资源保护过，未来需fresh准入。下一步以可区分同预算layout及真实主指标为方向，不扩微实验/强行改阈值。

## 2026-10-11 当前优先：FluidCKPT主体、组件与验收状态

先读AGENTS新组件复用规则、next_step顶部和docx/articles/2026-10-11_fluidckpt_paper_system_goal_components_and_status.md。Janus专题第1/5节及计划18.31已修订为按Fluid缺口摘取源码并适配进本项目；整系统海光移植不是前提，不能把任务转成Janus主系统。

当前能力和未通过项必须分层：真实QwenTP2历史闭环；新3node6GPU synthetic TP4恢复；已有LP/MILP/强简单框架；最新复杂场景同预算收益和live模型接管未认证。首操作A8+B8计划仍待实施。本次无GPU/实验/产品修改/代码迁入，下一步围绕Fluid主指标而非外部系统全面移植。

## 2026-10-11 当前优先：首操作验证协议与Janus复用范围

先读next_step最新块、docx/plans/2026-10-11_first_operation_state_validation_plan.md及docx/articles/2026-10-11_janus_retention_reuse_audit.md，再读总计划18.31/报告46/过程末段。来源results/version/task2下first_op_validation/design_r01、janus_reuse/implementation_r01与mapping_r01含实验设计/源码锚与证据；本轮没启动GPU、没修改系数或移植Janus。

A8+B8尚未实施，不把矩阵计为样本；先实际thread birth/target干预分离和真实CPU协议资格，A不支持即停止。Janus NPU具体保活hooks与genericfatal、xTensor池口径、已有NUMA/rolling/源实例锁都要保留，不能概括无拓扑/无保活。优先现HIP/Catalog/owner/多层执行器与janus机制，必要描述符/leaseadapter不等于原版复现。无新GPU入口可复跑，后续fresh现场准入。

## 2026-10-11 当前优先：首操作主机开销与模型计费边界

先读next_step最新块、docx/articles/2026-10-11_local_hbm_first_operation_cost.md、计划18.30/报告45和当前process末段。结果根codex_coding/results/v0.1.23.3/task2/local_slope/：review_final_r01为最终总资格（121refs）；execution_result_r01为八请求，execution_repeat_result_r01/report.md及interpretation.md为无事件同handler双轮；path_slice_installed_r01为实际torch2.10 header核对；review_actual_r02/review_repeat_actual_r01为142/78项实机独审。

源worker/runner是第一阶段，repeat_worker/runner/prepare/session是最后两请求，旧源全部保持。actual_r01及execution_repeat_actual_r01均结束/清理，不重跑旧run。first4 .347/first16 .290ms的反差在second4 .089/second16 .123ms转正，直接hostphase证据支持首操作成本，但旧每点微观根因、1.037ms长点、裸带宽未知。旧模型冻结e98b...未改。后续先拆成本定义，不叠加含首成本的旧service造成双计，不把second暖态替代首次恢复主指标。正常增量，不改history。

## 2026-10-11 当前优先：有限域模型独立验证与布局决策边界

先读next_step最新块、总计划18.29、实验记录44、当前basic_scenarios过程末段。结果根codex_coding/results/v0.1.23.3/task2/bounded_cost/，依次读review_final_r03/report.md与qualification.json、execution_result_r03/report.md/summary.json及composition_diagnostic.md、model_decision_r01/report.md、model_frozen_r01/report.md。当前模型已有local8/mixed24各一次先验新会话验证，不能沿报告43说全模型null；但published/共享容量/布局排序仍未认证。

唯一有效actual_r03，prepared_r04/session bounded_cost_runtime_r03，formalmodel e98b...始终不变。stage_r01命名冲突和actual_r01/r02的初始化顺序/live-JSON键错误均保留，两个失败零holdout；fix_r01–r03与真实CPU preparation回放解释必要修订。新实验进程、权重和SSD已清理，不重跑旧run名。代码入口src同version/bounded_cost/model及execution，产品未改。下一步只先同内容/target/预算两合法layout合同和CPU评分，别把异内容路径量级或排列同分描述成联合收益。

## 2026-10-10 当前优先：库存定位、完整请求收益与模型负结果

先读next_step最新块、总计划18.28、正式实验记录43及docx/articles/2026-10-10_multitier_breakdown_and_cost_model_alignment.md第8节，再读当前basic_scenarios过程末段。最新结果根为codex_coding/results/v0.1.23.3/task2/cost_calibration/：review_final_r01/qualification.json和report.md为独立结论，execution_result_r01/report.md和summary.json为时间/拟合/终态，inventory_actual_report_r01/report.md为48个RPC分段与fresh证据。execution_actual_r02是修正后完整实机，execution_actual_r01失败及execution_ssd_cleanup_r01必须一起保留；旧run名不能重跑。

关键认知：查询60.58→6.19ms、同源36MiB完整filled156.03→42.21ms，收益来自实验诊断库句柄缓存，搬运约23ms未变。filled和含校验的published114.39ms分开，均非live模型T_ready；原产品未改。local规定补测后仍负斜率，model/prediction=null，不存在正式预测准确率。三agent工作已验收，新GPU进程与SSD清零；下一步讨论小local成本形式，不能沿旧文章误说数值模型已准确或重复本轮GPU。

## 2026-10-10 当前优先：成本模型和实际时间边界

先读next_step最新块及docx/articles/2026-10-10_multitier_breakdown_and_cost_model_alignment.md、总计划18.27/报告42/过程末段。结果根cost_model_breakdown在version/task2下：evidence_r01原件分解、model_audit_r01模型代码/旧参数适用域、prediction_r03最终事后同4MiB诊断和arithmetic_check。正式模型本轮没有预测，不能把lookup误差当原始事前校准；大查询项使filled误差小，execute与排序另核。r02不同controller完整clock_domain误断言失败保留，r03只修顺序证据不改参数。实际GPU仍是上一轮multitier_runtime/operations_actual_r01唯一会话，本轮未新GPU/SSH。下一步讨论最小校准/主指标，别重造solver或直接报模型已准确。

## 2026-10-10 当前优先：六worker真实闭环

先读next_step最新块、三节点计划第12节、总计划18.26/报告41和当前过程末段。结果根codex_coding/results/v0.1.23.3/task2/multitier_runtime：review_final_r02/qualification.json与report.md为最新独立结论，runner_result_r01/report.md和summary.json为分段/空间/SSDIO，operations_result_r01/report.md为精确清理/同HIP预算上界。operations_actual_r01是唯一GPU原件（startup/mesh/qualification/cases/预算/日志/终态），勿原名重跑；当前9child都退出、无GPU实验残留。src对应operations/runner/review具可复用入口，产品worker.py仅synth tp_size267429...。确认计时是带诊断的replica事务，约62msinventory、外部gate/live模型handoff未纳入；不能将不同预算的四见证等同solver对比。下一步聚焦主指标口径、同预算强基线与必要共享并发，不重做小功能或全候选扫描。

## 2026-10-10 当前优先：多层执行衔接与新node27资格

先读next_step最新块、三节点计划第11节、正式计划18.25/报告40/当前过程末段。结果根codex_coding/results/v0.1.23.3/task2/multitier_scenarios/：mapping_binding_r03当前角色；mapping_refresh_r01/PCI_followup/owner_refresh原证据；mapping_admission27_run_r01/result.json与artifacts/summary.json唯一新GPU实机；adapter_delivery_r02精确测试源码逆推、adapter_cpu_r02与adapter_journal_cpu_r02；execution_review_minimum_r02/adapter_r01及最终综合审查。源adapter/README.md给CPU命令与可信物理gate边界。27GPU0已在新boot重验，旧D不可继续当阻塞；第二卡和六worker未准入。28GPU4旧keeper不可借，候选已改。当前copy容器无SYS_NICE，NUMA路径资格仍需补。无活跃GPU实验，不原名重跑旧run。

## 2026-10-10 当前优先：三节点多层场景

先读AGENTS、next_step最新块及docx/plans/2026-10-10_three_node_multitier_scenario.md；再读计划18.24/正式实验记录39、当前basic_scenarios过程末段。结果根codex_coding/results/v0.1.23.3/task2/multitier_scenarios/：contract_cpu_r02合同/structural_checks/revision_scope、capability_r01/report.md、review_final_r01/qualification.json与report.md。该合同通过结构验收；三GPU节点/六worker预算/SSD挂载/速率/时序与模型待绑定。C3下界是逻辑SSD来源，不当磁盘实读；384MiB是原serial条件预留，不是实际分配；r01及中间审查保留。下一步复用既有原语，不另造SSD框架或凭图大认定复杂solver必要。

## 2026-10-10 当前优先：多合法位置竞争

先读next_step最新块、docx/plans/2026-10-10_multiple_placement_competition.md、计划18.23/报告38及过程末段。结果placement_competition/case_active_preregister_r01先验区间、case_active_r01最终合同/比较/active_gate、objective_active_r01事件容量与唯一两rate变更、review_final_r02资质（完整前缀codex_coding/results/v0.1.23.3/task2）。旧case_cpu_r03/review_final_r01为负研究gate，不能抹去。所有时间理论单位，sixroles无预算/物理/model准入；后续主动用子agent，原strongpaired≠swap，交换已达joint同解。

## 2026-10-10 当前优先：TP4契约与部署门槛

先读next_step最新块、docx/plans/2026-10-10_tp4_layered_scenario_contract.md、正式计划18.22/实验报告37与过程末段。结果根codex_coding/results/v0.1.23.3/task2/tp4_scenarios：contract_cpu_r03合同/comparison/Pareto与唯一变量审计，feasibility_r03最终角色与每node预算，review_final_r02资质。禁止把symbolic资源分当实测、把单worker线性投影当物理下界、把角色复用当原DAG等价。现GPU命令null/原2GiB/node未准入，无产品/solver扩建。

## 2026-10-10 当前优先：联合规划理论成立条件

先读next_step最新块和articles/2026-10-10_joint_planning_necessity_and_boundaries.md，再读计划18.21/报告36、当前过程末段。结果theory_validation/proofs_cpu_r01/validation.json与review_final_r01/qualification.json（完整codex_coding/results/v0.1.23.3/task2前缀）；严格区分声明理论参数与旧实机四布局。已有strongsimple平台期反例要用瓶颈均衡补强对照，非MILP必要性；后备/relay/cut条件先对齐，理论角色不代表预算内可部署。

## 2026-10-10 当前优先：决策差异准入与复用

先读next_step最新块、计划18.20/报告35、基础场景过程最后段，再看decision_validation/case_cpu_r02/report.md及inspection.json、review_final_r01/qualification.json、measure_review_r01/measurement_contract.md（完整结果前缀codex_coding/results/v0.1.23.3/task2）。本轮CPU符号结果与上一轮真实四布局分开；预算受限case三成本区间无simple/joint代价差，不自动GPU对比。已有service_cost和strong_simple要复用，测量契约prepared-only，真实绝对误差仍unknown。

## 2026-10-09 当前优先：四布局预言与实测

先读next_step最新块、AGENTS/debug哲学，再读正式计划18.19、实验记录34和基础场景过程“先验拓扑与四布局认知验证”。数据根codex_coding/results/v0.1.23.3/task2/layout_validation：review_preflight_r01/predictions.json为实测前预言，execution_r01/execution.json为唯一GPU矩阵，operations_collect_r02/collection_result.json解释后处理补收，review_final_r02/qualification.json为最终116项独立验收。旧r01外层收集失败不覆盖，勿原名重跑。绝对成本/共享并发/模型T_ready/solver增量尚未由本轮验证。

## 2026-10-09 当前优先：最小成本、预算和产品恢复

先读AGENTS.md、next_step最新块与debug_philosophy，再读正式e1–e8计划18.18、实验报告33及基础场景过程最后追加。数据入口为`minimal_calibration/measure_finite_observations_r01/report.md`、`review_remote64_r01/summary.json`、`integration_product_r02/execution.json`及报告33最终独立验收链接（完整前缀codex_coding/results/v0.1.23.3/task2）。不要重跑旧run，不把prepared256当已执行，不把微测rate当产品profile，不把分散=nearest同解当solver收益。当前任务只做正常增量，无history覆盖。

## 2026-10-07 当前优先：keeper真实桥接与可运行冻结布局

先读next_step最新块、正式计划18.9和实验记录24节，过程细节在基础场景过程文档最后追加段。数据入口`keeper_bridge/bridge_real_r01`、`layout_smoke_t2_r01`、`layout_delivery_r01`、`audit_final_r01`（完整前缀codex_coding/results/v0.1.23.3/task2/）；读取next_step.md后再制定后续工作。当前运行会话仍baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json，target_b释放command已保留本次事件，后续先preflight；不重搭底本、不泛化T1/T3实机通过。

## 2026-10-07 当前优先：退化原因诊断

先读next_step最新块、正式计划18.8与实验记录23节；函数级过程在基础场景过程文档最后“有界因果诊断”段。最终结果为`topology_scenarios/diagnosis_audit_final_r02/report.md`，静态runtime证据diagnosis_static_r01，G1/G2最终摘要diagnosis_recollect_split_r01/diagnosis_recollect_split_boff_r01（原failed不覆盖）。完整前缀codex_coding/results/v0.1.23.3/task2。运行现场仍recovery_r03 ownedoverlay，下一步按生产桥接和公平baseline机会推进。

## 2026-10-07 当前入口：拓扑与场景契约

先看`docx/next_step.txt`最新阶段1/2块，再看正式e1–e8计划18.7与实验报告22节；函数级细节在基础场景过程文档最后追加段。数据使用`topology_scenarios/calibration_probe_r01`、`calibration_analysis_r01`、`scenarios_freeze_probe_r08`、`audit_final_r02`（完整前缀`codex_coding/results/v0.1.23.3/task2/`）。下一步按construction_decision.md的最小桥接推进，旧baseline和底本不重搭；当前运行会话仍是baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json。

## 2026-10-07 当前最小入口

先读`docx/next_step.txt`最新块、`AGENTS.md`与`debug_philosophy.md`，再读e1–e8正式报告第21节及基础场景过程文档的“真实baseline运行路径”追加段。三条成功run为`baseline_loading/native_observed_r01`、`blitz_s3_r02`、`serverless_qwen_full_r03`。后续现场只用`baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json`并先preflight；旧目标PID已更换。历史恢复文件本轮未改，不用其中旧现场覆盖这条更新。

2026-09-29晚间更新：先读当前过程文档附录I及`codex_coding/results/task2/cluster/managed04/managed_analysis.json`。
单目标联合控制层真实验收已通过，不再停留在待实施设计；下方历史阅读集仍用于溯源。

## 2026-09-29 最小必读集（按顺序）

1. `history-chat.txt`：完整交接块，先恢复固定编号、指标、环境、默认值及哪些未完成。
2. `docx/next_step.txt`与`docx/debug_philosophy.md`：当前用户覆盖规则、目录和控制变量方法。
3. `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md`：完整实验流程、失败、命令、数字和边界。
   该文已按v0.1.19.10深度增强：附录A/B恢复调用链与状态所有权，C/D恢复模型和字节计划，E/F恢复收益归因与profiler排错，G恢复假设/开关/下一步落点，H核对原始日志和机器摘录。
4. `docx/articles/2026-09-29_task2_joint_placement_findings.md`：联合优化/最大流的数学边界、HBM/DRAM tradeoff。
5. `docx/plans/2026-09-29_task2_joint_planning_control_plan.md`：下一阶段待实施计划，阅读后再讨论下一步。
6. `codex_coding/results/task2/cluster/placement01/placement_analysis.json`及`placement_solutions.json`：最新成功实证。
7. `codex_coding/results/task2/cluster/profile01/profiling_analysis.json`与`bulk_profile01/profiling_analysis.json`：profiling证据和失败状态限制。
8. `main_lib/fluidckpt/cluster/joint.py`、`planning.py`、`control.py`、`catalog.py`、`worker.py`及`run_placement_comparison.py`：恢复现有接口边界。

不需要先重搭环境、重跑所有benchmark或先解决GLM。下方旧集保留作历史背景，不是最新最小阅读顺序。

恢复当前 `v0.1-init-project` 现场所需的最小过程文档集合。目标是用最少的阅读量
重建「现在在哪、为什么在这、下一步做什么」。

## Required

- `code_building/process_docs/v0.1-init-project/v0.1.23.1-project_restructure_and_spec_migration.md`
  - Required because: 本轮（也是首次）把散落的成果按规范迁入仓库，确立了
    `main_lib/` / `codex_coding/` / `docx/` / `code_building/` 四层归属。
    不知道这次迁移，就无法理解为什么代码在 `main_lib/` 而不是仓库根。

- `code_building/progress_diff_summary.md`
  - Required because: 增量记录每轮进展与关键命令，是「怎么走到今天」的时间线。

- `code_building/key_conclusion.md`
  - Required because: 汇总最新结论，防止被历史文档中的过时推导误导
    （尤其 GLM-5.3 崩溃定性这条）。

- `docx/context_index/04_insight_ledger.md`
  - Required because: 机制层面的洞察账本（I1–I12），方向选择前必须查。

## Must-Read Non-Process Files

- `docx/next_step.txt` —— 阶段代号与建设原则（含 core dump 防护、main_lib 分层）
- `docx/context_index/01_stage_summary.md` —— 当前阶段与 **Do Not Repeat** 清单
- `docx/articles/2026-09-27_glm53_startup_vmfault_forensics.md` —— 崩溃定性的完整取证
- `docx/articles/2026-09-27_multinode_transport_findings.md` —— 传输能力数字（规划器输入）
- `codex_coding/results/logs/base2_n0.log` —— 纯 SGLang 复现崩溃的原始证据

## Not Included On Purpose

- 全量 40+ 份 `logs/` —— 只留 8 份精选；其余留在 `/home/zhujianian/fluidckpt/logs/`
- `.spec_backup/*.dllm_wh.bak` —— `dllm_wh` 继承内容，仅作来源参考
- `codex_coding/results/` 下的原始 JSON —— 是按需查证的证据源，不必通读
- `codex_coding/src/` 下的全部脚本 —— 用到哪个读哪个

## 2026-10-08 当前续作入口

- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md` 第30节及 `docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md` 18.15：27/28/30替代范围、各自准入阻断、30唯一init失败和只读配置事实。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_qualification_r02/report.md` 与 `audit_failed_node30_init_r01/report.md`：新27D与旧29两pending、28Dockerroot满、30精确回收/运行未通过。新流程不从旧29控制重试或旧GPU run重新开始。

## 2026-10-09 最终续作入口：IB通路已通过

- 正式实验报告32.7–32.12与计划18.17：两端初始化/复制、15s预算纠正、插件复用、三次NCCL的失败及最终完整通过。
- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/README.md`、`runtime_gate_r03.py`：当前60/90预算入口；原35s wrapper不是当前执行入口。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_nccl_2830_node28_r03/summary.json`与node30对应：最终真实IB/自有XML及数据正确性证据，下一步测速率/竞争；旧失败仍保留，不能从旧pending状态从头重搭。
