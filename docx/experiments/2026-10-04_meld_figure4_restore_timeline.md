# 从 Meld Figure 4 到 FluidCKPT 的恢复时间线

日期：2026-10-04，开发编号 v0.1.23.2。

## 1. 原图的论证方法

用户提供了本地论文 `/home/zhujianian/fluidckpt/3830418.3843857.pdf`。Figure 4 位于 PDF 第5页，印刷页1074。已读取原图、图注和第2.2节正文，原图及文件指纹保存在 `codex_coding/results/motivation/meld_figure4_reference/`。

原图横轴为时间（GPU cycles/K），纵轴为 Thread Block ID。绿色条带表示 split-KV attention block，蓝色条带表示一个序列的 combine。不同任务完成时间与阶段边界共同留下空白区域，图中报告 Total GPU idle: 39%。正文说明该观测使用 ShareGPT、batch size 16、H100。它用执行时间线把“动态负载→完成不均→阶段等待”直接展示出来。

## 2. FluidCKPT 的对应实验

FluidCKPT 图的横轴是控制端发出恢复请求后的秒数，纵轴是 TP rank 及其物理目标 DCU。每条带对应真实恢复操作：绿色为本地DRAM，青色为跨节点DRAM，橙色为SSD供给，灰色为布局构建及准备；斜线区域表示该rank权重已齐、其他rank仍在接收的时间。

模型为完整GLM-5.3，TP16计算布局在node27和node29，后备字节分布在三节点DRAM及node28 SSD。复用已初始化引擎和已发布副本，执行一次完整释放/恢复/生成/全量校验。来源和布局保持不变，无需重新捕获模型。

命令：

```bash
python3 codex_coding/src/task2/cluster/run_restore_lane_trace.py \
  --run-dir /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/glm_roundtrip/full_distributed02 \
  --run fig4_lanes11 \
  --route-result /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/transport_validation/pressure_pairs01/summary.json \
  --resume-run /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/motivation/owner_verify10
```

原始数据在 `codex_coding/results/motivation/fig4_lanes11/`：

- `rank_events.json`：16个rank的模型/keeper事件。
- `operation_events.json`：按本次transaction过滤的8,052条源端和目标端操作事件。
- `flow.json`：实际计划、执行记录、来源字节和资源描述。
- `summary.json`：请求到首token、输出比对、完整SHA、控制端事件窗口及跨节点时钟校准。
- `timeline_analysis.json`：逐rank/逐操作绘图数据与等待指标。
- `trace_validation.json`：每rank字节覆盖、区间顺序及事件一致性检查。

绘图脚本：`codex_coding/src/diag/plot_restore_lane_trace.py`。成图：[PNG](../../codex_coding/results/motivation/fig4_lanes11/restore_lanes.png)、[PDF](../../codex_coding/results/motivation/fig4_lanes11/restore_lanes.pdf)、[SVG](../../codex_coding/results/motivation/fig4_lanes11/restore_lanes.svg)。

## 3. 观测结果

| 事件或指标 | 实测 |
| --- | ---: |
| 开始执行权重传输 | 请求后2.774 s |
| 9个纯本地DRAM rank收完数据 | 请求后3.929–4.067 s |
| 最慢rank收完数据 | 请求后10.950 s |
| 所有rank weights_ready | 请求后10.981 s |
| 首个正确token | 请求后12.119 s |
| 最早与最晚rank数据到齐差 | 7.021 s |
| 权重到齐后等待的总量 | 69.916 rank·s |
| 等待占传输阶段rank-time比例 | 53.45% |
| 全量SHA字节 | 784,208,680,448 B |

9个本地rank为5、6、7、10–15。六个远端供给rank为0、1、2、3、8、9，每rank45.647GiB，合计294,078,255,168B，来源集中在node28。rank4含1GiB SSD供给，其余为本地DRAM，在请求后8.695s收完数据。

计时分母为 `16 × (最晚数据到齐 − 最早传输开始)`；分子为各rank的 `最晚数据到齐 − 本rank数据到齐` 之和。53.45%衡量rank恢复等待，不能与Meld的GPU idle百分比直接比较，也不等于可消除的端到端延迟。

图中操作跨度包括同步等待和传输。跨节点时间采用恢复前后各五次RPC的最小RTT校准，保存所有样本；估计对齐误差为node27 ±8.02ms、node29 ±2.95ms、node28 ±3.69ms。全量SHA在生成后执行，不计入首token。

## 4. 图支持的动机

**集群中的权重都已保住，模型的恢复仍由最晚到齐的必需片段决定。** 本地rank提前七秒完成，不能使TP16模型提前开始服务。优化单条最快路径或提高总体缓存命中率，并不直接对应模型恢复时间。

放置决策因此要同时理解两个层次：哪些rank/片段位于关键路径，以及这些供给是否共享同一个NUMA或网络瓶颈。本次六个远端rank都依赖node28；把等待时间画出来后，下一步需要改变瓶颈资源的实际负载，而非仅追求各条带更整齐。

合理的优化实验是：固定总HBM/DRAM/SSD和每节点预算，让简单策略与联合策略使用相同执行器，比较它们选择保留哪些片段、如何利用不同源节点，以及是否降低最后一个rank的完成时间。若瓶颈在源节点网络的共同割上，仅把相同远端字节均摊到更多rank可能没有收益，这应作为负对照。

## 5. 从动机到三个组件

**独立所有权的权重对象**把可复用权重从计算驻留中分离出来。版本、处理后布局与完整覆盖使A可以让出HBM而继续可恢复。

**恢复瓶颈驱动的放置与取数**把对象目录、目标TP组和共享资源结合起来。它优先减少决定模型完成时间的供给代价，MILP处理片段位置，资源LP处理实际来源分配；执行约束共同决定计划的代价。

**维持覆盖的分块迁移与召回**把选出的布局变为真实数据。新块发布后释放旧空间，请求使用当前已提交布局恢复，使A可以在迁移途中返回，同时为B提供空间。

新时间线具体支撑第二个组件的问题定义；此前的HBM让位和25/50/75%途中召回分别提供第一、第三组件的运行证据。下一轮2×2放置/取数消融负责检验联合决策的增益。

## 6. 本轮状态

fig4_lanes11状态passed，生成与原生参考一致，完整730.35GiB SHA通过，区间与字节检查通过。GLM保持服务，Qwen继续park，既有副本保留。最新运行状态由 `full_distributed02/active_session.json` 指向本轮session，后续操作需使用新session。
