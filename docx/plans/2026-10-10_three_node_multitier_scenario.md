# 三节点多卡、多层保活与恢复场景

日期：2026-10-10；开发编号：v0.1.23.3/task2。用户明确扩展场景至三节点、多GPU、CPU DRAM与SSD。对应总计划18.24、报告39。本文件给出同一模型的分层研究场景；逻辑资源已定义，实际节点/磁盘/多GPU运行资格另行绑定。

## 1. 场景的现实含义

一个TP4模型暂停后，需要让出部分HBM和DRAM给其他任务。下次恢复时，四个固定rank都要取得完整的已处理权重。系统可以把部分内容留在目标GPU、辅助GPU或指定NUMA DRAM；其余内容始终能从SSD完整底本取得。

研究目标是：在不均匀的保活额度和实际共享通信资源下，共同选择保存层级、保存位置与恢复来源，降低全部权重可用时间，同时记录保存空间、准备开销与恢复峰值。前台任务的压力在本场景中表现为外部给定预算，不同时建设全局多模型调度器。

与上一轮的区别是，每层都有明确角色：目标本地HBM可减少权重回载，远端HBM收益取决于实际路径；DRAM提供较大而受NUMA/网络限制的保活空间；SSD保证完整覆盖并在快层容量不足时提供必要内容。三节点的网卡/PCI/DRAM/SSD资源以共享身份计账，不给每条路径复制一份带宽。

## 2. 逻辑拓扑与模型布局

| 节点 | GPU0 | GPU1 | CPU内存位置 | 存储位置 |
|---|---|---|---|---|
| N0 | rank0恢复目标 | 辅助保活GPU | NUMA D0、D1 | SSD S0 |
| N1 | rank1恢复目标 | 辅助保活GPU | NUMA D0、D1 | SSD S1 |
| N2 | rank2恢复目标 | rank3恢复目标 | NUMA D0、D1 | SSD S2 |

GPU0/1是逻辑角色，不等于真实机器物理GPU编号。当前实际已知28/30有8GPU/8NUMA/4HCA结构，某些GPU与网卡亲和性不同；本表中每节点只选两张卡，但它们的真实BDF、NUMA和HCA都应通过之后的准入绑定。第三节点也不因被画入图就变成已可运行。

一个快照共8个内容块，每rank两个块，单位q默认4MiB，合计8q=32MiB仅作未来小功能规模。真实模型阶段使用实际processed-TP快照及其manifest，不能把原TP2快照换标签当TP4。

完整SSD底本固定分布如下：S0保存b0–b3，S1保存b4–b5，S2保存b6–b7；总唯一底本8q，分布4q+2q+2q。目标rank0需要b0/b1、rank1需要b2/b3、rank2需要b4/b5、rank3需要b6/b7。因此SSD后备既有节点本地读取，也有跨节点读取需求。每份SSD内容对所有策略始终合法，副本额外创建须另行计费。

底本是已处理后的权重字节与布局。原始checkpoint的解析、量化/转置/TP处理是另一条冷加载基线，其成本单列，不能只给某种方案承担。

## 3. 同一内容/候选图上的五个控制配置

位置顺序为N0g0、N0g1、N1g0、N1g1、N2g0、N2g1；DRAM顺序为N0D0、N0D1、N1D0、N1D1、N2D0、N2D1。候选集合与SSD底本对所有配置相同，零配额的位置由可行性规则排除。

| 配置 | HBM保存总额度 | DRAM保存额度 | 拓扑条件 | 要验证的取舍 |
|---|---:|---:|---|---|
| C0 | 8q，四target各2q | 12q | 逻辑独立路径参考 | HBM足够时简单target-local应足够 |
| C1 | 4q，四target各1q | 12q | 同C0 | HBM放不下的部分可由DRAM保活 |
| C2a | [1,1,0,1,1,0]q=4q | [2,0,1,1,0,2]q=6q | 同C1 | 空间不均引入远端HBM/异NUMA DRAM选择 |
| C2b | 同C2a | 同C2a | 加入明确节点/网卡/PCI/DRAM出口共享 | 只看预算会不会把恢复集中到共同瓶颈 |
| C3 | 同C2b | [1,0,1,0,0,1]q=3q | 同C2b | 快层总容量不足，需要SSD来源参与 |

