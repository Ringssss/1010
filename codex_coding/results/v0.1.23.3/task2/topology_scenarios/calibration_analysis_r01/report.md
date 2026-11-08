# 拓扑资源校准结果与应用测量入口

开发编号 `v0.1.23.3 / task2`。本报告离线引用 `calibration_probe_r01` 的9个配置观察，新增GPU样本0。源run通过准入、最终完整buffer SHA、实际HCA绑定、保护对象与物理回收；[独立审查](../audit_calibration_r01/report.md)200项通过。计算材料为[精确指标JSON](summary.json)和[9行速率CSV](rates.csv)，所有速率均以十进制GB/s计。

**本轮证据确认独立RCCL双流存在端到端共享路径退化。** 同字节单流的独立预期完成时间168.255970ms，双流共同完成时间235.603975ms，完成比值0.714147416，窗口增加40.027112%，共同聚合速度36.459209111GB/s。两机本地双H2D控制的窗口增加0.807183%和2.147854%，均未达到预登记退化门槛。下一项最小工作是把这个观察接到当前keeper的真实DRAM-staged取数窗口，以决定共享约束是否需要进入应用规划。

## 硬件与两条路径的亲和性

node27、node29的当前拓扑一致：GPU0为`0000:09:00.0`、NUMA0；GPU1为`0000:36:00.0`、NUMA3；实际HCA `shca_0`和OOB网卡`ib0`共同映射`0000:03:00.0`、NUMA0。三个PCI设备均报告`32.0 GT/s PCIe`、width16。GPU0与HCA共享祖先`0000:01:00.0`和`0000:00:01.1`；GPU1走另一组PCI祖先。原始映射见[前置inventory](../calibration_probe_r01/inventory_before.json)，derived JSON保留BDF、NUMA、祖先和fingerprint。

A路径为node27 GPU0→node29 GPU0，B路径为GPU1→GPU1，二者使用相同`shca_0:1`但GPU—NIC亲和性不同。A单流43.612397355GB/s，B单流25.526388728GB/s，因此共享门槛采用各自实测时间的较大值作为独立预期。node27近端H2D的GPU1反而比GPU0快；本轮证据支持特定网络路径的差异，具体NIC/PCIe/NUMA原因保留待定位。

## 逐流与共同窗口

每流正式累计字节4,294,967,296B（4GiB），由同一256MiB buffer连续16次搬运组成；每配置一个观察。每流速率独立重算为`bytes/(receiver_end_ns-receiver_start_ns)`；分子bytes、分母ns直接给出GB/s。双流共同窗口为同一接收host上的`max(end)-min(start)`。两机时钟域分别使用host/boot_id/monotonic_ns，未跨host相减。

| 配置 | A完成ms | A GB/s | B完成ms | B GB/s | 共同窗口ms | 共同聚合GB/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| network_single_a | 98.480422 | 43.612397355 | — | — | 98.480422 | 43.612397355 |
| network_single_b | — | — | 168.255970 | 25.526388728 | 168.255970 | 25.526388728 |
| network_pair | 232.469977 | 18.475363363 | 235.603975 | 18.229604556 | 235.603975 | 36.459209111 |
| h2d_node27_single_a | 94.762988 | 45.323257388 | — | — | 94.762988 | 45.323257388 |
| h2d_node27_single_b | — | — | 89.862239 | 47.795017616 | 89.862239 | 47.795017616 |
| h2d_node27_pair | 95.451701 | 44.996236327 | 90.008908 | 47.717135908 | 95.527899 | 89.920689997 |
| h2d_node29_single_a | 92.061125 | 46.653430490 | — | — | 92.061125 | 46.653430490 |
| h2d_node29_single_b | — | — | 94.028826 | 45.677134116 | 94.028826 | 45.677134116 |
| h2d_node29_pair | 92.056295 | 46.655878297 | 96.040808 | 44.720232841 | 96.048428 | 89.433369925 |

双流退化比值统一独立计算为`R=max(single_A_elapsed,single_B_elapsed)/pair_common_makespan`。门槛为R≤0.85、较短流调用区间overlap≥80%、start skew≤较短匹配单流duration的5%。网络共享gate另要求HCA载荷计数、背景检查、保护与回收通过。

| 双流配置 | 独立预期ms | 实测共同ms | R | 窗口增加 | A/B slowdown | overlap | start skew μs |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| network_pair | 168.255970 | 235.603975 | 0.714147416 | 40.027112% | 2.360570480 / 1.400271117 | 100.000000% | 25.090 |
| h2d_node27_pair | 94.762988 | 95.527899 | 0.991992800 | 0.807183% | 1.007267743 / 1.001632154 | 99.915344% | 76.198 |
| h2d_node29_pair | 94.028826 | 96.048428 | 0.978973086 | 2.147854% | 0.999947535 / 1.021397502 | 99.991722% | 7.620 |

三项start skew分别约为短单流时间的0.025477%、0.084794%、0.008277%。网络有明确探索退化信号；近端双H2D在这两个节点/NUMA组合下未出现同量级退化。H2D使用每GPU近端NUMA的256MiB registered host buffer，页采样分别为NUMA0/NUMA3的100%；该控制测的是不同NUMA近端供给，不涵盖两流争用同一DRAM bank或跨NUMA host读取。

## 预热、控制与IB计数

预热一次256MiB，计时外完成。网络A/B首次预热分别537.060445ms/513.524650ms，双流预热15.580259ms/15.578419ms；首次communicator实际使用的建立成本由预热吸收。H2D预热5.791026—6.227145ms。随后接收buffer归零并同步，Gloo barrier控制共同起点；正式16次调用每次stream synchronize完成，记录开始与完成区间，NIC后快照先于SHA。每次正式GPU窗口与16个同步调用区间之和的差为16.431—69.772μs，网络三项每流为21.280—25.200μs；这些差包含循环/记录间隙，不能据此拆分硬件kernel占用。

