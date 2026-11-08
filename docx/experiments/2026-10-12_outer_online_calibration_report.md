# 在线控制与权重接管成本：单控制器校准结果

开发编号v0.1.23.3 / task2；接续18.35简化两节点分支。对应[事前计划](../plans/2026-10-12_outer_online_calibration_plan.md)。真实会话`online_outer_two_node_r01`已完成：远端/本地留出误差分别−0.187%/−6.036%，均通过整体10%门，独审73项全过，资源已回收。适用范围为当前登记配置中同一常驻控制器完成首次恢复后的后续请求。

## 1. 为什么只校准外层

上轮simple r02的本地/远端恢复预测高估284.77/396.77ms，其中搬运DAG偏差只有9.99/7.47ms，其余96.49%/98.12%位于共同外层剩余。已有原件分析`outer_analysis_existing_r04`30项通过：布局物化196–205ms、导出40–43ms较稳定，变化主要在请求到达、rank启动错开及IPC映射；Hnear单独映射膨胀约100ms，不能全称首次开销。

更关键的混杂是旧C0 controller PID7613，Hnear/Hremote PID9259，而engine/keepers不变。本轮四个完整请求贯穿同一controller，显式绑定engine与keeper incarnation。首次C0_prime单列完整时延，不把它隐藏为无成本预热；只有其后的相同混合布局C0_cal校准共同outer，随后先Hremote再Hnear反序验证。

新模型只声明`post_one_fullrestore_samecontroller`，首次/任意新进程预测资格仍未知。单次first prime无法独立证明具体微观首次机制，若验证成功仅说明该明确运行域中的校准可迁移。

## 2. 复用范围和数据隔离

硬件与模型保持两节点三GPU、Qwen TP2、70,329,718,112B完整权重、66个extent/2096个32MiB chunk。node27GPU4/5为目标、node30GPU4为单sender，remote两分片DRAM NUMA4/7。预算和安全准入保持此前简化协议。

不重测路径与拓扑。继承r02 `checkpoint_path_model.json`，SHA `04febed84e3cda9fd937cf8aa5d958ab530832305919861b5dbbed7e2db42b1a`；六路径×两个实际尺寸服务表SHA `58eaacf941ce431e42f4b44ce2500138f962a882fc55d60097f7020eafdae644`。`outer_ops_source_bundle_r01`仅checkpoint＋18条原始warm/cal文件，19件共14,982,843B，逐字节独审与原tar一致，无旧C0或留出文件。旧model.json只用于分析，不进入新拟合。

新incarnation沿用固定路径参数是明确迁移假设：旧context/session原件保持，新clock/当前sourceepoch与运行绑定另列，所有物理角色、路径、内容/目标/NKC必须相同。旧outer常数不带入；新C0_cal只拟合一次，两个留出提前同时冻结、不得回填。

## 3. 实现与部署

outer_core新增outer_model/outer_controller/CPU检查；outer_ops新增outer_bundle/outer_prepare/outer_host，原HostOps仅添加默认保持`bootstrap/calibrate/validate`的RUN_PHASES钩子，Outer改为`bootstrap/sequence`后cleanup。sequence单次进程完成四请求，bootstrap/init/cleanup可以是独立进程但不冒充在线控制器保持。原simple和产品执行器/评分器不变。

核心41项、ops31项CPU检查通过；设计独审14项、具体冻结16项通过。继承包白名单只读regular文件、拒绝链接/重复/哈希不符，最大单件4MiB/合计32MiB，不使用extractall或写原历史目录。初期分析夹具键名/生命周期fixture错误和Python hashlib兼容失败分别保留，没有新GPU数据被覆盖。

`execution_setup_outer_two_node_r01`冻结179资产（含19旧参数原件）。新容器：

- node27 CID `fd8fcf443d1a443d88cfdd8f8d3f628e481d4812075c8a72397b999642cfaca3`，StartedAt `2026-10-12T13:16:23.181436075Z`。
- node30 CID `09fd630f233cc8b26cb83a5318a7cdfa53fbbc68d8cf911f5c8d02c22edf5814`，StartedAt `2026-10-12T13:16:25.107401543Z`。

`execution_host_outer_admission_r01`实际目标每卡free68,365,058,048B，辅助卡free7,377,780,736B，GPU身份/NUMA/主机余量通过。helper完整driver/RCCL归属仍unknown，只检查实际free、已知tensor与Torchreserved；采样不能当连续峰值。

主绑定`execution_host_outer_bind_r01`并启动`execution_host_outer_run_r01`。native mesh/warm、模型加载、保活副本建立及四次完整恢复全部完成，原件已从两节点归集。以下为最终验收结果。

