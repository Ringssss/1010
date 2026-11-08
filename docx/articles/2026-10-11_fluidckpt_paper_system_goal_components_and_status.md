# FluidCKPT论文系统：目标、组件与当前进度

日期：2026-10-11。开发编号：v0.1.23.3/task2。依据当前源码和已保存验收记录更新；本轮没有新增实机测量，历史机器可用状态不能代替下次fresh准入。

## 1. 主体和代码复用方向

FluidCKPT始终是我们建设的产品和论文系统。现有main_lib、执行平台、实验数据和接口继续作为基础。Janus提供组件源码参考和baseline机制，不把任务改成给Janus添加复杂拓扑、把Fluid搬到Janus、或先实现两套引擎互通。

复用按以下顺序决定：Fluid需要解决什么缺口→已有实现是否足够→Janus哪段成熟代码能补强→提取和适配到Fluid的范围→正确性、主指标或维护成本验收。可以直接参考并迁入具体代码，不限于借鉴思想；原版Janus能否在海光整体运行不是这一判断的前提。原外部仓库保持冻结，迁入代码保留来源与必要许可。

## 2. 论文建设目标

面向暂停大模型，在分布不均的保活预算与共享通信资源约束下，联合组织权重块的驻留和恢复，改善保活空间与权重可用时延之间的权衡。

直观地说，模型暂停后，如何利用本地/远端GPU显存、不同NUMA的CPU内存和SSD保存它；当模型需要在指定目标设备组恢复时，从哪些有效副本取哪些字节、按什么顺序搬运，才能让所有目标rank更早拿到正确权重，同时不超过保活和恢复过程中的资源预算。

主指标是权重开始加载/恢复到全部必要权重可用的时延。必要的在线规划、准入、准备和搬运应按统一协议计入；源数据/保活布局建立成本另列，不能被隐去。process启动、HTTP ready、数据filled、校验published、模型weights_ready和first token是不同边界。论文增量最终要兑现到权重可用，不能只报告预测误差降低或单段带宽变高。

需要证明：同资源预算下能恢复更快，或同恢复要求下占用更少保活空间；在复杂拓扑和共享资源条件下，相对强就近/均衡/交换等简单策略存在真实决策价值。共享空间归还与外部进程实际可用空间分开记账。并非每个工程组件都必须成为论文新贡献。

## 3. 组件及层次

系统因果链为：权重对象和资源状态→预算与拓扑下的保活/恢复决策→受控执行与模型接管→真实权重可用反馈。

| 组件 | 要解决的问题 | 当前状态 | 剩余重点 |
|---|---|---|---|
| 权重对象、存储和生命周期 | 知道每份权重的内容/版本/区间、谁持有、占哪层空间、何时能安全释放 | Catalog/ReplicaCatalog、HBM/NUMA DRAM/SSD owner、identity/epoch/generation、coverage/lease、暂停/恢复/退役已实现并有实机验证 | 保留底座；仅按实证缺口吸收并发发布、布局/缓存管理等成熟实现；逐位置和瞬态预算继续核对 |
| 物理资源地图与路径能力 | 知道GPU/NUMA/HCA/SSD位置、哪些路径能用、共享什么资源 | 三节点六GPU/六NUMA/三SSD角色及23条serial必要路径验证；有实际BDF/owner/配置准入 | 只补影响候选决策的共享容量/并发工作点，不扫全部路径或继续堆地图字段 |
| 成本模型和计时合同 | 估计给定布局/执行依赖的恢复成本，避免把诊断或首操作成本误作带宽 | 已有source/target ready DAG、有限三路径模型新尺寸验证；首操作A8实机完成，状态门未过、B按规则跳过 | 首项存在但统一thread/request收费规则未识别；原模型保持，按决策差量决定后续必要定位，不无限微调 |
| 联合保活与恢复规划器 | 在位置预算下选保留哪些块/放在哪，并选恢复来源与操作计划 | nearest/fastest/resource LP、强简单、交换、joint/multi_joint MILP及managed transition接口已有；有理论/CPU实例和历史模型实验 | 这是核心研究重点：与可信成本/必要共享约束接通，在同内容/target/预算下形成不同合法决策并验证收益；不从零造solver |
| 恢复执行与引擎接管 | 把计划转成真实local/peer/network/SSD搬运，保证数据和生命周期正确，最后交给模型 | HIP/RCCL、分块执行/暂存、全字节SHA、IPC/layout/handoff已通；历史真实Qwen TP2已正确恢复和输出 | 当前复杂三节点场景与完整真实模型/同预算策略闭环仍需衔接；并发优化只在实际瓶颈证明后建设 |
| 实验与对照评测 | 区分功能成功、预测准确、策略收益和完整系统效果 | 已有分段计时、预算/来源/校验/清理证据；SGLang/HIP IPC、Janus式host/D2D等对照和初步预算曲线 | 在统一机会和口径下证明真实模型主指标；原版/适配/机制身份保持，e7/e8等按论文主张和既有阶段计划选择 |

