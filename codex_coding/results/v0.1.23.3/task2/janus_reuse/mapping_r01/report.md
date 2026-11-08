# Janus 组件复用与现有 FluidCKPT 接口映射

日期：2026-10-11；开发编号：v0.1.23.3/task2。范围：冻结源码、现有产品组件和既有结果的本地只读审查；本次产出说明及证据索引。

## 复用决定

继续保留当前 HIP/RCCL 执行器、Catalog、NUMA/SSD owner、拓扑证据和 SGLang handoff。Janus 已提供的模型休眠/唤醒、完整活跃源复制、来源保活锁和失败 host 后备可以作为生命周期接口和机制策略复用；当前 `basic_scenarios.py` 已经承接其中的 host/D2D 策略。面向当前研究，新增工作应聚焦权重保活布局与现有恢复计划之间的窄接口。

“只加入复杂拓扑即可”有两种含义。若指在现有 FluidCKPT 底座上复用 Janus 的生命周期/来源组织思想，再接入已存在的物理资源映射和路径证据，这是可行的最小路线。若指 Janus 原版只加拓扑图就能直接实现海光平台上的分散 HBM/DRAM/SSD 保活恢复，源码显示还需要权重身份/extent 转换、来源租约、实际层级预算、目标布局和硬件执行适配。完整 Janus 引擎替换会引入额外 CUDA/NPU、xTensor、模型算子和服务依赖，应在原版复现成为明确必要目标时另行评估。

## 版本与身份

- Janus HEAD：`ec1beca2dbef5628b38e266fdc9afa0dba451678`，与锁定清单一致；`git -C lib_cite/Janus status --short` 为空；origin 为 `https://github.com/Janus2026/Janus.git`。
- 父仓库 HEAD：`c67b1fa45043fb9be0e70b8c98c953d2a85a436a`；工作区含既有 tracked 修改和 untracked `main_lib/`、`codex_coding/` 等。父 HEAD 不能代表这些实际产品源码，逐文件 SHA 见 `evidence.json`。
- `lib_cite/baselines.lock.json`：Janus `original_system_not_reproduced`，已测范围是共同执行器中的 host/D2D mechanism controls。获取冻结源码与运行原版系统是两个状态。
- `main_lib/fluidckpt/benchmarks/basic_scenarios.py:27–45` 注册 `janus_original` 为 pending/original_system，`janus_host` 与 `janus_d2d` 为 implemented/mechanism。报告中的复用机会保持这个身份边界。

## 具体组件矩阵

优先级 P0 表示当前最小原型直接复用；P1 表示有目标接口需求时补充；P2 表示独立整系统工作。

