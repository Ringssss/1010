# 双流退化独立审查：原观察与同路径复现

编号保持 `v0.1.23.3/task2`。本审查只读本地记录，不启动GPU、HTTP或SSH。两个run的独立计算与保护检查108项通过，新增GPU样本0；本目录JSON使用第一版审查脚本，后续脚本追加了缺日志失败记录和派生summary输入支持。

原观察 `calibration_probe_r01` 的单流独立完成预期168.255970ms，并发235.603975ms，ratio0.714147；同路径复现 `calibration_contention_repro_r01` 的预期172.742962ms、并发237.870026ms，ratio约0.726207。两次独立观察重现同端到端退化。各run三项网络配置分别只有一次观察，16次同buffer传输属于配置内计时迭代。

原summary保留的8条通道节选显示：GPU0对应rank0/2带 `/GDRDMA`，GPU1对应rank1/3不带。完整日志取证进一步记录A两端GDR Enabled、B两端Disabled；见同目录体系下 `diagnosis_static_r01/report.md`。当前接口是GPU tensor broadcast，不能写成两流都采用直接GPU RDMA。旧分析报告的笼统GDR措辞应按流更正，共享退化判断本身不因此失效。

独立复核直接读取各rank的接收端同主机monotonic区间、4GiB/流总字节与source/target最终buffer SHA，不依赖derived速率。NIC计算按实际接收端HCA/port分别取前后快照并集，同一端口只计一次，IB数据原值乘4B。保护验收读取前后keeper目录/事务/分配、parked命令、旧可访问端点以及独有PID/starttime退出与物理HBM余量，未用空cleanup错误列表替代证据。

原同端口A单流43.6124GB/s、pair聚合36.4592GB/s。标称400Gb/s对应50GB/s；A和pair分别约87.22%和72.92%标称payload字节率。端口计数覆盖全部payload只证明路径归属，不证明wire busy或NIC内部队列饱和。一个把36.4592GB/s设为所有并发状态共同恒定capacity的模型还会错误限制A单流，因此该数字只能是当前混合transport双流工作点的有效service。

论文中已获证据支持的资源可定义为：特定有向node27→node29路径、各端HCA/port选择、GPU—NIC transport模式、RCCL版本/插件、256MiB块与完成同步执行条件共同确定的共享service group。先保留单流service与并发组合service，尚不把该group等同唯一物理NIC、PCIe或CPU组件；也不覆盖生产keeper_serial profile。

有效后续对照须各自测matched singleA、singleB、pair，分别计算独立预期。仅B改用shca1会同时改变HCA、PCIe/NUMA路径，可能改变GDR；它能检验路径选择的缓解作用。若要区分GDR切换与路径拆分，可在split条件下仅B禁GDR并确认实际通道模式，保持A模式及CPU配置；仍不能把结果唯一归因NIC实体。既有近端独立NUMA H2D控制不能排除网络host-backed buffer、跨NUMA读取和NIC DMA路径。

停止规则：路径选择与transport证据足以解释已观测工作点和给出可用缓解时停止硬件扩扫描。若仍存在影响论文建模的实际歧义，只补一个可区分假说的变量；唯一硬件根因未定可明确保留。生产共享约束是否需要进入规划，应另按既定最小keeper桥接验证，整模型收益另按e4/e5验收。

复核命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/audit/review_runs.py --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01 --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_contention_repro_r01 --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_audit_initial_r01
```

该输出目录已存在，重跑必须使用新目录。
