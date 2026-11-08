# fluidckpt_zjn 备份范围

本仓库是 FLUIDCKPT 项目的完整开发仓库快照，根目录
`/home/zhujianian/zhujianian/fluidckpt_zjn`。它同时保存**核心库**（会被 import 的
产品代码）与**过程记录**（脚本、结果、理解文档、过程文档）。

## 纳入范围

| 目录 | 内容 |
| --- | --- |
| `main_lib/fluidckpt/` | **核心库**，FluidCKPT 本体，6 子包 21 个 `.py`（2,768 行）。keeper、SGLang hook、max-flow 规划器、bucket 打包、资源模型等。 |
| `codex_coding/src/` | 一次性实验脚本，按 `bench/ transport/ diag/ serving/ tests/` 分类。 |
| `codex_coding/results/` | 脚本产物：带宽、mesh、诊断、模型尺寸、计划、资源画像、精选日志。 |
| `docx/` | 理解层：`articles/`（findings）、`cites/`（立项依据）、`context_index/`（五份固定索引）、`next_step.txt`、`00_docx_storage_and_lookup_guide.md`。 |
| `code_building/` | 过程层：三本账、`process_docs/`、`history_chat/`。 |
| `history-chat.txt` | 恢复交接文件（根目录），内容与 `code_building/history_chat/` 下最新一份一致。 |
| `scripts/` | 仓库级辅助脚本。 |
| `data/` | 小体积项目数据。 |
| `AGENTS.md` | 用户确认的主/子agent分工、baseline位置与当前任务约束。 |
| `lib_cite/README.md`、`lib_cite/baselines.lock.json`、`lib_cite/patches/` | baseline来源、冻结版本及后续适配补丁；外部源码检出本身继续按现有忽略规则管理。 |

`lib_cite/` 中本轮取得的四个外部源码库为独立Git仓库，不自动作为父仓库源码或submodule纳入。新环境依照锁定版本恢复源码；后续适配若产生改动，必须另存可追踪补丁或明确分支来源，不能只依赖被忽略目录中的工作树。保留既有dInfer例外规则。

## 与 `dllm_wh` 模板的关键差异

本仓库继承自 `dllm_wh` 的规范模板，但**备份范围有两处刻意的不同**：

1. **`main_lib/` 是纳入项，不是排除项**。`dllm_wh` 把 `main_lib/` 当作独立嵌套仓库
   排除在外；FLUIDCKPT 的核心库规模小（2,768 行）且与实验强耦合，
   开发中经常需要库与脚本同步改动，因此**故意纳入本仓库**
   （建设原则 16：核心库进 `main_lib/`，一次性脚本进 `codex_coding/src/`）。

2. **`codex_coding/results/` 是纳入项**。带宽、资源画像、模型尺寸这些 JSON
   是规划器与资源模型的输入，也是 I1–I13 洞察的原始证据，
   丢失后无法复现结论，因此纳入。体积大、可重新生成的原始日志不在此列。

## 排除范围

- `.spec_backup/` —— `dllm_wh` 继承内容的来源备份（8 份 `.dllm_wh.bak`），仅作追溯用。
- `__pycache__/`、`*.pyc` —— 字节码缓存。
- `core.*` —— GPU 进程崩溃时的内存快照，单文件 17–22 GB。**已由建设原则 17 禁止产生**
  （跑 GPU 实验前必须 `ulimit -c 0`）；若出现应立即删除并检查 `ulimit`。
- 大体积生成物：`*.pt`、`*.nsys-rep`、`*.sqlite`、`*.gz`。
- 虚拟环境与 agent 会话状态。

## 遗留说明

- 更早一批日志（40+ 份）与 GLM-5.3 崩溃时的 core dump 清单之外的原始文件
  留在 `/home/zhujianian/fluidckpt/logs/`，未纳入本仓库；
  core dump 清单本身已归档在 `codex_coding/results/logs/core_inventory/core_list.txt`。
- 精确来源与清单以服务器侧审计与 SHA-256 manifest 为准。