| 必需能力 | Janus 具体组件与代码锚点 | 当前组件与代码锚点 | 实际复用机会/最小接缝 | 适配依赖与已知边界 | 优先级；保留组件 |
| --- | --- | --- | --- | --- | --- |
| 模型生命周期 | `core/distributed_runtime/llm_engine.cpp:1298,1353` 的 sleep/wakeup；`runtime/worker_impl.cpp:726,752` light/deep 权重路径 | `serving/lifecycle.py:20,45`；`serving/ctl.py`；`cluster/control.py:129` | 抽取 pause/restore 状态合同；已有执行器负责数据搬运，服务 engine 负责权重提交 | Janus 要求 `enable_xtensor`；light sleep 先 host materialize 再释放页；当前 handoff 的 ready 事件需对齐权重可用 | P0；保留 serving lifecycle/handoff |
| 完整活跃来源选择和 host 后备 | `service/.../instance_mgr.cpp:2943` 找 awake/有效 xTensor/segments/P2P，最高 EPDT；`:1864` D2D 失败 H2D | `benchmarks/basic_scenarios.py:155,320` 完整逻辑 TP 源、存活证据、最高 EPDT；`:135` 本地完整 host 源；`:343` 调现有 restore_plan | 已有策略直接复用；保留 Janus 规则作对照，可在同合法源上比较 topology policy | 当前 mechanism 比 upstream 的描述符额外执行格式、完整 TP 和存活准入；两者不是同一 runtime | P0；保留 basic_scenarios、强简单策略 |
| 来源保活/安全回收 | `instance_mgr.cpp:2953,3053` 锁所有 awake 源，再仅保留选中源锁；`:1885` 成功/失败后释放 | `cluster/catalog.py:43,62,68` generation 校验、事务 lease、退役延迟关闭；`serving/replicas.py:70,84` 版本化 extent 读租约 | 复用已有 lease；若加入 Janus wrapper，把 source awake lock 的有效期接到 transfer tx | Janus lock 属于服务实例状态；Catalog lease 属于实际 replica allocation，必须保留两者所有者/时效语义 | P0；保留 Catalog/ReplicaCatalog |
| 固定 TP 权重布局 | `llm_engine.cpp:1381` 相同 worker 数时 i→i 远端地址；`common/types.h:470,478,516` GlobalXTensor offset/size | `serving/replicas.py:10` 布局 identity；`cluster/planning.py:13,71` extent 覆盖和目标身份；`cluster/catalog.py:49` generation | 薄描述符转换：模型版本/格式/固定 TP rank/逻辑 offset/n、owner epoch、generation、位置与实际 allocation ID | Janus source picker 没有逐源 TP/dtype/content 检查；其 offset 是 GlobalXTensor 地址空间偏移，不能直接当本项目逻辑 offset；没有任意重分片映射 | P0/P1；保留现有 manifest/layout |
| 分散保活、混合层级 | `InstanceXTensorInfo:482` 每 worker free physical pages；`:485` 每模型 segment 集；`get_min_free_bytes:492` 最小 rank 空闲 | `cluster/joint.py:8`、`multi_joint.py:17`；`memory/owned_host.py:17`；`memory/ssd.py:8`；`cluster/worker.py:90` | 当前分块候选/预算/owner 接口直接用；Janus 整模型来源作为合法候选子集 | Janus 的 segment 能表示模型的非连续物理区域，但 source picker 选择一个实例；这不同于暂停权重分布在多个 helper/NUMA/SSD 上的布局决策 | P0；保留 joint、owner、SSD |
| 物理拓扑与路径准入 | `dist_manager.cpp:77` CUDA NUMA 检测、engine process affinity、跨 NUMA worker spawn isolation；`worker_server.cpp:69` worker NUMA affinity；`instance_mgr.cpp:68` pack 的 bandwidth 值 | `topology/discover.py:35`；`resource_map.py:242,327`；`cluster/path_catalog.py:50,93`；`planning.py:34` directed path/resources | 复用实际 BDF/NUMA/HCA/SSD 身份、fresh owner 和匹配路径证据；把来源策略输出接现有 plan；Janus已有本地运行上下文放置能力应保留 | Janus `types.h:529,556` 的 bandwidth 是 HBM→SRAM 利用率，不能当 HCA/PCIe 链路带宽；本地进程隔离/affinity 与权重保活/恢复拓扑规划分属两个接口 | P0；保留拓扑和 path catalog |
| 共享资源/执行关键路径 | Janus D2D source scorer 使用 EPDT；其原 copy 逐 segment 执行（权重报告详审） | `planning.py:47` 资源 incidence；`service_cost.py:59` source/target readiness DAG；`resource_map.py:123` 有限实测共同工作点 | 保留源/目标串行 DAG；已测有限路径内评分；有同预算候选差异时才补必要共享工作点 | 当前 physical capacity/shared-resource 性能资格仍有限；旧 service coefficients 含首操作，拆模型时避免双计；拓扑文件自身不提供运行性能 | P0/P1；保留现有模型框架 |
| 跨节点/同节点搬运 | `parallel_state/cuda_process_group.h:20` c10d NCCL；`comm_channel.cpp:416` brpc 唤醒 RPC；D2D 为 Mooncake 路径 | `cluster/worker.py:44` mesh；`:217` local/peer/network 执行；`executor/pipeline.py` double buffer | 直接使用已通的 HIP/RCCL 和 staging；Janus policy 可映射当前 ops | 原版 engine CMake 有 CUDA/NPU/MUSA 分支，未找到 ROCm/HIP 构建分支；`worker_impl.cpp:740` 远端权重 wakeup 仅 `USE_NPU` | P0；保留现有 transport/worker |
| IPC/目标引擎接管 | Janus xTensor VA/page mapping，与常规 Tensor IPC 是不同地址/owner 合同 | `serving/ipc_attach.py:41`；`ipc_layout.py`；`keeper.py`；`handoff.py:6`；`cluster/worker.py:375` export | 继续现有 engine IPC、layout 和 ready 协议；若选择 Janus target，新增该 engine 的 fill/commit 边界 adapter | CUDA VMM 不能凭 API 名称直接视作已具备海光 IPC 兼容；需要 owner 生存期、映射、dtype/layout/设备证据。此为整 engine 复用阻碍，不阻碍现有 mechanism 底座 | P0/P2；保留 IPC/handoff |
| 常驻执行上下文 | `worker_service.cpp:24` 4线程池；`:590` Wakeup 进入该池；`threadpool.cpp:8,66` 线程一次建立并循环 | `cluster/worker.py:390` 长寿 worker + ThreadingHTTPServer handler；local_slope 既有首/次 op 诊断 | 可以借鉴“服务线程与执行线程分离”的运行合同；优先核验 current runtime 的单 worker 执行队列设计需求 | 常驻进程与线程不能自动证明已做 tensor/copy 初始化；Janus 池 round-robin。当前首操作观察来自 Python/HIP handler，同 Janus C++/NPU/CUDA 的系数分别认证 | P1；保留现有计时证据和 worker |
| 多模型在线调度/请求前端 | `instance_mgr.cpp:162` repack/demotion/autoscale 线程；dual-pool/GP resource model；etcd watch | 当前实验固定 workload/预算；`benchmarks/basic_scenarios` 和脚本统一协议 | 把固定实验配置保持为控制输入；有在线调度主张时才接服务 lifecycle/来源查询 | 搬入 Janus front-end/etcd/GP/autoscale 会扩成多模型服务系统，当前主指标与必要性证据不依赖它 | P2；保留实验 runner |

