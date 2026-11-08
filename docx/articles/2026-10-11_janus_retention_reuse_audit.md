# Janus 权重保活实现与 FluidCKPT 最小复用路线

日期：2026-10-11。编号：v0.1.23.3/task2。本轮仅审查本地冻结源码，未安装或运行 Janus、未移植 HIP、未修改外部仓库。`lib_cite/Janus` HEAD 为 `ec1beca2dbef5628b38e266fdc9afa0dba451678`，与锁定清单一致，工作区干净。[详细实现审计及行锚](../../codex_coding/results/v0.1.23.3/task2/janus_reuse/implementation_r01/report.md)

## 1. 结论与系统定位

Janus 已建设模型睡眠/唤醒、处理后权重缓存、共享设备页池、活跃实例复制及层级滚动加载。我们应沿用这些基础能力所解决的问题和接口，不把它们重新包装成论文增量。当前 Fluid 已有目录、owner、NUMA固定内存、lease、HIP IPC、恢复执行器及 Janus 式机制对照，继续建同功能底座没有必要。

本版本不能直接在海光上插入一个复杂拓扑规划器就得到完整系统：权重保活的具体 model hooks 主要在 NPU 实装，remote wakeup 明确限制 USE_NPU，处理后 tensor layout、ACL/ATB/Mooncake/xTensor 注册和生命周期相互依赖。拓扑之外还需要后端、格式、所有权与空间口径的衔接。当前未验证任何 Janus 源码模块可直接在本海光环境运行。

用户最新明确边界：FluidCKPT始终是产品和论文主体。复用的方向是从FluidCKPT实际缺口出发，选择性提取Janus已经优化的组件源码，经最小适配进入FluidCKPT；不建设“Janus加复杂拓扑”，不以Janus引擎互通或整体移植为默认目标。原版Janus在海光能否整体运行，不是组件源码可复用性的前提。

最低成本路线是保留已经跑通的Fluid执行和存储基础设施，逐项检查Janus缓存、布局绑定、来源保护和执行上下文中是否有值得迁入的具体实现。已有合同与代码均可参考，但迁入要对应明确缺口和验收收益，把主要新增工作集中在预算约束的块驻留、取数与共享资源规划。本文列出的整系统适配约束只说明不能直接运行完整baseline，不应被当作拒绝小组件源码复用的理由。

## 2. Janus 怎样保活和恢复权重

| 机制 | 冻结实现行为 | 对本项目的含义 |
|---|---|---|
| LIGHT_SLEEP | 各worker先形成或复用主机处理后权重，之后归还模型weight/KV页。兼容device storage存在时可D2H保存；也可能复用host cache或读取checkpoint。 | 保存完成再允许复用源页；不能一概描述为每次从盘重读或每次必做D2H。 |
| DEEP_SLEEP | 释放模型的host cache引用；无其他引用才实际释放缓存。恢复重新走模型checkpoint loader。 | 引用释放与物理空间释放分别记账；名字cold不证明介质缓存已冷。 |
| warm wakeup | 重新分配模型设备页，从host processed storage H2D，按slice信息重绑tensor。 | 保留模型对象/运行时和权重数据是两个生命周期；需要格式兼容。 |
| D2D fork/wakeup | 选一个awake且有有效描述符、该模型segments及P2P地址的实例；源地址数匹配worker数时按下标对应，读取segments后绑定目标tensor。完整内容、固定TP和processed layout是兼容前提，源选择函数未逐项认证。 | 可复用已有活跃源；目标仍有自己的完整权重空间，不是IPC别名，也不是任意块多源拼接。 |
| rolling load | NPU路径保留完整host层权重，以有限device slots逐层H2D，load/compute streams与events保护slot复用。 | Janus不只支持全部权重同时常驻HBM；滚动服务终点与我们的全部必要权重可用终点分开比较。当前remote wakeup拒绝rolling组合。 |

主要调用链为 [LLMEngine::sleep/wakeup](../../lib_cite/Janus/janus_engine/janus/core/distributed_runtime/llm_engine.cpp:1298) → [WorkerImpl](../../lib_cite/Janus/janus_engine/janus/core/runtime/worker_impl.cpp:726) → [NPU模型hooks](../../lib_cite/Janus/janus_engine/janus/models/llm/npu/llm_model_base.h:474) → loader/cache或远端传输。fork在常驻engine里构造新Master，不是POSIX fork复制整个地址空间。