实现入口：`main_lib/fluidckpt/cluster/{catalog,planning,joint,multi_joint,managed,service_cost,worker}.py`、`memory/`、`topology/`、`serving/{lifecycle,keeper,layout,ipc_attach,handoff}.py`及`benchmarks/basic_scenarios.py`。模块存在不等于全部目标场景已完成性能验收。

## 4. 到了哪一步

功能底座已经打通，当前在“把可信的资源/成本信息接到布局选择，并形成论文核心收益证据”的阶段。不是从头建设缓存或通信，也没有完成复杂集群上的全部论文验证。

1. 历史真实模型：Qwen TP2已有完整权重搬运、全量校验、引擎接管和正确输出；已有集中/分散和0/8/16GiB等有限预算观察。这些不是最新六GPU拓扑的自动资格。[历史基础场景证据](../../codex_coding/results/v0.1.23.3/task2/basic_scenarios/complete_table_r01/review_r02/report.md)
2. 最新复杂拓扑：node28/node30/node27的六worker、六NUMA和三个SSD服务已完成合成TP4式32MiB八块、四target恢复。SSD_only/C0/C1/C3是不同预算的手工见证，证明可执行和正确，不能当同预算联合规划胜出；该场景尚无live TP4模型handoff。[多层实机资格](../../codex_coding/results/v0.1.23.3/task2/multitier_runtime/review_final_r02/qualification.json)
3. 规划和模型：算法已有；有限三路径模型中24MiB新组合execute误差约−5.77%，但同预算布局排序仍未认证。published预测null，local常数只在有限单op域适用。[有限模型审查](../../codex_coding/results/v0.1.23.3/task2/bounded_cost/review_final_r03/report.md)
4. 首操作问题：同handler主机首操作诊断之后，A阶段8请求已实际完成。持久线程R2仍有首差，两目标条件交互较大，统一thread/request状态门未过；B按预登记跳过，未准入新模型。不能说规律不存在，也不能把B说成待补完实验。[A最终分析](../../codex_coding/results/v0.1.23.3/task2/first_op_validation/analysis_result_A_r01/report.md)、[协议与执行状态](../plans/2026-10-11_first_operation_state_validation_plan.md)
5. Janus：已完成冻结源码与当前组件映射，没有向Fluid迁入新Janus代码，也没有运行原版Janus。既有janus_host/janus_d2d是共同执行器机制对照。[组件审查](2026-10-11_janus_retention_reuse_audit.md)

不报告一个整体完成百分比：功能资格、当前拓扑资格、模型校准、同预算策略收益、完整模型主指标和论文论证分别有不同证据。

## 5. 哪些地方值得重点建设，Janus能提供什么代码

| Fluid重点/缺口 | Janus具体可参考实现 | 当前决定 |
|---|---|---|
| 并发加载时同一权重只建一次，发布后才能使用，最后引用释放 | PinnedHostMemoryEntry与acquire_or_create/publish/release的等待、通知、引用同步 | 可按缺口把同步逻辑补入现有Fluid owner/Catalog。分配器用我们已验证的NUMA/HIP实现，不再建第二个cache服务。当前未证明必须替换。 |
| 处理后权重布局与分段绑定 | PinnedHostMemoryWeightSlice、init_weight_slices中的offset/bytes/shape/dtype组织和校验 | 可提取元数据辅助到Fluid manifest/layout；保留版本/coverage/epoch校验，NPU专用NZ/ATB绑定不照搬。已有布局足够时不增加模块。 |
| 稳定的实际执行上下文与成本模型 | Janus threadpool/WorkerService的长期执行线程和调度结构 | 作为现有worker执行分派设计参考；先运行首状态区分实验。Python实现可复用标准库/已有队列，不为了参考C++代码引入整个Janus服务。 |
| 多来源传输接口的特定缺口 | MooncakeWeightTransfer的pull/push/link及session引用代码 | 仅现HIP/RCCL路径不能完成必要取数或有性能证据时，评估作为Fluid transport adapter；不以“可移植”自动开启Mooncake工程。 |
| 联合驻留、取数和共享资源决策 | Janus的完整host/active源策略、来源保护/后备作为强基线 | 继续在Fluid现有planner中建设。这是论文核心新增；复杂拓扑块级目标不是迁入一个cache就自动完成。 |

组件迁入的验收问题是“是否补足Fluid明确功能、降低主指标或减少维护负担”，而不是“是否成功把Janus编译起来”。外部代码可以是实现来源，最终控制面、数据面接口、实验体系和论文系统仍为FluidCKPT。

## 6. 接下来顺序

首操作A阶段已完成并按规则停止，不继续给未识别状态补统一系数。后续在Fluid现有预算与路径框架里构造同内容、同target、同预算的两个合法布局，先CPU确认决策差异及其是否超过已观测不确定性，再实际验证恢复主指标；细差不足以区分时不能宣称排序。只有某个未识别成本确实影响选择时，才提出针对性新测量。最后把有价值的决策接回真实模型恢复，形成组件消融和空间—时延证据。

这几步过程中再选择性吸收Janus具体实现；不单独开“Janus拓扑改造”或“整体换底座”路线。不预设复杂求解器一定胜出，简单策略足够时就采用简单策略。