表中 Janus engine 路径前缀为 `lib_cite/Janus/janus_engine/janus/`，service 路径前缀为 `lib_cite/Janus/janus_service/janus_service/`，本项目组件前缀为 `main_lib/fluidckpt/`。

## 实际恢复调用链与首操作模型

远端 worker 路径：`LLMEngine::wakeup` (`llm_engine.cpp:1381–1401`) → `RemoteWorker::wakeup_async` (`remote_worker.cpp:359–370`，client-side pool) → `CommChannel::wakeup` (`comm_channel.cpp:416–440`，brpc RPC) → `WorkerService::Wakeup` (`worker_service.cpp:590–611`，service-side pool) → **同步** `Worker::wakeup` (`worker.cpp:214–215`) → `WorkerImpl::wakeup` (`worker_impl.cpp:740–749`) → local reload 或 NPU remote-weight copy。Sleep 对应 `llm_engine.cpp:1327` → `RemoteWorker::sleep_async:344` → `CommChannel::sleep:402` → `WorkerService::Sleep:577` → `Worker::sleep:210` → `WorkerImpl::sleep:726`。

因此服务端实际执行是在 `WorkerService` 的常驻 4线程池内；该调用链不会再次经过 `Worker::wakeup_async`。pool 构造在 `worker_service.cpp:24–31`，各线程初始化回调只显式调用 `device_.set_device()`。`util/threadpool.cpp:34–47` 用轮转队列分配任务，`:66–73` 持续执行队列任务；相邻 wakeup 的具体线程可以不同。pool 构造前的 context/stream 初始化属于构造线程，不能替代对每个执行线程 tensor/copy 首调用状态的认证。

local/direct 路径不同：`WorkerClient::wakeup_async` (`runtime/worker_client.cpp:175–177`) → `Worker::wakeup_async` (`worker.cpp:218–223`) → Worker 本身的 pool → Impl。该 `WorkerClient::sleep_async` (`:171–172`) 仍为 `LOG(FATAL)` 未实现。实际 serving `DistManager` remote-worker 模式应按其路径说明，而不能将 local/direct 模式泛化成完整 sleep 流程。

