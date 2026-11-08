# docx 文档地图与存放查找指南

## 1. 目的

这份文件用于告诉后续 AI：

- `docx/` 各个子目录分别承担什么职责；
- 新文档应该放到哪里；
- 遇到一个问题时，应该先去哪里找；
- 哪些内容不应该放进 `docx/`。

这是一份稳定入口文档，优先级高于零散归档。

讨论下一步建设、增加特性或调整实验范围时，先参考[论文目标驱动的范围管理与最小建设指南](01_paper_scope_management_guide.md)，再结合当前计划和实验证据决定投入。

讨论多节点权重布放、通信拓扑或流模型时，先查[文献与理论长期参考](articles/2026-10-06_weight_placement_and_topology_literature.md)，核对已有方法与适用条件，再决定新增什么。

确定论文对照、相关工作表述与后续实验取舍时，参照[Baseline、论文差异与建设路线](articles/2026-10-06_baselines_delta_and_research_roadmap.md)。该文为暂定专题方案；实际预登记和结果仍使用原e1–e8计划与报告。

## 2. 总体分层

`docx/` 当前应理解为一个分层文档库，而不是单一笔记目录。

| 位置 | 主要作用 | 应存放的内容 | 不应存放的内容 |
| --- | --- | --- | --- |
| `docx/` 根目录 | 当前工作入口、恢复材料、稳定指南 | `next_step.txt`、恢复说明、全局指南 | 大量专题分析、测试脚本、运行结果 |
| `docx/cites/` | 项目立项依据、论文 framing、理论基础 | 架构定位、理论抽象、POC 范围界定 | 运行日志、阶段进展、临时 brainstorm |
| `docx/articles/` | 专题技术分析与阶段性技术归档 | 针对某个主题的深入分析、brainstorm、技术归档 | 过程流水账、仅面向恢复的索引 |
| `docx/context_index/` | 恢复导航与文档索引 | 文档注册表、阶段总结、活跃线程、最小必读集 | 长篇技术正文、实验脚本 |
| `docx/plans/` | 正式计划稿和可执行设计稿预留区 | 未来的阶段计划、设计计划、执行计划 | 技术随想、原始测试输出 |
| `docx/experiments/` | 与实验计划对应的正式实验报告和记录规范 | 实验预登记、逐次关键数据表、分析、失败记录和原始证据链接 | 原始大日志、二进制产物、替代 results 的数据副本 |

## 3. 各目录的具体用法

### 3.1 `docx/` 根目录

这里放的是“先读文件”和“跨会话恢复文件”。

当前已知代表文件：

- `next_step.txt`：当前阶段代号、开发规范、归档约束、目录要求。
- `2026-03-27_context_compression_recovery_note.md`：上下文压缩或重启后的恢复说明。
- `00_docx_storage_and_lookup_guide.md`：本文件，用于指导文档存放和查找。

适合放在根目录的文件类型：

- 当前阶段总入口；
- 全局约束说明；
- 会话恢复说明；
- 整个 `docx/` 的稳定导航文档。

不适合放在根目录的内容：

- 面向单一技术主题的长篇分析；
- 原始 benchmark 输出；
- 过程归档和测试记录。

### 3.2 `docx/cites/`

这里放“为什么做这件事”的上层依据。

当前文件表明，这一层主要用于：

- 论文问题定义；
- 项目范围边界；
- 理论抽象；
- POC 的构建原则。

适合放入 `cites/` 的文件：

- 新的 paper architecture note；
- 新的理论框架说明；
- 新的 POC scope / build plan；
- 来自论文、系统设计、方法论层面的总结。

如果一个文件主要回答的是下面这些问题，优先放在 `cites/`：

- 我们的项目到底想解决什么问题？
- 论文应该如何定位？
- 为什么当前范围应该收敛到这个 POC？
- 理论层面的核心抽象是什么？

### 3.3 `docx/articles/`

这里放“已经展开过的专题技术分析”。

当前文件表明，这一层主要用于：

- 针对某个方向的技术归档；
- 代码和运行现象结合后的技术分析；
- 阶段性 brainstorm；
- 未来可以继续深化为设计稿或实验计划的技术笔记。

