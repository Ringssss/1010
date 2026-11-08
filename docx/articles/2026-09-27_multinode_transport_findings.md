# 多节点 FluidCKPT：节点间数据通路（第一批实测）

日期 2026-09-27。参与机器：node27 / node28 / node29（node30 的 8 张卡被他人占满，本轮排除）。
测量脚本：`scripts/cross_node_bw.py` + `scripts/run_on_pair.sh` / `scripts/mesh_sweep.sh`。

## 1. 一句话

**节点间 GPU 直达能跑到 46–48.5 GB/s，是本地 NVMe（2.8 GB/s）的 17 倍，是 NFS（0.27 GB/s）的 180 倍。**
但这需要三个条件同时满足，缺一个就掉回 0.74 GB/s（差 65 倍）——这也是本节的主要发现。

## 2. 实测

### 2.1 IB 网格（每个方向都是单 rank，`broadcast` 单向搬运）

| 链路 | 256 MiB | 1 GiB | all_reduce（环形，双向） |
|---|---|---|---|
| node27 ↔ node28 | 48.59 GB/s | 46.04 GB/s | 17.7–17.9 GB/s |
| node27 ↔ node29 | 48.38 GB/s | 46.01 GB/s | 17.9 GB/s |
| node28 ↔ node29 | 48.61 GB/s | 45.88 GB/s | 17.7 GB/s |

三条链路在噪声内完全一致 → **跨机带宽可以当作单一常数建模**。

### 2.2 修正前 vs 修正后（node27 ↔ node29）

| 配置 | 256 MiB broadcast | 有效带宽 |
|---|---|---|
| 默认（无插件 + 容器 memlock 64 KB） | 0.69 GB/s | 退回 TCP socket |
| `NCCL_NET_PLUGIN=librccl-net-shca.so` + `--ulimit memlock=-1:-1` | **48.57 GB/s** | 真 IB RDMA |

### 2.3 裸 verbs（对照，用于确认链路能力上限）

`ib_write_bw` 单条 HCA 11.7 GB/s；4 条 HCA 并发合计 **75.9 GB/s**。
RCCL 用一层就拿到 48.5，说明单条 400 Gb/s 链路接近打满（48.5/50 = 97% 线速）。

### 2.4 多 rank 同时跨机：会叠加，不是共享单链路（2026-09-27 补测）

单链路 48.5 GB/s 之上，每节点 8 个 rank 同时说话的结果（node27↔node29 与 node27↔node28 各测一遍，
两次一致；512 MiB 扇出一格出现 985 GB/s 离群值，复测 1825.5 与首次 1816.4 吻合，按离群处理）：

| 模式 | 64 MiB | 512 MiB |
|---|---|---|
| 8 条独立跨机流（聚合） | 230.4 / 232.5 GB/s | 205.8 / 203.7 GB/s |
| 同上，单条流 | 28.8 / 29.1 | 25.7 / 25.5 |
| 单根扇出到远端 15 rank（root 出口） | 97.6 / 98.0 | 121.1 / 121.7 |
| 16 rank all_reduce | 100.2 / 102.0 | 121.3 / 120.9 |

读法：

- **8 条流聚合 230 GB/s，是单链路 48.5 的 4.7 倍。** 四张 HCA 各走一部分，链路层面确实是并行的，
  不是抢同一条 400 Gb/s。
- **单流从 48.5 掉到 25.7**：并发时每条流只分到一半左右，说明聚合还没到 4×48.5 的硬件上限，
  而是被别的东西卡住（八成是单卡 HBM/PCIe 的读写口，或者是 RCCL channel 的分配方式）。
- **扇出/ring 稳定在 100–121 GB/s**：root 单卡往外推的上限就是百 GB/s 量级，和单卡 PCIe 出口同数量级。

**对规划器的直接结论**：跨机搬运不能用一个常数建模。分两种情形：

- 一个源喂多个目标（复制保活副本）→ root 出口 ~120 GB/s，与目标数基本无关。
- N 个源各自喂一个目标（打散搬运、TP 分片并行拉）→ 聚合 ~205–230 GB/s。

也就是"谁在说话"比"多少数据"更决定上限。这一点必须写进模型，否则多节点计划的 makespan 会差 2–4 倍。

## 3. 三个必要条件（每个都是 65 倍差距的开关）

### 3.1 厂商 IB 插件必须显式加载

RCCL 按默认名找 `librccl-net.so`，找不到就**静默退回 TCP socket**：

