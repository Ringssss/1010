# 两节点单发送者：完整权重恢复与在线成本验证

开发编号保持v0.1.23.3 / task2。用户明确同意先简化两节点，资源可用后恢复复杂拓扑；本计划作为原18.35的当前执行分支，原四GPU并发方案和失败原件保持。

**执行终点：**r02六预热/十二校准/C0/两个留出全部完成，完整恢复、正确性和回收通过。C0实测3.28749s；Hnear实测1.31220s/预测1.59697s（+21.70%未过）；Hremote实测4.56026s/预测4.95703s（+8.70%通过）。整体10%尚未认证，排序正确。两个误差主要来自共用外层残差迁移，搬运DAG偏差仅约10/7ms。详见[正式结果](../experiments/2026-10-12_simple_two_node_online_report.md)。本轮按两会话结束，不追加第三次或用留出回填；下一步先利用现有分段证据限定控制/接管开销校准，原复杂拓扑仍待资源恢复。

**r02实测前修订：**r01已完成完整bootstrap与12个正确路径样本，但两点正斜率拟合拒绝远端rank0的非递增测量（512MiB约73.13ms、1GiB约66.85ms）；无C0/留出，准确清理已通过。不是带宽为负，也不将失败改判。为服务用户整体10%主门，唯一后续新会话`online_simple_two_node_r02`改用实际块尺寸的服务成本表：每路径先1GiB bulk warmup一次（准备项），随后对805,120,688B尾块和1GiB各测一次，共6warmup+12校准；原生执行/全SHA均保留。模型只接受六具体路径×两注册尺寸的正服务时间，交给原worker DAG，不拟正斜率、不插值/外推、不把单点反算率称物理带宽。仍只有一个完整C0共同外层残差，两完整留出预测在运行前冻结，逐案10%门保持。原r01所有数据保留；这是测量前限定的模型修订，不看留出后调整。无需第三会话或五轮重复来追微项。

## 1. 本轮要完成的事

在已有真实Qwen TP2完整权重、单映射捕获和物理暂停正确性的基础上，完成完整在线恢复和预先冻结预测的独立验证。目标仍为每场景整体权重可用时延预测误差不超过10%；不引入新solver，不追逐微项误差，不重跑已经证明的小IPC机制实验。

主指标为同一个控制器时钟从必要inventory/plan之前开始，到完整resume ACK且两rank weights_ready。真实materialize、IPC接管、复制完成、GPU同步和TP barrier保留；全SHA与固定参考输出在ready之后，source创建、初始磁盘加载和额外检查单列。

## 2. 简化拓扑与复用

| 角色 | 物理绑定 | 保活位置与作用 |
| --- | --- | --- |
| 目标worker0/1及TP2引擎 | node27 GPU4/5，CPU NUMA4/7 | 每rank完整本地DRAM底本及首1GiB HBM缓存，完整恢复目标 |
| 唯一远端worker2 | node30 GPU4，PCI NUMA4 | 两rank分别保存于CPU NUMA4/7，经同一个GPU staging发送 |

只需三张GPU，mesh为`node27:4-5,node30:4-4`。单发送者按原生操作顺序服务两个目标；其两个目标路径仍各自校准，但共享发送者的串行限制由原worker依赖DAG计费。不使用旧四worker双流经验因子，也不要求并发重叠证明。本轮通过不能证明复杂多路径/多NIC/三节点/SSD资格；这些待资源恢复后重新启用原方案。

复用现有capture、Catalog、HIP/RCCL、SGLang生命周期、原生restore plan和service_cost DAG。版本实验新增simple适配文件，避免修改历史运行快照。helper一个NUMA不足以容纳约70.33GB完整内容，因此显式按rank拆到NUMA4/7，各实际触页/注册；同一个worker可以持有多个NUMA来源，不能把worker PCI NUMA当成所有DRAM的位置。

## 3. 资源口径修正

此前helper的整卡used≤2GiB是保守准入协议，包含他人占用，不能直接当作“本任务需要的显存超过预算”。当前分开记录：

- 目标node27保持任务DRAM192GiB、容器hard224GiB，初始每卡free≥52GiB，运行每卡used上界≤48GiB（含本次model/cache/runtime等）及余量。
- helper node30保持任务DRAM96GiB、容器hard128GiB，完整两分片分别落NUMA4/7。Catalog DRAM预算改为同一worker96GiB；不复制完整权重到helper GPU。
- helper规划GPU额外空间2GiB用于上下文、RCCL、staging和临时空间；初始实际free≥4GiB，运行保留实际free≥2GiB，显式已知分配/PyTorch reserved单独检查。剩余空间不足立即停止。
- 整卡total/free用于物理容量准入；不把foreign占用算成本任务占用，也不把整卡前后差量当准确的任务归属。PyTorch reserved不包含全部driver/RCCL，所以若无完整归属数据，报告本任务总HBM精确值unknown，不能声称严格验证了2GiB完整进程上限。
- 保存helper warm后实际free、known allocations及NUMA、cgroup DRAM。常驻监测记录检查间隔与样本，不冒称连续峰值已知。不做KFD/vram owner遍历、drop caches或终止其他任务。

上述是数据前修改的简化协议；旧helper全卡2GiB门的失败不回写改判。主统一现场准入与进程生命周期，原XML/默认mapping保持。

## 4. 最小实验矩阵与判断

完整处理后权重70,329,718,112B，每rank35,164,859,056B；每rank33个1GiB量子extent，末块805,120,688B，共66操作。所有完整方案目标内容、extent顺序与N/K/C一致。

1. 六条必要路径各测512MiB与1GiB各一次：每rank本地HBM、本地DRAM、远端DRAM（远端worker同为2，DRAM NUMA分别4/7）。全SHA验收，原始时间与资源同时保留。
2. C0一次完整恢复：首1GiB HBM，后16GiB远端，再接本地DRAM；只校准共同在线外层残差一次。
3. 在任何留出执行之前冻结Hnear、Hremote的两份预测：首1GiB HBM保持；余量分别全部本地或全部远端。
4. 各留出运行一次，比较整体在线误差≤10%，全SHA/参考输出和物理park验收；预测接近时排序弃权。不从旧微块数据外推、不逐布局单独拟常数、不平均抵消失败、不用旧50us/20%门。

单发送者路径成本与DAG串行限制应足以描述当前简化域。如果完整在线误差不通过，只定位主要未解释窗口；不为救模型无限加重复或新参数。资源变化、正确性、模型拟合和精度分别报告，失败部分照样保存阶段原件。

## 5. 分工、命名与后续

主负责范围/预算、文档、资源启动与验收；simple_core负责controller/contract/model适配，simple_ops负责部署/worker资源观测与精确关闭，simple_review独立检查源码/预测冻结/实际结果。子agent不自行启动硬件。

脚本和结果继续位于`codex_coding/{src,results}/v0.1.23.3/task2/online_cost/`的新simple命名目录，首会话为`online_simple_two_node_r01`。新源码快照、新容器、新结果，不复用旧PID/IPC凭证，失败重试独立编号。每配置通常一次，必要才两次；不归档全历史、不覆盖history-chat或上传GitHub。

完成后再把相同主指标与已验收路径接入规划方案比较；复杂资源可用时恢复原多发送者/共享网络场景，独立补竞争校准和复杂域留出，而非假定本次简单域资格自动覆盖。