实际NCCL日志同时给出`NET/IB : Using [0]shca_0:1/IB ; OOB ib0`和`via NET/IBext.../GDRDMA` channel证据。接收端`port_rcv_data`按4B/raw-unit转换；`ib0` sysfs rx_bytes按1B/unit单列。双流按共享端口快照区间并集计一次。

| 网络配置 | 正式载荷B | HCA接收delta B | 代表NIC快照窗口ms | ib0接收delta B | idle背景估计占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| single A | 4,294,967,296 | 4,321,543,748 | 99.676689 | 1,968 | 0.0000382% |
| single B | 4,294,967,296 | 4,321,557,744 | 169.485168 | 2,762 | 0.0000805% |
| pair | 8,589,934,592 | 8,643,065,468 | 236.574440 | 3,502 | 0.0000404% |

HCA数据计数覆盖载荷，约高0.619%；网卡字节只有少量OOB/control量级，与实际RDMA数据传输分开记录。H2D各流窗口中的HCA接收delta约1,884—3,620B，同样符合控制流量量级。每case的0.2s idle baseline显示当时背景很小；端口计数仍覆盖全体用户、barrier和协议开销，时间窗口也比GPU窗口多约1ms，不能把计数差全部归因某一GPU流。该证据支持实际选中HCA上的端到端共享路径压力，根因级的NIC饱和仍需另外的归因证据。

## Runtime身份、资源与应用profile

| 项目 | 本轮probe | 当前keeper | 保留SGLang引擎 |
| --- | --- | --- | --- |
| AMD_SERIALIZE_KERNEL | 0 | 0（4份worker spec） | 3（node27源/目标与node29目标spec） |
| GPU可见集 | 0,1 | 0—7 | 0,1 |
| 通信/复制 | 独立进程group `[0,2]`、`[1,3]`；256MiB GPU buffer直接broadcast | 原四worker mesh；source→staging→RCCL→target，按chunk完成同步 | 生命周期/IPC/模型runtime |
| 操作顺序 | 两对独立流受控开始 | 每worker按相同op顺序；disjoint pair可独立推进，共享worker串行 | 按已有模型切换/恢复协议 |
| 内容与就绪 | 最终synthetic buffer全量SHA | 目录/manifest/租约、全量操作验证和publish | 完整权重与模型输出 |

`AMD_SERIALIZE_KERNEL=0`与`3`的差异已按实际进程角色记录：当前keeper自身与probe相同；engine路径保留3。桥接前须固定每个角色的实际值，才能把等待、staging和engine侧恢复时间解释清楚。本轮也保留相同`librccl-net-shca.so`、`NCCL_IB_HCA=shca_0`、`NCCL_SOCKET_IFNAME=ib0`与`HSA_FORCE_FINE_GRAIN_PCIE=1`；group、chunk、GPU可见集和数据路径仍有差异。

4个rank各256MiB synthetic HBM、各256MiB registered host，合计HBM1GiB、host1GiB。buffer建立处各卡物理free变化402MiB，Torch allocation/reserved明确256MiB；差额146MiB保留为该采样区间的runtime/物理净变化，尚未精确分组件。清理后4卡headroom逐字节回到前值，unreclaimed_bytes均0；4个独有PID退出、旧14个可访问keeper、完整DRAM底本、两个parked engine、目录/事务/allocated-reserved与代码/输入hash核对通过。资源记录见[源summary](../calibration_probe_r01/summary.json)。

本轮速度是固定256MiB块、热路径、独立直接GPU通信及近端H2D的完成同步调用有效速度。应用恢复还包含合法来源、DRAM/staging供给、scatter/copy、worker依赖、target准备、验证和publish，因此本轮`application_profile_ready=false`，原始速度不填入`keeper_serial`的完整容量profile，也不作物理最优上界。9配置均只有一次观察；H2D负结果和网络压力信号按这一条件保留。

## 下一阶段最小application bridge建议

建议仅做一组三配置：现有合法DRAM rank0→node29 GPU0单流、rank1→GPU1单流，以及相同两条取数同时参与一个plan。复用当前四keeper的`Cluster.transfer`、既有`serial` executor和相同真实来源；采用专有run/target IDs读取小extent、仅分配有界临时HBM target。完整Qwen底本和parked引擎保持原状态，临时目标逐项验证/退役，原目录epoch/generation/lease和物理headroom前后核对。该桥接同时测到实际DRAM-staged网络供给，直接回答当前论文恢复路径的共享限制能否兑现。

只补完成同步调用所需的同接收host单调时钟事件或外部trace、启动偏差、NIC快照和每stage字节；原`execution.records`已有elapsed_s，现有worker日志为wall-time，补时间证据要单独版本登记。冻结实际source层级、NUMA、chunk、AMD角色值、校验/发布边界，每配置一次，模糊项才一次定向确认。测量前由主agent重新登记临时字节预算和源/目标cleanup契约；这是下一阶段建议，本报告未执行。

若这组应用窗口也通过同口径的overlap/start-skew/载荷门槛并保留退化，才把一个已验证端到端共享group或服务项加入现有profile，随后检查冻结同预算场景的决策是否改变。若worker串行依赖占主导、profile仍不足或决策相同，就保留简单方案及相应功能冻结结果。该入口复用当前执行器，建设量止于测量证据与必要约束，不需要新执行器或全面路径扫描。

离线复算命令（新输出目录；已存在目录不可复用）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/analysis/analyze_calibration.py --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01 --audit codex_coding/results/v0.1.23.3/task2/topology_scenarios/audit_calibration_r01 --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_analysis_r02
```
