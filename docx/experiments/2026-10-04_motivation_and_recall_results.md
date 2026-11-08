# FluidCKPT 动机实验与三节点召回结果

日期：2026-10-04。开发编号：v0.1.23.2。

## 主线

模型暂停后，权重仍有复用价值，原有 HBM 则需要交给新模型和 KV cache。FluidCKPT 把已物化权重交给集群保存，使服务可以让出显存，再从当前副本布局恢复。保存位置决定可用来源，执行方式决定传输代价，分块发布决定迁移中的哪些进展已经可以使用。

这条逻辑对应三个组件：独立所有权的权重对象、恢复代价驱动的放置与取数计划、支持中途召回的分块迁移。本轮分别给出容量动机、执行模型对来源选择的影响和真实迁移中的恢复证据。

## 图与数据

- 总图：[PNG](../../codex_coding/results/motivation/figures/motivation.png)、[PDF](../../codex_coding/results/motivation/figures/motivation.pdf)、[SVG](../../codex_coding/results/motivation/figures/motivation.svg)。
- 来源清单：`codex_coding/results/motivation/figures/sources.json`。
- 物理显存时间线：[PNG](../../codex_coding/results/glm_roundtrip/full_distributed02/lifecycle_hbm.png)、[PDF](../../codex_coding/results/glm_roundtrip/full_distributed02/lifecycle_hbm.pdf)。数据包含引擎/KV与keeper开销，排除node28 GPU0/1。
- 逐点压力数据：`codex_coding/results/motivation/verified_pressure/summary.json`。
- 生命周期：`codex_coding/results/glm_roundtrip/full_distributed02/lifecycle_analysis.json`。

### (a) 本地容量与恢复

横轴为迁出本地 DRAM 的权重比例，纵轴为恢复请求到首个正确 token 的秒数。真实模型为 Qwen3.6-35B-A3B TP2，目标为 node27 GPU0/1。两策略使用相同引擎、处理后权重、完整后备容量和搬运代码；本地策略优先 DRAM，只有缺失范围才能使用本地 SSD，集群策略可使用 node29 的 DRAM。

| 本地保留比例 | 本地 DRAM + SSD | 集群 DRAM | 单次观测比值 |
| --- | ---: | ---: | ---: |
| 100% | 2.113 s | 1.736 s | 两者均只使用本地DRAM |
| 50.38% | 32.617 s | 3.007 s | 10.85× |
| 0% | 67.349 s | 3.214 s | 20.96× |

本地保留减少时，SSD读取量随缺失范围增长；集群DRAM曲线仍保持秒级恢复。SSD使用fsync和文件级FADV_DONTNEED，配套图保存逻辑读取和kernel read_bytes，验证磁盘路径实际参与。

0%和50.38%来自cpu_first03；100%来自修复所有权和NUMA传播后的cpu_first08。每个位置的两策略使用相同物理布局。100%时源集合完全相同，单次耗时差异用于观察运行波动。所有点的32-token输出一致。原始先导local_pressure02完整保留；其中100%点允许LP额外选择SSD，独立用于下一项诊断。

### (b) 执行模型改变来源选择

在完整本地DRAM和完整SSD副本同时可用时，只改变规划器对目标执行的约束，按A-B-B-A顺序进行四次真实恢复。

| 模型假设 | 首token两次观测 | 平均 | 选择的SSD字节 |
| --- | --- | ---: | ---: |
| 不同来源可并行 | 2.582 / 2.562 s | 2.572 s | 498,936,496 B |
| 匹配当前串行目标执行 | 1.780 / 1.782 s | 1.781 s | 0 |

匹配执行方式后平均首token减少30.76%。实际执行器在一个目标上逐项完成copy；此前functional profile缺少serial_target_execution，使LP把低带宽SSD也当成可并行增益。Cluster现在默认包含该约束。35GB CPU回归用例和本次实机配对都验证了来源选择变化。

该结果支持执行代价必须进入取数计划。联合放置相对简单放置的收益，将使用相同执行器的2×2消融单独评估。

原始数据：`codex_coding/results/motivation/serial_cost09/summary.json`。

### (c) 迁移过程中召回

