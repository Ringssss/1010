# Key Files Index

## 2026-10-06｜论文范围管理指南

- `docx/01_paper_scope_management_guide.md`：每次规划下一步建设的长期参考，包含建设准入、证据门槛、e1–e8 决策点、停止规则和组件建设决策报告模板。
- 已接入文档地图、注册表及 e1–e8 计划；本次仅文档维护，开发编号仍为本会话 `v0.1.23.3 / task2`。


## 2026-10-04｜v0.1.23.3/task2 e1–e8 实验计划与记录规范

- `docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md`：正式研究实验计划，包含 e1–e8 的变量、基线、指标、预算和停止/推进规则。
- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md`：一一对应的实验章节、预登记和报告模板、完整数据契约；当前全部待执行。
- `code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_e1_e8_document_protocol_20261004.md`：本次文档建设与核查记录。
- 原 `.2` 研究线及旧结果保留；本次不新增实验数据、不覆盖 `history-chat.txt`。


## 2026-10-04｜完整GLM跟进与生命周期入口

- `codex_coding/results/glm_three_node/native_hca0_full05/summary.json`：完整GLM相同HCA原生输出重复通过；不是隔离启动性能。
- `codex_coding/results/glm_roundtrip/diagnostic_distributed06/summary.json`：8层TP16三节点后备/释放/恢复全SHA与HIP IPC三次命中通过，非完整GLM。
- `codex_coding/src/task2/cluster/glm_distributed_capture.py`：分阶段capture→remote DRAM→校验→释放本地→剩余capture。
- `codex_coding/src/task2/cluster/continue_glm_full_validation.py`、`continue_glm_lifecycle_validation.py`：原生参考→完整roundtrip→GLM/Qwen切换的自动衔接。
- `codex_coding/src/task2/cluster/continue_glm_ab.py`：真实双模型、三节点DRAM/SSD、迁移中本地召回驱动；尚待运行验收。
- `codex_coding/src/diag/profile_glm_warm_restore.py`：准备好的首forward profiling入口；尚无trace。
- `codex_coding/src/diag/analyze_glm_roundtrip.py`：按真实PCI/DRM映射生成物理内存曲线与阶段放大图。
- `main_lib/fluidckpt/serving/{layout,ipc_layout}.py`：属性与别名重建、旧Tensor存活期保护、精确惰性alias验证。
- `main_lib/fluidckpt/cluster/block_migration.py`：逐块提交和可行交换顺序，后者不宣称全局最优。
- `codex_coding/results/native_cpu_backup/qwen35b03/summary.json`：原生CPU备份首次恢复正确、第二次错误，整体failed；不能计为成功baseline。
- `codex_coding/results/tms_dtk_fixed2/`：隔离依赖wheel、source、兼容补丁及provenance；没有全局安装。

## 2026-10-04｜GLM执行及HLL分析

- `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_glm_three_node_execution_20261004.md`：独立算子故障复现、完整TP16尝试和三节点支持改动。
- `codex_coding/src/serving/run_glm_native.py`：原生TP16首生成验证及自动清理。
- `codex_coding/src/task2/cluster/run_glm_roundtrip.py`：原生通过后运行的TP16 capture/park/restore驱动，目前未验收。
- `codex_coding/src/diag/probe_glm_{nsa_rope,fp8_linear}.py`：隔离NSA融合调用与FP8后端，结果分别见glm_three_node/rope01、gemm_triton01及glm_gemm_auto01.log。
- `codex_coding/src/task2/cluster/probe_three_node_ssd.py`：node28本地NVMe extent真实往返验证。
- `docx/articles/2026-10-04_hll_and_recall_planning.md`：HLL适用范围、近似观测/精确执行、慢路径块保留假设与实验矩阵。

## 2026-10-03｜三节点 GLM 复核

- `docx/plans/2026-10-03_three_node_glm_followup.md`：完成/未完成逐项对照、准确基线口径及三节点TP16候选配置。
- `codex_coding/src/diag/audit_three_node_glm.py`：只读节点/模型/历史故障核查，可复跑并保留证据。
- `codex_coding/results/three_node_glm_audit_20261003/evidence.json`：最新三节点状态和原始日志摘要；无新增GLM跑分。

## 2026-10-03｜生命周期实现与 final03 验收

- `docx/articles/2026-10-03_lifecycle_results.md`：最终端到端结果、图、成本与结论边界。
- `codex_coding/src/task2/cluster/run_lifecycle.py`：双模型四节点真实轨迹，支持阶段checkpoint/续跑；默认零本地A保活DRAM、22GiB服务HBM预留。
- `main_lib/fluidckpt/serving/layout.py`、`lifecycle.py`：storage/view/alias重建及warm engine的release/resume。
- `main_lib/fluidckpt/cluster/eviction.py`：有限块单份覆盖MILP；现有resource LP执行恢复。
- `main_lib/fluidckpt/memory/ssd.py`、`serving/ssd_store.py`：node30 CPU/本地SSD extent服务。
- `codex_coding/src/task2/cluster/analyze_lifecycle.py`、`codex_coding/src/plt/plot_lifecycle.py`、`codex_coding/src/diag/write_lifecycle_report.py`：分析、PNG/SVG和报告。
- `codex_coding/results/lifecycle/final03/`：未中断的最终验收，summary/analysis/session、进程命令、连续HBM样本、源码指纹、paired及lifecycle图。
- `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_lifecycle_execution_20261003.md`：完整过程与失败链。

## 2026-10-03｜生命周期设计审计

- `docx/plans/2026-10-03_fluidckpt_lifecycle_design.md`：官方 daemon 边界、历史证据、双向切换期限、MILP/LP、page cache、端到端协议和实验矩阵。
- `codex_coding/src/diag/audit_keepalive_evidence.py`：只读取既有产物和历史工具调用的审计脚本。
- `codex_coding/results/design_audit_20261003/evidence.json`：5组历史run与4组真实历史命令；非新benchmark。
- `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_lifecycle_design_audit_20261003.md`：审计经过、命令、限制和恢复入口。

## 2026-09-29｜managed04 单目标控制层验收

- `main_lib/fluidckpt/cluster/managed.py`、`managed_control.py`：接手时已存在的规划/预算/候选/变更/恢复接口，本次完成验证。
- `main_lib/fluidckpt/topology/placement.py`：本次新增NUMA容量预选，先保护本地需求、再邻近回退，输出审计。
- `codex_coding/src/task2/cluster/run_managed_placement.py`、`codex_coding/src/task2/configs/managed_single_target.json`：配置驱动的真实Qwen TP2验收，含提交后中断续跑。
- `codex_coding/src/task2/cluster/analyze_managed_placement.py`：预测、实际加载、传输和预算报告。
- `codex_coding/src/tests/test_managed_placement.py`、`test_numa_placement.py`：新增8＋3项区分性CPU测试。
- `codex_coding/src/task2/cluster/inspect_node.py`、`run_system.py`：端口占用检查及显式NUMA选择接线。
- `codex_coding/results/task2/cluster/managed04/{summary,placement_plan,placement_journal,managed_analysis}.json`：passed完整证据。
- `codex_coding/results/task2/cluster/managed0{1,2,3}/summary.json`：端口/NUMA启动失败，保留failed。
- 完整过程同`v0.1.23.2-task2_system_profiling_joint_placement.md`附录I。

## 2026-09-29｜过程文档增强补充入口

- `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md`：新增附录A～H，覆盖调用链/状态机/模型实现/字节账本/排错与假设矩阵/后续代码落点，参考v0.1.19.10深度。
- `codex_coding/src/diag/extract_task2_archive_evidence.py`：只读既有结果，断言覆盖、哈希、加法账本后生成并同步附录H；没有GPU或重新求解。
- `codex_coding/results/task2/archive_20260929/detailed_evidence.json`、`detailed_evidence.md`：7个来源的摘录及SHA256，包含原worker日志、原始验证哈希和GPU标签纠错证据。
- `history-chat.txt`与对应v0.1.23.2历史副本：增加增强附录及提交/退休能力边界的恢复提示。

## 2026-09-29｜v0.1.23.2 / task2 最新恢复与证据入口

| 路径 | 作用 |
| --- | --- |
| `history-chat.txt` | 最新完整恢复块，已覆盖旧内容 |
| `codex_coding/src/diag/check_task2_archive.py`、`codex_coding/results/task2/archive_20260929/check.json` | 归档完整性、必读路径、恢复副本一致性和文档SHA256核验 |
| `code_building/history_chat/v0.1-init-project/v0.1.23.2-task2_joint_planning_recovery.txt` | 与根恢复文件同文的历史副本 |
| `code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md` | 从单机NUMA到三节点、控制面、流水、profiling失败链、MILP和真实placement的完整过程 |
| `docx/articles/2026-09-29_task2_joint_placement_findings.md` | 数学结构、tradeoff、证据边界和集中vs均分限制 |
| `docx/plans/2026-09-29_task2_joint_planning_control_plan.md` | 下一阶段七步待实施计划及验收定义 |
| `main_lib/fluidckpt/cluster/joint.py` | 有限候选单场景联合MILP |
| `main_lib/fluidckpt/cluster/planning.py` | 固定布局恢复LP、启发式placement、来源层path_rates及可选serial约束 |
| `main_lib/fluidckpt/cluster/control.py`、`wire.py` | compact控制协议、版本/manifest绑定、执行事务 |
| `main_lib/fluidckpt/cluster/catalog.py`、`worker.py` | 副本状态、容量/覆盖、leases/epoch、实际搬运 |
| `main_lib/fluidckpt/executor/pipeline.py`、`profiling.py` | 可选双缓冲与按需HIP/CPU诊断 |
| `codex_coding/src/task2/cluster/run_system.py` | 宿主机协调三节点、SYS_NICE隔离容器、preflight与owned-process回收 |
| `codex_coding/src/task2/cluster/run_placement_comparison.py` | 同预算旧/新布局各一次真实模型比较 |
| `codex_coding/src/task2/cluster/solve_placement_case.py`、`sweep_placement_budgets.py` | 离线策略比较与预算预测 |
| `codex_coding/src/task2/cluster/stop_profile_worker.py` | 先结束worker，再等hipprof提交，最后清理wrapper |
| `codex_coding/src/task2/cluster/analyze_restore_profile.py`、`analyze_placement_comparison.py` | 不重复计等待的加法分解及联合规划验收 |
| `codex_coding/src/tests/test_joint_placement.py` | MILP对穷举、串行路径成本和非法输入验证 |
| `codex_coding/results/task2/cluster/placement01/placement_analysis.json` | 最新成功实证：传输7.8%、完整窗口样本15%及限制 |
| `codex_coding/results/task2/cluster/placement01/placement_solutions.json`、`budget_sweep.json` | 模型内最优/贪心对比和纯预测预算曲线 |
| `codex_coding/results/task2/cluster/profile01/profiling_analysis.json` | 真实模型CPU/阶段诊断，完整GPU trace不可用 |
| `codex_coding/results/task2/cluster/bulk_profile01/profiling_analysis.json` | 定向GPU诊断；原run指纹failed，不是干净benchmark |
| `codex_coding/results/task2/cluster/pipeline01/pipeline_analysis.json` | 流水功能/故障重试与同字节计划比较 |
| `codex_coding/results/task2/cluster/control01/control_analysis.json` | 控制面压缩证据 |
| `codex_coding/results/task2/cluster/system02/analysis.json` | 用户要求的八阶段系统验收 |
| `codex_coding/results/plt/task2_restore_profile01.png`、`task2_placement01.png` | 最新图，SVG同名；图源在`codex_coding/src/plt/` |

下方为旧轮次记录，保留溯源，不能据旧“平台阻塞”条目覆盖当前Qwen已跑通状态。

本索引只记**本轮新产生或本轮改动过**的关键文件。更早的文件请查
`docx/context_index/00_document_registry.md`。

## 2026-09-27 (v0.1.23.1 仓库规范化迁移)

### 核心库（`main_lib/`，会被 import 的产品代码）

- 路径：`main_lib/fluidckpt/serving/keeper.py`
  - 作用：keeper 主力，454 行。持有 pinned DRAM 中的权重副本，接受 `ctl` 指令做
    export/import，是保活链路的服务端。
- 路径：`main_lib/fluidckpt/serving/sglang_hook.py`
  - 作用：import 期 hook，230 行。利用 SGLang `spawn` 的 re-import 语义（I8），
    在不改引擎源码的前提下接管 16 个 worker 的权重加载路径。
- 路径：`main_lib/fluidckpt/serving/ctl.py`
  - 作用：keeper 控制客户端，85 行（`stop()` 只 pkill `fluidckpt.serving.keeper`，
    不碰 SGLang 进程——这是排除「keeper 干扰」假设的依据）。
- 路径：`main_lib/fluidckpt/transport/flowplan.py`
  - 作用：流动规划器，147 行。把「谁给谁多少字节」建模成带容量约束的 max-flow。
- 路径：`main_lib/fluidckpt/transport/bucket.py`
  - 作用：张量打包，130 行。针对 I1（per-tensor 每次 ~99.3 µs 固定开销）把散张量
    合并成 bucket——这是对 SGLang 逐张量远程加载的核心优势所在（27.8×）。
- 路径：`main_lib/fluidckpt/transport/stream.py`
  - 作用：多流并发传输封装，115 行，对应 I3（聚合 205.8 GB/s vs 单流 25.7）。
- 路径：`main_lib/fluidckpt/model/fit.py`
  - 作用：单节点资源模型拟合，214 行。产出 `model_fit_node27_frozen.json`（I12）。
- 路径：`main_lib/fluidckpt/model/resources.py`
  - 作用：资源枚举，114 行，采集 PCIe/NVMe/NUMA 拓扑供模型拟合输入。
- 路径：`main_lib/fluidckpt/profiler/workers.py`
  - 作用：节点探针，376 行，本轮最大的单文件，三节点资源画像的采集端。
- 路径：`main_lib/fluidckpt/planner/plans.py`
  - 作用：计划生成，274 行，把 max-flow 结果落成可执行的 export/import 序列。
- 路径：`main_lib/fluidckpt/executor/run_plans.py`
  - 作用：计划执行器，185 行。

### 脚本（`codex_coding/src/`，跑一次看结果）

- 路径：`codex_coding/src/conftest.py`
  - 作用：**本轮新增**。把 `<repo_root>/main_lib` 插入 `sys.path`，使 `pytest` 能
    `import fluidckpt` 而不依赖 `PYTHONPATH`。迁移后 `tests/test_planner.py` 报
    `ModuleNotFoundError` 就是靠它修好的。
- 路径：`codex_coding/src/serving/glm_e2e.py`
  - 作用：端到端驱动，217 行。`ROOT` 已从硬编码绝对值改为自 `__file__` 上溯 4 层推导。
- 路径：`codex_coding/src/serving/glm_keepalive.py`
  - 作用：保活实验驱动，164 行，产出 `keep2_*` 系列日志。
- 路径：`codex_coding/src/bench/bucket_bw.py`
  - 作用：I1 的测量脚本（per-tensor 99.3 µs vs bucketing 27.8×）。**已知缺陷**：
  外推段有 bug，尚未修。
- 路径：`codex_coding/src/bench/multi_rank_bw.py`
  - 作用：I3 的测量脚本（单流 25.7 vs 8-rank 聚合 205.8 GB/s）。
- 路径：`codex_coding/src/transport/glm_histogram.py`
  - 作用：I6 的测量脚本，产出 118,629 张量的尺寸直方图。
- 路径：`codex_coding/src/transport/ipc_probe.py`
  - 作用：I7 的验证脚本，证明 `_share_cuda_()` / `_new_shared_cuda()` 可零拷贝交接权重。
- 路径：`codex_coding/src/transport/flowplan_smoke.py`（原 `logs/flowtest.py`）
  - 作用：规划器冒烟测试。本轮从 `logs/` 抢救进 `src/`。
- 路径：`codex_coding/src/transport/flowplan_mesh_solve.py`（原 `logs/flowtest2.py`）
  - 作用：三节点 mesh 的 max-flow 求解。本轮从 `logs/` 抢救进 `src/`。
- 路径：`codex_coding/src/serving/mesh_up_timing.py`（原 `logs/mesh_up.py`）
  - 作用：mesh 建立计时。本轮从 `logs/` 抢救进 `src/`。
- 路径：`codex_coding/src/serving/probe_kv_b_{loader,modelconfig,weights}.py`
  - 作用：kv_b 加载路径的三段探针（原 `logs/probe_kvb{,2,3}.py`）。本轮抢救进 `src/`。

### 结果（`codex_coding/results/`）

- 路径：`codex_coding/results/bw/{bucket_27_29_r8.json,multi_rank_27_29_r8.json}`
  - 作用：I1 与 I3 的原始数据，规划器边容量的输入。
- 路径：`codex_coding/results/model/glm53_tensor_sizes.json`
  - 作用：118,629 张量的尺寸表，I6（97.9% 字节集中在 8–16 MiB 的 58,776 个张量）的来源，
    也是 I4（`/dev/shm` 252 GiB < 352 GB）的分母。
- 路径：`codex_coding/results/model/model_fit_node27_frozen.json`
  - 作用：冻结的 V9 单节点资源模型，held-out median 4.4% / max 8.9%（I12）。
- 路径：`codex_coding/results/model/model_fit_node27_v10_rejected.json`
  - 作用：V10 被否决的记录——「共享 PCIe switch 容量」假设拟合出零容量。**保留而非删除**，
    因为被否决的假设本身是信息。
- 路径：`codex_coding/results/logs/core_inventory/core_list.txt`
  - 作用：**本轮新增**。32 个 core dump 的清单（PID / 大小 GB / 时间戳），
    作为删除 203 GB 遗留物的唯一凭据。core 本体已删。
- 路径：`codex_coding/results/logs/base2_{n0,n1}.log`
  - 作用：**决定性证据**。纯 `sglang.launch_server`（无 hook）复现出与带 hook 完全相同的
    `KERNEL VMFault` 崩溃栈，证明崩溃与 FluidCKPT 无关。

### 文档

- 路径：`docx/cites/2026-09-27_fluidckpt_scope_and_positioning.md`
  - 作用：**本轮新增**，`cites/` 首篇。定义问题（模型实例作为可流动可保活对象）、
    收窄 POC 的三条边界、核心抽象（max-flow）、判据表（头条是「全面胜过 SGLang 自身的保活」）。
- 路径：`docx/context_index/{00,01,02,03,04}_*.md`
  - 作用：**本轮全部重写**。五份固定文件的职责见 `next_step.txt`：
    `00` 文档登记、`01` 阶段总结（含 Do Not Repeat 清单）、`02` 活跃线程、
    `03` 当前必需过程文档、`04` 机制洞察账本（I1–I13，canonical）。
- 路径：`docx/articles/2026-09-27_{glm53_startup_vmfault_forensics,multinode_transport_findings,node27_resource_profile,glm53_model_freeze}.md`
  - 作用：**本轮改名迁入**的四篇 findings。
- 路径：`docx/next_step.txt`
  - 作用：**本轮重写**。指向新根 `/home/zhujianian/zhujianian/fluidckpt_zjn`；
    新增建设原则 16（`main_lib` 与 `codex_coding` 分层）、17（跑 GPU 实验前 `ulimit -c 0`）。
- 路径：`docx/00_docx_storage_and_lookup_guide.md`
  - 作用：**本轮第 8 节重写**为 8.1 路径沿革 / 8.2 三层归属 / 8.3 崩溃防护。

### 过程记录

- 路径：`code_building/process_docs/v0.1-init-project/v0.1.23.1-project_restructure_and_spec_migration.md`
  - 作用：**本轮新增**，本轮（也是首次）规范化迁移的完整过程文档。
- 路径：`.spec_backup/*.dllm_wh.bak`
  - 作用：**本轮新增**，8 份。继承自 `dllm_wh` 的账本与 `context_index/` 原文备份，
    仅作来源参考，不再维护。


## 2026-10-04｜论文主张与三组件证据审计

- docx/cites/2026-10-04_fluidckpt_paper_claim_audit.md：实际成熟度、动机、同预算消融、相关工作和FAST/OSDI定位。
- codex_coding/src/diag/probe_ipc_reclaim.py、results/ipc_reclaim/probe02_run_environment：同GLM环境64MiB IPC回收复现；重用句柄保留、每次重新导出归零。


## 2026-10-04｜动机实验与三组件

- docx/plans/2026-10-04_recall_pressure_motivation.md：论证顺序、三个组件、局部DRAM压力的图轴与执行协议。
- recover_owned_glm_transport.py：保留CPU owner重建GPU传输；reclaim01全730GiB接管通过。
- run_recall_pressure_motivation.py、plot_recall_pressure.py：真实权重迁移后的两策略恢复与SSD物理读量绘图。


## 2026-10-04｜完成动机实测与恢复

- docx/experiments/2026-10-04_motivation_and_recall_results.md：三栏数据、原始来源、故障与修复、复现入口。
- codex_coding/results/motivation/figures/motivation.pdf：容量、成本模型、途中召回三栏图；sources.json保存来源。
- full_distributed02/active_session.json：最新可用模型状态入口，指向owner_verify10/session.json。


## 2026-10-04晚间｜Meld Figure4对应实测图

- docx/experiments/2026-10-04_meld_figure4_restore_timeline.md：原图已读、轴映射、计时定义、7.021s rank跨度、53.45%等待及动机。
- results/motivation/fig4_lanes11：完整GLM恢复事件、flow、校准、SHA与restore_lanes.png/pdf/svg。
- run_restore_lane_trace.py / plot_restore_lane_trace.py：可复现实机采集与绘图。

### 2026-10-06｜v0.1.23.3/task2 e1–e3 执行与判断

- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md`：第7–9节逐项报告，第17节证据映射、加法breakdown入口、失败分析链及最小组件建设决定。
- `codex_coding/src/v0.1.23.3/task2/paper_e1_e8/`：register_first_stage.py、audit_existing.py、e3/run_layouts.py、e3/analyze_layouts.py、review_first_stage.py；预登记、原始事件重分析、隔离布局试验与收尾核查。
- `codex_coding/results/v0.1.23.3/task2/paper_e1_e8/`：e1/e2历史分析、e3失败r01和通过r02、protocols；first_stage_review03为最终派生表/证据/40产物hash清单。旧review01/02仅保留分析脚本修复记录，不增加GPU实验数。

### 2026-10-06｜e1–e8 计划v1.2更新

- `docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md`：统一论文/共享基建；IPC基线成熟度与两循环验收；Qwen/GLM分层；e3–e8条件；已有组件代码入口；固定上游源码；同步测量与子agent监督。优先读第4、5、15、16节。
- 配套实验报告仅同步下一动作，未增加实测次数；glm_stop01记录说明旧GLM已停止、下一实验需重新检查资源与有效句柄。

### 2026-10-06｜Qwen e1–e6实测与GLM边界

- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md`第18节：新驱动、全部阶段结果、失败链、账本、源码和命令入口；计划v1.3反映当前状态。
- `codex_coding/results/v0.1.23.3/task2/paper_e1_e8/analysis/through_e6_r02/`：最终分析、原始输入hash、资源采样前缀；r01及更早分析保留为分析器修复历史，不作为最新解释。
- `codex_coding/results/v0.1.23.3/task2/paper_e1_e8/figures/e6_curve_r01/`：真实Qwen0/8/16GiB资源曲线，PNG/PDF/SVG及sources；不包含CPU mock或把alias重复计样本。
- `codex_coding/results/v0.1.23.3/task2/paper_e1_e8/runtime/glm_bootstrap_r02/`与`r03/`：SHA成功但输出参考不一致，r03同engine第二次匹配；full GLM IPC新gate未执行。
- `runtime/cleanup_after_e6_r01/`、`retained_after_e6_r01/`（以上results根下）：自建模型清理、22keeper及原底本保留证据。下轮重新preflight。

### 2026-10-06｜范围收敛统一规划

- `docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md` v1.4第17节：论文主张/证据矩阵、e1–e8未闭合问题、三组件缺口、建议唯一顺序、停止条件、最小完成标准；本次无新实验。

### 2026-10-06｜多节点权重布放与通信理论长期参考

- `docx/articles/2026-10-06_weight_placement_and_topology_literature.md`：9条研究线、术语、保存/通信/引擎三层映射、共享容量/时序/下界条件、多节点真实拓扑与e3–e6解释、基线复用及建设准入。以后改planner/执行器先读7–10节。
- `code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_topology_literature_reference_20261006.md`：本次授权范围、来源核对、子agent审查纠偏、导航变更和文档验证；无GPU实验。

### 2026-10-06｜Baseline与论文差异专题

- `docx/articles/2026-10-06_baselines_delta_and_research_roadmap.md`：暂定baseline集合、Janus固定源码与保活边界、各系统相关工作表述、公平对照协议、三组件复用重点、现有证据和e1–e8建议安排。
- 原文献过程记录追加本次专题形成过程；计划第17节与document registry可直接进入。未改变原实验run或history-chat。

### 2026-10-06｜协作准则与Baseline源码

- `AGENTS.md`：用户要求的代码建设委派规则；主agent负责计划、接口、协调、审查与验收。
- `lib_cite/README.md`、`lib_cite/baselines.lock.json`：四个独立外部源码仓库的来源、冻结版本、用途、依赖状态与恢复方式；源码本身按既有忽略规则管理，适配补丁可纳入父仓库。

### 2026-10-06｜基础场景首轮实现与实测

- `main_lib/fluidckpt/benchmarks/basic_scenarios.py`：S1–S5契约、方法身份、本地host/单active实例选源、预算与证据门槛；复用已有planner。
- `codex_coding/src/v0.1.23.3/task2/basic_scenarios/README.md`：驱动/只读前检/重建/精确释放平台的入口；tests与offline analysis同目录。
- `codex_coding/results/v0.1.23.3/task2/basic_scenarios/analysis_r01/report.md`：12输入run、4新恢复观察、同决策与计时、原14keeper保护核查。
- 同结果根`qwen_bootstrap_r01/session.json`：新4keeper、完整Qwen DRAM与2parked engine，原22mesh旧句柄不可替代本入口；每次先preflight。
- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md`第19节与`code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md`：设计、失败、复用、命令、关键数据、现场与下一步。

### 2026-10-06｜S1/S4/S5与完整baseline表

- `basic_scenarios/complete_table_r01/review_r02/`（版本化results根下）：最终MD/JSON/主表CSV/详细CSV/输入指纹，首版与review_r01仅导出修订记录。
- 同results根`s1_r01`、`s4_r01`、`s5_r01`、`s5_r02`：本轮9个实测观察；`s5_control_review_r01`为只读独立复核，`s5_reverse_protocol_r01/source_before`保存逆序开关修改前源码。
- 同src根`run_layout_scenarios.py`、`complete_table.py`：固定缓存与软件顺序控制、完整表生成；`main_lib/fluidckpt/benchmarks/baseline_scope.py`及`baseline_scope_r01/review_r02`为上游条件和CPU估价适配。
- 正式实验报告第20节与基础场景过程记录第二轮：完整命令、实际字节、顺序证据、适配边界、保护核查和后续重点。
## 2026-10-07｜真实baseline加载路径（v0.1.23.3/task2）

- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md`第21节：vendor native、Blitz单目标分发、真实Store HIP实测；失败、24KiB控制修复、资源与可比性边界。
- `code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md`追加段：函数入口、源码冻结、等待期限/事务生命周期/PyTorch IPC初始化的debug过程。
- `codex_coding/src/v0.1.23.3/task2/baseline_loading/`：native驱动、Blitz/Store适配实验入口和独立汇总脚本；产品适配位于`main_lib/fluidckpt/benchmarks/{blitz_adapter,serverless_adapter}.py`。
- `codex_coding/results/v0.1.23.3/task2/baseline_loading/native_observed_r01/summary.json`、`blitz_s3_r02/summary.json`、`serverless_qwen_full_r03/summary.json`：三条最终通过的原始测量。
- `codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_full_review_r03/final_review/report.md`：Store独立41项验收，含原始事件重算、2018storage SHA、物理回收和源码/身份边界。
- `codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json`：当前有效保留现场，覆盖旧node29目标进程引用；不覆盖原session。
- `lib_cite/baselines.lock.json`及`lib_cite/patches/`：冻结原版来源与独立运行适配身份/补丁记录，原版整系统与适配分支分开。
### 2026-10-07 最终汇总表入口

`codex_coding/results/v0.1.23.3/task2/baseline_loading/summary_r02/revised_export_r01/report.md`为唯一最终表；同目录含summary.json、四类CSV和36输入SHA。保留3个成功新样本与44行历史引用，失败/诊断/setup不冒领性能。初版和中间派生仅供修订溯源。
## 2026-10-07｜拓扑阶段1/2最终入口

- `docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md`18.7与配套records第22节：九配置预登记、控制反例、实际结果和场景边界。
- `codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01/summary.json`：唯一九配置实机、原始rank/计数、NUMA/PID、保护与清理证据。
- `codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_analysis_r01/report.md`和`rates.csv`：精确逐流/共同窗口结果，区分隔离probe与应用执行器。
- `codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_freeze_probe_r08/manifest.json`：唯一最终三场景契约，hypothetical候选、预算/来源/hash/readiness；不能直接执行预览ID。
- `codex_coding/results/v0.1.23.3/task2/topology_scenarios/audit_final_r02/report.md`与`construction_decision.md`：390项独立验收、复用/最小新增/暂缓清单。
- 产品契约：`main_lib/fluidckpt/benchmarks/topology_calibration.py`、`topology_cases.py`；运行入口在version/task2/topology_scenarios的calibration/scenarios/audit子目录。
## 2026-10-07｜双流根因诊断最终入口

- 正式e1–e8计划18.8、实验报告23节和基础场景过程文档最后追加段：G0/G1/G2因果控制、失败补证、边界和公平性。
- `codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_audit_final_r02/report.md`及summary.json：263项独立核验及三组最终因果对照。
- 同结果根`diagnosis_static_r01/report.md`与`diagnosis_static_loc_r01/report.md`：逐rank GDR/affinity、部署库/XML与物理拓扑差异、实际binary的LOC解析证据。
- 同结果根`diagnosis_recollect_split_r01`、`diagnosis_recollect_split_boff_r01`：G1/G2后置补齐日志后的最终摘要，source_run指向未改raw failed；不能增加样本数。
- 同结果根`diagnosis_source_snapshot_g1_r01`：G1当时driver/补采代码与原failed快照指纹，供代码变更后追溯。
- `codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/`：仅本run HCA/B-LOC薄driver、只读补采和独立audit/static入口，不改原calibration或生产XML。
## 2026-10-07｜keeper桥接与冻结layout执行最终入口

- 正式e1–e8计划18.9、实验报告24节、基础场景过程文档最后追加段：协议、计时口径、真实小块与T2功能验收、失败/未定项。
- `codex_coding/results/v0.1.23.3/task2/keeper_bridge/bridge_real_r01/summary.json`：真实1GiB三配置、RPC/worker/counter/lease/清理；T_ready=null。
- 同根`layout_compare_t1_r01`、`layout_compare_t2_r02`、`layout_compare_t3_r01`：同复制机会强simple/jointCPU同解，不算GPU样本。
- 同根`layout_smoke_t2_r01/summary.json`和`layout_delivery_r01/report.md`：6+2GiB复制布局真实ID/epoch、全SHA/输出/顺序/回收，T_ready2.827939181s，限定本组合。
- 同根`audit_final_r01/report.md`、`summary.json`、`next_step.md`：366项独立验收与收口判断。
- `main_lib/fluidckpt/benchmarks/keeper_bridge.py`、`frozen_layout.py`；脚本`codex_coding/src/v0.1.23.3/task2/keeper_bridge/run_bridge.py`与layout/run_frozen_layout.py：新增薄接口，不修改保活worker/solver/旧freeze。

### 2026-10-08｜v0.1.23.3/task2：物理与RCCL拓扑对齐

- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md`第25节：image来源、实际NUMA/PCI、vendor fallback、empty-seed四图、受并行负载干扰的性能观察与停止决定。
- `codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r01/report.md`：原始sysfs/env/ELF/XML及startup事实；`runtime_after.json`为实验后库/XML指纹。
- `codex_coding/src/v0.1.23.3/task2/topology_alignment/probe/README.md`：仅进程级切换拓扑输入，pre-Torch有效环境、四rank图与postexit认证；`empty_seed.xml`为合法空输入，不修改生产XML。
- `codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_runtime_r01/`：当前其他vLLM占卡取证；不能把测后点样本写成完整时序归因。
- 当前基础场景过程文档最后追加段：审查修订、端口准入失败、startup与正式运行、物理free门槛失败及未执行剩余性能组。
- 最终独立审查：`codex_coding/results/v0.1.23.3/task2/topology_alignment/audit_final_r01/report.md`，472项中466通过/6失败，准确图/通道事实通过、性能比较未就绪；不能把overall failed或unset回收失败抹掉。

### 2026-10-08｜资源地图与自有配置（v0.1.23.3/task2）

- `main_lib/fluidckpt/topology/resource_map.py`：物理对象/共享祖先/路径证据查询及严格输入准入；不是solver。`rccl_xml.py`：sysfs证据生成每node保守物理XML与验证。
- `codex_coding/results/v0.1.23.3/task2/resource_map/build_r04/{resource_map.json,report.md}`：唯一最终地图、4GPU诊断请求及当前control/cleanup阻断，旧snapshot非live。
- `codex_coding/results/v0.1.23.3/task2/resource_map/xml_owned_r01/`：新node27/node29 XML，完整8GPU4HCA/节点，runtime未验收；原厂XML未改。
- `codex_coding/results/v0.1.23.3/task2/resource_map/audit_visual_r02/physical_map.svg`：实际NUMA/GPU/NIC/DRAM/HBM地图，fabric内部未知。
- `codex_coding/results/v0.1.23.3/task2/resource_map/audit_final_r01/report.md`：最终地图与状态边界独立验收。`xml_recovery_r02/summary.json`：最新remote两own PID清理未知；先读账本再恢复实验。
- 正式计划18.11/实验报告26节、当前基础场景过程最后追加：完整构建、源码漂移/负例修订、GDR取证137未定、startup/Gloo/控制失败及停止决定。

### 2026-10-08｜资源地图续作与初始化故障边界

- `codex_coding/results/v0.1.23.3/task2/resource_map/resume_map_r02/resource_map.json`：最新失败/状态派生，保留build_r04物理和旧测量，runtime/性能false。
- `resource_map/resume_control_r02/report.md`（results同版本task2前缀）：节点控制/生命周期/精确旧账本新证据，node29清理未证。
- `resource_map/local_xml_diagnosis_r01/report.md`：all-visible HIP超时与0/1库加载SIGSEGV不同边界、core0/子进程退出、sysfs/RPC free差。
- `resource_map/local_xml_source_snapshot_r01/r02`：旧失败显式12源码与修订7源码；`local_xml_cpu_r02/report.md`解释RELEASED误拒修复。
- 正式计划18.12/实验记录27/当前基础场景过程最新段：CPU通过与运行阻塞分别记录，后续禁止按旧PID泛清理或自动再测GPU。
- 本轮最终独立地图审查：`codex_coding/results/v0.1.23.3/task2/resource_map/resume_audit_map_r02/summary.json`（107项）；`resume_map_r02/report.md`（失败状态派生与source）。正式报告27.6链接全证据。

### 2026-10-08｜节点连通与远端依赖对照

- `codex_coding/results/v0.1.23.3/task2/resource_map/connectivity_nodes_r01/report.md`：27/28/29/30两网全表、严格hostkey与网络失败区分。
- `codex_coding/results/v0.1.23.3/task2/resource_map/connectivity_cross_alt_r01/report.md`：28交叉来源确认29异常；11.211.3.29的准确MaxStartups响应及限制。
- `codex_coding/results/v0.1.23.3/task2/resource_map/dependency_check_r01/report.md`：本机库/项目存储、node28模型NFS可读、OCI失败与静态补证，不能把HIP和29断联同因推定。
- 正式计划18.13/报告28/当前过程最新段为进度入口；旧两PID清理与XMLruntime仍未验收。

### 2026-10-08｜SSH准入调查与控制台只读入口

- `codex_coding/results/v0.1.23.3/task2/resource_map/sshd_investigation_r01/report.md`：两次明确拒绝、global/per-source机制边界、待取server证据、控制台命令。
- `codex_coding/results/v0.1.23.3/task2/resource_map/access_review_r02/report.md`：本用户无可复用会话、TCP22过滤0、被动NFS字段限制，非server现场。
- `codex_coding/src/v0.1.23.3/task2/resource_map/sshd_investigation/console_readonly.py`：仅node29现有会话/控制台运行，不新SSH；失败计数null、权限和effective配置缺口明示，5合成CPU通过，尚未server执行。
- 计划18.14/正式报告29/当前过程最后段为最新状态；节点29旧PID仍未确认清理。

### 2026-10-08｜三节点替代范围与fresh运行准入（v0.1.23.3/task2）

- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/inventory/`：参数化资源采集、固定镜像容器setup/guard、磁盘只读取证；node27 vramtbl读取已禁止。
- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/`：fresh init/copy/NCCL、namespace/产品XML复用、exact-owner回收和host launch/collect。
- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/audit/`：独立CPU/preflight/前后guard证据审核。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/`：所有失败/候选/容器/单次init及独立审原件；优先inventory_summary_r03、probe_node30_init_r01、inventory_dockerroot_node28_r01。
- 正式计划18.15、实验报告30、基础场景过程最新段：当前27/28/30状态及旧29两pending；history-chat不是本轮新快照。

### 2026-10-09｜node28自有磁盘/内存归属

- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/disk_attribution/`：独立只读collector、四phase和后续独立容器内存元数据脚本。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/disk_attribution_phase1_r01`至`disk_attribution_phase4_r01`：df/身份、权限失败、daemon层大小、keeper共享内存去重与/data占用原件。
- 正式实验报告31与计划18.16：当前根盘已恢复60GiB，不能以旧storage_blocked阻止再次准入；keeper主要占用DRAM，宿主日志归属仍缺权限。

### 2026-10-09｜两节点自有拓扑IB功能完成

- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py`、`README.md`：当前统一功能入口，预算修正、GPU/NET严格派生dump与真实设备/通道/物理链验收。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_2830_gdr0_r01/`：两机完整自有XML/manifest/每node360结构验证。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_nccl_2830_node28_r03/`及node30对应：最终两端passed、全SHA/actualIBext_v8/HCA/原始及派生dump/清理原件。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/admission28_nccl_deploy_r03/`：父并行执行的准确命令与源码bundle/SHA，30单源部署证据diagnosis30_nccl_runtime_r03_stage_r01。
- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/diagnosis30_final_analysis_r01/`与`diagnosis30_rdma_analysis_r01/`：15s误判、30存储绕行及海光插件复用的依据。正式报告32/基础过程最新段包含所有失败和恢复路线。
- 最终机器可读准入：`codex_coding/results/v0.1.23.3/task2/three_node_recovery/review_runtime_final_r01/qualification.json`与`evidence_refs.json`，绑定选定IB路径的功能资格、全部原件SHA及未测性能/旧pending边界。

### 2026-10-09｜资源地图讲解图（沿用task2，不新增实验）

- `codex_coding/results/v0.1.23.3/task2/three_node_recovery/map_visual_r01/resource_map.svg`及PNG：绿色28/30已验收路径、黄色27待复核、灰色其余结构；总容量与动态预算区分，IB交换网络内部未知。
- 同目录`report.md`与`resource_summary.json`：完整GPU/HCA的BDF/NUMA/CPU归属、快照时刻与验收边界；`evidence_refs.json`与`artifacts.json`记录来源与成果SHA。
- `codex_coding/src/v0.1.23.3/task2/three_node_recovery/map_visual/build_map.py`：子agent生成的可复现说明图脚本；无远端查询/GPU实验，不改变原地图/资格/历史记录。

## 2026-10-09｜v0.1.23.3/task2 最小成本与执行器接入

- `docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md` 第33节：新GPU组合资格、NUMA页归属、64MiB两路径窗口、2GiB预算失败、产品接入及后续实机状态；对应计划18.18。
- `codex_coding/results/v0.1.23.3/task2/minimal_calibration/measure_finite_observations_r01/observations.json`及report.md：真实local64/remote64有限观察、实测源码SHA/时间/内容/预算；不作产品profile。
- 同根`review_candidate_qualification_r01/qualification.json`：28GPU0→30GPU4的新1MiB IB路径资格，区别前轮GPU2→GPU0。
- 同根`review_remote64_r01/summary.json`：source采样DRAM超预算1,974,272B独立复算；内核peak缺失、profile未就绪。
- 同根`integration_delivery_r02/manifest.json`与`review_delivery_r02/summary.json`：四产品变更及薄脚本冻结SHA、14+18 CPU检查、独立23项验收。
- `main_lib/fluidckpt/benchmarks/calibrated_recovery.py`：有限组合查询、布局账本绑定、同controller filled/published指标；真实engine binding单列。
- `codex_coding/src/v0.1.23.3/task2/minimal_calibration/integration/run_session.py`、`supervise_worker.py`：原Worker启动/mesh/pair warm/运行预算/2×16MiB布局/精确停止；旧run不得原名重跑。
- 同根`integration_product_r02/execution.json`：真实原产品两块16MiB同预算布局、同clock分段、每字节SHA、源epoch/账本及完整退役结果。
- 同根`measure_integration_controller_r02/summary.json`：有界startup/fresh guards/mesh+warm/app/STOP原始控制轨迹；actualplan r02和源码e553指纹。
- 同根`measure_integration_resource_review_r02/summary.json`：lifetime采样对fresh preworker baseline、projection误差、前后保护与精确PID退出；fullpeakfalse。
- 同根`review_final_r01/qualification.json`、`report.md`、`evidence_refs.json`：本轮最终独立验收（47条件/33原件SHA），后续首读；功能通过与formal capacity/modelTready/solverdelta/fullpeak未验证分层。

## 2026-10-09｜四布局认知验证

- 正式计划18.19/实验报告34及基础场景过程最后段：固定预言、同预算矩阵、完整计时、收集失败及补收。
- `codex_coding/src/v0.1.23.3/task2/layout_validation/runner.py`：显式四布局，复用原guard/transfer/cleanup，不改产品；build_r02/manifest.json锁定源码及7CPU。
- `codex_coding/results/v0.1.23.3/task2/layout_validation/contract_r01/contract.json`、`review_preflight_r01/predictions.json`：启动前冻结布局/路径/roles/order/弱时间预言。
- 同根`execution_r01/execution.json`：唯一GPU矩阵原件，每布局的phase/workerop/SHA/账本/退役。
- 同根`operations_collect_r02/collection_result.json`：补收原worker/rawdump/全生命周期预算与精确退出；原r01失败和外层status保留。
- 同根`review_final_r01/qualification.json`：独立最终路径、时间预言、资源和退出资格。
- 最终入口补充：同根`review_final_r02/qualification.json`（116项），r01为112项阶段产物保留；后续以r02作为最终验收。

## 2026-10-10｜决策差异准入与成本组件复用

- `docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md`18.20、同日期experiment records报告35：为何本轮负gate停止GPU比较，已有组件/缺口与必要测量边界。
- `codex_coding/src/v0.1.23.3/task2/decision_validation/case/inspect_finite.py`及results同版本`decision_validation/case_cpu_r02/{contract.json,inspection.json,analysis.md}`：6个CPU符号设置、原strong_simple/joint及固定fastest有限subset参考，无实测参数。
- 同结果根`review_scope_r01/dependency_audit.json`：复用旧service scorer核资源bound漏执行依赖的机制反例，不是硬件误差。
- 同结果根`measure_review_r01/measurement_contract.{md,json}`：两单op+独立holdout最低契约，prepared-only、无GPU启动命令。
- 同结果根`measure_refresh_r01/summary.json`、`review_final_r01/qualification.json`：只读现场和独立52项验收，后续先读scope而非当性能通过。

## 2026-10-10｜联合规划理论推导

- `docx/articles/2026-10-10_joint_planning_necessity_and_boundaries.md`：必要性与适用边界正文，3类明确假设反例、交换证明、cut/可达性、Helix映射、已有组件与最小硬件条件。
- `codex_coding/src/v0.1.23.3/task2/theory_validation/proofs/validate_proofs.py`及results同根`theory_validation/proofs_cpu_r01/validation.json`：精确分数与原strong_simple/joint CPU调用，16subset复核；理论参数非硬件profile。
- 同结果根`review_boundaries_r02/checks.json`：独立27项主文8→5版本/后备/relay/共享cut边界，r01变体历史保持。
- 正式e1–e8计划18.21/报告36及当前基础场景过程最后段：本轮执行和范围。
- 理论最终入口`codex_coding/results/v0.1.23.3/task2/theory_validation/review_final_r01/{qualification.json,report.md,link_checks.json}`：27精确+14scope项、主文SHA/原product一致性、7相对文件链接。

## 2026-10-10｜TP4场景合同与可行性

- `docx/plans/2026-10-10_tp4_layered_scenario_contract.md`：可读四层契约、强对照规则、空间账本、CPU结果和部署门槛；正式计划18.22/报告37对应。
- `codex_coding/src/v0.1.23.3/task2/tp4_scenarios/contract/{build_contract.py,schema.json,README.md}`：TP4离线schema/旧planner复用/新瓶颈均衡参考，无产品修改。
- `codex_coding/results/v0.1.23.3/task2/tp4_scenarios/contract_cpu_r03/`：合同、各策略队列与分数/Pareto、256/81/36/36subset、C1/C2影响审计、源码快照。
- 同根`feasibility_r03/deployment_feasibility.{json,md}`：绑定r03的精确角色/每node允许空间/保守runtime投影；物理未准入，fresh metadata在feasibility_r01。
- 同根`review_final_r02/qualification.json`：独立44合同+4依赖+28部署项通过，CPU准备与硬件资格分层，旧审查失败保留。

## 2026-10-10｜多位置竞争与非支配来源

- `docx/plans/2026-10-10_multiple_placement_competition.md`：稀缺池竞争、完整fallback、公平baseline、原negative及1<v<12/7预登记修订、最终结果。父计划18.23/正式报告38对应。
- `codex_coding/src/v0.1.23.3/task2/placement_competition/case/build_active_case.py`：唯一active实例执行脚本，source snapshot与合同先写后求解，原r03不覆盖。
- `codex_coding/results/v0.1.23.3/task2/placement_competition/case_active_preregister_r01/`与`case_active_r01/`：先验推导/witness、8candidate实际取数见证、121布局/公平同16MiB结果。
- 同根`objective_active_r01/{report.md,reevaluation.json,revision_scope.json}`：531项复评与4项唯一两rate修订核对，原LP route与fastest witnesses一致。
- 同根`review_final_r02/{qualification.json,report.md}`：独立95项，研究gate true、joint相对交换增量false、物理GPU/预算/modelready未认证。旧review_final_r01为negative保留。

## 2026-10-10｜三节点多层场景

- `docx/plans/2026-10-10_three_node_multitier_scenario.md`：三node六GPU/四rank、NUMA DRAM/SSD完整底本，五控制配置和下一步物理绑定。
- `codex_coding/src/v0.1.23.3/task2/multitier_scenarios/contract/`：机器合同builder/schema/README；同级review目录为独立审查脚本。
- `codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r02/`：最终104候选合同、249结构检查、14修订不变量、源码快照。r01保留。
- 同结果根`capability_r01/report.md`：已有SSD/DRAM/GPU/规划器可复用能力与接口缺口。
- 同结果根`review_final_r01/qualification.json`和report.md：247项独立验收通过，实际GPU/成本/模型未准入。
- 正式实验记录39及当前basic_scenarios过程末段：角色/预算/变化理由/命令/结论，编号保持v0.1.23.3/task2。

## 2026-10-10｜多层场景物理绑定和执行衔接

- `docx/plans/2026-10-10_three_node_multitier_scenario.md`第11节、总计划18.25/正式记录40：当前物理候选/新预算/真实单卡资格及下一步。
- `codex_coding/results/v0.1.23.3/task2/multitier_scenarios/mapping_binding_r03/machine_binding.json`：28 GPU0/1、30 GPU4/5、27 GPU0/4与实际NUMA、本地SSD；runtime unknown逐项保留。
- 同结果根`mapping_refresh_r01`、`mapping_pci_followup_r01`、`mapping_owner_refresh_r01`：新boot/旧pending身份、纠正class03误判、四卡当前VRAM/可见owner。
- 同结果根`mapping_admission27_plan_r02`、`mapping_admission27_setup_r01`、`mapping_admission27_run_r01`：精确新容器、冻结原60s探针、唯一实机全SHA/进程回收证据。
- `codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/README.md`：原serial API适配及CPU测试命令。结果adapter_cpu_r02、adapter_journal_cpu_r02和adapter_delivery_r02提供测试、故障journal、最终源与exact重构证据。
- 同结果根`execution_review_minimum_r02`、`execution_review_setup_r01`、`execution_review_adapter_r01`：有限预算和最小序列/独立审查。
- 最新综合入口：`codex_coding/results/v0.1.23.3/task2/multitier_scenarios/execution_review_final_r01/qualification.json`，179项独立资格与明确未完成项；`mapping_final_r01/report.md`为物理候选/唯一单卡结果总览。

## 2026-10-10｜六worker三层真实执行

- `codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/`：RuntimeWorker诊断、实际三阶段控制、CPU测试、只读结果parser；原adapter不改。
- `codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations/`：三容器setup/stage/bind、9进程精确监督/统一STOP、原件收集；`review/`独立审查。
- `main_lib/fluidckpt/cluster/worker.py`：synth新增兼容tp_size与分配前参数验证，最终SHA267429...。
- `codex_coding/results/v0.1.23.3/task2/multitier_runtime/operations_actual_r01/`：唯一六GPU实机session r02的startup/mesh/23资格/4case/预算/全SHA/终态/日志/XML原件。
- 同根`review_final_r01/qualification.json`和report.md：独立功能资格及未认证的性能/模型/完整峰值边界。
- 同根`runner_result_r01/report.md`、summary.json、artifact_shas.json：同clock分段时间/空间/SSD IO/103SHA操作逐块对照。
- 同根`operations_result_r01/report.md`：9child精确回收/old保护/53来源SHA/同HIP占用上界与派生XMLparse views。
- 总计划18.26/正式实验记录41、三节点计划12与当前process末段：完整动机、实现、失败、命令、数据表、结论及下一关。
- 最终补充入口为同根`review_final_r02/qualification.json`及report.md：加入13组同HIP占用采样上界、53条操作来源SHA和w5仅localP2P范围；r01原件保留，无新增GPU。

## 2026-10-10｜breakdown与成本模型证据

- `docx/articles/2026-10-10_multitier_breakdown_and_cost_model_alignment.md`：完整时间表、C3依赖与SSD路径、容量LP/serviceDAG/主指标三层、正式预测缺席、单点诊断及最小下一步。
- `codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/evidence_r01/`：原件breakdown/逐op/worker顺序/SSD读量、27引用SHA。
- 同根`model_audit_r01/`：117项模型调用/公式/旧topology参数/方向tier匹配审计，formal accuracy=null。
- 同根`prediction_r03/`：事后同4MiB查表+原DAG、四组execute/filled误差、21误差字段及15inputSHA算术复核；r01/r02失败不覆盖。
- 计划18.27/正式实验记录42及当前process末段：解释过程与范围，当前讨论不能直接沿用“成本模型已准确”的假设。

## 2026-10-10｜查询定位与三路径校准实际证据

- `codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/`：保留实时动态字段的诊断覆盖层、PID内库句柄缓存与模式切换；原产品未改。
- 同src根`execution/`：三路径预登记校准/先冻结后混合请求、六worker会话监督、原失败精确SSD清理脚本。
- `codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_actual_r01/`、`execution_actual_r02/`：首次monitor失败和修正后的唯一完整session原件；都不可覆盖。
- 同results根`inventory_actual_report_r01/`：查询分段、48 RPC、实时字段/缓存来源和329只读检查；`execution_result_r01/`：36MiB配对时间、三path参数、null模型、预算/退出。
- 同results根`review_final_r01/qualification.json`和`report.md`：最终独立结论，查询与功能通过，完整模型未识别、正式预测准确性未认证。
- 同results根`execution_fix_r02/source_previous/`与`execution_ssd_cleanup_r01/`：首轮冻结源码与唯一自有16MiB文件精确清理证据。
- 总计划18.28、正式记录43、breakdown article第8节及当前过程文档最新段：本轮完整动机、协议、失败、实际命令、表格及下一步边界。

## 2026-10-11｜有限域成本模型与独立两配置验证

- `codex_coding/src/v0.1.23.3/task2/bounded_cost/model/`：训练冻结、load/bind/predict、有限域与身份拒绝、CPU验证，产品与原fit未改。
- 同src根`execution/`：复用六worker/九监督进程，fresh绑定、两预测先冻结、两新配置、精确回收；lifecycle/preparation CPU回放覆盖实测脚本失败。
- `codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/`：正式e98b...模型与规则说明；`model_decision_r01/`：真实误差分解、合法布局前提和粗/细差建设判断。
- 同results根`execution_prepared_r04/`及`execution_actual_r03/`：最终launch2047cae...和唯一有效原件；`execution_result_r03/`：实测/预测表、composition诊断、源快照。
- 同results根`review_final_r03/`：96项/48来源通过，预测观察成功但decision_ready=false；`review_final_r01/r02`保留两个GPU准备失败清理资格。
- 同results根`execution_fix_r01/r02/r03`、`execution_preparation_cpu_r01`：快照命名、rank生命周期、live/JSON键修复与旧源保全/真实回放，不覆盖原实验。
- 总计划18.29、正式实验报告44、当前process末段和next_step最新块：动机、实现、全部失败/命令/数据/预算/下一步，正常增量记录。

## 2026-10-11｜本地HBM首操作成本诊断

- `docx/articles/2026-10-11_local_hbm_first_operation_cost.md`：反直觉斜率、首/次host分段、未定范围与模型双计风险，可读首入口。
- `codex_coding/results/v0.1.23.3/task2/local_slope/path_audit_r01/`、`path_slice_installed_r01/`及`path_installed_r01.json`：原路径与实际torch2.10源码header/版本/SHA，不以旧upstream代替实物。
- 同results根`execution_actual_r01/`、`execution_result_r01/`：八请求原native/仪表诊断；`execution_repeat_actual_r01/`、`execution_repeat_result_r01/`：最后两host-only同handler双轮，report/interpretation含准确边界。
- 同results根`review_final_r01/`：121唯一引用核验及结论资格；`review_actual_r02/`142项、`review_repeat_actual_r01/`78项分阶段独审，原失败与superseded资格保留。
- `codex_coding/src/v0.1.23.3/task2/local_slope/execution/`：第一阶段worker/runner及append四repeat脚本，复用原Catalog/HTTP/监督/清理；`path/inspect_installed.py`为不importtorch的只读头文件探针。
- 计划18.30、正式报告45、当前过程末段：全部动机、原件、两stage失败、真实CPU回放、实机数据、计时/字节口径与下一步。

## 2026-10-11｜首操作状态验证计划与Janus组件复用

- `docx/plans/2026-10-11_first_operation_state_validation_plan.md`：待实施唯一A8+B8固定8MiB四cell，线程/目标控制、独立模型准入、停止和预算；不是新实测结果。
- `docx/articles/2026-10-11_janus_retention_reuse_audit.md`：Janus实际保活/后端/空间与线程合同、复用矩阵、最小adapter及论文增量边界。
- `codex_coding/results/v0.1.23.3/task2/first_op_validation/design_r01/`：原设计、17证据来源与一致性检查；无GPU/脚本建设。
- `codex_coding/results/v0.1.23.3/task2/janus_reuse/implementation_r01/`：50行锚/30源码证据、NPU hooks/fatal generic、cache、xTensor、segments/rolling及源码可提取范围；`mapping_r01/`45文件/105锚映射当前Fluid和真实线程链。
- `codex_coding/src/v0.1.23.3/task2/janus_reuse/implementation/audit_refs.py`：只读引用核查入口，results旧脚本为已执行快照。
- 正式计划18.31、实验记录46、basic_scenarios过程末段与next_step最新块：本轮设计/只读事实、修正、命令及下一步，保留未执行状态。

## 2026-10-11｜FluidCKPT主体与论文组件进度

- `docx/articles/2026-10-11_fluidckpt_paper_system_goal_components_and_status.md`：论文目标、六层组件、真实模型历史/新复杂场景资格、核心建设缺口、Janus具体源码吸收候选与顺序。
- `AGENTS.md`：新增Fluid为主体、按缺口提取外部组件源码到本项目的协作规则；原版外部系统整体运行不是组件复用前提。
- Janus专题第1/5节、计划18.31、报告46.6和当前process末段：按用户澄清纠正复用方向，无新实验。

## 2026-10-11｜首操作A执行、状态未识别与B跳过

- `codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/`：F/P线程与prepare匹配干预、实际BUILDING lease、原native传输校验/监督/清理薄层；`analysis/`事前gate/拟合/候选冻结与新只读报表；`review/`独立核验。
- `codex_coding/results/v0.1.23.3/task2/first_op_validation/analysis_frozen_r01/`：A前规则与源码快照；`execution_prepared_A_r02/`和`execution_actual_A_r01/`：唯一实际launch/source/8请求/observer/资源/终态原件。
- 同results根`model_frozen_r01/frozen_prediction.json`：A同集诊断、state unidentified、frozen=false/预测0，名字不代表可用模型；`analysis_result_A_r01/report.md`最终因素与完整时间表。
- 同results根`execution_result_A_r01/`211项实际功能/生命周期/资源、`execution_closure_A_r01/`明确B跳过；`review_actual_A_r01/report.md`和`final_qualification.json`独立raw164/resource59/model4通过，状态未识别如实保留。
- 计划18.32/报告47、首操作专题第5节、系统进度概览及next_step最新块：已执行A与B跳过状态、原规则不回调、下一步边界。

## 2026-10-11｜成本账本与可辨识实验设计

- `docx/plans/2026-10-11_cost_accounting_and_identification_plan.md`：当前优先计划，三层成本、真实计时归属、N/K/C八请求上限、停止规则，硬件尚未跑。
- `codex_coding/results/v0.1.23.3/task2/cost_contract_design/ownership_r01/report.md`：31处源码锚、controller/worker/op/chunk/SSD/live归属。
- 同根`identification_r01/report.md`、`rank_checks.json`：矩阵/顺序/可辨识必要条件；`theory_r01/report.md`：资源界与异步重叠/残差审查。
- `codex_coding/src/v0.1.23.3/task2/cost_contract_design/check_identification_rank.py`：Fraction离线秩及混淆验证，合成示例不是性能。
- 总计划18.33/记录48/当前process增量：任务范围、实际验证与下一步；next_step/context索引优先级已更新。

## 2026-10-12｜成本八请求实验和最终资格

- `docx/experiments/2026-10-12_cost_ledger_calibration_report.md`：本轮首读，八点成本、细分账、预测与实测、控制偏差/停止理由；总计划18.34/总记录49。
- `codex_coding/src/v0.1.23.3/task2/cost_ledger/execution/`、`analysis/`：GPU0原冻结实现；`execution_gpu4/`、`analysis_gpu4/`：唯一备用域、原生计时/传输不变；`review/`：独立CPU/launch/raw/model/资源审计。
- `codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_actual_C1_r01/`：GPU0准入失败0样本；`execution_gpu4_actual_C1_r01/`和`execution_gpu4_actual_C2_r01/`：唯一两个成功会话全部原件。
- 同results根`analysis_gpu4_predeclared_r01/`、`model_gpu4_C1_r01/`、`model_gpu4_C2_r01/`：事前规则与source SHA、五点冻结候选预测、独立留出结果；旧模型不覆盖。
- 同根`analysis_gpu4_final_r01/report.md`：八请求worker分区、三项局部资格与整体未升级、V1库存/准备窗口偏差。
- 同根`review_gpu4_actual_C1_r01/`、`review_gpu4_actual_C2_r02/`及各resources：C1/2最终原始数据/资源/模型独审；`execution_gpu4_closure_r01/`：72项执行闭环、704MiBpayload/1408MiB双端digest逻辑工作、清理与预算采样。

## 2026-10-12｜在线ready成本验证与IPC修复

- `docx/plans/2026-10-12_online_ready_cost_validation_plan.md`：整体10%目标、真实模型/两节点四卡/C0与留出设计；最新资源阻塞说明。
- `docx/experiments/2026-10-12_online_ready_cost_validation_report.md`：实际准入、两次完整工作流失败、暂停显存证据、单映射修复、32MiB诊断拒绝、回收与续跑顺序。
- `codex_coding/src/v0.1.23.3/task2/online_cost/`：执行、CPU资格、模型冻结/验收、独审、小IPC探针；原大模型原件在对应results目录。实际成本校准/留出尚0。
- `main_lib/fluidckpt/cluster/capture.py`：可选全SHA在唯一原IPC映射内执行，默认不启用；新wrapper移除第二次消费凭证，待实机验收。
- `codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_owned_stop_r01/`：本轮两个准确自有容器最终停止证据；外部GLM与原XML保持。

## 2026-10-12 18:52｜IPC与完整物理暂停新增实证

- `docx/experiments/2026-10-12_online_ready_cost_validation_report.md`第7节：覆盖原“未验”状态，小IPC对照与真实70.33GB物理pause已通过，远端成本矩阵仍待资源。
- `codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r02/`与`resume_review_r05/`：小实验24项/完整物理pause31项独审，数据引用及SHA完整。
- `codex_coding/src/v0.1.23.3/task2/online_cost/execution/local_pause.py`、`local_pause_host.py`：独立node27诊断入口，原始成功功能和engine旧guard失败分列；下一轮不需重复探索。
- `codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r06/`、`execution_host_final_closure_cpu_r01/`：未来精确最终closure账本、不加信号，原件不改；`resume_owned_stop_r01/`记录新r03两个容器最终准确停止。

## 2026-10-12｜简化两节点三GPU在线恢复闭环

- `docx/plans/2026-10-12_simple_two_node_online_validation.md`：单sender范围、新helper余量协议、r01→r02事前模型修订和后续恢复复杂域边界。
- `docx/experiments/2026-10-12_simple_two_node_online_report.md`：完整C0/Hnear/Hremote数据、逐场景10%、控制外层误差、资源/回收和执行命令；当前首读。
- `codex_coding/src/v0.1.23.3/task2/online_cost/execution/simple_{controller,contracts,model,host,worker,resources}.py`：复用原生执行器/DAG的三worker适配，实际尺寸成本表和私有预测冻结。
- `codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/`：独审61项/分段账本/停止证据，Hnear精度失败而完整执行成功分列。
- `codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_run_r02/`：两node原始归集tar、精确运行计划、监督器和最终quiet；旧r01拟合失败原件保持。

## 2026-10-12｜单控制器后续恢复成本校准

- `docx/plans/2026-10-12_outer_online_calibration_plan.md`：事前四恢复顺序、仅继承旧路径、首次/后续域、10%门。
- `docx/experiments/2026-10-12_outer_online_calibration_report.md`：最终两留出−.187%/−6.036%、首次成本、同主机分段账、73项验收、命令及退出；当前首读。
- `codex_coding/src/v0.1.23.3/task2/online_cost/execution/outer_{model,controller,bundle,prepare,host}.py`：原参数来源绑定、常驻controller四请求、单次outer、部署与精确清理。原simple默认流程保持。
- `codex_coding/results/v0.1.23.3/task2/online_cost/outer_analysis_actual_r02/`：独审report/qualification/breakdown，完整原件核验和旧/新分段比较；r01审计兼容失败保留。
- `codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_outer_run_r01/`：两节点归集tar和运行/清理/quiet；`outer_owned_stop_r01/`为准确容器停止证据；`outer_ops_source_bundle_r01/`为19件旧路径参数原件。

## 2026-10-23｜授权归档与上下文恢复

- `history-chat.txt`：当前用户模板恢复入口；论文目标、规范与子agent分工、已完成工作、两节点完整模型校准、适用范围、必读顺序与下一步。
- `code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_outer_calibration_recovery_20261023.txt`：本轮固定副本，与当前history一致。
- `code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_pre_archive_history_20261023.txt`：覆盖前467行旧history完整备份，SHA=f96f1d897e2264d3fac16e8f1b0cb16419f87d4a5200b99ff8509f17c902a729；保留GLM历史并行复用线索。
- `code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_basic_scenarios_20261006.md`末尾：10/12实验详细过程、10/23归档/阶段/失败/范围/资源时间边界与命令。
- `docx/next_step.txt`和`docx/context_index/`：最新入口统一，编号遗留.2按用户既定.3纠正。outer报告tar成员路径更正为`phase_sequence/`，原实验数据未改。

## 2026-10-23｜DSV4粒度的详细过程补强

- `code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_online_cost_calibration_detailed_20261023.md`：12节详尽专题，逐函数行号/控制变量/来源冻结/7段账/rank等待/park实际字节/失败原文/资格和完整命令。恢复实现细节首读。
- `code_building/history_chat/v0.1-init-project/v0.1.23.3-task2_outer_calibration_recovery_detailed_20261023.txt`：本次补强后恢复快照，新增专题和计时边界；首版固定快照仍保留。
- `docx/experiments/2026-10-12_outer_online_calibration_report.md`第4节：实际begin之前前置项排除的源码说明，登记误差不变。
