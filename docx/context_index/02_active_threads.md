# Active Threads

## 2026-10-23 最新阅读入口：在线校准详尽过程专题

先看[详细专题](../../code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_online_cost_calibration_detailed_20261023.md)第1–5节恢复实现/测量边界，第6–9节核原始账与验收，第10节找只读原件命令，第11节讨论规划器下一步。0.19%/6.04%仍是原登记窗误差，恢复前资源门和command发布在窗外。此次只补文档，无新实验；恢复后重点仍是同预算真实决策价值。

## 2026-10-23 当前交接：用户授权归档，下一轮先讨论

读取history-chat.txt、next_step顶部、outer_online_calibration_report和outer_analysis_actual_r02。编号.3/task2；成本校准阶段已完成，本次没有新GPU/SSH/Docker或fresh资源检查。10/12自有容器停止证据仍为历史终态。恢复后围绕同预算布局选择及真实主指标讨论下一步，未开启新规划实验。旧GLM交接已完整备份，复用时先核原件与资源，不按旧serving记录操作owner。

## 2026-10-12 最新：本域整体10%已过，下一步规划选择价值

首读[外层校准报告](../experiments/2026-10-12_outer_online_calibration_report.md)。online_outer_two_node_r01四次完整Qwen恢复、同controller、唯一后续C0校准；remote4.581127s/预测4.572579s、near1.290400s/预测1.212516s，误差−0.187%/−6.036%，独审73项通过。首次3.024893s单列，不在后续拟合域；旧12点成本不变，outer由.808030变.423580s。两节点三GPU的登记后续域可用，不外推首次/新进程/三节点多NIC/SSD。

四次正确性、物理park和回收通过，精确两个实验容器已停止，无后台GPU。本轮已完成，不继续带宽或IPC微项纠偏；下一步固定同预算候选场景，让现有规划器与强简单策略实际选择/执行，在线规划开销按协议计入，检验端到端收益。复杂域留到资源恢复。下方“下一步外层校准”等为旧状态。

## 2026-10-12 最新：简化两节点在线闭环完成，下一步外层校准

首读[简化实际报告](../experiments/2026-10-12_simple_two_node_online_report.md)及[当前计划](../plans/2026-10-12_simple_two_node_online_validation.md)。两节点三GPU单sender、完整70.33GB模型的C0+Hnear/Hremote已实测，全部正确性/资源/准确回收通过。Hnear1.31220s预测1.59697s（21.70%未过）；Hremote4.56026s预测4.95703s（8.70%过）；排序正确，整体10%仍不认证。两次路径DAG误差仅10/7ms，共同outer迁移占总偏差96–98%。下一步从现有beforecopy/控制/IPC时间账限定外层/状态校准，不重做拓扑、不追加本轮第三session、不回填留出。所有自有容器已stop，无后台GPU；原复杂四卡方案保留。有资源再恢复复杂域，当前功能/局部精度不扩域。

## 2026-10-12 18:52 最新：小IPC与完整物理暂停已过，待远端资源恢复后直接测在线成本

看[在线ready报告第7节](../experiments/2026-10-12_online_ready_cost_validation_report.md)。一次/两次同凭证导入32MiB对照已复现0/32MiB保留；实际Torch2.10，原计数偏移读法无效，未来默认unknown。node27完整Qwen70.33GB全SHA、DRAM保活和物理pause通过，每rank释放35.18GB、整卡free增加35.21GB，独审31项。无需再探索小IPC/物理pause，后续作为正常每次运行正确性门即可。远端30最新每卡used49.05GB，完整cal/C0/留出仍0，10%未知；新旧自有容器均停止，无后台GPU。原engineguard失败与subreaper/quiet成功分列，未来closure独立账本CPU16/独审5项通过，待新快照生效。资源可用后fresh准入、新freeze原两节点矩阵，不改开发号/预算或重复跑旧目录。

## 2026-10-12 最新优先：真实在线成本验证，资源阻塞