C2a是隔离预算变化的理论参考，C2b才引入共享资源关联；不能将两个变量一起变化后唯一归因于拓扑。所谓独立参考不是当前物理硬件已具备独立通道，而是待与真实资源图比较的逻辑对照。

主场景给N0g1/N1g1两个helper各1q额度，它们应在完整见证中实际保存并提供内容，避免六GPU中两张从未参与。所有源可选，不先指定哪种方法必须赢；路径速度与层级好坏需要实际证据。

## 4. 每一层为何有作用

令模型唯一需求S=8q，暂停期间HBM、DRAM可容纳的内容上限分别为H、D。

- C0存在全部8q在HBM的布局，是最简单机制对照。
- C1中H=4q<S，无法把所有唯一内容留在HBM；DRAM允许剩余4q常驻，提供不读SSD的可行布局。SSD仍合法，最终是否选DRAM取决于实际成本。
- C2a/b仍可用HBM4q+DRAM4q覆盖模型，但位置限额不同；恢复可以在同节点NUMA跨域、helper GPU及跨节点来源间选择。
- C3中H+D=7q<S，因此无论如何放置，至少q唯一内容没有快层初始副本。在限定来源集合中，这部分至少需要一次来自SSD层的供给。

C3的下界约束的是逻辑SSD来源字节，不等同真实磁盘介质读取量。系统SSD服务使用buffered IO和fadvise，Linux page cache可能提供数据；冷/热文件缓存、own文件范围的I/O取证与内存计账需独立协议，不能用逻辑SSD bytes宣布磁盘带宽或绕过DRAM预算。

复制只能增加占用而不能扩大给定总空间可覆盖的不同字节上限，因此C3的逻辑缺口在允许副本时仍成立。额外活跃模型源、未计费pagecache或新储存层若合法，必须加入来源和预算再重新计算。

## 5. 应进入资源图的路径

| 来源 | 路径与共享资源 |
|---|---|
| 目标本地HBM | 本地copy或后续可比协议中合法IPC接入；GPU内存/复制资源 |
| 同节点另一GPU HBM | 源GPU、目标GPU、已验证GPU互联/PCI资源 |
| 指定NUMA DRAM | 该NUMA内存、实际CPU/PCI路径、host注册及GPU入口 |
| 远端HBM/DRAM | 源侧资源、实际源HCA、网络、目标HCA/PCI/GPU入口 |
| SSD底本 | 实际存储介质与pagecache、读取/HTTP服务、主机缓冲、必要网络腿、GPU暂存与目标落地 |

同一SSD队列、NUMA带宽、PCI上行、HCA出口在多路径中使用相同ResourceID。独立/共享关系和有效容量分开：静态PCI树并不能直接给容量，标称链路速率也不能替代应用路径有效速率。

当前Cluster SSD路线是CPU SSD HTTP服务→RemoteExtent读取到主机buffer→GPU暂存→必要RCCL→目标。SSD服务端到代理worker的HTTP网络腿，和代理worker到目标的RCCL网络腿，都需纳入；不能仅根据代理worker与target同位置将整个SSD读取称本地。已有路径标签和resource约束要按实际源位置检查。

## 6. 保存阶段、恢复阶段与空间账本

暂停时保存处理后的有效内容，建立选定快层副本、校验后发布；SSD底本保持完整。恢复时根据已存在副本选择来源，分块搬运到四target，全部正确可用才完成。恢复后可释放临时target/staging或按已登记生命周期保留。

| 账本 | 计法 |
|---|---|
| SSD完整底本 | 总8q，按S0/S1/S2分4q/2q/2q，只计一次；是否已属于外部固定资源同时披露 |
| HBM/DRAM暂停副本 | 每个实际物理副本收费，含重复内容 |
| GPU恢复目标 | 总8q，暂停HBM额度之外单列，但进入恢复峰值 |
| Worker staging | 原执行器6×64MiB=384MiB；实际部署按角色/分配验证 |
| SSD读取临时buffer/pagecache | 单列峰值和归属，不包含在持久DRAM cache名额中后就假装免费 |
| runtime/通信组/注册/校验 | 多worker聚合实际未知，单进程历史观测只用于保守投影 |
| 保存/迁移成本 | 单独记录建立耗时、写入量和峰值，动态调整才计算回本 |

