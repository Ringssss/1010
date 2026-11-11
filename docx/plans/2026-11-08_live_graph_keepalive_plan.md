# 实时资源图保活（live graph keep-alive）建设计划

日期：2026-11-08。开发编号沿用 `v0.1.23.3 / task2`（新编号需用户确认）。
代码：`main_lib/fluidckpt/live/`；脚本与测试：`codex_coding/src/v0.1.23.3/task2/live_graph/`；结果：`codex_coding/results/v0.1.23.3/task2/live_graph/`。

## 1. 动机：从 SGLang 的单机单模型保活到全局实时资源图

**参照系统**：SGLang（LMSYS）的 `release_memory_occupation` / `resume_memory_occupation` 配合 `torch_memory_saver`，再加上 `--enable-weights-cpu-backup`（`lib_cite/sglang/python/sglang/srt/managers/scheduler_components/weight_updater.py:321`、`model_executor/model_runner_components/load_model_utils.py:300`）。模型 A 让位时，把权重拷到本进程的 CPU 内存并释放显存；恢复时再原样拷回。

**它的局限**（也就是本计划要补上的部分）：
1. 一个引擎只能保活自己的一份权重，没有多模型共享的保活池。
2. 只能用本机 HBM 和本进程 DRAM 两层：不用 SSD，不用其他 NUMA 节点，也不用另外 3 个节点的 HBM/DRAM/SSD。
3. 不感知别的租户：其他人随时可能占用 HBM/DRAM/SSD 和 PCIe/IB 带宽，而 SGLang 按固定路径整块拷贝。

**本项目实测背景**：本机 node27 直连 node28/29/30，每个节点有 8×64GiB HCU、约 503GiB DRAM（8 个 NUMA 节点）、NVMe，以及 4×400Gb/s NDR IB。2026-11-08 采样时 node27 的 GPU0–3 已被别人占用约 43.5GiB/卡。这说明"有人随时在用"是常态，不是特例。

**与已有结论的关系**：10-25 的 decision_value 实验表明，在**静态、无共享竞争**的登记场景下，联合规划相对逐块最快没有收益：目标函数可分离，贪心即为最优。当时的结论是：要体现价值，必须先制造耦合（共享出口、多 NIC 竞争）。本计划中，**实时背景流量和容量变化本身就是天然的耦合来源**：最快路径会被别人占用，单一来源会被打满，于是多源并行（最大流）和动态重规划才有意义。这一点是本计划要用证据回答的核心问题，不预设它一定成立。

## 2. 两个组件

### 组件一：实时动态资源图（`live/graph.py`、`live/probe.py`、`live/agent.py`）

- **顶点**：四个节点上的每块 HBM（每 GPU 一个）、每个 NUMA 节点的 DRAM、每块 NVMe SSD。
- **边**：H2D/D2H（PCIe）、GPU P2P、NVMe 读写、跨节点 IB（经 NIC）。每条边列出它消耗的共享资源（GPU PCIe 入口/出口、NUMA 内存读写、NVMe 设备、NIC 收发），共享同一资源的边互相竞争。
- **两层结构**：拓扑（慢变，缓存候选路径）与状态（快变，剩余容量和背景负载），数据契约见 `live/types.py`。
- **实测可用的采集源**（2026-11-08 已在 node27 验证）：
  - HBM 已用/总量：`/opt/hyhal/lib/librocm_smi64.so` 的 `rsmi_dev_memory_usage_get`，8 卡一次 **0.5ms**（`hy-smi` CLI 一次 8.3s，不可用于实时）。
  - GPU PCIe 流量：`rsmi_dev_pci_throughput_get`，每次阻塞约 1s，放到后台线程以 1Hz 采样。
  - IB 收发：`/sys/class/infiniband/shca_*/ports/1/counters/port_{xmit,rcv}_data`（单位 4 字节），取差分。
  - NVMe：`/proc/diskstats` 差分（读写字节、io_ticks 利用率），`statvfs` 取剩余空间。
  - DRAM：`/sys/devices/system/node/node*/meminfo`，以 MemFree 为基础，可回收页缓存单独列出、保守使用。
  - 亲和性：`/sys/class/drm/card*/device/numa_node`、`/sys/class/infiniband/*/device/numa_node`。