## 4. 完整在线结果与冻结证据

计时从控制器必要inventory/plan之前开始，到完整resume ACK且所有rank权重可用结束。在线必要的物化、IPC接管、复制、同步、barrier均保留；全量SHA、固定参考输出检查及再次物理暂停在ready之后执行。准备模型和创建保活源属于另列bootstrap，不能与本次恢复时间混算。

2026-10-23按源码补充精确边界：`simple_controller.py:200`的恢复前park证据、资源门和command控制文件发布发生在`begin`之前；本次10%资格对应从后续inventory之前起计的登记窗口。接入规划器比较完整请求时，应统一审核这些每请求必要项的归属，不把排除项写成0成本，也不回写本次冻结结果。逐函数过程、七段原始时间和rank级等待见[详细过程专题](../../code_building/process_docs/v0.1-init-project/v0.1.23.3-task2_online_cost_calibration_detailed_20261023.md)。

| 顺序/请求 | 实际完整在线时间（s） | 事前预测（s） | 有符号相对误差 | 用途与结果 |
| --- | ---: | ---: | ---: | --- |
| 1 / C0_prime | 3.024892887 | — | — | 首次恢复完整记录，不拟合 |
| 2 / C0_cal | 2.903039095 | — | — | 唯一外层校准，完整正确性通过 |
| 3 / Hremote | 4.581127216 | 4.572578937 | −0.186598% | 独立留出，10%通过 |
| 4 / Hnear | 1.290400136 | 1.212516366 | −6.035629% | 独立留出，10%通过 |

相对误差定义为`(预测−实际)/实际`，准入使用绝对值，不平均两布局误差。两份预测在C0_cal完成后同时冻结；先remote再near，与上一轮反序。相同控制器PID7613贯穿四次请求，engine、keeper身份一致；身份限定在本次run、容器ID/StartedAt/boot和bound plan内，与历史容器相同数字PID没有跨会话连续性的含义。

仅一次C0_cal识别共同外层：`2.903039095 − 2.4794586711796 = 0.4235804238204s`，此前为0.8080300058204s。六路径×两个尺寸的全部12成本点逐值不变，没有新路径采样，没有利用prime或新旧留出拟合，没有按布局增加常数，也没有第五次恢复。

冻结预测payload SHA为`d2b4f7f7c353d5a299fa6dc7e428180417a910eb9df871369c64076625a79db0`；worker incarnation摘要为`4e7b6f9f93a6b28e04af57238c414bca1a8f69f46b1a2a9411f2bb04344fb090`。新bound plan SHA为`5e6c2dd6a61dd6a8aa5e9cf973dc85951f6a80d2cf9b03a45cd1f1f8ec0896f4`。这些字段与注册、模型、实际原件由独审重新交叉核验。

Hnear快于Hremote的排序正确：预测差3.360062571s，实际差3.290727080s。它说明成本模型可以分辨本次来源布局；两者仍为事前指定布局，这个差值不能记为联合规划器收益。共同outer的重新校准本身也不改变候选间的预测差值，主要改善绝对时延估计与可行性判断。

## 5. 误差具体改善在哪里

下表是同node27单调时钟重建的窗口。rank到达差属于前复制时间的内部结构；各rank内部阶段重叠，不能把所有窗口与DAG重复相加。跨run仅比较持续时长，不相减跨run或跨节点的绝对时间戳。

| 窗口（ms） | 旧C0 | 新prime | 新cal | 新Hremote | 新Hnear |
| --- | ---: | ---: | ---: | ---: | ---: |
| 请求→第一个rank开始 | 247.042 | 147.765 | 79.711 | 81.456 | 80.429 |
| 两rank开始时刻差 | 216.691 | 36.107 | 0.994 | 3.984 | 0.508 |
| 最后rank开始→最后导出 | 247.529 | 239.933 | 251.416 | 254.057 | 246.895 |
| 最后导出→最后IPC映射完成 | 90.831 | 87.498 | 83.086 | 90.808 | 170.178 |
| 最后映射→首次execute | 2.893 | 2.515 | 3.263 | 3.311 | 3.334 |
| 最后复制完成→控制器ACK | 11.329 | 10.267 | 10.974 | 8.006 | 8.763 |

| 新留出误差分解（预测−实际，ms） | Hremote | Hnear |
| --- | ---: | ---: |
| 搬运DAG与实际最长worker窗之差 | +9.339 | +8.380 |
| 共同外层与实际剩余之差 | −17.888 | −86.264 |
| 完整在线总偏差 | −8.548 | −77.884 |

