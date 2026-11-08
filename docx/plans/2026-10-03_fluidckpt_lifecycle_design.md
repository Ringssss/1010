# FluidCKPT：内存压力下的模型保活、让位与恢复设计

日期：2026-10-03。状态：基于现有源码、历史产物、四节点只读检查和上游资料的设计；不是新性能结果。沿用项目编号，不因设计文档新增版本号。用户本轮明确将端到端切模作为目标，因此旧文档的 weights_ready 单窗口不再是唯一主指标。

项目根目录：`/home/zhujianian/zhujianian/fluidckpt_zjn`。环境为海光 DCU/gfx936、DTK/HIP；PyTorch 中的 `torch.cuda` 接口名不表示硬件为 NVIDIA。本文中的 HIP VMM、图重用及 host RDMA 都需要在此厂商栈上单独验证。

## 1. 核心判断与研究问题

这个系统有必要做。不可变的推理权重在 A→B→A 中没有产生新信息，却可能因显存让位、本机 DRAM 不足及 page cache 回收，重复支付磁盘读取、TP 分片、量化重排、设备传输和框架初始化。保留一份权重也不等于能及时用上：它可能位于拥塞的 NUMA、网卡或 SSD 后方。系统应管理“在什么资源预算下，模型能在多久内再次服务”，并把显存释放时间和被借用节点的业务干扰一起纳入。

建议核心主张：**在固定集群资源预算下，通过联合选择权重驻留位置、无丢失的迁移顺序和恢复依赖顺序，使更多模型满足恢复服务的延迟目标。**

“所有硬件都工作”不是目标函数。目标是正确请求的等待时间、SLO goodput、显存让出时间与保活成本。额外路径没有经过瓶颈割，或者破坏 NUMA 亲和性时，更多并行反而更慢。

对纯命中的目标要诚实：相同引擎状态、相同设备和完整 resident 权重下，零拷贝已经不搬 payload；远端读不能保证比它快。可以优化 IPC 的元数据、映射和模型绑定开销，但这与提高远端传输吞吐是两个机制。FluidCKPT 应包含同样的本地零拷贝快速路径，并在内存压力、迁移、重复切模和缓存未命中时降低端到端延迟，画出同预算下的性能曲线；不能宣称所有点严格更快。

## 2. 对手已精确定位

用户指的是 LMSYS 2026-08-21 的 [Weight Cache Daemon](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery)。它让独立 daemon 保存处理后的权重，新引擎通过 IPC 共享物理 HBM。引擎退出不等于权重丢失。因此“能活过推理 producer 的退出”不能单独构成相对官方 daemon 的优势。多节点启动也不等于从任意节点的 DRAM/SSD 聚合恢复。

