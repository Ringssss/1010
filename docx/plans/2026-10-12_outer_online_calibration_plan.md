# 在线控制与权重接管成本：单控制器最小校准

开发编号v0.1.23.3 / task2不变，接续18.35简化两节点分支。用户明确授权继续校准控制/权重接管开销；本轮新增实验边界在任何新测量之前登记。既有历史原件与简单/复杂拓扑方案保持。

**执行状态：已完成。** 四次完整恢复按下列原协议执行，两留出误差remote−0.187%/near−6.036%，73项独审通过；停止本域精度补测。首次完整时间单列3.024893s，预测资格只覆盖同controller的后续恢复。详见[结果与分解](../experiments/2026-10-12_outer_online_calibration_report.md)。以下保留事前设计，不用结果改写原假设和门槛。

## 1. 已有证据与问题

上一轮r02完整Qwen TP2约70.33GB在线恢复和正确性已跑通：C0 3.28749s；Hnear实际1.31220s/预测1.59697s（+21.70%），Hremote实际4.56026s/预测4.95703s（+8.70%）。搬运DAG偏差仅约9.99/7.47ms，大部分误差来自一次C0得到的共同外层残差0.80803s迁移。

同主机时间账显示，模型布局物化约196–205ms、导出约40–43ms基本稳定；请求到第一rank开始为247/81/79ms，rank到达差为217/2.6/0.3ms，keeper映射阶段约62–65/147–164/60–62ms（C0/Hnear/Hremote）。Hnear映射阶段的独立膨胀说明不能只断言“全部是首次开销”。

另外，原C0控制器PID7613，Hnear/Hremote控制器PID9259，引擎和keeper不变。首次恢复与控制器进程切换混在一起。因此本轮统一常驻controller，并明确标注首次和后续状态。

## 2. 只做哪些工作

复用两节点三GPU配置：node27 GPU4/5运行TP2及两个目标keeper，node30 GPU4是唯一sender，两个DRAM源分片位于NUMA4/7。模型、目标66extent、1GiB块/805120688B尾块、serial32MiB chunk、预算/资源门/单映射IPC/正确性与回收保持。

**不重测路径或拓扑。** 精确复用上一轮r02冻结的六路径×两尺寸服务表，逐字节保留checkpoint_path_model及其18个预热/校准原件与SHA。跨新incarnation沿用这些参数是本轮明确待验证的迁移假设，不能把旧样本称新测量。旧outer常数、旧留出结果不参与新拟合。

新会话为`online_outer_two_node_r01`。四次完整在线请求在**同一个controller进程、相同engine与keeper incarnation**中执行：

| 顺序 | 请求 | 用途 |
| --- | --- | --- |
| 1 | C0_prime | 首次真实完整恢复，记录全部在线成本与正确性；不拟合后续outer |
| 2 | C0_cal | 相同混合布局，唯一一次校准后续请求共同outer |
| — | freeze | 两份预测同时冻结，发生在任何新留出之前 |
| 3 | Hremote | 远端来源留出，顺序与上一轮反转 |
| 4 | Hnear | 本地来源留出，检验较短主时延的10%误差 |

四次均恢复完整模型；禁止空目标/假done。C0首1GiB/rank来自HBM，后16GiB来自remote，其余local；Hremote/Hnear首1GiB保持HBM，其余分别全部remote/local。目标工作量和顺序与之前一致。反转两个留出顺序减少固定序号与布局混淆，但一次试验不能唯一识别所有因果。

只有一次prime、一次cal、每留出一次，不默认五轮重复，不自动追加第二个校准常数或更多会话。此前“不追加第三session”属于上一轮r01/r02范围，本轮是用户新授权的独立校准任务，不回写其结果。

## 3. 计时、模型与验证

主时间仍为本次必要inventory/plan之前→全部rank weights_ready且完整resume ACK；在同一controller monotonic时钟计量。引擎materialize、IPC接管、复制、同步、TP barrier均计入。全SHA/参考输出/再次park在ready后且不重入计时；全live原有lease和完成门保持。

C0_prime首次时间独立报告。新模型只声明适用于`post_one_fullrestore_samecontroller`，不声称认证首次启动或任意新控制器。没有独立首次预测验证时，其预测资格保持未知；不能只报预热后时间作为首次恢复收益。

新outer=`C0_cal完整在线时间−旧路径表在当前合法plan上的原生DAG预测`，只计算一次，不按layout分别拟合。参数继承与新context绑定必须显式保存：物理GPU/BDF/NUMA、模型内容/目标布局/NKC、serial配置一致，旧路径校准context/session保持原件，新controller/engine/keeper incarnation另列；不篡改旧原件context来制造同会话证据。

两留出逐场景`abs(predicted−actual)/actual <=10%`，不平均抵消，不看数据后改constant。固定路径参数是否能迁移由新完整验证共同检验；若主要误差再次在路径，报告域迁移未过而不临时重测带宽救门。

保留每次互不重叠的same-node27生命周期窗口、controller PID、请求序号和完成恢复计数，比较prime/cal/两个留出。模型达标后停止精度建设，转向规划方案的主指标价值；未达标时先用现有事实定位，而非扩拓扑或无限重复。

## 4. 资源、分工与产物

目标DRAM192GiB/hard224GiB、初始free52GiB/card/运行48GiBcard上界；helperDRAM96/hard128、initialGPUfree4GiB/livefree2GiB、known/reserved2GiB分开，driver/RCCL完整任务归属unknown。两NUMA触页与注册、helper0.5s采样、原始阶段失败记录及精确清理保持。GPU由主统一启动，原XML/其他服务不改。

outer_analysis负责现有原件归因与新协议独审；outer_core实现单进程sequence和outer参数绑定；outer_ops负责旧路径参数原件的安全导入、新冻结部署及有限监督，主负责审查/预算/启动/验收。写入范围互不重叠。

产物放版本task2/online_cost的新outer目录，包括继承参数包和SHA、注册协议、首次恢复记录、唯一C0_cal、两份冻结预测、两个独立留出、资源/回收和误差分解。正常开发增量，不全历史归档、不覆盖history-chat或发布GitHub。
