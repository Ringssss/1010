# FluidCKPT Intro 草稿 v1

日期：2026-10-28。开发编号：v0.1.23.3 / task2。
范文对齐：`/home/zhujianian/atc/LYYMOE.pdf`（CoMoE，全文 6 段 + 3 条贡献）、`/home/zhujianian/zhujianian/2609.36800v1.pdf`（AI lowering，5 段 + 3 条贡献）。
本文件只写 Intro，不改代码、不跑实验。

## 写作约定

- **段落骨架**（与两篇范文对齐）：
  1. 领域与代价 —— 为什么这个问题重要
  2. 机会 —— 现成的、更便宜的资源在哪
  3. 但是（实测）—— 今天的系统为什么用不上它，给出测量数字
  4. 第二层原因（实测）—— 不是硬件，是设计假设；再给一个测量
  5. This paper presents X + key insight + 两个机制
  6. 评估结果
  7. 三条贡献（bullet）
- 语言：短句，主动语态，先讲现象再讲机制；不用"我们首次""现有系统均未考虑"这类断言。
- **数字来源**：动机实验（2026-10-04）、成本模型校准（2026-10-12）、记录 51（2026-10-25）。凡未有证据的，用 `【待填：…】` 显式留空，不用估计值充数。

---

## 1 Introduction

Large language models are expensive to keep online. A served model holds all of its weights in GPU memory for as long as the process lives, and those weights are the single largest thing the serving stack owns: a 35B-parameter model in two-way tensor parallelism occupies 70.3 GB, or 32.75 GiB per rank. When traffic falls, the operator has two choices. Keep the model resident and pay for idle GPU memory. Or stop the process and pay for a full reload the next time a request arrives. At the scale of a modern serving cluster, neither choice is cheap, and the cost lands on exactly the moment that is most visible to users: how long they wait before the model can answer.

This is not a storage problem. The cluster already has the capacity we need, just not in the place we usually look. Between GPU memory and the checkpoint on disk sit several tiers that are already paid for: host DRAM attached to each NUMA node, local SSDs, and DRAM on other servers reachable over the network. These tiers are large, cheap, and unused while a model is paused. A paused model's weights are still valuable, and they do not have to live in HBM to stay valuable. If we can hold them somewhere cheaper, release the HBM for other models and for KV cache, and still bring all ranks back to a correct, ready state quickly, we turn a binary decision into a placement decision.

Our measurements show how much that decision is worth, and also that today's systems cannot make it. We pause a real Qwen3.6-35B-A3B model in TP2 and resume it under identical engine, formats, and transfer code, varying only how much of the weight stays near the target. With full local retention, resume to first correct token takes 2.113 s. Keep only half of the weights locally and let the rest fall back to local SSD, and the same resume takes 32.617 s. Keep nothing, and it takes 67.349 s. The difference is not compute and not the model. It is where the bytes were, and how many of them had to come off disk. A cluster-wide copy of the same weights, read over the network from another server, holds this at 1.736 s, 3.007 s, and 3.214 s across the same three points. Retaining locally is not automatically better; the tier and the path decide.

The second reason is less obvious and more damaging. Existing loaders assume that sources add up. If several copies exist, the planner expects to read them in parallel and take the bandwidth of the sum. Our executor does not work that way: on a target rank it completes one copy at a time. We ran the same real recovery four times in an A-B-B-A order, with full DRAM and a full SSD copy both available, changing only whether the planner's cost model knew about serial execution. When it assumed parallelism, resume to first token averaged 2.572 s and it pulled 498.9 MB from SSD. When it matched the executor, resume averaged 1.781 s and it pulled nothing from SSD. A 30.76% improvement came from correcting the model of the machine, with no new hardware, no new transfer code, and no change to the workload. Placement and retrieval are only as good as the cost they are compared against, so the cost has to come first.

