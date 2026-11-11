# FluidCKPT vs 单机保活 / D2D fork —— 本集群实测对照

生成日期：2026-11-11
机器：Hygon DCU（ROCm/HIP 6.3, DTK 26.04, RCCL 2.22.3），node27–node30，容器 `zjn-fluid-live-r01-{27..30}`
网络：IB NDR 400 Gb/s/端口，跨节点广播实测 45.0–45.4 GB/s（`pair_bcast.py`，接收端计时）

## 一句话结论

FluidCKPT 的**跨节点多源权重取回**在本集群上跑通并逐字节校验通过；
两个 baseline 的保活能力在本机**测不出数字**：SGLang 的 `torch_memory_saver` 未安装导致 release/resume 是 no-op，
Janus 的 D2D fork 在非 NPU 构建中根本不编译。表中因此不是"我们更快 N 倍"，而是"同一格对面为空"。

## 主表

| # | 系统 / 路径 | 载荷 | 条件 | 时延 | 速率 | 校验 | 来源 |
|---|---|---|---|---|---|---|---|
| 1 | **FluidCKPT 冷启动**（每进程新 pin + 读盘 + 落地） | 1177.7 MiB | 跨节点 node30→node27，256 MiB 块 | 0.59–1.13 s | 1.1–2.1 GB/s | sha256 全匹配 | `chunk_fluid` `first_s` |
| 2 | **FluidCKPT 稳态**（pinned 池热、源已驻留） | 1177.7 MiB | 同上，进程内第 2/3 次 | **均值 0.094 s / 末次 0.061 s** | **20.3 GB/s（末次）** | 同上（同池同字节） | run 53 `steady_s` |
| 3 | FluidCKPT 稳态 + 目标 DRAM/源 SSD 突发 | 1177.7 MiB | 60 GiB DRAM + 6 GiB SSD 写入 | 0.053 s | 23.2 GB/s | — | run 54 |
| 4 | FluidCKPT 稳态 + 源 DRAM 120 GiB + 邻卡 HBM 突发 | 1177.7 MiB | 见左 | 末次 0.053 s / 均值 0.082 s | 23.5 GB/s | — | run 55 |
| 5 | FluidCKPT 写盘路径（`--persist`） | 1177.7 MiB | 含 pageable D2H + 落盘 | 1.96–2.29 s | 0.54–0.63 GB/s | sha256 全匹配 | run 59 |
| 6 | SGLang 单机保活 `release` | 8B (15.36 GiB 权重) | `--no-memory-saver` | 0.2913 s | **释放 0 GiB** | n/a | `nosaver_013810.log` |
| 7 | SGLang 单机保活 `resume` | 同上 | 同上 | 0.0018 s | **Noop `pass`** | n/a | 同上 |
| 8 | SGLang 冷启动到 ready | 8B | 单卡，含权重加载 | **72.36 s** | 15.36 GiB 权重 | n/a | `engine_started` |
| 9 | SGLang `enable_memory_saver=True` | 8B | — | 6.01 s 后**启动失败** | — | — | `run_013358.log` |
| 10 | Janus D2D fork（引用，未实测） | 8B TP=2 | Ascend 集群 | 408 ms | — | — | `Janus/README.md:193` |
| 11 | Janus D2D fork（引用，未实测） | 32B TP=2 | Ascend 集群 | 783 ms | — | — | `Janus/README.md:194` |
| 12 | Janus 构建 | — | 本机 HIP | **不可构建** | — | — | `worker_impl.cpp:433`, `setup.py:117` |

## 必须与数字一起出现的限定

1. **第 1 行与第 2 行不能只报后者。** 冷启动多出的 0.54–1.08 s 中，0.5–0.64 s 是 `torch.empty(1177 MiB, pin_memory=True)` 的首次注册成本（实测：256 MiB 0.07 s / 512 MiB 0.09 s / 1177 MiB 0.61 s），其余是读盘。稳态之所以是 53 ms，是因为这两项都不再发生。
2. **第 6/7 行不构成"对方更快"。** release 前后空闲 HBM 40.998→40.930 GiB（−68 MiB 噪声），resume 1.8 ms 是 `_TorchMemorySaverAdapterNoop.pause/resume` 的 `pass`（`sglang/srt/utils/torch_memory_saver_adapter.py:86-113`）。它没有迁移任何字节。
3. **第 10/11 行是论文声明，不是本机测量。** 且仓库正文（`README.md:39`）写 "388–783 ms"，与其自身表格的 408 ms 矛盾；表格与 commit 历史一致，取 408/783。
4. **第 2 行的 23.5 GB/s 与第 10 行的 408 ms 不是同一个操作。** 前者是跨节点、多源、目标单卡；后者是同机卡间 D2D、TP=2。按字节与链路归一前不可直接相除。
5. **校验只有我们有。** FluidCKPT 对每个块与整个文件做 sha256；Janus 的 D2D 与本机 SGLang 路径均无等价校验。

## 冷/热分界线的物理来源（本机实测）

| 阶段 | 耗时 | 说明 |
|---|---|---|
| pinned 池首次注册 1177 MiB | 0.50–0.64 s | 进程级一次性；同进程第二次为 0.000 s |
| 读 1177.7 MiB 块文件 | 0.25 s（4.56 GB/s） | 与广播流水后只暴露首块 |
| H2D 预置 | 27 ms（43 GB/s） | `staged` |
| 256 MiB ×4 + 153.7 MiB ×1 广播 | 5.9 ms/块（≈43 GB/s） | 接收端 `probe_sync` |
| 稳态总时延 | **53–61 ms** | ≈20–23 GB/s 端到端 |

## 复现

```bash
# 冷启动
CHUNK_MIB=256 PERSIST=1 python3 fetch_serial.py --work <W> --dst node27 --dst-card 0 --sources node30=0
# 稳态（同进程 3 次，源已驻留）
CHUNK_MIB=256 SOURCE_POOL=1 REPEAT=3 python3 fetch_serial.py --work <W> --dst node27 --dst-card 0 --sources node30=0
# 字节校验
python3 chunk_fluid.py verify --store /root/chunkstore --manifest-dir <W>/meta2 --out <W>/verified
# SGLang baseline
HIP_VISIBLE_DEVICES=4 python3 baseline_sglang.py --model /data/node28/shared/models/9g_8b_thinking_llama --no-memory-saver
```

## 未确定项（不得推断）

- `torch_memory_saver` 上游是否支持 HIP/ROCm（本机未安装，无可检视源码）。
- Janus 内核移植到 HIP 的可行性（未尝试构建）。
- Janus 在任何硬件上的运行时行为（从未运行）。
- 4–7 号卡上跨节点 collective 不工作的根本原因（现象确定：`HIP_VISIBLE_DEVICES=4` 或 `4,5,6,7` 时首个真实 collective 不推进；0–3 号正常）。
