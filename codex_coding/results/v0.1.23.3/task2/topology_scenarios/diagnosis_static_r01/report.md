# 跨节点双流退化：原始日志与静态路径取证

开发编号 `v0.1.23.3 / task2`。本次 GPU 操作和新性能样本均为 0；读取原 `calibration_probe_r01`、两端现有容器库/内置拓扑和 builder 的 `diagnosis_inventory_r01`。所有底本、生产配置、旧结果和运行进程保持；新增仅独立取证脚本与本目录。逐行日志证据、全部连接计数、原始分块耗时和文件 SHA 见 [evidence.json](evidence.json)。

**当前确定的是两条不同 transport 模式共用 shca_0 的端到端退化：A 两端 GDR，B 两端非 GDR。还不能认定 NIC 链路饱和或唯一 PCIe/CPU 根因。** 另有实际内置 RCCL 拓扑和 sysfs 不一致的直接证据；它改变 CPU 亲和选择，也可能影响换 NIC 后的 GDR 判定，必须与真实物理拓扑分别记录。

## 已知、推断、未定

| 问题 | 已知原始证据 | 可支持推断 | 未定/不能推出 |
|---|---|---|---|
| 共享退化 | A 单 98.480422ms/43.6124GB/s，B 单 168.255970ms/25.5264GB/s，双 235.603975ms/36.4592GB/s；起点差 25.09µs、完成区间充分重叠 | 在此独立 broadcast/synchronize 路径同时工作显著互相影响 | 生产 keeper、整模型收益或唯一硬件根因 |
| GDR | rank0/2 GPU9000 distance4<=4，send/receive 均 32 个 `/GDRDMA` 连接；rank1/3 GPU36000 distance7>4，send/receive 均 32 个无 GDR 连接 | A 与 B transport 模式不同；B 的 host-backed NIC buffer 路径是首要候选 | 插件内部完全没有额外 staging；A 用户 tensor 必然直接零拷贝 RDMA |
| NIC | 四 rank 皆 `Using [0]shca_0:1/IB`、`Using network IBext_v8`；接收联合计数覆盖 8GiB payload；端口当前 ACTIVE 400Gb/s | 同一端口实际搬运两流 | 计数覆盖不等于链路饱和；idle 测量不排除正式突发背景 |
| 本地 H2D | 两机分别 NUMA0→GPU0、NUMA3→GPU1 registered buffer，双流 ratio 0.991993/0.978973 | 这两个近端独立 H2D 供给未见同量级退化 | 不能排除 B 网络 host-backed buffer 的 NUMA1/跨 NUMA 路径、NIC DMA、GPU 内核访问 mapped host memory |
| Channels | 每组 32 coll / 32 p2p channels，每 rank 均有 proxy send/recv 连接；每组相同数目 | 并发会增加同口 plugin/QP/proxy 工作量 | 初始化的 32 个连接不等于每次正式 broadcast 使用了全部 channels，也不证明 CPU 饱和 |
| CPU | A 日志 mask `ffff`，B `ffff0000`；B 两端 Init COMPLETE on core16；实际内置 XML GPU1 cpu numaid1、mask16–31；真实 GPU1 NUMA3 CPU48–63 | RCCL 的初始化/host 分配所用 CPU affinity 来自与 sysfs 不一致的拓扑 | 不能把该日志说成整个 Python 进程永久锁16–31；proxy线程正式运行 affinity、负载、run queue 和页归属未采集于旧run |
| PCIe | GPU0 与 shca0 同上层 PCI01；GPU1 PCI31，shca1真实 PCI71；所有端点及采集桥链路32GT/s×16 | 混合 host-backed/GDR 路径可能在 shca0 分支/NIC DMA/跨NUMA供给竞争 | 静态峰值和共同祖先不能证明链路已饱和，未有 PCIe transaction 计数 |
| 首次预热 | A/B 首次约537/513ms，在正式计时前；pair 预热约15.58ms | 已吸收首次 communicator 建立 | 不能把500ms解释成正式235ms退化来源 |

## 实际 GDR 和 transport 证据

本目录 evidence 按 rank、文件 SHA、原始行号保存全部相关记录。关键行：