[上游 roadmap #33522](https://github.com/sgl-project/sglang/issues/33522) 已讨论跨 GPU/fleet 供给、VMM、元数据、更新和其他后端，并链接 daemon-to-daemon transfer 工作。它们是需要跟踪的演进方向，不能把 roadmap 当作已经在海光环境验证通过的能力。

[GMS RFC #27310](https://github.com/sgl-project/sglang/issues/27310) 已讨论独立内存所有者、稳定虚拟地址、sleep/wake 和 reader/writer leases。这些不能直接作为本文的新颖性。官方 blog 的 daemon 死亡描述与当前 [IPC loader](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/weight_cache/ipc_loader.py) 的 liveness watchdog 存在差异；正式实验必须固定源码 commit，按该版本实现和故障测试判断。

本机实验容器 `zjn-keep-v02-n27` 的 SGLang 为 `0.5.12+das185.dtk2604.torch2100.2608121934.gcbf025`。本轮检查未发现 `sglang/srt/weight_cache` 目录或 `--weight-cache-mode` 参数。它有 `--enable-memory-saver`、`--enable-weights-cpu-backup` 源码，但 `torch-memory-saver` 未安装。故现有成绩不能称为与官方 daemon 或原生 CPU backup 的完成对照。需要移植/运行兼容基线，并披露适配改动；安装缺失也不证明算法胜出。

## 3. 目前真正做到什么

证据包：[evidence.json](../../codex_coding/results/design_audit_20261003/evidence.json)。它保存原产物哈希、当前源码漂移、关键指标，以及从 Claude 工具调用记录提取的四组历史命令。

| 产物 | 已有事实 | 不能据此声称 |
| --- | --- | --- |
| `v0.2.0/compare06_b2fix` | capture load 16.963188s；FluidCKPT attach load 0.341205s；两条生成相同 | 不是官方 daemon 对照，也不是 49.7× 端到端加速 |
| 同一 B2 产物 | producer ready 124.174s；consumer ready 125.174s | 没记录首 token；不能猜成 2–3s 服务启动 |
| `v0.1.23.3/task2/cluster06` | 跨节点 managed restore load 1.836312s；execute 1.223125s；70,329,718,112 bytes 全量校验和三条生成通过 | 一次样本，非 P99 或官方 SGLang 加速比 |
| `v0.1.23.3/task2/preplan01` | 同一路由 load 1.748662→1.700246s；dispatch-to-ready 1.346748→1.309652s | 是控制准备差异；不能冒充新带宽或 TTFT 收益 |
| `v0.1.23.3/task2/baselocation03` | remote base load 2.572175s；local base 4.647308s，均校验通过 | 负结果必须保留；“本地一定快”不成立，也不能凭一次结果证明远端必胜 |
| `v0.2.0/armf02_plan` | LP 0.791946s，输入是 raw ingest 布局、每 rank 33.93GiB | 不是处理后 capture 布局的实测恢复 |
| `v0.2.0/armf12` | producer 在 2026-10-03 00:07:45 因等待 captured 标记超时退出 | 当前 F 没通过；不是仍在后台缓慢完成 |

历史 `run_system.py.prepare_model()` 顺序为 capture、验证、释放 live 引用、停止 producer、adopt；因此已有旧路径不是仅解 LP，也不是必须 producer 存活才能恢复。当前 `keeper.py`、`sglang_hook.py` 与这些成功 run 保存的源码哈希不同，必须先恢复可重复性，不能直接把旧结果视为当前 HEAD 通过。

已有库基础值得保留：NUMA 分配/注册、实际副本目录、epoch/generation/lease、覆盖检查、prepare/execute/complete、固定布局资源 LP、有限候选 MILP、多互斥场景、迁移收益门槛、可续作 journal。主要缺口是完整底本可迁移、真正的多模型 A→B→A 生命周期、常驻执行壳、服务级指标和并发恢复资源分配。多互斥未来场景不等于同时多个恢复请求。

## 4. 当前代码的确定性问题

1. `arm_f_keepalive.py:271` 先等 health，`:278` 才提交 keeper capture；`sglang_hook.py:260` 在 load_model 内等 captured。必须先启动 capture，再等 producer 完成初始化。这是代码可证明的循环依赖。
2. producer 绑定 `127.0.0.1`，health/generate 客户端使用 `keepctl.IP[src]` 的 IB 地址，监听与访问地址不匹配。
3. `flowplan.to_ops()` 不写 `snap`，该 driver 也未补；keeper 源端用 `o['snap']`。老 `arm_f.py` 明确补过这个字段，新 driver 漏了。会导致源端异常而对端进入 collective 等待。
4. capture 中 30 个 `conv_weights` 是 `conv1d.weight` 的 storage alias；本轮读取 pkl 验证了对应的 `storage_name`。`_install_derived()` 按 shape 新建独立 storage，会破坏 alias 和严格元数据匹配。1009→1039 不能解释成“只差 30 个新权重所以已兼容”。应按完整 storage/view 描述重建。
5. `keeper.run_flow()` 在目标校验与通信之间没有跨参与者 prepare 栅栏；源端可能先进入 collective。新 HTTP 线程或放开全局锁不能解决对端死亡、CUDA stream/allocator 生命周期问题。优先复用 `cluster.control.Cluster.transfer()`。
6. 超时循环有的只退出 while 而不拒绝继续；capture error 被装进 dict 后仍可能继续。handoff 复用固定 tag、`FLUIDCKPT_MODEL_ID` 缺失、GPU 列表参数与固定 `HIP_VISIBLE_DEVICES=0,1` 不一致，均需契约化处理。
7. `summary_load` 不要求所有 rank 完成；应用已有严格 `measure.summarize()`。单机跨 rank monotonic 可用，跨主机不可直接相减；TTFT 用同一客户端时钟。

这些发现来自静态阅读和已保存日志；本轮没有修改运行实现，也没有宣称新复现成功。

## 5. 最有潜力的机制：按恢复时间分配缓存与迁移预算

一个模型的保活状态应包含：内容版本、已物化布局、完整字节覆盖、各副本位置、恢复目标、资源约束下的预计恢复时间。模型拥有很多缓存字节，但最后一个 TP rank 的必需区域仍在慢 SSD 上，可能仍然很慢。因此缓存价值不应只由热度/大小决定，还应看该区域是否减少瓶颈资源流量、是否解除下一层等待、是否与其他 rank 的区域互补。

具体提出三个可证伪假设：

- **H1：保留一个较小、选择正确的 HBM 子集，加上其他层的并行供给，可让更多模型达到近似 resident 的服务延迟。** 子集不固定为前几层：选取引起启动等待、慢路径等待、TP 最慢 rank 等待的区域；还要考虑 prefill/decode 和图预热。容量代价计入总预算。
- **H2：联合计划 A 的退出和 B 的进入，比各自最大流更快。** 两方向争用相同 NUMA 控制器、PCIe root、网卡与暂存空间；必须优化完整切换 DAG。本项目已有“一个 root port 混多个 NUMA 后吞吐下降”的实测，是值得深入的动机。
- **H3：权重只需一次物化，重复让位主要是释放引用和改变副本位置。** 对同一不可变版本，DRAM/SSD 已有完整处理后副本时，不应每次把 A 从 HBM 再写一遍；只有唯一副本或版本变化的字节需要转移。这降低 A→B 让位延迟，同时维持 A 的回归速度。

H1 没有保证胜过相同条件下已经 resident 的模型；H2/H3 也要与具备 CPU backup、缓存和预取能力的强基线比较。贡献应来自可测的互补性、可执行的容量/时序约束和收益，不来自给标准 MILP 换名字。

最值得暴露给调度器的接口是一个双向契约：`yield(model, bytes_to_free, release_deadline, return_deadline, destination_set)`。它同时询问“多久能把多少显存让给 B”和“A 被再次召回后多久能服务”。恢复规划只优化第二半；永远 HBM 驻留只优化第二半却可能无法让位；不保留副本则让位快但回归慢。FluidCKPT 的核心实验应寻找并扩大两个期限同时可行的区域。没有预留资源时 return_deadline 只是预测；只有准入、资源保留和明确故障假设成立后才能称为保证。

## 6. MILP 和最大流仍然需要，但职责分开

离线/慢路径 MILP 决定块放哪里、保留几份、哪些副本退休、峰值迁移预算与未来场景；在线 LP 根据已经发布的真实副本、当时的可用链路以及恢复 deadline 分配流量。MILP 不在每个 token 或每个 chunk 上求解。超时用可行解和明确回退，记录 gap、求解成本和模型误差。

对权重块 b、位置 j、场景 s、路径 p、资源 r，可沿用：

```text
x[b,j] ∈ {0,1}                         副本放置
f[b,j,p,s] ≥ 0                         恢复字节
Σ_j,p f[b,j,p,s] = bytes[b]            每个必需区域完整覆盖
Σ_p f[b,j,p,s] ≤ bytes[b] x[b,j]       不能读取未发布副本
Σ_b alloc_bytes[b] x[b,j] ≤ budget[j]  含页对齐、模型/KV/暂存预留
Σ_b,j,p a[r,p] f[b,j,p,s] ≤ C[r] T[s]  共享资源，而非独立路径带宽相加
```

不同 tensor 不能互相替代，所以通常是资源约束、多商品流；简单最大流仅在可互换数据的简化图成立。连续 LP 是流体近似，不自动保证有同样快的执行顺序。

新的有限时域模型还需要：每个时间片实际驻留容量、构建副本先于读取、接收完成先于源回收、层计算先于下一层消费、同时恢复请求共享资源、前台服务预留。目标先最小化 deadline 违约与请求等待，再在近最优延迟下减少迁移字节、HBM·秒、DRAM·秒和 SSD 写入；不建议一开始靠任意权重合成所有目标。

需要先以标准整模型 MILP/LP 和简单贪心作基线，再逐步增加时序约束。LP 的资源紧约束和影子价格可指导候选裁剪，但 TP minimax 下的互补副本需要成组考虑；仓库已有单点贪心停滞、成对贪心达到 MILP 值的证据。

一个必要下界是 `T ≥ max_cut(required_bytes_crossing_cut / residual_capacity)`。按当前 F 简化模型每目标 GPU 46GB/s，完整处理后 shard 35,164,859,056 bytes 的数据下界约 0.76445s；两 rank 并行时不是把两份字节除以单卡带宽。更多源节点无法越过同一目标入口。要进一步降延迟，必须减少跨该瓶颈的字节、换已测更快路径、提前搬运或重叠可隐藏部分。HSW/P2P 可能走不同资源，不能一律按 PCIe 限死，也不能未经测量宣称 NIC→邻卡→HSW 绕路有收益。

## 7. 端到端 A→B→A 协议

```mermaid
flowchart LR
  A[A 已加载并验证] --> C[发布处理后权重与布局]
  C --> P[选择 HBM / NUMA DRAM / 远端 / SSD 副本]
  P --> Q[A 排空请求并释放显存]
  Q --> B[B 恢复并服务]
  B --> R[A 再次请求：目录与资源检查]
  R --> F[复用执行壳与映射；分块恢复]
  F --> T[A 首个正确 token]
```

**一次发布。** 先捕获处理后 storage，生成内容版本、engine/DTK/kernel/quant/TP/PP/EP fingerprint，以及 tensor 到 storage 的 offset/shape/stride/alias。scalar 属性、非持久 buffer 和推理必需派生状态都由 engine adapter 明确处理；不能只按 tensor 名字假设跨版本可用。HF 原文件保留作兼容性兜底，处理后 packed extents 用于快速恢复。

**A 让位。** 排空或按明确策略处理在途请求，确认传输源 lease。已有有效后备副本的块可直接撤销 HBM 引用；唯一副本必须先复制、验证、发布。只有物理引用和 pending GPU 操作都释放，才向 B 宣告可用 HBM。记录从切换请求到 B 真能分配显存的时间，不能只看 `torch.cuda.memory_allocated()`。

**承受 DRAM 压力。** 根据预计再访问与瓶颈成本，把一部分块放到别的 NUMA、远端 DRAM/HBM 或 SSD；原位置不必按整模型保留。目标收到并提交后才退休源。每块至少保留符合承诺的覆盖；若要求承受节点故障，副本必须分故障域，容量与复制成本单列。DRAM 常驻并不等于掉电持久化。

**B 服务时。** A 的后台搬运受带宽额度、低优先级和收益门槛约束。若未来预取没有比现有副本更高收益则不搬，避免以“流动”为目的无限循环迁移。前台 P99 超阈值则降速或暂停；实测残余带宽，而非假定网络空闲。

**A 回归。** 本地 HBM 命中走 HIP IPC/已有映射；部分命中只补缺块；远端 DRAM 和 SSD 按实际瓶颈提供互补数据。先 prepare 全部参与者，检查 schema、覆盖、预算与 epoch，再执行，按块发布完成事件。恢复失败不能把坏块标 ready，也不能让一个失败 rank 把所有 peer 卡住数小时。

**执行壳常驻。** 保留已初始化的框架、tokenizer、context、TP 通信等，显式剥离权重物理驻留。不同架构使用相应 warm shell，并记录 CPU 内存与空闲 GPU 开销。HIP VMM 若可用再验证固定 VA remap 与图重用；不可用时先 eager 模式显式 rebind，图重新构建计入时间。不能把 NVIDIA 的 GMS/CRIU 方案直接当作 DCU 已具备能力。

**按层恢复是后续优化。** 若到达时间为 A_l，计算耗时为 C_l，则 `finish_l = max(finish_(l-1), A_l) + C_l`。deadline 来自实际依赖图，TP 同层跨 rank 需要协调。embedding 到达不代表首 token 完成，更不能把原 `weights_ready_all` 改名为第一层 ready 来美化数字。保留 layer_ready、weights_ready_all、first_correct_token 三类事件。

## 8. 论文启发与差异化边界

用户提到的 [Taming Dynamism on GPUs](https://doi.org/10.1145/3830418.3843857) 已在作者主页和机构列表确认；ACM 正文访问返回 403，本轮未宣称通读论文。作者的 [tilelang.dataflow](https://github.com/He-Jingkai/tilelang.dataflow) 描述了细粒度依赖、动态任务、slot lifetime 和带 epoch 的通信完成协议。可借鉴的是“按真实依赖释放消费者、明确源数据何时可复用”，进而把全模型恢复栅栏拆为块/层的 ready 与回收事件。NVIDIA cluster/TMA/mbarrier 实现不能直接套到海光。Just-in-time reduction 也不能等同为 checkpoint 按需读取。

[BlitzScale，OSDI’25](https://www.usenix.org/conference/osdi25/presentation/zhang-dingyan) 已做通过计算网络供给权重、网络优化扩散及按层开始承担计算；因此“远端 + 按层重叠”不足以证明新颖性。[ServerlessLLM，OSDI’24](https://www.usenix.org/conference/osdi24/presentation/fu) 已包含优化 checkpoint 格式、多层加载和位置感知调度。实际基线须允许把请求调到权重所在节点；目标节点固定的实验也要给出合理约束。

[PPC/MAIO，FAST’26](https://www.usenix.org/conference/fast26/presentation/liu-yubo) 已优化模型 I/O 模板、预取、NUMA/XPU 亲和性与回收。它启发本项目研究 cache 生命周期；本文应额外处理多模型重复切换、处理后布局和集群最后副本约束，不能只重做通用预读。

适合 FAST 的候选贡献：处理后 checkpoint 的生命周期与分层保留；可执行、有限内存下的最后副本迁移；恢复延迟驱动的跨层/跨节点放置；SSD/DRAM/HBM 真实容量压力与字节账本。适合 OSDI 的扩展：warm shell + 权重驻留分离的接口、服务调度与跨请求资源隔离。投稿潜力需要由相对强基线的结果证明，当前不能保证。

## 9. Linux page cache：要纳入，但先验证是否需要改内核

page cache 占用同一份 DRAM，不能与显式 DRAM 池重复记容量。原始 safetensors 的文件页通常也不等于处理后的 TP/quant storage；缓存层必须区分 raw 与 materialized 两种表示。

建议先实现受预算的用户态层级：长期保活的处理后块、有限 registered staging pool、packed SSD extents。比较 buffered I/O（计入 cache）、direct I/O（对齐/文件系统支持须验证）和显式预取。是否让全部 DRAM 常驻 pin 应由恢复收益与系统压力决定，不能无限 pin 整机。

最值得研究的 page-cache 语义是“该文件范围的处理后等价数据是否已在 GPU/远端保住，何时还会需要它”。文件页读完可能是可回收的重复表示，也可能是 A 下一次恢复的唯一廉价来源；单靠访问时间看不出这个差别。

第一版用目录、`posix_fadvise`/预取、NUMA 分配和 cgroup 预算做可测实现。仅当实测表明回收时机、NUMA placement 或额外 CPU copy 是决定性瓶颈，且用户态方法不能解决，再设计 kernel/PPC 扩展。`mmap` 和 tmpfs 都不能自动保证可用性；`DONTNEED` 也不是持久化协议。packed SSD 在声明持久完成前需处理 fsync、manifest 提交与校验，当前 keeper.save 不能当成完成的崩溃一致性设计。

## 10. 实验必须围绕什么

主指标统一为“客户端发出切换/恢复请求→首个正确 token”，分列 A→B、B→A、A 让出 HBM 时间、整体请求 P50/P95/P99、SLO goodput、B 的服务降速、峰值物理内存、网络/SSD 字节、预热与迁移成本。weights_ready 仍保留作机制指标。短 prompt、长 prefill、持续 decode 都测；MoE 不能按 top-k/总专家比例直接推断首轮需要搬运的比例。

| 阶段 | 问题与控制变量 | 必须得到的证据 |
| --- | --- | --- |
| E0 复原 | 复用 cluster06 控制面；修复布局 alias/身份/时序；不扫参数 | producer 停止、物理 HBM 已释放、跨节点恢复、全量 byte hash、生成一致 |
| E1 真基线 | 官方 daemon 的固定 commit/HIP 适配；原生 CPU backup；FluidCKPT B2 单列 | 命中映射耗时、让位能力、缺失能力的明确标记；不能拿 unsupported 算无限延迟 |
| E2 核心动机 | A→B→A；HBM 放不下 A+B；DRAM 预算逐级下降；相同节点资源 | daemon/本地备份在哪个预算出现 cliff；集群保活能否消除重复冷加载 |
| E3 联合退出/进入 | A 降级与 B 加载，串行/盲目并行/NUMA 约束联合计划 | B-ready、A 保留覆盖、峰值容量及受害业务尾延迟；展示并行反而慢的原因 |
| E4 cache 价值 | 同样缓存字节：随机、前缀、热度、整模型 LRU、互补区域选择 | 首 token 与所有 rank 的 layer stall；若简单规则同样好，削减算法复杂度 |
| E5 多源瓶颈 | 单源/2源/4源；目标入口和源端热点分别受限 | 实际 byte path、链路/NUMA 计数器、预测/执行误差；包括无法因加源变快的负例 |
| E6 生命周期 | 多模型 trace、同模型重复重载、突然需求反转 | 每个 immutable epoch 的实际复制次数、迁移收益、抖动、空间-延迟 Pareto 曲线 |
| E7 回收与故障 | 唯一副本迁移中断、keeper 退出、坏 epoch、SSD 半写、压力回收 | 不提前释放最后覆盖、不暴露未完成权重、可恢复重试；故障域承诺单列 |

另加 daemon + 手工理想预放置、无优化多层缓存、nearest/成对 greedy、LP、MILP 的消融；给基线相同的整机/集群预算和可用信息。已知未来的 oracle 单独标注，不能让本方法偷用未来再访问时间。

早期每方案 1–2 次仅功能/趋势。论文阶段在冻结代码后执行随机化成对重复、真实/合成切模 trace 和足够数量恢复事件，报告误差区间；少数启动样本不足以报告可靠 P99。图模式、量化、TP、输出长度、warm shell、预热成本、所有 daemon/KV/暂存 HBM 都对齐。GLM-5.3 先通过厂商原生推理与正确性，再纳入大模型矩阵；不能用平台 fault 代替对照结果。

四节点当前状态：node27、29 GPU 约 1% 驻留；node28 GPU0/1 约 82%、其余约 1%；node30 全部约 81%。SSH/共享目录可达，但 node30 还没在 keepctl 的三节点映射中。用户允许停止 zhujianian 的任务，不表示这些占用均属该用户；实施时先按 PID/容器确认归属。本轮只读检查，没有停止服务。

## 11. 实施顺序与代码落点

1. 先统一到 `main_lib/fluidckpt/cluster/{control,worker,catalog}.py` 的事务路径，修复 serving manifest；保留历史 F 失败产物。用短小 alias fixture 和一次真实恢复定位代码漂移。
2. 新增 engine adapter 的 quiesce/detach/bind/resume 接口，接入 warm shell；写 A→B→A driver。先让服务级时间确实不再反复支付约 125s 初始化。
3. 给 `managed.py/managed_control.py` 扩展可迁移底本与块级覆盖，允许预算收缩时迁移最后副本；把 SSD 加入真实目录与发布协议。
4. 在 `planning.py/multi_joint.py` 上先接入并发请求/退出-进入资源预算；保持完整恢复 LP 作对照。复用已有实测 NUMA admission，不直接使用 F 的统一 DEFAULT_CAPS 当作四节点校准。
5. 根据 E2–E5 的证据决定是否加入 layer ready、目标 HBM 子集和时域 MILP。稳态 byte buffer 32MiB、规划块约 1GiB 只是当前起点，按设备/模型验证。
6. 最后决定是否需要 page-cache 内核扩展以及能否证明 FAST 级贡献。先提供可复现的同预算优势，再加复杂机制。

“完全 beyond”建议写成可验收的要求：resident 命中快速路径无显著回退；容量受限切模与远端恢复在端到端和成本上扩展出更好的可行范围；至少一个简单基线无法达到的 SLO/容量组合，经多模型实验和机制消融证明。不要把“所有 remote miss 都严格快过已驻留 local hit”作为一个违反相同条件比较的目标。