原2GiB/node DRAM/HBM实验额度未自动增加。三节点各两个GPU worker的runtime按单worker约2GB投影约4GB/node，尚未准入；共享页效应与通信额外开销未知，投影不是硬物理下界。

## 7. 复用现组件与必要缺口

已有Catalog/覆盖/lease/SHA、HostAllocation/CPU owner、GPU Worker/Cluster.transfer、SSD服务、restore_plan与multi_joint可以作为基础。SSD串行路线已有历史验证；本次28/30新配置尚未做SSD恢复实测。

需要明确的边界：

- 原SSD RemoteExtent创建新extent，已有extent重开、manifest/catalog恢复不是现成通用持久恢复接口。若底本跨owner重启仍要使用，需最小元数据绑定，不能因fsync就宣称完整掉电恢复。
- 当前double_buffer源依赖tensor，SSD owner读接口不能直接混用；先复用serial SSD执行，若真实主指标受限才补异步接口。
- multi_joint的tier预算是通用的；旧placement_plan的usage与部分描述符、frozen_layout的HBM账本并非完整多层适配。先薄适配各层资源/来源绑定，不重新建设solver。
- 原service_cost刻画worker先后，不自动建模SSD队列与disjoint flow共享硬件动态。有限恢复时序仍须对应执行能力。

## 8. 对照与可交付结论

对照首先保持相同processed内容、target布局、允许资源、已有副本机会与计时边界。可比较：SSD完整重载、DRAM保活、目标HBM/IPC可用范围、简单就近或分层填充、资源感知均衡/交换、已有联合方案。SGLang/Janus原版、海光适配与共同执行器机制对照分别标记，不能用机制小实验代替原系统复现。

本轮先交付5个完整合法布局/来源见证、线性预算/覆盖/资源审计以及C3缺口下界。每块在HBM/DRAM/SSD有何合法位置由图明确，但速率全部unknown，所以不能认定每个候选当前有性能价值，也不进行指数候选枚举来寻找赢家。待真实成本和执行准入后，再比较主指标及空间曲线。

## 9. 物理映射与下一关

逻辑N0/N1/N2尚未绑定为三台全部可用的GPU服务器。当前证据认证的是28/30选定GPU对和旧配置部分能力；node27/29旧问题和pending记录保持，各节点本地SSD介质/mount/I/O资格未取得，模型所在共享路径也不能称为每节点独立SSD。

第三节点只承担CPU DRAM/SSD服务是可研究的部署替代，但它是另一执行图：CPU SSD服务可以由GPU worker读取；CPU DRAM owner本身不是当前Cluster独立GPU通信端点。不得将该替代称为本表三GPU节点六worker已跑通。

接下来按最小顺序核：真实节点角色与可用额度→processed SSD底本及持久目录绑定→必要NUMA/SSD/网络路径功能→共享资源工作点→同场景强对照及完整模型T_ready。每步只补影响本场景的问题，探索每配置一次必要两次，不扩大模型/任意重分片/通用容错范围。

本轮仅构造场景、审已有能力与离线必要性，不启动GPU、远端操作或改共享XML，不上传GitHub、不全历史归档/history覆盖。全部原始代码/结果按版本目录存放，正式记录39提供最终合同与验收入口。

## 10. 本轮交付与核验结果

最终机器合同为[contract_cpu_r02/contract.json](../../codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r02/contract.json)，内部hash为`741def7882e9163fb6afd299c27c1bff9c0207529d9d555c754d66f5492111fe`。构建schema与249项结构检查通过，[独立验收](../../codex_coding/results/v0.1.23.3/task2/multitier_scenarios/review_final_r01/report.md)247项通过。检查数量是审计条目数，不是性能实验样本数。

