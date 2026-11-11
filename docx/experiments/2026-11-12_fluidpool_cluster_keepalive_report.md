# FluidPool:四节点全介质权重保活 —— 实现与真机对比

日期:2026-11-12。分支 `live-graph`。代码 `main_lib/fluidckpt/live/{rdma/,noded.py,pool.py}`,
驱动 `codex_coding/src/v0.1.23.3/task2/live_graph/pool/`,
原始记录 `codex_coding/results/v0.1.23.3/task2/live_graph/pool_r01/`。

本报告取代 `gpu_r01/COMPARISON.md`:那份的主表是单源、1.18 GiB、无落盘日志,且 baseline 两格为空。

## 1. 一个抽象,两个组件

权重是一组内容寻址的 64 MiB 块;node27–30 的 HBM / DRAM(按 NUMA)/ NVMe 是一张带实时剩余容量的图,
PCIe / IB / NVMe 是边。下放、拉起、让位都是图上的流。

- **组件一 实时资源图**:每节点 agent 10 Hz 采样(HBM、各 NUMA 内存、NVMe、IB 计数器),协调器汇总成图。
- **组件二 无感知分块**:safetensors 原样切块,块按各顶点当下的空闲见缝插针地放,拉起时从所有持有者并行取回,
  落到 HBM 后按 safetensors 偏移直接成为张量。

上一版的问题是两者没接上(GPU 路径不读图)。这一版的闭环是:
图快照 → `flow.plan_fetch` 选源 → 目标节点守护进程拉取 → 落 HBM → 从 HBM 逐块 sha256。

## 2. 实现

| 部件 | 文件 | 作用 |
|---|---|---|
| RDMA 块传输 | `live/rdma/rchunk.c` + `__init__.py` | librdmacm,RDMA READ。源端注册内存后不再参与:无源端 CPU、无源端 GPU、无 collective |
| 节点守护进程 | `live/noded.py` | 持有本节点三层介质的块;执行落到本节点 GPU 的恢复 |
| 协调器 | `live/pool.py` | 图 + 块目录;`park` / `restore` / `react` |
| 租户模拟 | `pool/tenantd.py` | 真实占用 DRAM/HBM,真实 NVMe / IB / PCIe 流量 |
| 轨迹驱动 | `pool/maas_run.py` | clusterdata 请求序列 + 三套系统 |

恢复执行器是拉取式的:每条路径(对端节点 × 网口、本机内存块、本机 NVMe 读线程、对端暂存)一个 worker,
先取计划给它的块,再从剩余时间最长的路径窃取;源可以在恢复中途被撤回(`revoke`),未取的块改走别的源。

为什么换掉 RCCL 广播:它要求源端占一块 GPU、4–7 号卡不工作、三方并发不可靠(原 `fetch_serial.py` 实际是逐对串行)。

## 3. 本集群的实测常数(`pool_r01/rdma_bench`、`pin_probe`、`smoke/land_probe_*`)

| 项 | 实测 |
|---|---|
| IPoIB TCP,node28→node27 | 0.46 GB/s,加流不涨 |
| 主机内存 RDMA READ,1 / 2 / 4 口 | 24–27 / 35 / 70 GB/s,逐 MiB 校验 |
| H2D / D2H(锁页) | 47–48 / 47 GB/s |
| H2D(可分页内存) | 13.5 GB/s |
| GPU 锁页内存分配 | 3 GB/s;已锁页内存再做 RDMA 注册 4 GiB 用 5 ms |
| 本机 RDMA 回环 | 12–13 GB/s(所以本机 DRAM 走直接 H2D) |
| 首次建 GPU 上下文 | 约 14 s / 卡 |
| HIP IPC 零拷贝挂载 15.25 GiB | 中位 1.9 ms(首次 6.2 ms),首尾块 sha256 一致 |

**PCIe 同根干扰**(本轮最重要的拓扑发现):HCA 口与 GPU 在同一 NUMA 节点时,该口收数据会压低该卡的 H2D。

| 目标卡(NUMA) | 四个口全用 | 避开同根口 |
|---|---|---|
| 0 号(与 0 号口同在 NUMA 0) | 31.7 GB/s | 41.9 GB/s |
| 4 号(与 2 号口同在 NUMA 4) | 20.5 GB/s | 40.1 GB/s |
| 1、2 号(不与任何口同根) | 43–45 GB/s | 44–46 GB/s |