`PinnedHostMemoryCache` 是进程内singleton，entry含pinned segments、weight slices、loading/ready、refcount和mutex/cv。引用者等待唯一加载者发布，refcount归零且加载完成才释放底层host allocation。这不是独立跨进程keeper daemon。缓存分配使用ACL host API，该类本身未提供本项目所需的逐位置字节预算或保留子集求解。[缓存接口](../../lib_cite/Janus/janus_engine/janus/core/layers/npu/loader/pinned_host_memory_cache.h:17)、[分配/发布/释放](../../lib_cite/Janus/janus_engine/janus/core/layers/npu/loader/pinned_host_memory_cache.cpp:9)

host缓存key包含规范化model path、component、rank/world size、dtype/quant；这是复用身份，不等于文件内容版本或布局hash证明。恢复的是每rank经过合并、量化、布局处理后的storage；NPU NZ/ATB格式不能直接和我们SGLang/HIP快照混用。[loader及key](../../lib_cite/Janus/janus_engine/janus/core/layers/npu/loader/base_manual_loader.cpp:363)

## 3. 不能只增加拓扑配置的三个原因

### 3.1 后端接口存在不等于保活实现可用

`WorkerImpl::wakeup`远端分支受USE_NPU限制。generic CausalLM接口有lazy/free/reload hooks，但缺具体模型实现时走NOT_IMPLEMENTED，宏为LOG(FATAL)，不能把generic worker的调用语句视为CUDA warm已完成。[接口转发和默认行为](../../lib_cite/Janus/janus_engine/janus/core/framework/model/causal_lm.h:111)、[宏](../../lib_cite/Janus/janus_engine/janus/core/common/macros.h:75)

xTensor底层有多种硬件后端，CUDA分支用Driver VMM API；这个冻结版本没有直接HIP构建后端。即使移植分配API，还需要验证页粒度、映射访问权、传输注册范围及模型tensor重绑定。因此“替换拓扑矩阵”不足以完成海光适配。

### 3.2 池内可复用空间与外部可用显存不同

PhyPagePool启动时预分配物理页，GlobalXTensor建立全池映射。模型sleep后free_weight_pages主要更新池的allocated/free bookkeeping，供Janus内部其他模型使用，物理owner通常仍是共享池。[预分配](../../lib_cite/Janus/janus_engine/janus/core/framework/xtensor/phy_page_pool.cpp:10)、[归还页](../../lib_cite/Janus/janus_engine/janus/core/framework/xtensor/phy_page_pool.cpp:247)

这不是实现缺陷，而是Janus多模型共置的资源管理方式。我们的论文如果声称给其他进程减压，必须报告模型占用、共享池reserve、runtime/staging和设备实际可用空间，不能只比较“模型名下用了多少页”。移入整套xTensor会改变这个资源合同。

### 3.3 当前传输合同与块级规划不同

Janus的源选择在合格awake实例中取最高EPDT，减少选择即将被排空的源的风险；D2D调用结束后释放源锁，失败时重置目标状态，再走H2D后备。它有服务状态判断，不能称为随意选择。该函数没有逐块tier/路径成本/共享HCA约束；选择一个逻辑源，源地址数等于目标worker数时按下标对应，否则向各worker原样传递options。[find_d2d_source](../../lib_cite/Janus/janus_service/janus_service/scheduler/managers/instance_mgr.cpp:2943)、[释放锁与后备顺序](../../lib_cite/Janus/janus_service/janus_service/scheduler/managers/instance_mgr.cpp:1864)

`WeightSegment`的offset/size表达不连续设备区段，但目标按段顺序填充，段描述没有本项目逐replica的内容覆盖、epoch/generation与事务lease字段；Janus另有实例级源锁和传输session引用管理。给源选择函数加一个“距离”字段，仍不会自动获得跨HBM/NUMA DRAM/SSD的分块取数。

Janus已有CUDA设备到NUMA映射、进程/线程亲和性和跨NUMA worker隔离，因此不能宣称它不考虑拓扑。这里的增量是**位置预算下的块驻留与恢复取数、共享路径约束**，不是首次引入NUMA或跨节点通信。[NUMA设置](../../lib_cite/Janus/janus_engine/janus/core/distributed_runtime/dist_manager.cpp:78)

## 4. 应复用什么，哪些不再建设