| 配置 | 可行布局实际HBM/DRAM保存 | 恢复SSD逻辑来源 | 两个helper的参与 |
|---|---|---:|---|
| C0 | 32MiB / 0 | 0 | 此控制不需要helper |
| C1 | 16MiB / 16MiB | 0 | 此控制不需要helper |
| C2a/C2b | 16MiB / 16MiB | 0 | N1g1供rank0的b1；N0g1供rank1的b2 |
| C3 | 16MiB / 12MiB | 4MiB | 同上，N2 SSD补rank3的b7 |

这给出了每个阶段完整且不超保存配额的覆盖见证，还没有完成运行时间最优求解。完整SSD底本始终为32MiB，表中SSD来源4MiB不表示底本只有4MiB。

r01原件保留。r02只补齐原serial执行器的384MiB条件staging预留、SSD读取HTTP腿和GPU恢复腿，以及同节点不同worker的DRAM/SSD源GPU暂存资源；14项修订边界检查确认内容、候选、容量与见证不变。实际未分配staging，速度和物理绑定仍为空。保存HBM+目标+条件staging的已知项为C0 448MiB、其余432MiB，尚非完整生命周期峰值。

[组件盘点](../../codex_coding/results/v0.1.23.3/task2/multitier_scenarios/capability_r01/report.md)已审12项能力、35个证据SHA与19项AST。现成SSD服务与串行恢复可以复用；`RemoteExtent.close()`会删除extent，完整底本生命周期必须显式保护，不能把研究合同里的完整后备假设等同现成持久重启功能。

第一轮实机目标应是确认这张逻辑图的必要路径和共享点能够被已有执行器兑现，然后比较同预算的强就近、均衡/交换与联合方案。是否需要更复杂规划，以它们的权重可用时间差和空间曲线决定。

## 11. 实施进展：真实候选绑定与执行接口（2026-10-10，报告40）

用户已批准按该场景推进。现场更新、接口适配和node27单卡实机资格已推进，最新候选应以[mapping_binding_r03](../../codex_coding/results/v0.1.23.3/task2/multitier_scenarios/mapping_binding_r03/machine_binding.json)为准，不直接使用前两版静态候选：

| 逻辑节点 | 实际节点 | 目标GPU | 辅助GPU | 逻辑D0/D1对应实际NUMA | 本地SSD父目录 |
|---|---|---|---|---|---|
| N0 | node28 | GPU0/BDF09 | GPU1/BDF36 | 0/3 | /data/node28 |
| N1 | node30 | GPU4/BDF85 | GPU5/BDFb5 | 4/7 | /data/node30 |
| N2 | node27 | GPU0/BDF09、GPU4/BDF85 | 无 | 0/4 | /home |

该调整避开node28 GPU4已存在的旧keeper和node30 GPU0–3可见vLLM分配。10:56 UTC对28/30精确四卡只读补采，28GPU0/1 used各584761344B，30GPU4/5 used各3751936B；数字KFD字段与同boot历史deviceID映射对照，无选中卡非零VRAM owner。低占用不代表独占或新通信资格。

node27已经跨重启边界，旧D child不再存在、三个旧容器exited。主启动全新独立6GiB容器后，GPU0/BDF09的一次1MiB H2D/D2H全SHA通过，进程自然退出、组空、旧停止容器未变。整进程19.9906秒含初始化，不是传输时延。数据在[mapping_admission27_run_r01](../../codex_coding/results/v0.1.23.3/task2/multitier_scenarios/mapping_admission27_run_r01/result.json)。GPU4/跨node/六worker/NUMA注册不因GPU0通过而自动就绪。

预算适用范围已纠正：旧18.18每node2GiB是双worker小实验暂定配额，历史失败保留；用户本轮批准三node六GPU方向后，另建DRAM聚合增量6GiB/node、HBM聚合增量2GiB/node的候选运行预算，包含runtime与临时空间，须fresh资源与postwarm聚合验收。它不改变本计划各阶段暂停权重配额。新node27隔离容器6GiB实际生效，完整物理峰值和六worker账本尚待验证。

版本实验[adapter说明](../../codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/README.md)已经把104候选与见证转换为原serial保存/恢复plan，提供当前owner绑定、失败journal与精确临时副本清理。原Cluster/Worker/Catalog/wire在CPU替代GPU/NCCL条件下79项检查过，真实本地SSD HTTP读写也执行；另7项失败journal验证通过。最终源码与测试版本差异可按SHA追溯。没有新增solver、速率样本或模型handoff。

