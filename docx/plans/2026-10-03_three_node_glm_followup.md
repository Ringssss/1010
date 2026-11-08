# 三节点与 GLM-5.3：完成状态核查及后续配置

开发编号：v0.1.23.2。此次是只读核查，没有新增 GLM 推理成功记录，也没有重跑已经完成的 Qwen 生命周期实验。

## 1. 当前目标完成到哪里

| 目标 | 实测状态 |
| --- | --- |
| A 让位给 B，再恢复 A | final03 已完成，两个真实模型，生成输出一致 |
| A 的字节迁到远端 DRAM 和 SSD | 已完成，70,329,718,112 bytes 全覆盖并全量 SHA256 校验 |
| A 在远端及原节点恢复 | 已完成 |
| 空闲 A 不占本地保活 HBM | 已完成，本地 A 保活 DRAM 也归零；引擎/KV 仍占显存 |
| MILP 放置、资源 LP 恢复 | 已运行；尚未证明相对独立缓存/贪心策略的增量收益 |
| 严格统一预算、含 page cache 和迁移峰值 | 未完成 |
| 迁移中立即召回、并发恢复、长轨迹尾延迟 | 未完成 |
| 三节点生命周期 | 当前成功记录用 node27/28/29/30，三节点配置待适配验证 |
| GLM-5.3 端到端 | 未完成；有历史原生 SGLang VMFault 证据 |

最终数据引用 [final03 报告](../articles/2026-10-03_lifecycle_results.md)。A→B、B→A 和远端 A 的 3.75×、2.29×、3.11×来自显存逐出后的原生磁盘回载对照。两边复用初始化后的引擎。独立 resident HIP IPC 命中为 0.82775s，仍更快。

这不是当前厂商镜像原生 Weight Cache Daemon 的测量；用户接受的 HIP IPC 适配测量保留为该机制的本地命中基线。后续还需等预算的 CPU backup、独立多层缓存和简单迁移基线。

迁移准备、传输、全量校验与发布共 58.999s，在 B 服务期间执行；表中 A 恢复是在迁移完成后发起。该成本不能从整个生命周期消失。初始化亦仍为分钟级。

## 2. 三节点实地核查

| 节点 | 当前设备状态 | 后续角色 |
| --- | --- | --- |
| node27 | 8 张 DCU 空闲，约 64GiB/卡 | GLM TP16 的 rank 0–7；本地服务及部分 DRAM 保活 |
| node29 | 8 张 DCU 空闲，约 64GiB/卡 | GLM TP16 的 rank 8–15；部分 DRAM 保活 |
| node28 | GPU0/1 各占约 52.4GiB，GPU2–7 空闲 | DRAM 保活、本地 NVMe extent 服务；小模型对照可用空闲卡 |

候选布局不依赖 node28 已占用的 GPU0/1。GLM 的恢复目标是一对计算节点。

三台主机各约 503.63GiB DRAM。`/home/zhujianian` 仅在 node27 为本地盘；node28/29 的该路径是 NFS。`/data/node28/shared` 在 node28 为本地 NVMe，在其他节点为 NFS。因此三节点版本应把 SSD 服务运行在 node28，并用其 `/data/node28/shared/zhujianian` 下的独立测试目录。node28 根分区已接近满载，不能沿用 `/tmp` 作为 SSD 数据位置。

当前 `run_lifecycle.py` 固定 TP2、两卡实例和 node30 SSD。需要把 SSD 节点/路径、跨主机 TP rank、worker 映射和容量需求参数化；不能用换模型路径来冒充 GLM 适配完成。

## 3. GLM 的证据与限制

模型：`/data/node28/shared/zhujianian/GLM-5.3`，141 个 safetensors 文件、118,629 个索引张量、755,617,140,416 tensor bytes（703.7233GiB）。架构 `GlmMoeDsaForCausalLM`，78 层、FP8 e4m3、block[128,128]。这些是原始 checkpoint 容量，处理后 capture 的大小仍需实测。

历史纯 SGLang 日志 `codex_coding/results/logs/base2_n0.log` 第 392 行已有 VMFault，后续栈落在 `nsa_indexer.py` 的 `_get_q_k_bf16`。历史定位关联 `lightop_attention.fuse_layernorm_rotary_embedding`，本轮没有通过新实验确认它是唯一根因。

当前节点镜像中的 SGLang 是 `0.5.12+das185.dtk2604.torch2100.2608121934.gcbf025`，NSA 源码 SHA256 为 `f803e875c84c6bf55656123981134f5538bd0474bf8f97bbb2b3bc03532d0bd8`；仍有该调用。此证据意味着尚无修复验证，不等于本轮重新复现了故障。

## 4. 后续执行顺序

1. GLM 平台验证：先确认原生 SGLang 在 node27+29 TP16 能返回正确 token。保存启动命令、版本、错误和生成输出，GPU 进程禁用 core dump；将内核修复也用于基线，避免仅给 FluidCKPT 优化平台。
2. 三节点权重管理：在 node27/28/29 DRAM 与 node28 本地 SSD 分布后备副本，扩展当前 TP2 驱动到 TP16，验证 FP8 scale、NSA 派生属性、storage alias 和 rank 映射。
3. 分块迁移与中途召回：一块目标发布后即可释放相应源，召回可读取已发布目标和仍有效源。单主机装不下 GLM 后备副本，迁移中制造两份完整 DRAM 副本也会逼近三节点总容量；这是联合容量与传输管理需要解决的实际约束。
4. 同预算端到端对照：相同模型/请求轨迹/引擎预热；分别测 resident IPC 命中、逐出回载、CPU backup/独立缓存、FluidCKPT。保留同位置、同预算的放置/路由消融，统计让位、首正确 token、迁移峰值及对 B 的影响。

在 GLM 原生推理恢复前，可以独立验证其真实 checkpoint 字节的容量、分布与 I/O 吞吐，但该结果必须标为存储传输实验，不能称 GLM 端到端恢复或使用 raw safetensors 填入处理后模型骨架。

## 本轮命令及输出

```bash
python3 codex_coding/src/diag/audit_three_node_glm.py --out codex_coding/results/three_node_glm_audit_20261003
```

脚本只读三个节点，保存型号配置、文件容量、设备/内存/文件系统状态、镜像版本、代码哈希及历史日志摘要。全部查询成功；`native_glm_retested=false`。

证据：`codex_coding/results/three_node_glm_audit_20261003/evidence.json`。