| 要解决的问题 | Janus可复用内容 | 我们已有组件与本轮决定 |
|---|---|---|
| 暂停后保留正确权重 | LIGHT/DEEP生命周期、先保存后归还页、引用发布语义 | 保留`serving/lifecycle.py`、Keeper、Catalog和owner；补接口契约映射，不新增一套生命周期服务。 |
| 主机固定内存保活 | pinned entry、segments、单加载者/引用者发布与释放协议；C++源码可提取但需allocator/type适配 | 保留`HostAllocation`/NUMA实页注册/CPU owner及Catalog预算，不移入第二个ACL缓存。 |
| 处理后布局与恢复绑定 | weight slice的offset/bytes/shape/dtype、tensor rebind入口 | 保留现有manifest、layout、HIP IPC attach；若接入Janus，做格式/版本/覆盖验证适配，不默认两种processed格式相同。 |
| 可用完整源选择 | 单逻辑active源、rank对应、源保护和host fallback | 现有`janus_host/janus_d2d`已经实现共同执行器机制对照，继续用相同来源机会和预算；不写第二个同功能策略。 |
| 实际搬运 | Mooncake pull/push的endpoint+offset+size调用入口 | 当前local/RCCL/HIP/SSD执行器已通，优先复用；需要原版Janus引擎时再适配backend与注册区域。 |
| 设备页与流式逐层计算 | xTensor、RollingLoadManager的页/slot管理和事件依赖 | 暂缓整套移植；它们涉及另一种池和逐层执行合同，当前没有必须重建的证据。 |
| 复杂拓扑与保活预算决策 | Janus现有状态、可用源及段描述可作为上层输入 | 保留Fluid资源地图、路径测量、Catalog/lease及现有planner，新增量只围绕合法块布局、共享资源和恢复计划差异。 |

源码可复用与立即可执行分开：可提取的小cache/metadata代码依然有torch/ACL/模型布局依赖；目前没有已通过海光运行验收的Janus原始模块。可以先复用接口与状态机合同，并使用现有等价执行原语，明确标注为机制对照。它不等于原版Janus复现。[已有机制注册及策略](../../main_lib/fluidckpt/benchmarks/basic_scenarios.py:38)

## 5. 以FluidCKPT为主体的组件复用顺序

1. 保持完整host及完整活跃源策略作为强简单对照，先核same content/version/TP/target/budget和来源机会。原版Janus、HIP适配版、共同执行器机制列保持分开。
2. 逐项选择FluidCKPT需要补强的组件，列出本项目现有代码与Janus具体实现差异。例如缓存单加载者/并发发布、片段到tensor布局重绑定、来源保护与失败回退、常驻执行线程。若值得迁入，直接适配成FluidCKPT内部模块，沿用本项目manifest、版本/coverage/generation与预算约束，不为读取Janus对象另建跨引擎互通系统。
3. 复杂拓扑规划器输出已有执行器可接受的分块计划，复用当前预算、事务、校验发布与恢复完成计时。先同内容、同目标、同预算比较已有强简单与联合候选；选同一计划就记录没有增量。
4. 每个源码复用项都设置小范围正确性、主指标或维护成本验收，并记录上游来源与改动。具体实现进入本项目产品目录，baseline原件保持冻结；整套Janus引擎运行与其拓扑功能开发不属于本任务。完整baseline复现如将来必要，单独安排，不改变FluidCKPT主体。

这条路线不预设Fluid必须超过Janus。论文要证明在有限且不均匀的保活预算、复杂通信和共享资源条件下，联合驻留/恢复决策有增量；完整host、活跃源复制、NUMA亲和性和页池本身是已有基础。

## 6. 与首操作实验的关联

Janus远端wakeup链进入服务侧长期4线程池，设备set_device有线程初始化；线程池轮转意味着连续请求未必使用同一线程。local/direct WorkerClient路径又不同，不能从“常驻进程”推出“所有执行线程已暖”，更不能把我们Python/HIP首索引的微秒数直接当Janus C++/NPU成本。

首操作验证必须记录实际执行线程birth与先前操作，而不只记录HTTP handler、进程PID或通信pair warm。成本模型应绑定实现/版本/线程生命周期/日志策略和目标状态；跨实现只复用计费结构，参数重新测量。具体线程调用链和我们的组件映射见[配套mapping报告](../../codex_coding/results/v0.1.23.3/task2/janus_reuse/mapping_r01/report.md)。

## 7. 证据边界

本轮没有重新获取源码、构建Janus、运行GPU、改baseline或修改模型系数。实现审计50唯一行锚/30源码文件通过，只认证可追溯的源码结论。分段次序、目标注册区域等静态适配疑点列入未来验收，未证明为运行bug，更不能当Fluid性能优势。

Janus rolling mode、常规warm恢复与本论文全部必要权重可用具有不同终点，后续比较按机制和场景限定。对源码未展示的能力使用“本冻结路径未展示”，不扩展为对论文、其它版本或其它后端的绝对否定。