本轮支持的解释是：此前C0外层代表的状态不适合作为后续请求的统一常数；明确同控制器和首次/后续域后，一次后续校准可以迁移到这两个留出。首次请求到达和rank错开明显较大，但单会话结果不足以将所有差额唯一归因某一个first-call内部机制。

Hnear仍有单独IPC接管开销：两个rank的accepted→mapped约134.47/149.29ms，cal约57.56/57.59ms，remote约63.04/63.31ms。反转顺序后它仍存在，不能简单解释为“第二次恢复还没热起来”。本次其影响落在整体6.04%误差内；依据用户10%目标，保留证据而停止微项调优。若后续候选只差数十毫秒，再按决策需要补测，当前无需为此扩模型。

## 6. 正确性、预算与完整退出

四次每次均真实恢复70,329,718,112B、66extent，包含2096个32MiB chunk（末块不足32MiB按实际字节）。每次完整SHA、固定参考输出、再次物理park均通过；park同时要求两rank各自allocated下降及整卡free增长达到逻辑权重的90%。远端两个完整NUMA4/7分片实际触页并注册，源epoch/lease一致，单sender顺序通过，不把原有DRAM影子或RPC返回冒充显存物理释放。

helper共1846个0.5s配置采样，最低free6,958,350,336B，最大PyTorchreserved69,206,016B，符合登记门。完整driver/RCCL任务归属仍unknown；观测极值不是连续峰值。目标预算、源NUMA、源生命周期及ready资源检查均过。

host `run_summary.json`为passed=true/failure=null/cleanup_failure=null。最终Catalog为空、无事务，三个keeper和engine的精确owned closure全部通过；两节点归集完成且各仅PID1。主随后准确停止本轮两个容器，`outer_owned_stop_r01/node{27,30}_after.json`均Running=false/PID0/OOMKilled=false。退出137是限时docker stop处理sleep PID1，不是OOM。本任务无后台GPU实验，未操作其他服务或原共享XML。

独审首轮`outer_analysis_actual_r01/failure.json`记录主机Python不支持hashlib.file_digest的工具兼容错误。只将审计SHA计算改为分块读取，新`outer_analysis_actual_r02`73项全过；实验原件及模型未改，没有为审计失败追加GPU实验。

## 7. 证据位置、命令与下一步

以下路径均相对仓库根。结果前缀为`codex_coding/results/v0.1.23.3/task2/online_cost/`：

- `execution_host_outer_run_r01/collection_node27.tar`与`collection_node30.tar`：全部实际原件，SHA分别`85af40e10134dc2e35bd38956afab17bd4f483959c10da3db4ef531b1f1ca602`和`32f6a170133c2f6e8ac602c2f27b083dc03394dc05767f4e9276480bd4808860`。
- `collection_node27.tar`内直接是`phase_sequence/`：四请求JSON/raw/record/physical_park、注册、继承模型、唯一校准、冻结预测、两留出与independent_validation。归集前这些位于运行目录`execution_runtime_online_outer_two_node_r01/`下；tar成员不带该前缀。读取归集原件，不依赖已停止容器。此处路径说明于2026-10-23归档核验时纠正，原件未改。
- `outer_analysis_actual_r02/{report.md,qualification.json,breakdown.json}`：73项独审、误差及同主机时间账；`outer_analysis_existing_r04/`保存旧误差归因。
- `execution_setup_outer_two_node_r01/`、`execution_host_outer_{setup,admission,bind}_r01/`：179资产冻结、实际准入、精确容器身份及运行计划。
- `outer_ops_source_bundle_r01/`与`outer_ops_cpu_r01/commands.txt`：19件继承包和setup/admission/bind/run完整命令；`outer_owned_stop_r01/`为最终容器退出证据。

【本轮关键命令】已完成，不能原名重跑；新实验须新run/输出目录。

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/outer_host.py run --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_outer_two_node_r01 --plan codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_outer_bind_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_outer_run_r01 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/outer_actual_review.py --run-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_outer_run_r01 --plan codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_outer_bind_r01/launch_plan.json --old-run-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_run_r02 --old-breakdown codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/breakdown.json --out codex_coding/results/v0.1.23.3/task2/online_cost/outer_analysis_actual_r02
python3 codex_coding/src/v0.1.23.3/task2/paper_e1_e8/check_documents.py
```

当前可以停止本域精度建设，回到同预算的布局决策：固定有区分度的候选场景，对比强简单策略和已有规划器的选择，再比较实际权重可用时延；必要在线规划与准备开销按相同协议计入。现有证据没有自动认证任意新controller、首次恢复、新尺寸、三节点/SSD/多NIC共享拓扑。资源恢复后扩域时沿用同一验证方式，仅补变化路径和组合，而不从头建设。当前本轮未改产品加载算法，没有新的端到端加速主张。