`fork_master` (`distributed_runtime/master.cpp:376–425`) 检查 xTensor，继承/修订 Options，再构造新的 Master/AssistantMaster 并 run；这是应用对象/模型上下文扩展。`WorkerServer` 的 thread/spawn 分支由 `use_spawn_worker` 决定 (`worker_server.cpp:261–299`)：offline 模式可 spawn，CUDA 多 NUMA 在线 worker 也会因隔离规则强制 spawn (`dist_manager.cpp:77–125,238–247`)。因此“Janus fork”必须按实际应用接口解释，不能从名称推导 Linux fork 继承已暖线程/TLS。

当前 `ClusterWorker` 的长寿对象由 `ThreadingHTTPServer` 请求 handler 调 `execute/_execute` (`cluster/worker.py:390–443,217`)。既有 `local_slope/review_final_r01/report.md` 支持同 handler 内首→次 op 的 host 成本差，并保留 target first-touch/cache 条件。它提供的是当前 runtime 的状态条件；是否迁移执行上下文、哪些项被消除以及 Janus 路径表现，需要各自操作上下文的证据。常驻 pool 是具体结构差异，性能差值目前未测。

## 拓扑、MPI/NCCL 与 worker 准入

Janus 已包含本地 NUMA 感知：`dist_manager.cpp:77–125` 读取 CUDA device NUMA，将 engine process 绑定到第一个可用 NUMA，其他 NUMA worker 强制 spawn；`:238–247` 把该条件与 offline 条件共同传给 WorkerServer。`worker_server.cpp:69–86` 另给 worker creation thread 设置设备 NUMA affinity。这些能力处理运行上下文局部性，可供本项目 worker 合同借鉴；实际恢复 source selection 仍按下述 EPDT 规则。

本次对 `janus_engine/janus` 的 `MPI_`/`mpi.h` 搜索无匹配；third-party Mooncake 的 Ascend/HCCL 部分含 MPI header/CMake 引用。engine CUDA collective 通过 c10d `ProcessGroupNCCL` + TCPStore (`parallel_state/cuda_process_group.h:20–36`，`process_group.cpp:53`)；模型服务调用和恢复控制使用 brpc，远端权重拉取走 Mooncake。不能将其概括为“已有 MPI 分发器，接入拓扑就行”。

Janus source eligibility 在 `find_d2d_source:2943` 有清楚的边界：awake 状态锁、排除 target、有效 heartbeat xTensor、该 model 非空 segment、非空 P2P 地址、最高 EPDT。它没有在此处逐来源比较 TP/rank/dtype/content epoch，也没有比较源位置到目标的 NUMA/PCIe/HCA 代价。`D2DWakeupInfo::is_valid` (`types.h:524`) 只检查两个集合非空；`:3042` 给每个源地址复制同一个模型 segment vector。

`LLMEngine` 在远端地址数量等于目标 worker 数时执行 i→i 配对 (`:1381`)；其他情况把 options 原样发给各 worker (`:1394`)。固定 TP 兼容是当前正常映射的前提，任意 TP 改变不应由地址列表推导。当前 `basic_scenarios.py:155–181,243–275` 已额外实现完整 TP 逻辑源、格式、目标排除和存活准入，是继续保留的公共合同。

## 已有资产与真实验收范围

| 已有资产 | 当前有效证据 | 本轮复用决定 |
| --- | --- | --- |
| `basic_scenarios/complete_table_r01/review_r02/report.md` | S2 host、S3 active-D2D、S4 分散 8GiB 与 S5 软件顺序对照已经实际运行；Janus 列为 mechanism。S2/S3 共同有序操作相同，样本差值不能当算法收益 | 保留 scenario/format/source eligibility、runtime runner、摘要器、既有完整模型结果；后续新布局只新增必要 contract |
| `multitier_runtime/review_final_r02/qualification.json` | 三节点六 worker、六 NUMA、三 SSD、23条 serial 路径全字节 SHA、四 synthetic frozen recipe 和清理通过；shared-resource capacity、live-model T_ready、完整 peak 等未认证 | 保留实际 BDF/NUMA/HCA/config、owner/SSD、runtime_worker、当前有界路径；现成多层设施已够最小原型 |
| `bounded_cost/review_final_r03/qualification.json` 与最新 next_step | 有限域冻结模型独立预测验证已通过；同预算布局决策资格尚待建立 | 保留估计执行 DAG、冻结/验证流程；首操作分离须修合同并避免计数重叠 |
| `local_slope/review_final_r01/report.md` | 同 handler 无 event 首/次 op 支持 host first-op state；具体 TLS/内部函数根因未识别 | 保留真实线程/handler 状态和计时 phase evidence；不将 warm 第二轮作为首次恢复收益 |