单独测 H2D 为 47 GB/s;同根口在收时 4 号卡 H2D 掉到 9.4 GB/s。协调器据拓扑里卡与口的 NUMA 归属排除同根口
(`pool.restore(avoid_shared_root=True)`)。**这条规则目前在协调器里,没有作为共享资源写进 `graph.py` 的资源模型。**

## 4. 轨迹驱动对比(`pool_r01/maas_r01/`)

**负载**:`clusterdata/cluster-trace-v2026-GenAI/lora_request_trace.csv`(26,823 条请求,87 个基础模型)取请求最多的 9 个模型,
按热度映射到 9 组真实权重(1.4–67 GiB,共 225 GiB)。node28 两张服务卡(1、2 号)承载活跃模型;
请求到非活跃模型即一次恢复,LRU 活跃模型让位。租户 DRAM 占用的时间形状取自 `pod_memory_util_anon.csv` 中波动最大的容器。
每套系统 8 次预热 + 7 个阶段 × 16 次 = 120 次恢复,其间另有 335 次命中活跃模型(不计)。

**权重来源**:Qwen3-0.6B、9g-8b、Qwen3.6-35B 是完整模型;其余 6 组是 GLM-5.3 的连续分片组(10–40 GiB),
用来充当该体量的其他模型,不是完整模型。

**三套系统(同一执行器、同一持久文件、同一让位策略)**

- `fluidpool`:块放在四个节点的 DRAM(每 NUMA 顶点上限 8 GiB,合计 256 GiB)+ node30 NVMe 一份 + 对端实例的 HBM。
- `janus`:按 Janus 源码三条路径的最小实现。LIGHT_SLEEP = 整模型留在本机锁页内存(64 GiB,LRU);
  D2D fork = 从一个活跃对端实例单源拉取(最热的两个模型在 node30 有活跃实例);DEEP_SLEEP = 重读。
- `sglang`:release/resume。每个引擎(卡槽)只保留它上一次运行的那个模型,整模型放本机锁页内存;其余冷启动。

两个 baseline 是等价实现,不是原系统:Janus 在本机 HIP 上无后端,SGLang 缺 `torch_memory_saver`。
D2D fork 在这里走 D2H → RDMA → H2D,15–20 GiB 用 0.55–0.70 s;Janus 论文报 8B 408 ms、32B 783 ms。

**结果**(时延 = 从发起恢复到全部权重字节在目标 HBM)

| 阶段 | fluidpool 均值 / p95 (s) | janus | sglang | 对 janus | 对 sglang |
|---|---|---|---|---|---|
| calm | 0.46 / 1.43 | 1.11 / 7.12 | 1.51 / 7.12 | 2.4× | 3.3× |
| dram_home(node28 上租户占 DRAM) | 0.65 / 2.30 | 1.49 / 10.94 | 2.09 / 10.94 | 2.3× | 3.2× |
| ssd_home(node28 NVMe 读写流量) | 0.99 / 2.27 | 6.08 / 17.35 | 6.96 / 16.82 | 6.2× | 7.1× |
| ib_home(node28 两个口有 RDMA 流量) | 0.89 / 2.34 | 5.13 / 18.84 | 4.00 / 10.95 | 5.8× | 4.5× |
| dram_remote(node30 上租户占 DRAM) | 0.59 / 2.36 | 1.90 / 11.04 | 2.57 / 10.98 | 3.2× | 4.3× |
| peer_gone(对端实例下线) | 0.51 / 2.26 | 1.71 / 10.98 | 2.23 / 10.94 | 3.3× | 4.3× |
| all(叠加) | 0.69 / 2.29 | 6.00 / 23.50 | 5.46 / 23.84 | 8.7× | 7.9× |
| **合计(112 次)** | **0.68 / 2.26** | 3.35 / 16.78 | 3.54 / 14.23 | **4.9×** | **5.2×** |