计划18.35/记录50，首读[在线ready执行报告](../experiments/2026-10-12_online_ready_cost_validation_report.md)。目标为线上整体误差<=10%，全SHA后置，不沿用旧微项门。两节点四GPU真实Qwen TP2已完成源构建，r01线程上限失败已修；r02暴露物理暂停未释放及新wrapper重复消费IPC凭证，单映射修复/完整失败事实/物理释放检查CPU35项通过，实际效果待验。32MiB诊断在创建payload前因外部GLM占用拒绝，pathcal/C0/留出均0，准确率未知。两个本轮自有容器已停止，无后台任务。资源可用时先小IPC、完整真实park，再继续原预测冻结/留出；不重跑旧目录，不扩solver或追微项。

## 2026-10-12 当前优先：从已完成成本账本定位控制段迁移

成本C0/C1/C2已在8请求上限完成，不再“等待继续C1”。node27GPU4两会话六校准/两留出全SHA和清理过；GPU0前置资源拒绝原件保留，无活跃GPU任务。三项局部worker候选通过独立门，但整体复杂度升级未过；V1filled低估1.676ms主要inventory/prepare前段多1.622ms，服务内微调解释不了。先读docx/experiments/2026-10-12_cost_ledger_calibration_report.md和cost_ledger/analysis_gpu4_final_r01/report.md，下一步以现有原件区分必要控制与实验诊断，需要才定最小补测；不自动扩C3/C4或solver。编号.3/task2不变。

## 2026-10-11 当前优先：成本账本与辨识，暂缓直接规划

按用户最新要求执行计划18.33/记录48，先读docx/plans/2026-10-11_cost_accounting_and_identification_plan.md。新矩阵最多8请求仍为设计；已有CPU秩检查通过，未拟合新参数。子agent后续实现最小计时覆盖层/CPU协议，主资源准入与审查。首状态A不支持统一收费，B已经跳过，不再续跑；旧模型有限域保持。只按必要布局扩remote/SSD/竞争，不做全路径扫描；成本可靠后才进入同预算规划与live主指标。

## 2026-10-09 当前优先：已验证IB路径的容量与共享竞争校准

28GPU2/NUMA2→shca_0/NUMA0↔30shca_0/NUMA0→GPU0/NUMA0，自有XML与1MiB完整IB功能已在r03通过；先读报告32.12和当前runtime_gate_r03/README。下一步核资源负载、测这条及候选路径速率/并发竞争与实际NUMA页驻留，不重做初始化/安装插件、不把标称400Gb/s当可用容量。30root满、小功能暂用私有shm；正式长期存储另安排。27/29旧pending仍未证，旧模型会话不自动迁移；性能规划未就绪。

## 2026-10-08 当前优先：27/28/30运行准入

用户将范围切到其余三节点，先读计划18.15/报告30及three_node_recovery/inventory_qualification_r02。27本轮KFD采集新D child待退出、28Dockerroot根盘无空间、30fresh set_device未在15s总deadline完成；暂停copy/XML/性能，优先30隔离配置的有证据差异核对。旧29控制/两pending继续保留，暂不远端调用。原有论文目标与主指标weights-ready保持。

## 2026-10-07 最新：生产桥接与冻结布局接口收尾

keeper_bridge/bridge_real_r01与layout_smoke_t2_r01均passed；audit_final_r01/report.md和next_step.md为当前建设判断，正式报告24节。当前薄runner能执行T2 split_rank_prefix→target_b/fastest/commoninterleave，其他组合仍未测；三CPU策略同解，暂不扩solver或共享成本项。下一步可靠服务参数与相同NIC机会下的有界比较，避免将配置/GDR/重复内容差异包装成布放收益。所有GPU工作已结束、无pending，现场仍recovery_r03 ownedoverlay，两引擎parked。

## 2026-10-07 最新：有界诊断收尾

用户关于退化原因的追问已由G0/G1/G2控制回答到路径组合/GDR层。读实验记录23节与`topology_scenarios/diagnosis_audit_final_r02/report.md`；不要继续把36.46GB/s当全局固定cap、把两流统称GDR，或把split两NIC改善说成同网络预算算法增量。原场景配置仍保留其原身份，后续生产桥接需明确baseline共同可用NIC/模式。探针已全部清理，无运行中GPU实验，未改生产XML或history-chat。

## 2026-10-07 最新：阶段1/2已收尾，下一步生产路径桥接