```
NCCL INFO NET/Plugin: Could not find: librccl-net.so. Using internal network plugin.
NCCL INFO NET/IB : No device found.
NCCL INFO NET/Socket : Using [0]ib0:12.11.3.27<0> ...
```

而内置 IB 插件认不出这套国产 HCA（vendor `0x1cb8`，`shca_*`）。厂商插件就在
`/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so`，但 RCCL 不会自动找它：

```bash
NCCL_NET_PLUGIN=librccl-net-shca.so     # 必须显式指定
```

`NCCL_IB_HCA=shca` **不起作用**——内置插件根本没走到那一步。

### 3.2 容器必须给足 memlock

我起 node29 容器时漏了 `--ulimit memlock=-1:-1`，容器里 `ulimit -l` 是 64 KB，
插件加载后立刻挂在 `ibv_reg_mr failed`（注册不出 RDMA 内存区）。
node27 的容器是别人起好的，本来就是 unlimited。

```bash
docker run ... --ulimit memlock=-1:-1 --ulimit nofile=1048576:1048576 --device /dev/infiniband
```

### 3.3 RCCL 的 send/recv 跨机是坏的，集合通信是好的

同一次运行里，`dist.send` / `dist.recv` 跨机必挂：

```
ncclSocketInit: connecting to address  with family 0 is neither AF_INET(2) nor AF_INET6(10)
ncclInternalError: Internal check failed.
```

日志里那个地址是空的（代理路径里地址没填上），伴随 `proxy.cc Accept failed Operation not permitted`。
`broadcast` / `all_reduce` / `all_gather` 全部正常。

**对 FluidCKPT 的影响**：搬权重必须用集合原语（`broadcast` 从源 rank 推，天然就是"一个源喂多个目标"），
不能按点对点写成 `isend/irecv`。这其实和 SGLang 用 `broadcast` 展开权重是同一个路数。

## 4. 存储层的对照（同一批机器）

| 路径 | 实测 | 说明 |
|---|---|---|
| `/data/node28` 本地 NVMe（O_DIRECT 读） | 2.5 GB/s | 写 2.8 GB/s |
| node29 读 node28 的 NFS 挂载 | 0.27 GB/s | NFS over TCP，没用 RDMA |
| node27 读 node28 的 NFS 挂载 | 0.27 GB/s | 同上 |
| 跨机 GPU 直达（RCCL + IB） | **48.5 GB/s** | 本节结论 |

755 GB 的 GLM-5.3 走 NFS 要 47 分钟，走 RDMA 只要 15.6 秒。**NFS 不能作为多节点搬运的基础设施。**

## 5. 节点家底（2026-09-27）

| 节点 | 空闲 DCU | 内存 | 本地盘余量 | 权重 |
|---|---|---|---|---|
| node27 | 8/8 | 503 G | /home 197 G | Qwen3.6-35B-A3B 67 G（本地） |
| node28 | 6/8（0、1 被占） | 503 G | — 已满 | GLM-5.3 755.6 G（本地） |
| node29 | 8/8 | 503 G | 1.4 T | Qwen3.8-Flash-Next 336 G（本地） |
| node30 | 0/8 | — | 1.7 T | 排除 |

`/home` 由 node27 经 IB 导出，四台机器共享同一份代码；`/data/nodeN` 各自本地、互相挂载（走 NFS）。
容器镜像 `sglang:0.5.12-ubuntu22.04-dtk2604-py3.10-sf_b075` 到处一致。

## 6. 复现

```bash
# 单个链路  (路径已按 v0.1.23.1 迁移后的结构更新)
ssh node27 'cd /home/zhujianian/zhujianian/fluidckpt_zjn && PORT=29781 \
    bash codex_coding/src/run_on_pair.sh codex_coding/src/bench/cross_node_bw.py \
    node27 node29 NCCL_NET_PLUGIN=librccl-net-shca.so \
    -- --out codex_coding/results/mesh/mesh_27_29.json --tag 27-29 --sizes 256,1024'
# 全网格
ssh node27 'bash /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/mesh_sweep.sh 256,1024'
```

## 7. 还不知道的

- 聚合 230 GB/s 卡在哪一层：单卡 HBM 出口、PCIe root，还是 RCCL channel 分配。要定位得做
  "同节点内 8 流"对照（算出本地那一段的上限），再叠加跨机。
- `all_gather_object` 这类 object collective 走不走 IB。
- 跨机时目标卡与源卡不同 NUMA 节点的组合会不会踩到 node27 单机上那条"root port 碰两个节点"的规则。
- node28 / node29 的 gpu_numa 映射还没量，规划器目前只有 node27 的。
- node30 的卡释放后要重测。