This paper presents FluidCKPT, a checkpointing system for paused large models that decides what to keep, where to keep it, and how to fetch it back, under one shared resource budget. Our key insight is that a paused model's weights should stop being an attribute of a process. We give every weight block an identity that outlives the process that materialized it, and we treat recovery as a dependency graph over block copies rather than a loop over files. Once weights are independent objects, three things that used to be fixed become choices: which blocks survive a pause, which tier each copy sits in, and in what order copies run at each target rank. FluidCKPT makes all three from one measured cost model. It measures a per-path, per-size service point for every source tier instead of assuming a bandwidth, and it charges the same model with the execution order the executor will actually follow. A joint planner then chooses retention, placement, and retrieval together, and an IPC takeover hands the ready weights to a new engine without reloading them. We keep strong simple planners, including per-block fastest and bottleneck balancing with local exchange, as first-class comparisons, because a joint solver is only interesting if it beats them.

We evaluate FluidCKPT on real multi-node GPU servers with Qwen and GLM models in TP2 and TP16. A full pause and resume of Qwen TP2 moves 70.3 GB, passes a full byte-wise SHA check, produces the reference output, and completes engine takeover through IPC without re-running weight loading. An independent mid-migration recall on three nodes moves 50.1 GB of GLM weights while Qwen resumes underneath it, and GLM later returns with all 784.2 GB verified. On the cost side, a held-out check of the service-point model stays within 10% on both a remote and a near path (−0.187% and −6.036%). Across the full decision space, predicted resume time ranges from 0.528 s with the whole rank resident to 4.700 s when every block must come from the base copy, an 8.9× spread that the placement and retrieval plan controls. Whether a joint planner captures more of that spread than a strong simple rule is the question we take up directly: 【待填：同预算 joint vs 最强简单基线的真实恢复时延差】.

Our contributions are as follows:

- We identify why paused large models cannot be recovered cheaply today: weights are bound to the life of the process, intermediate tiers exist but are not addressable, and loaders plan against a cost model that ignores execution order. We quantify each with real measurements: 2.1 s to 67.3 s depending on tier and locality, and 30.76% from execution-aware planning alone.
- We introduce FluidCKPT, which gives weight blocks process-independent identity, retains them across HBM, NUMA DRAM, local SSD, and remote DRAM under a shared budget, prices every source with a measured service-point model, and jointly plans retention, placement, and retrieval before executing it and taking over the engine by IPC.
- We implement FluidCKPT and evaluate it on real multi-node GPU servers, showing complete verified recovery of real models, a cost model within 10% on held-out paths, and an open, measured account of when joint planning does and does not beat strong simple rules.

---

## 2 说明：这份草稿里哪些地方还没落地

| 段落 | 已有什么证据 | 缺什么 |
| --- | --- | --- |
| 第 1 段 | Qwen TP2 每 rank 32.75 GiB、全模型 70.33 GB 均为实机校验值 | — |
| 第 2 段 | HBM / NUMA DRAM / 本地 SSD / 远端 DRAM 四层均已实机可用 | — |
| 第 3 段 | 2.113 / 32.617 / 67.349 s 与 1.736 / 3.007 / 3.214 s 来自 2026-10-04 动机实验，单次观测 | 顺序控制未做（e3 未闭合），论文里要写"单次观测" |
| 第 4 段 | 2.572 → 1.781 s、SSD 用量 498.9 MB → 0，A-B-B-A 四次配对 | 样本量小，需在正式稿标明 |
| 第 5 段 | 机制全部已建 | "joint planner 是核心贡献"这个定位目前**只有框架、没有正收益**，是最大缺口 |
| 第 6 段 | 恢复、SHA、参考输出、IPC 接管、成本模型 −0.187% / −6.036%、0.528 vs 4.700 s 均有记录 | **joint vs 强简单策略的真实时延差为零**（记录 51：六策略同布局、极差 0，压到 8 GiB/card 仍同解，唯一分歧 5.67 ms）。第一个 `【待填】` 就是这个数 |
| 第 7 段第 2 条 | 能力已建 | 若一直测不出正收益，这条贡献要改写为"给出该权衡的可复算边界与何时退化为简单规则"，不要硬写成"优于简单策略" |

**一句话**：第 1–4 段现在就能写，因为有真实测量；第 5–7 段的能力描述也能写，但"联合决策带来收益"这句必须先有数，否则第 7 段第 2 条是空的。