| 模型 | GiB | fluidpool 次数 / 均值 / 最差 (s) | janus | sglang |
|---|---|---|---|---|
| qwen3-0.6b | 1.4 | 17 / 0.05 / 0.05 | 17 / 0.16 / 0.53 | 17 / 0.20 / 0.55 |
| glm-a | 10.0 | 15 / 0.29 / 0.38 | 15 / 1.28 / 2.87 | 15 / 1.63 / 3.47 |
| 9g-8b | 15.3 | 32 / 0.47 / 0.53 | 32 / 0.88 / 5.79 | 32 / 1.83 / 5.95 |
| glm-b | 20.0 | 14 / 0.58 / 0.67 | 14 / 1.07 / 6.88 | 14 / 3.00 / 6.89 |
| glm-f | 25.0 | 15 / 0.95 / 1.00 | 15 / 4.31 / 9.11 | 15 / 4.20 / 9.47 |
| glm-e | 40.0 | 7 / 1.43 / 1.55 | 7 / 11.02 / 14.51 | 7 / 7.88 / 14.23 |
| qwen36-35b | 67.0 | 10 / 2.28 / 2.36 | 10 / 16.20 / 23.50 | 10 / 14.45 / 23.84 |

恢复方式:janus 冷启动 47、D2D 20、H2D 唤醒 45;sglang 冷启动 74、resume 38;fluidpool 112 次均为多源混合。
fluidpool 字节来源:远端 DRAM 48.7%、本机 DRAM 37.9%、远端 NVMe 8.2%、本机 NVMe 5.1%。
每套抽检 14 次从 HBM 逐块 sha256,坏块 0。fluidpool 规划耗时中位 47 ms,最大 224 ms(已计入时延)。

### 这个倍数的限定

1. **优势主要来自容量,不是同等资源下的速度。** fluidpool 用四个节点 256 GiB DRAM 加一份 NVMe;baseline 用 node28 的 64 GiB。
   单节点只能装下目录的 28%,大模型一被请求就是冷启动。
2. **SSD 流量放大差距。** node28 NVMe 有读写流量时,67 GiB 冷启动从约 11 s 变成 23.5 s;fluidpool 不依赖该盘。
3. **DRAM 突发没有被这组实验充分检验。** 出于安全,租户每 NUMA 节点最多取 27 GiB(node28 实际取到 39–81 GiB,node30 52–108 GiB)。
   baseline 的本机备份一次都没被挤掉(`evict_whole` 0 次),fluidpool 只被驱逐 21 次共 21 GiB。
   `dram_home` / `dram_remote` 两行的差距来自那一段请求了哪些模型。
4. 各阶段请求的模型不同,阶段之间不可横比;只能同一行内三套系统互比。
5. fluidpool 各模型的最差值与均值几乎重合,说明这些突发基本没影响到它;但这同时意味着"实时反应"在这里没有被逼出来。

## 5. 受控突发(`pool_r01/burst_r01/burst_bench.log`)

**35B(67 GiB)落到 node28 的 1、2 号卡**

| 来源 | 时延 | 速率 | 校验 |
|---|---|---|---|
| 本机 NVMe 冷读(O_DIRECT,8 读线程) | 10.94 s | 6.6 GB/s | 1140 块,坏块 0 |
| 本机锁页 DRAM(单节点保活的最好情况) | 0.85 s | 84.7 GB/s | — |
| 三个远端节点的 DRAM,本机一份都没有 | 2.20 s(中位) | 32.7 GB/s | 1140 块,坏块 0 |
| 只用一个远端节点(node27 / 29 / 30) | 2.12 / 2.14 / 2.12 s | 33.6–33.9 GB/s | — |

- 远端 DRAM 比冷读快 5.0 倍,但**比本机锁页 DRAM 慢 2.6 倍**。经主机内存中转的管线在目标节点上限约 33 GB/s,
  双卡没有带来翻倍(探针里 1 / 2 / 4 卡是 28 / 37 / 44 GB/s)。单卡时两者接近(42–46 对 47)。
- **三个源并不比一个源快。** 瓶颈在目标端,DRAM 来源下"多源最大流"没有带来速度收益;它的价值在可用性和慢源(NVMe)。

**恢复中途源被收回**:恢复开始 0.25 s 后令 node30 交回整份副本。恢复 2.22 s 完成(与无干扰相同),
失败取块 0,1140 块坏块 0;撤源等待 26 ms。但 node30 真正释放这 67 GiB 用了 15.8 s(约 4 GB/s),大块内存归还是慢的。

**IB 流量突发**(租户往 node28 的 2 个 / 3 个口灌 RDMA)

| 条件 | 静态计划、不窃取 | 实时计划 + 窃取 |
|---|---|---|
| 2 个口被占 | 2.21 s | 2.20 s |
| 3 个口被占 | 2.24 s | 2.20 s |
| 无租户流量 | — | 2.22 s |