- **背景负载 = 观测值 − 本系统自身已上报的传输量**。否则会把自己的流量当成别人的。
- **跨节点**：每个节点一个只读采集代理（HTTP/JSON，快指标 10Hz、PCIe 1Hz），node27 汇总成全局图。样本过期即标记 stale，规划时保守降额。
- **名义带宽**：沿用已有标定（`transport/flowplan.py` 的 DEFAULT_CAPS、`codex_coding/results/bw/`、`profile/`），每个数值记录来源；未标定的值标注为 `assumed`，不冒充实测。

### 组件二：用户无感知的分块存储（`live/chunks.py`、`live/formats.py`）

- 用户给的是原始 safetensors（单文件或带 index 的多分片）或 GGUF。系统内部把文件切成小块（默认 64MiB，可配置 4–256MiB），每块是**原文件的一段字节范围**，以 sha256 作为内容身份。
- 切块尽量对齐张量边界：小张量打包进同一块，超过块大小的张量才拆分；文件头单独成块。原文件的每个字节恰好被覆盖一次。
- 清单（manifest）记录 文件→块→张量 的映射。块可以零散地存放在图中任意顶点。
- 读取接口与位置无关：`read(file, off, n)`、`tensor(name)`、`materialize_file()`（字节级还原出原格式文件）。块存在哪里由 locator 决定，用户看到的仍然是原格式。

### 规划（`live/flow.py`、`live/placement.py`）

- **取回（restore）**：输入是块副本位置、目标 GPU 和实时快照，按同源集合/同目标对块分类后做最大并发流 LP，取整到每块的路径与顺序。要求在约 1100 块（35B 模型、64MiB 块）规模下 <50ms，并提供贪心兜底。执行中按快照变化阈值重规划，各路径做 work stealing。
- **放置（park）**：模型下放时，在各顶点"剩余容量 − 安全余量"的约束下，选择块的副本位置，使预期恢复时间最小。分散到多源以叠加带宽，但不超过目标 GPU 的 PCIe 入口上限。
- **让位（evict）**：别的租户占用增长、某个顶点余量不足时，把该顶点上的块迁移到其他顶点或降级存放；始终保留至少一份持久副本（原文件或 SSD）。

## 3. 本轮范围（Phase 1，CPU-only）

| 子任务 | 写入范围 | 验收 |
| --- | --- | --- |
| A 图与采集 | `live/{graph,probe,agent}.py`，`live_graph/tests/test_graph*.py` | node27 实采一次全图；node28/29/30 通过 ssh 只读采集（stdin 传脚本，不在远端落盘、不常驻）；拓扑构建、快照、背景扣减、stale 降额都有单测；记录采集延迟 |
| B 分块存储 | `live/{chunks,formats}.py`，`live_graph/tests/test_chunks*.py` | Qwen3-0.6B（`/data-aisoft/mechdancer/models/Qwen3-0.6B`，只读）切块后字节级还原 sha256 一致；任取张量与 safetensors 原读一致；自造 GGUF 同样通过；manifest 存入结果目录 |
| C 规划与模拟 | `live/{flow,placement}.py`，`live_graph/sim/` | LP 与贪心正确性；规划延迟实测；在动态背景负载下比较三种策略：SGLang 式本机 DRAM 单备份、静态放置+静态计划、实时图+最大流+重规划 |

**不在本轮**：真实 GPU 数据搬运（后续复用 `serving/keeper.py` 的 pair-group broadcast / H2D 执行器）和启动 GPU 实验，均需用户另行授权。远端采集只读、短时，用完即退出，不影响其他租户。

## 4. 预先登记的判断标准

- 模拟结论只作为"是否值得上 GPU"的门槛，不当作论文结果。
- 如果在合理的背景负载下，实时图+最大流相对静态策略的恢复时间改善不足 15%，就如实记为负结果，并分析原因（例如目标 GPU 的 PCIe 入口才是唯一瓶颈）。