适合放入 `articles/` 的文件：

- 某个模型结构的分析；
- 某个缓存机制的专题讨论；
- 某个编译器 / runtime 技术的启发式总结；
- 某个研究方向的阶段性技术归档。

建议命名方式：

- `YYYY-MM-DD_topic_name.md`

如果一个文件主要回答的是下面这些问题，优先放在 `articles/`：

- 这个技术方向目前我们已经想清楚了什么？
- 代码和运行现象说明了什么？
- 某个优化点值得如何继续推进？

### 3.4 `docx/context_index/`

这里放“恢复导航层”，不是技术正文层。

当前四个文件已经形成了固定分工：

- `00_document_registry.md`：重要文档总表，说明每个关键文件的角色、主题、优先级和关键结论。
- `01_stage_summary.md`：当前阶段总结，包含已完成工作、已验证事实、未验证假设和建议下一步。
- `02_active_threads.md`：当前活跃线程，说明每条线的状态、缺口和下一步建议。
- `03_current_required_process_docs.md`：最小必读过程文档集合，用于下一次恢复时快速聚焦。

适合放入 `context_index/` 的文件：

- 新的索引；
- 新的阶段快照；
- 新的恢复阅读顺序；
- 面向“快速接手现场”的轻量导航文档。

不适合放入 `context_index/` 的文件：

- 大段技术原理推导；
- 运行实验正文；
- 详细过程日志。

### 3.5 `docx/plans/`

该目录已包含task2第一阶段、第二阶段历史设计，以及2026-09-29联合规划控制层待实施计划。阅读时核对日期和状态，不把设计稿当作已完成结果。

后续如果开始写更正式的计划文档，建议优先放这里，例如：

- 里程碑计划；
- 研究执行计划；
- 设计落地计划；
- 实验计划与评估计划。

只有当一份文档已经从 brainstorm 进入“准备执行”的状态时，才建议从 `articles/` 升级到 `plans/`。

## 4. 遇到问题时该怎么查

### 2026-10-04 新增：论文缺口实验入口

- 计划：[v0.1.23.3/task2 e1–e8](plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)。
- 正式报告与逐次记录规范：[配套实验总记录](experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)。
- 每份计划与配套报告双向链接；2026-10-06 已完成 e1/e2 历史重分析及 e3 执行层先导，当前完整模型验收边界详见报告；e4–e8 待执行。
- 完整原始数据仍在 `codex_coding/results/<开发编号>/task2/`，实验报告必须写关键数据、单位、实际命令和精确证据路径，不能只有结论。
- 本系列遵循本会话 `.3` 与权重可用时延口径；其他 `.2` 生命周期记录和首 token 口径保留，不互相覆盖。

### 4.1 想知道当前开发规范

先读：

- `docx/next_step.txt`

### 4.2 想知道当前有哪些重要文档

先读：

- `docx/context_index/00_document_registry.md`

### 4.3 想知道项目现在进行到哪里

先读：

- `docx/context_index/01_stage_summary.md`

### 4.4 想知道当前最热的研究线程是什么

先读：

- `docx/context_index/02_active_threads.md`

### 4.5 想知道最少需要补哪些过程文档

先读：

- `docx/context_index/03_current_required_process_docs.md`

### 4.6 想找某个技术主题的深入分析

按主题去：

- `docx/articles/`

### 4.7 想找论文 framing、理论依据、POC 范围定义

按主题去：

- `docx/cites/`

### 4.8 想在上下文压缩或模型重启后快速恢复

先读：

- `history-chat.txt`（当前完整恢复块）
- 然后回到 `context_index/`

## 5. 新文档应如何落位

可以用下面的判断规则：

| 新文件类型 | 建议位置 |
| --- | --- |
| 当前阶段入口、全局说明、恢复说明 | `docx/` 根目录 |
| 论文定位、理论框架、POC 边界 | `docx/cites/` |
| 技术专题分析、brainstorm、机制归档 | `docx/articles/` |
| 恢复导航、阶段摘要、文档注册表 | `docx/context_index/` |
| 正式计划稿、执行计划、实验计划 | `docx/plans/` |
| 与实验计划对应的正式报告、关键数据表及运行记录规范 | `docx/experiments/` |