读取正式计划18.7、实验记录22节和`topology_scenarios/audit_final_r02/construction_decision.md`。当前共享证据来自独立RCCL小探针，不能直接用36.4592GB/s替换keeper_serial profile。三个最终case在scenarios_freeze_probe_r08；六对fastest/LP同解，不重复整模型。尚需现有worker小桥接、外部layout/caps/目标与共用schedule薄runner、同复制机会强简单对照；不重写solver或执行器。新4keeper/旧14keeper/两parked引擎保护通过，当前无GPU实验在跑。

## 2026-10-07 最新：baseline真实加载分支已通过

v0.1.23.3/task2下vendor native122.266963s、Blitz单目标适配2.804379s、上游Store HIP131.121135s均完整验收；失败/微probe/recovery不混入成功性能样本。正式报告第21节为当前结果入口。最新现场为`baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json`（完整路径前缀`codex_coding/results/v0.1.23.3/task2/`）；原node29目标与recovery_r02进程已停，源keeper/DRAM保留。下一步仍为同预算和共享资源下的论文差异验证，未启动原版系统全面移植、物理S5-P或e7/e8。

## 2026-10-03 当前交接

- 已完成：固定TP2双模型A→B→远端A→本地A；warm engine、HIP IPC对照、远端DRAM+真实SSD、MILP/LP及全量校验。
- 下一研究：同预算简单策略对照，分解0.5GiB/rank慢SSD供给，细粒度迁移发布/中途召回，完善峰值内存与page-cache账本。
- 无在运行的本轮任务；不要接续历史full/final PID。结果与复现命令见`docx/articles/2026-10-03_lifecycle_results.md`。

## 2026-09-29续作：单目标控制层首交付已通过

managed04已完成全模型候选→联合求解→预算内布局变更→真实恢复。
42项CPU测试、两次70.33 GB和生成校验、提交后中断续作/幂等重放通过；无活跃实验资源。
结果及失败链见过程文档附录I。下方“七步待实施”是先前归档时状态；当前仍未做的是多目标、
可迁移底本、通用故障恢复和在线反馈等扩展。

## 当前主线：联合规划控制层（2026-09-29，v0.1.23.2/task2）

- **已验证基础**：Qwen TP2三节点八阶段完成；联合MILP模型与物理placement对比成功；资源LP在实验中明确启用无回退。
- **当前缺口**：候选/预算/布局变更部分在实验脚本中；joint函数未接统一placement RPC；多目标、峰值迁移预算、反馈控制尚未闭环。
- **下一步顺序**：统一输入→自动候选→成本→联合求解→变更执行→恢复衔接→反馈。先单目标自动闭环。
- **验收限制**：允许集中8 GiB的场景还未做；4 GiB/卡是先验限制；当前速度只是一轮样本。
- **执行状态**：最新用户要求归档与恢复说明；本次已归档，无活跃GPU实验，七步计划待后续实施指令。
- **必读计划**：`docx/plans/2026-09-29_task2_joint_planning_control_plan.md`。

## 当前开放诊断旁线

- 双缓冲真实模型收益不明显的原因尚未完成设备级因果证明；不能仅由GPU event重叠下结论。
- 成本模型的并发/资源容量、真实scatter开销与首次准备仍需补齐有效范围。
- 严格SGLang自身保活对照与GLM适配均是后续旁线，不阻塞当前联合控制层。

## 历史线程（2026-09-27，状态已被以上条目更新）

## T1: 三节点 Keeper Mesh 保活与 max-flow 流动

- **Status**: **active（被平台缺陷阻塞）**
- **Priority**: 最高（项目主线）
- **Current problem definition**: 让 GLM-5.3 的 TP shard 在三节点的 SSD/DRAM/HBM 之间被保住并
  按 max-flow 计划流动，使得任意节点都能快速 restore；并证明全面优于 SGLang 自身的保活。
- **Confirmed facts**:
  - Keeper mesh 已实现并跑通：每卡一个 worker，HTTP 控制在 `PORT_BASE=39500 + local gpu`，
    dispatch 表支持 `mesh/warm/capture/save/load/flow/synth/checksum/drop`。
  - `ctl.MESH = "node27:0-7,node28:2-7,node29:0-7"`，master `12.11.3.27:39600`。
  - HIP IPC 零拷贝交接已验证（consumer 峰值 HBM 0.01 GiB）。
  - 跨节点聚合带宽 205.8 GB/s，规划器可按此估算。
  - `fluidckpt.transport.flowplan.solve()` 可解多源多汇 max-flow 并给出 `to_ops()` 操作序列。