完整GLM-5.3 TP16与真实Qwen TP2在三节点上完成让位、跨节点DRAM迁移、1GiB SSD保存、三次途中召回和最终返回。

| 剩余迁移块完成比例 | GLM恢复首token | 随后Qwen恢复首token |
| --- | ---: | ---: |
| 约25% | 11.840 s | 2.021 s |
| 约50% | 11.517 s | 2.038 s |
| 约75% | 13.222 s | 2.033 s |
| 100% | 11.680 s | — |

此前为Qwen腾出本地DRAM，先迁走GLM rank2的49,013,042,528 B；后续迁移50,086,784,352 B，包含rank3及1GiB SSD。最终node27的GLM DRAM占用达到指定194,978,428,288 B。GLM bootstrap和最终恢复各通过784,208,680,448 B全量SHA，Qwen首次恢复通过70,329,718,112 B校验，全部生成一致。

图使用恢复请求窗口。完整切换还包括前一个模型释放、同步内存采样和结果保存，记录在lifecycle_analysis.json的switches字段；不得把约2秒Qwen恢复当作完整A→B切换时间。该轨迹固定计算设备组，检验数据布局变动下的恢复。

## 故障、修复与数据所有权

cpu_first03在回迁100%点时，node27 CPU owner被OOM杀掉。898个已提交块仍由GPU worker持有memfd映射。通过记录实际FD、inode、长度和原租约，新的owner重新映射240,612,455,808 B，没有复制或重载权重。CPU测试验证同一页面和租约退役语义。

后续保护检查定位到执行错误：prepare把target.numa覆盖为worker默认NUMA。已修复目标NUMA的协议保留与分配传播，并把该方法装入22个空闲worker，分配和数据保持不变。恢复路径按真实空闲页选NUMA，新node27 owner逐次保留1GiB空闲页；剩余92块返迁和100%恢复通过。

最终owner_verify10将完整GLM恢复为可服务状态：首token13.588 s，输出一致，784,208,680,448 B全量SHA通过。证据位于`full_distributed02/owner_recovery01`、`numa_prepare_fix`和`motivation/owner_verify10`。

其他修复包括SSD状态计数的并发更新锁。相关CPU回归27项通过；在实际容器中所有权重建和分配前余量检查2项通过。既有SSD服务运行的旧计数器曾低报committed_bytes，目录完整覆盖、文件大小、SHA和实测kernel读取量分别记录。

## 复现与下一实验

执行脚本在`codex_coding/src/task2/cluster/`：

- `recover_owned_glm_transport.py`
- `continue_glm_ab.py`
- `run_recall_pressure_motivation.py`
- `run_serial_cost_counterexample.py`
- `verify_reconstituted_glm.py`

绘图脚本在`codex_coding/src/diag/`：`plot_glm_migration_recalls.py`、`plot_recall_pressure.py`、`plot_motivation_evidence.py`。所有失败目录保留。

下一实验保持同一数据格式、传输实现和资源预算，交叉比较简单/联合放置与简单/资源约束取数；再加入持续前台负载和可选择目标设备组。它们分别检验联合管理增益、服务干扰和调度灵活性。

本轮是固定设备组、已初始化引擎的探索性测量；压力图每策略每点一次，执行模型每条件两次，生命周期一条真实轨迹。SGLang官方daemon和原生CPU备份的完整同预算策略比较仍使用独立协议。三栏图为独立设计：Meld论文DOI已核对，Figure4的ACM全文端点返回403，原图尚未取得。


## 2026-10-04晚间：已按用户提供PDF核对Figure4

原图已从`/home/zhujianian/fluidckpt/3830418.3843857.pdf`第5页读取并渲染。X为GPU cycles/K，Y为Thread Block ID，展示任务完成不均和阶段等待。新增完整GLM实测fig4_lanes11：X恢复请求后的秒，Y TP rank/目标DCU，数据到齐跨度7.021s、等待53.45% transfer rank-time、首token12.119s，730.35GiB SHA通过。新时间线与解释见[Figure4恢复时间线报告](../experiments/2026-10-04_meld_figure4_restore_timeline.md)。此前403记录为取得本地PDF之前的历史状态。