## 6. 哪些内容不要放进 `docx/`

下面这些内容应该放到别处：

| 内容类型 | 正确位置 |
| --- | --- |
| 过程文档、建设记录、归档流水 | `code_building/process_docs/` |
| 进度增量总结 | `code_building/progress_diff_summary.md` |
| 关键文件索引 | `code_building/key_files_index.md` |
| 关键结论 | `code_building/key_conclusion.md` |
| 测试脚本 | `codex_coding/src/` |
| 测试结果、benchmark 输出、运行产物 | `codex_coding/results/` |

`docx/` 存“重要文档”和“面向理解的文档”，不存执行产物。

## 7. 命名建议

建议统一采用以下风格：

- 稳定入口文件：`00_*.md`
- 技术归档文件：`YYYY-MM-DD_topic_name.md`
- 恢复索引文件：`00_*`、`01_*`、`02_*`、`03_*`
- 计划文件：`YYYY-MM-DD_plan_topic.md` 或阶段代号风格文件名

## 8. 当前项目的一个重要注意点

### 8.1 路径历史

本仓库的文档骨架来自 `dllm_wh` 项目（`/home/wuhang/wuhang/dllm_wh`），
`main_lib/` 之前的目录名也沿用了那套约定。**本仓库在 2026-09-27（v0.1.23.1）
完成了整体迁移**，现在的实际根目录是：

- `/home/zhujianian/zhujianian/fluidckpt_zjn`

如果在本仓库任何文档中看到 `/home/wuhang/...` 或 `/home/zhujianian/fluidckpt`（旧路径），
那都是迁移前的历史记录，**不能直接当可执行路径使用**。执行前先核实。

### 8.2 本项目的三层归属（务必分清）

| 层 | 目录 | 放什么 | 判据 |
| --- | --- | --- | --- |
| 核心库 | `main_lib/` | 会被 `import` 的产品代码 | 推理引擎、核心部件、库本体 |
| 执行层 | `codex_coding/src` + `codex_coding/results` | 跑一次看结果的脚本与产物 | 为了验证/测量/诊断 |
| 文档层 | `docx/` + `code_building/` | 理解性文档 + 过程记录 | 为什么做 / 做了什么 |

一句话：**会被 import 的进 `main_lib/`，跑一次看结果的进 `codex_coding/src/`。**

### 8.3 崩溃防护（本项目特有）

本机 `kernel.core_pattern=core`、`core_uses_pid=1`，内核对每个崩溃的 GPU 进程
dump 整个显存地址空间。本项目是 8 卡/节点、单卡 18–22 GB，**一次崩溃 = 8 个 core 文件 ≈ 180 GB**，
会瞬间写满 `/home`。2026-09-27 已实际发生过一次（203 GB）。

因此：**跑任何可能让 GPU 进程崩溃的实验前，必须在 launcher 里加 `ulimit -c 0`。**
如果已经生成，`codex_coding/results/logs/core_inventory/` 下有清单归档的流程可参考。

## 9. 后续维护规则

每次新增一个重要文档时，至少同步下面几处：

- 如果它属于 `docx/` 中的重要长期文档，更新 `docx/context_index/00_document_registry.md`
- 更新 `code_building/key_files_index.md`
- 更新 `code_building/progress_diff_summary.md`

每次形成一个新的关键结论时，还需要同步：

- `code_building/key_conclusion.md`

每次形成一次有意义的建设动作时，还需要补：

- `code_building/process_docs/` 下的过程文档

并在过程文档中保留：

- `本轮命令`

## 10. 推荐阅读顺序

新 AI 进入现场时，先读`history-chat.txt`，随后推荐按以下顺序阅读：

1. `docx/next_step.txt`
2. `docx/00_docx_storage_and_lookup_guide.md`
3. `docx/context_index/00_document_registry.md`
4. `docx/context_index/01_stage_summary.md`
5. `docx/context_index/02_active_threads.md`
6. `docx/context_index/03_current_required_process_docs.md`
7. 按需进入 `docx/cites/` 或 `docx/articles/`

这样可以先建立规则感，再建立现场感，最后进入专题内容。