- **Current hypothesis**: 保活的端到端收益主要在「省掉 `disk → decode → TP shard → quantize`
  整条路径」，而非省掉权重读取那 14 s（见 `articles/2026-09-27_glm53_startup_vmfault_forensics.md`…
  注：该结论出自 `findings_startup.md`，用户已纠正「保活只省 12%」的框架）。
- **Missing evidence**:
  - 完整的 7 步端到端计时（capture → save SSD → spread node28 DRAM → drop node29 → restore → fired up）。
  - `hostrd` 路径的真实带宽（当前仍是 180 GB/s 占位值）。
  - node28/node29 的 `gpu_numa` 实测值。
- **Suggested next step**: 用 Qwen3.6（已验证可启动）跑通完整链路拿端到端数字，绕过 GLM-5.3 阻塞。

## T2: 与 SGLang 保活的端到端对比

- **Status**: **active**
- **Priority**: 高（这是「全面胜过」的判据）
- **Current problem definition**: 在同一硬件、同一模型上，量化 FluidCKPT 保活 vs SGLang
  自身保活的 restore 时间差。
- **Confirmed facts**:
  - SGLang 的 `--enable-weights-cpu-backup` 路径在本环境**不可用**（`torch_memory_saver` 未安装）。
  - SGLang 逐张量远程加载的固定开销是 **99.3 µs/次**；bucketing 可将其摊薄到 **27.8×** 更优。
  - 纯 vanilla SGLang 的冷启动在 GLM-5.3 上根本无法完成（崩溃），所以基线只能用
    Qwen3.6 或其它可启动模型测。
- **Current hypothesis**: SGLang 逐张量搬 + 每张量一次集合通信，是我们的主要性能优势来源；
  差距应随张量数量（118,629）放大。
- **Missing evidence**:
  - SGLang 保活的**实测** restore 时间（当前只有机制分析和单次广播成本）。
  - 对比实验的公平口径（同模型、同 tp_size、同网络条件）。
- **Suggested next step**: 先在 Qwen3.6 上建立口径，再迁移到 GLM-5.3。

## T3: `keep2` 神秘死亡定性（收尾中）

- **Status**: **warm（基本已定性）**
- **Priority**: 低
- **Current problem definition**: `keep2` 在 19:26–19:30 启动后无 VMFault、无 traceback、
  干净退出（`resource_tracker: 8 leaked semaphore objects`），零个 `[fluidckpt]` 标记。
- **Confirmed facts**:
  - 它跑在 19:25–19:35；17:47–17:49 两批共 16 个 core 已把 `/home` 写到 100%。
  - `keep2_resume.log` 是 **0 字节**——创建成功但写入失败，符合 ENOSPC 形态。
  - 8 个 rank 都到达 "Load weight begin"。
  - `ctl.stop()` 只 pkill `fluidckpt.serving.keeper`，**不碰 SGLang**，所以当时怀疑的
    「keeper 重启干扰」在机制上不成立。
- **Current hypothesis**: **ENOSPC**。worker 因无法写日志/临时文件而起不来，被回收。
- **Missing evidence**: 当时写入确实返回 ENOSPC 的直接证据（日志本身没写进去，所以是自证困境）。
- **Suggested next step**: 接受 ENOSPC 为最可能解释，不再投入；在 `next_step.txt` 第 17 条
  已加入 core dump 防护来防复发。

## T4: NSA path 平台缺陷规避（外部依赖）

- **Status**: **warm（等 vendor）**
- **Priority**: 中（决定 GLM-5.3 是否可用）
- **Current problem definition**: 找到让 GLM-5.3 在 gfx936 上启动的路径。
- **Confirmed facts**: 见 `01_stage_summary.md` 的 Do Not Repeat 第 1–4 条。
- **Current hypothesis**: 需要 Hygon/SGLang 侧修复：要么让 `gfx936` 支持 native fp8 index cache，
  要么给 `lightop_attention.fuse_layernorm_rotary_embedding` 一个 bf16 可用路径。
- **Missing evidence**: vendor 侧的修复计划与时间表（不可控）。
- **Suggested next step**: 不阻塞主线；用其它模型推进 T1/T2。