**没有差别。** 这个突发既没有拖慢恢复,实时计划也没有显出优势。负结果,如实记录。

**DRAM 突发**(glm-c 30 GiB,2 副本分在 node27/29/30;租户在 node30 三个 NUMA 节点各取 36 GiB)

- 恢复时延:突发前 1.10 s,突发进行中 1.10 s(校验坏块 0),租户占着时 1.09 s,补副本后 1.09 s。
- 驱逐 8 次,共交回 6.9 GiB,单次 0.06–0.51 s;首次驱逐发生在租户开始后 7.5 s(租户爬坡慢,共 75 s,那时才越过阈值)。
- 覆盖率始终 1.0(另一副本在别的节点);6.9 GiB 在 1.47 s 内补到其他顶点。

**图本身的开销**:68 顶点 / 2336 边 / 240 资源;四节点一轮拉取中位 3.1 ms、p99 8.5 ms;快照 0.24 ms;压力扫描 0.025 ms。

## 6. 恢复后直接起服务(`burst_r01/servable_demo.log`)

Qwen3-0.6B 只放在 node27/29/30 的 DRAM。恢复到 node28 的 HBM 用 0.08 s;另一个进程经 HIP IPC 句柄零拷贝映射并
建好 311 个张量视图用 7 ms(无一需要拷贝);绑进 transformers 骨架后生成的 token 与原始加载器完全相同,logit 最大差 0.0。
从请求到模型可调用共 5.05 s,其中权重只占 0.09 s,其余是导入 transformers 和构建骨架;首次生成另需 4.78 s。
也就是说权重已不是瓶颈,引擎初始化是。

## 7. 事故与修正

- **OOM**:第二次试跑时,租户模拟器以严格 NUMA 绑定加锁页一次性申请 32 GiB,node28 触发内核 OOM,
  杀掉了本项目的 noded 与 tenantd。`docker ps` 显示其他容器均在运行;内核日志无读权限,未能完全确认无他人受影响。
  修正:逐 GiB 申请,每步先读该 NUMA 节点可用量并留 16 GiB,不锁页;两个进程设 `oom_score_adj` 为 900 / 1000。
- 试跑暴露并修掉的系统缺陷:并发建内存块导致 11 GiB 的块占 60 GiB 内存;驱逐与采样同线程导致全图过期;
  缓冲读把空闲内存变成页缓存被误判为压力;窃取估计按类速率而非单 worker 速率。

## 8. 还没做到的

1. 同等内存预算下的对比(给 baseline 更大的本机池,或给 fluidpool 更小的预算)没有跑。
2. 能真正挤掉 baseline 本机备份的 DRAM 突发没有跑;HBM 层只用于对端实例,闲置 HBM 被占的驱逐没有单独测。
3. 实时计划相对静态计划的收益在真机上没有显现(IB 突发为零差别)。
4. 远端双卡恢复 33 GB/s 的上限没有查清根因。
5. PCIe 同根规则未写进图的资源模型;我方内存块用的是"偏好 NUMA",物理位置可能与记账不一致。
6. `flow.plan_fetch` 的 LP 没有与贪心在真机上对比;放置用的是水位填充,不是 `placement.park` 的 LP。
7. park 的字节来自持久文件(约 2–6 GB/s),没有做从 HBM 直接下放。
8. 每个配置只跑了一遍,没有重复种子;clusterdata 的时间戳被压缩,只保留了请求顺序。

## 9. 复现

```bash
P=codex_coding/src/v0.1.23.3/task2/live_graph/pool; R=codex_coding/results/v0.1.23.3/task2/live_graph/pool_r01
for n in node27 node28 node29 node30; do $P/pool_container.sh $n; done
$P/pool_ctl.sh start $R/logs                       # noded + tenantd,约 90 s 就绪
$P/run_maas.sh $PWD/$R/maas_r01 --per-phase 16 --warmup 8 --step 4
docker exec zjn-fluid-pool-27 bash -lc "cd $PWD && python3 $P/burst_bench.py $R/manifests $R/burst_r01 scale,loss,ib,dram,graph"
ssh node28 "docker exec zjn-fluid-pool-28 bash -lc 'cd $PWD && python3 $P/ipc_attach_bench.py $R/manifests 9g-8b 2 $R/burst_r01 && python3 $P/servable_demo.py $R/manifests qwen3-0.6b 2 $R/burst_r01'"
$P/pool_ctl.sh stop
```