- rank0 `rank_0.nccl.9754.log:30`：GPU9000 GDR Enabled；131 起 receive `/GDRDMA`；send 亦同。
- rank1 `rank_1.nccl.9772.log`：GPU36000 GDR Disabled distance7>4；32 send 和 32 receive 均无 `/GDRDMA`。
- rank2 `rank_2.nccl.18120.log:386`：`recvProxyProgress: issued GDR flush`；GDR send/receive 连接与 rank0 相同。
- rank3 `rank_3.nccl.18138.log:30,95`：显式 Disabled distance7>4；98 起 receive 无后缀，send 亦同。

所有 rank 还有 HCA 级 `GPU Direct RDMA Disabled for HCA0`，随后 A 又明确 Enabled。不能单独取 HCA 检查行否定 A，也不能从 A 的连接后缀泛称两流都是 GDR。`Disabled GDRCopy equivalent memory allocation on gfx936` 是另一项控制/同步内存机制，不能和 GPU Direct RDMA 开关混为一谈。插件 CollNet 符号缺失日志也不能解释成 NET 插件加载失败：P2P v8 IBext_v8 已实际选中。

官方 RCCL 的 [`net.cc`](https://github.com/ROCm/rccl/blob/rocm-6.3.3/src/transport/net.cc) 在连接建立时以 `req.useGdr` 决定后缀；buffer 映射根据 useGdr 选择 host/device memory，向 NIC 注册时分别使用 HOST/CUDA 类型。因此本日志支持 B 采用非 GDR host-backed 通信缓冲的推断。它不等价于某个显式 hipMemcpy staging，也不能给出其 host pages 的实际 NUMA 归属。供应商确切 19c80f7 源码未找到，本次不以官方参考源码冒充部署版本验收。

## 实际部署库、拓扑输入与 affinity

原日志报告 RCCL `2.22.3-HEAD:19c80f7`、HIP6.3.26102、ROCm6.3.3。实际 Librccl `/opt/dtk/lib/librccl.so.1`，plugin `/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so`。两台节点本次只读 SHA 完全相同：

| 文件 | SHA256 |
|---|---|
| RCCL | `32aa13da8bbd4d9a4ec37be08014f0783f5f576c7439dc10a3e1c661aa35427a` |
| plugin | `e0c3fd25c91cdb19c078b8350dd9daadded89d66600b0e5ed298840658af3af6` |
| 内置 XML | `5a10dbdec6e762ceb18861e98a556422e197a3351cf2d17df3246415fb4236ef` |

库目录只找到二进制、静态库、头文件及libtool元数据，未找到部署 `net.cc/paths.cc` 实现。二进制中确有 `NCCL_NET_GDR_LEVEL`、`NCCL_NET_GDR_READ`、`RCCL_FORCE_ENABLE_GDRDMA`、`NCCL_IGNORE_CPU_AFFINITY` 字符串；其存在不证明环境变量在具体 case 的预期效果。插件 `.la` 的0.0.0是 ABI元数据，不是准确源码 commit。原run没有库/XML hash，当前一致只证明现时两端相同，旧run身份以运行日志为据。

原日志皆加载 `/usr/local/built-in-508-topo-input-tj-default.xml`。文件快照见 [node27_topology.txt](node27_topology.txt) 和 [node29_topology.txt](node29_topology.txt)。对照 builder sysfs：

| 对象 | 内置 XML | 真实 sysfs |
|---|---|---|
| GPU0 BDF09 | cpu0，CPU0–15，PCI01 | NUMA0，CPU0–15，PCI01 |
| GPU1 BDF36 | cpu1，CPU16–31，PCI31 | NUMA3，CPU48–63，PCI31 |
| shca1 BDF73 | cpu1，CPU16–31，放PCI31下 | NUMA1，CPU16–31，PCI71下 |

这不是 GPU logical ordinal、RCCL CPU数组下标或压缩 CPU编号可以消除的差异。官方 [`cpuset.h`](https://github.com/ROCm/rccl/blob/rocm-6.3.3/src/include/cpuset.h) 仅省去高位0，输出与解析保留真实 bit位置，`ffff0000`是CPU16–31。官方 [`topo.cc`](https://github.com/ROCm/rccl/blob/rocm-6.3.3/src/graph/topo.cc) 根据gpu.rank匹配节点，找最少hops的CPU，取其cpu.affinity与当前thread允许CPU的交集；`CPU/0-1`本身是建模节点标识，不能当物理socket。

官方 [`init.cc`](https://github.com/ROCm/rccl/blob/rocm-6.3.3/src/init.cc) 在初始化时调用`sched_setaffinity(0,...)`，在该affinity下做host分配，退出恢复原mask。pid0针对当前线程，不能推出整个进程永久锁定。官方 [`proxy.cc`](https://github.com/ROCm/rccl/blob/rocm-6.3.3/src/proxy.cc) 服务线程显式设affinity的代码被注释，线程创建/继承可能保留初始化mask，正式各TID affinity必须实测。builder当前main PID0–127允许集并不能推翻旧初始化日志，也不能替代各线程证据。B的host-buffer近NUMA1是候选，缺页归属证据，尚不称故障因果。

## 是否 NIC 饱和

400Gb/s的标称方向字节率50GB/s。原 payload A43.6124（87.22%）、B25.5264（51.05%）、pair36.4592（72.92%）；IB counter union约36.53GB/s。计数记录的是有效数据量，并非 wire busy fraction或PCIe occupancy。

pair聚合反而比A单流低约16.40%，所以“容量固定且工作守恒、只在两流间分配同一带宽”的单一理想模型解释不了全部下降。NIC内部队列/混合注册方式/QP调度仍可能成为更低的工作点，链路50GB/s并非已测最大有效速率；但目前没有 wire-busy、credit stall或专属发送/接收wait计数支持“物理链路打满”。结论应写共享端到端路径退化，不能写已定位NIC饱和。

## 最小区分实验与当前 G0/G1 的归因边界

主agent选择 G0 原三case复现，G1仅B两端改 `NCCL_IB_HCA=shca_1`、OOB仍ib0，每组matched singles和pair。这是有用的最小“路径选择”实验，现阶段不执行SYS强制或生产拓扑修订。

G1不仅解除同一NIC，还可能同时：(1) 根据内置XML将GPU1-NIC距离SYS→PXB，B GDR off→on；(2) 让真实NIC从NUMA0变NUMA1，靠近原XML选的CPU16–31；(3) 改PCIe分支。因此若G1变快，可证明所选通信路径重要，不能孤立声称唯一NIC瓶颈。必须逐rank保存actual HCA、GPU/HCA GDR distance/read mode、send/recv suffix、affinity、channel数量、proxy TID affinity和两NIC计数。若G1 B启GDR，还应明确已产生transport改变；不能把改变吞吐全部归因于拆NIC。

之后只按结果选择一个变量：

| 待区分假说 | 最小下一变量（未执行） | 必须固定/检查 |
|---|---|---|
| GDR/host-backed效应 vs拆NIC | 原shca0下仅B两端 `NCCL_NET_GDR_LEVEL=SYS`，或G1拆NIC下仅B禁GDR再matched singles/pair | 硬件支持与正确性必须实测；runtime启用才算干预成功；不同时改channel/affinity。官方[2.22.3变量说明](https://docs.nvidia.com/deeplearning/nccl/archives/nccl_2223/user-guide/doc/env.html#nccl-net-gdr-level-formerly-nccl-ib-gdr-level)以字符串SYS/LOC选择距离阈值，供应商实际结果以log为准 |
| 初始化/代理CPU与host页位置 | 相同 NIC/GDR/channel 下，仅临时诊断拓扑副本CPU mask16–31→48–63，或有实测保障的thread affinity干预 | 不改生产XML；先证干预仅affinity、GDR/channel不变；外部taskset和IGNORE_CPU_AFFINITY默认逻辑可能取交为空或仍覆盖，不能假定有效 |
| channel/proxy/QP压力 | 固定transport/NIC/affinity，仅限制通信channel数32→8，单流与双流matched | 必须log确认；若改善只能定位对并发粒度敏感，不自动等价CPU瓶颈 |

当前证据没有CPU占满、proxy串行wait或页布局的测量。最便宜先补新probe运行中的`/proc/PID/task/TID/{comm,status,stat}`、sched affinity及runtime connection log，保留receiver同host计时，不引入全网拓扑扫描或新执行器。

## 复现与失败记录

本次命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/static/forensics.py --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01 --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_static_r01 --container zjn-fluid-basic-1bce3897a2075b
```

输出4rank、GPU操作0；旧run完整日志SHA留存，现时两端库/XML hash一致。容器无rg，只读定向查询使用grep/find回退；初始readelf没有Build ID返回1，不推导库身份失败。官方下载net.cc/init.cc的urllib超时保存在evidence.references.error，语义通过同一官方URL的web读取；paths/topo/proxy/cpuset成功保存，均标注上游参考、并非精确vendor源码。无GPU启动、生产config修改或cleanup动作。