以上路径前缀为 `codex_coding/results/v0.1.23.3/task2/`。这些历史文件描述当时运行状态；当前 GPU/owner 生存状态必须由下一次实际运行的 fresh inspection 决定。

## 最小集成原型

1. **保持执行底座，先确定一个 contract。** 固定 TP、model version/dtype/quantization/layout、相同逻辑权重和 target；按 location 列 HBM/DRAM/SSD 预算和 staging 峰值。Janus-style 完整 source 集与 Fluid chunk-source 集在同实际资源范围内合法化。
2. **复用现有描述符与来源策略。** 输出 `identity,tp,off,n,worker,tier,numa,generation,epoch`；Janus XTensor metadata 若作为输入，须显式把 VA offset 转逻辑权重 offset 并提供 layout 证明。已有 `janus_host/janus_d2d` 机制不需要重建；使用其结果构造相同 executor plan。
3. **将恢复计划直接接 `Cluster.transfer`。** `prepare` 用 generation/epoch/lease 锁定来源，`execute` 使用已通路径，权重 filled/verified/published/handoff 分开计时。保留现有控制/回收和总 budget gate。
4. **用有限拓扑证据比较两个同预算合法 layout。** 复用 `resource_map`、`path_catalog` 和 `estimate_execution`；优先选粗 SSD 来源替代量足够区分的候选，与 fastest/交换/现有 joint 比较。根据当前 next_step，先 CPU contract/评分；GPU执行由主 agent 统一安排。
5. **有实际计划差异才做运行验收。** 验收包括逐rank相同内容、完整覆盖、资源账本、目标可用事件、source 生存、failed fallback 和清理；若来源/计划相同，记录策略别名，停止该配置重复扩建。

需要 Janus 的真实 C++ engine 当目标或原版 baseline 时，最小独立工程是固定模型单配置的 build/platform gate → 固定 TP host sleep/wakeup 正确性 → target fill/ready adapter；远端 NPU-only 分支与海光 HIP/xTensor 是明确的额外适配关口。这个选择应以原版复现目标及新增证据决定。

保留清单：Catalog/ReplicaCatalog、CPU owner/NUMA/SSD、RCCL mesh/pair/staging、IPC/layout/handoff、path/resource map、service DAG、强简单策略和已有 joint、basic_scenarios runner/摘要/审查与原始结果。当前避免重复建设的组件：GPU transport、整套 owner/cache、拓扑扫描器、模型加载前端、多模型 autoscaler、etcd 目录、GP 资源模型和通用任意 TP 重分片。

## 检查与未定项

本次检查命令为本地 `git rev-parse/status/remote`、`rg`、带行号源码读取及逐引用 SHA 索引。证据索引保存 HEAD、锁定版本、来源文件哈希和代码锚点，CPU consistency check 见 `qualification.json`。无新安装、构建、SSH或GPU样本；新增写入限于本报告目录。探索搜索中若候选路径不存在，以 `rg --files` 定位实际 `core/runtime`、`core/distributed_runtime` 与 `core/util` 后读取，未使用缺失路径作为能力证据。

最终索引含45个文件、105个代码行锚点，45个SHA读回一致；所有源码存在性/身份/关键分支检查通过。新增 NUMA 断言首版误用 line238 的 `USE_CUDA` guard 索引，失败原件为 `qualification_failed_line_index_r01.json`；改成 line240 的 force-spawn expression 后通过，源码与结论没有改变。

未定项集中为：Janus 本冻结版本原版可构建/可运行状态；Hygon HIP 对 xTensor VMM/IPC 的实际兼容；原版 host/D2D 的真实恢复时间；各执行线程首 tensor/copy 状态；动态 TP/layout 转换正确性；共享物理资源容量与同预算 layout 的主指标差异。权重缓存/xTensor详细结果由同轮 `janus_weight_reuse` 报告给出；本报告保持调度和接口边界。
