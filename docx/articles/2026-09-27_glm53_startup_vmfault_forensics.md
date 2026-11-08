# SGLang 冷启动的 122 秒里，权重只占 14 秒

日期 2026-09-27。机器 node27，模型 `Qwen3.6-35B-A3B`（67 G，26 shard），
容器 `zhujianian-fluidckpt` 镜像 `sglang:0.5.12-...-sf_b075`，`--tp-size 2`。

## 1. 时间线（端到端 122.12 s）

| 阶段 | 时刻 | 增量 | 占比 |
|---|---|---|---|
| 进程启动 → import → 参数解析 | 0 → ~29 s | 29.2 | 24% |
| 模型结构构建 + CUDA context（→ `Init torch distributed`） | 29 → 59.27 s | 30.3 | 25% |
| 加载前准备（→ `Load weight begin`） | 59.27 → 100.50 s | 41.2 | **34%** |
| **权重读取（26 shard，67 GB）** | 100.50 → 114.74 s | **14.24** | **12%** |
| KV cache + memory pool | 114.74 → 116.02 s | 1.3 | 1% |
| tokenizer/warmup → `Uvicorn running` | 116.02 → 122.12 s | 6.1 | 5% |

**权重读取 14.24 s ≈ 4.7 GB/s 聚合**（比我早先按单盘 2.5 GB/s 估的 27 s 快一倍，
SGLang 的多线程 shard 加载确实有效）。

## 2. 这个数字推翻了原本的假设

原来以为：cold start 慢主要是因为从盘上读几百 GB，所以"保活权重"能大幅提速。

实际：**122 s 里 107.9 s 与权重无关。** 保活把权重读取压到 0，上限也只能到 ~108 s，
**改善 12%**。对 Qwen3.8（336 G，131 shard）权重读取按同速率约 71 s，占比会升到 ~37%，
保活上限约 37% —— 仍然不是"几分钟变几秒"那个量级。

## 3. 那 108 秒是什么

`python -X importtime` 实测 `import sglang.srt.entrypoints.http_server`：

| 模块 | 累计 |
|---|---|
| `sglang.srt.entrypoints.http_server`（整条 import 链） | **29.17 s** |
| └ `sgl_kernel`（**单个模块，self time**） | **14.47 s** |
| `sglang.srt.utils.hf_transformers_patches` | 19.50 s 累计 |
| `sglang.srt.entrypoints.engine` | 4.69 s |
| `torch` | 1.81 s |
| `torchvision.models.convnext`（推理根本不用） | 1.03 s |

`Init torch distributed` 在 59–61 s，TP=1 和 TP=2 都是这个数 → **这 30 秒不是分布式初始化**，
是 CUDA/HIP context + 模型结构 + 各种 runtime 探测。

## 4. 结论：该攻的不是权重搬运，是脚手架

保住权重能让 122 s → ~108 s。要让"拉起"真的从分钟级掉到秒级，收益全在那 108 秒里：

1. **进程常驻**：把 import + context 建好一次，之后重新起引擎复用（省 59 s+）。
2. **`sgl_kernel` 那 14.5 s**：看是不是可以做一次预热缓存。加载器/引擎根本不用的
   `torchvision.convnext` 这类纯浪费（~1 s+）。
3. **`Load weight begin` 之前那 41 s**：这一段最长且最不透明，要单独打点拆开。

**FluidCKPT 的正确定位因此调整**：权重保活的价值不在于"省 14 s"，
而在于它和"进程常驻"结合后能省掉整条 `disk → decode → TP shard → quantize` 路径，
同时让多节点之间不必走 NFS（47 分钟级）。这两件事是叠加的，不是二选一。