下一步具体执行序列为：三机任务容器与六worker当前身份/聚合预算→必要GPU pair与NUMA触页/注册→完整32MiB SSD底本创建/提交/校验→SSD_only、C0、C1、C3各一次→精确退役。完整SSD-only新增为机制基线，用于验证三个存储位置均实际参与；C3只有N2 SSD读入不能覆盖全部底本路径。node27本次copy容器仅有IPC_LOCK；NUMA mbind的任务权限与已成功SYS_NICE配置需要显式对齐，不能由copy成功推断。

当前三节点六GPU完整恢复和主指标比较仍未运行。可信物理准入、所选保存/恢复路径、三机SSD服务与NUMA分配是具体剩余项；已完成的静态地图和CPU接口不重建。

## 12. 六worker实机闭环已完成（2026-10-10，报告41）

本轮按第11节顺序实际执行一次，结果为：[独立最终资格](../../codex_coding/results/v0.1.23.3/task2/multitier_runtime/review_final_r01/qualification.json)、[完整结果解析](../../codex_coding/results/v0.1.23.3/task2/multitier_runtime/runner_result_r01/report.md)。三节点六GPU、六个实际NUMA页分配/注册位置、三份SSD服务和23条必要4MiB路径均已通过；四配方各恢复完整32MiB并全SHA。两个helper实际跨node供给，C3实际使用4MiB SSD来源。

实际session=`multitier_runtime_r02`、run=`operations_actual_r01`。三容器使用SYS_NICE/IPC_LOCK/privateIPC/core0；SSD新目录在28/30的`/data/nodeXX/shared`同本地NVMe下，27在本地/home项目结果目录。初次直接/data目录PermissionError发生于GPU启动前并保留，第二次新run修正目录成功。不能重新沿第11节父目录直接mkdir或复用旧run。

| 配方 | 暂停HBM/DRAM副本(MiB) | 恢复SSD来源(MiB) | 目标填满(ms) | 校验发布完成(ms) |
|---|---|---:|---:|---:|
| SSD_only | 0 / 0 | 32 | 132.188 | 194.581 |
| C0 | 32 / 0 | 0 | 72.915 | 99.886 |
| C1 | 16 / 16 | 0 | 71.450 | 96.808 |
| C3 | 16 / 12 | 4 | 81.052 | 111.562 |

时间从原Cluster.transfer事务开始计，包含库存查询/prepare，末列再包含全SHA和发布；外部可信gate/binding的前置查询尚不在窗。当前是replica target和合成TP4式字节，没有live模型handoff。每配置n=1、暂停配额不同、布局是冻结见证，不据此声称规划器优势或统计排序。约62ms库存查询已成为小数据窗口的重要部分，诊断成本需单独验证，不能将所有时间当通信。

实际六pair为01/02/03/12/24/45；跨node使用IBext_v8/shca_0:1/gdr0，同node日志为P2P/IPC。其他九pair、替代HCA路径、共享容量曲线未测，不能将六pair功能升级为整图性能模型。完整SSD32MiB在组间始终保持，最终明确end-of-run释放所有本任务底本、cache和target；9supervisor正常结束、child按STOP收到TERM，组空、三容器pids1/noOOM，原keeper与容器身份保持。

DRAM250ms采样最大值28/30/27约5.288/5.425/4.325GB，小于每node6GiB并留256MiB；HBM原sysfs/HIP跨来源差仅增量估算，独立同HIP绝对占用采样上界约1.911/1.539/1.854GB，均<2GiB。完整峰值仍unknown；28绝对上界不同时证明256MiB余量，不重写原增量gate口径。

可复用执行入口已建立。下一研究关口是先减少或单独计量诊断/前置控制对主指标的影响，再在相同内存预算下比较强简单布局与已有联合规划，并补一个必要的共享竞争工作点。逐项为主指标服务，不重建地图/通信框架，不因功能已通而扩任意模型/重分片或全候选扫描。原C2a继续作为离线独立资源对照，C2b的共享影响需实测单路/组合证据，当前四配方不能替代该比较。
