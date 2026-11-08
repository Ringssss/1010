# e1–e6 实验分析快照

原始运行只读；本报告不替代正式实验总记录。正的节省表示加速，负值表示变慢。

输入在分析期间保持不变：PASS。

## e1 / e1_c02_qwen_ipc_r01

执行状态：`passed`；科研状态：`baseline_functional_gate_passed`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e1/e1_c02_qwen_ipc_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

海光 SGLang HBM 常驻 HIP IPC 适配基线；eager warm engine；非上游原版 daemon 或新进程启动成绩。

owner 内容校验：PASS；owner 回收验收：PASS。

| 循环 | 同机 T_ready(s) | 协调端 ready 确认(s) | fresh export(s) | 首token(s) | 生成 |
| --- | --- | --- | --- | --- | --- |
| 0 | 0.160595 | 0.191284 | 0.008518 | 0.768863 | PASS |
| 1 | 0.153792 | 0.168561 | 0.009679 | 0.748404 | PASS |

| 循环 | 主机 | 主机内权重窗口(s) |
| --- | --- | --- |
| 0 | m03r2n07 | 0.160595 |
| 1 | m03r2n07 | 0.153792 |

跨主机的协调确认窗口包括导出、RPC 和返回确认；不冒充跨机器统一时钟下的权重窗口。

1 秒资源采样（包含同设备其他进程；采样最大值不等于精确峰值）：

| 节点 | 窗口内样本 | 同时总HBM最大GiB | 较窗口前样本增加GiB | 新增OOM kill |
| --- | --- | --- | --- | --- |
| node27 | 98 | 83.059074 | 65.501293 | 0 |
| node28 | 98 | 115.360165 | 0.000000 | 0 |
| node29 | 97 | 11.891327 | 0.000000 | 0 |

关联窗口：recorded coordinator event envelope; initial preflight may precede it。主机间 wall clock 误差未校准。

测量边界：

- two cycles validate lifecycle; they are not a performance confidence interval
- cross-host coordinator acknowledgment includes export, RPC and final acknowledgment; not a global clock subtraction
- preserved DRAM/SSD source checks compare metadata, not a post-run byte rehash
- disk fingerprints alone do not certify code already loaded into persistent processes

## e3 / e3_c02_qwen_r01

执行状态：`passed`；科研状态：`exploratory_primary_metric_evidence`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e3/e3_c02_qwen_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

独立有效测量：4；逻辑配置覆盖：4/4；引用别名：0；预算合规：PASS；清理：PASS。

| 布局 | 目标 | 取数 | 预算GiB | 实占缓存GiB | T_ready(s) | 生成 | 全量校验 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| concentrated | target_a | resource_lp | 8.000000 | 8.000000 | 1.277977 | PASS | PASS |
| concentrated | target_b | resource_lp | 8.000000 | 8.000000 | 3.798325 | PASS | PASS |
| distributed | target_b | resource_lp | 8.000000 | 8.000000 | 2.624751 | PASS | PASS |
| distributed | target_a | resource_lp | 8.000000 | 8.000000 | 1.237442 | PASS | PASS |

| 对照 | 对照均值(s) | 候选均值(s) | 节省(s) | 改善% | 完成 | 同布局 |
| --- | --- | --- | --- | --- | --- | --- |
| distributed_vs_concentrated | 2.538151 | 1.931097 | 0.607054 | 23.917190 | PASS | 否（布局不同） |

1 秒资源采样（包含同设备其他进程；采样最大值不等于精确峰值）：

| 节点 | 窗口内样本 | 同时总HBM最大GiB | 较窗口前样本增加GiB | 新增OOM kill |
| --- | --- | --- | --- | --- |
| node27 | 327 | 91.789856 | 74.236389 | 0 |
| node28 | 327 | 115.360165 | 0.000000 | 0 |
| node29 | 327 | 83.406094 | 66.229645 | 0 |

关联窗口：recorded coordinator event envelope; initial preflight may precede it。主机间 wall clock 误差未校准。

测量边界：

- one observation per policy/target; differences are exploratory, not a statistical ranking
- same budget means equal permitted extra HBM plus equal charged DRAM base; actual selected HBM is also reported
- target allocations, runtime memory and unrelated owners coexist; high-water verification is separately flagged
- identical physical layouts share measurements; aliases do not increase sample counts
- zero and negative improvement points are retained

## e4 / e4_c01_qwen_r01

执行状态：`passed`；科研状态：`exploratory_primary_metric_evidence`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

独立有效测量：2；逻辑配置覆盖：8/8；引用别名：6；预算合规：PASS；清理：PASS。

| 布局 | 目标 | 取数 | 预算GiB | 实占缓存GiB | T_ready(s) | 生成 | 全量校验 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 8gib_greedy | target_a | fastest | 8.000000 | 8.000000 | 1.307431 | PASS | PASS |
| 8gib_greedy | target_b | fastest | 8.000000 | 8.000000 | 2.483472 | PASS | PASS |

| 对照 | 对照均值(s) | 候选均值(s) | 节省(s) | 改善% | 完成 | 同布局 |
| --- | --- | --- | --- | --- | --- | --- |
| 8GiB_placement_at_fastest | 1.895451 | 1.895451 | — | — | PASS | 是（布局相同） |
| 8GiB_placement_at_resource_lp | 1.895451 | 1.895451 | — | — | PASS | 是（布局相同） |
| 8GiB_routing_at_greedy | 1.895451 | 1.895451 | — | — | PASS | 是（布局相同） |
| 8GiB_routing_at_joint | 1.895451 | 1.895451 | — | — | PASS | 是（布局相同） |
| 8GiB_combined | 1.895451 | 1.895451 | — | — | PASS | 是（布局相同） |

1 秒资源采样（包含同设备其他进程；采样最大值不等于精确峰值）：

| 节点 | 窗口内样本 | 同时总HBM最大GiB | 较窗口前样本增加GiB | 新增OOM kill |
| --- | --- | --- | --- | --- |
| node27 | 169 | 83.797722 | 65.523502 | 0 |
| node28 | 170 | 115.360165 | 0.000000 | 0 |
| node29 | 169 | 91.413864 | 73.523426 | 0 |

关联窗口：recorded coordinator event envelope; initial preflight may precede it。主机间 wall clock 误差未校准。

测量边界：

- one observation per policy/target; differences are exploratory, not a statistical ranking
- same budget means equal permitted extra HBM plus equal charged DRAM base; actual selected HBM is also reported
- target allocations, runtime memory and unrelated owners coexist; high-water verification is separately flagged
- identical physical layouts share measurements; aliases do not increase sample counts
- zero and negative improvement points are retained

## e5 / e5_c01_qwen_decision_r01

执行状态：`decision_only`；科研状态：`no_decision_increment`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e5/e5_c01_qwen_decision_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

科研结论：本场景没有新增放置或取数决策；引用已有 e4 正确性证据，新增 GPU 测量为 0。

引用源：[e4 summary](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json)，指纹核对：PASS。

| 策略 | 取数 | 目标 | 引用phase | 历史T_ready(s) |
| --- | --- | --- | --- | --- |
| greedy | fastest | target_a | paper_e4_c01_qwen_r01_8gib_greedy_target_a_fastest | 1.307431 |
| greedy | fastest | target_b | paper_e4_c01_qwen_r01_8gib_greedy_target_b_fastest | 2.483472 |
| joint | resource_lp | target_a | paper_e4_c01_qwen_r01_8gib_greedy_target_a_fastest | 1.307431 |
| joint | resource_lp | target_b | paper_e4_c01_qwen_r01_8gib_greedy_target_b_fastest | 2.483472 |

这些引用不增加样本数，也不构成新的性能提升结论。

1 秒资源采样（包含同设备其他进程；采样最大值不等于精确峰值）：

| 节点 | 窗口内样本 | 同时总HBM最大GiB | 较窗口前样本增加GiB | 新增OOM kill |
| --- | --- | --- | --- | --- |
| node27 | 2 | 18.285980 | 0.000000 | 0 |
| node28 | 2 | 115.360165 | 0.000000 | 0 |
| node29 | 1 | 17.902115 | 0.000000 | 0 |

关联窗口：recorded coordinator event envelope; initial preflight may precede it。主机间 wall clock 误差未校准。

测量边界：

- same placement and executable routes imply no decision increment in this registered scenario
- referenced timings are existing e4 observations, not new e5 measurements
- identical transfer decisions do not prove identical planning overhead or end-to-end performance

## e6 / e6_c01_qwen_curve_r01

执行状态：`passed`；科研状态：`exploratory_primary_metric_evidence`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

独立有效测量：8；逻辑配置覆盖：12/12；引用别名：4；预算合规：PASS；清理：PASS。

| 布局 | 目标 | 取数 | 预算GiB | 实占缓存GiB | T_ready(s) | 生成 | 全量校验 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0gib_greedy | target_a | fastest | 0.000000 | 0.000000 | 1.269331 | PASS | PASS |
| 0gib_greedy | target_b | fastest | 0.000000 | 0.000000 | 2.770961 | PASS | PASS |
| 8gib_greedy | target_a | fastest | 8.000000 | 8.000000 | 1.292621 | PASS | PASS |
| 8gib_greedy | target_b | fastest | 8.000000 | 8.000000 | 2.501615 | PASS | PASS |
| 16gib_greedy | target_a | fastest | 16.000000 | 16.000000 | 1.291710 | PASS | PASS |
| 16gib_greedy | target_b | fastest | 16.000000 | 16.000000 | 2.348826 | PASS | PASS |
| 16gib_joint | target_a | resource_lp | 16.000000 | 16.000000 | 1.267927 | PASS | PASS |
| 16gib_joint | target_b | resource_lp | 16.000000 | 16.000000 | 2.355012 | PASS | PASS |

| 对照 | 对照均值(s) | 候选均值(s) | 节省(s) | 改善% | 完成 | 同布局 |
| --- | --- | --- | --- | --- | --- | --- |
| 0GiB_joint_vs_greedy | 2.020146 | 2.020146 | — | — | PASS | 是（布局相同） |
| 8GiB_joint_vs_greedy | 1.897118 | 1.897118 | — | — | PASS | 是（布局相同） |
| 16GiB_joint_vs_greedy | 1.820268 | 1.811470 | 0.008798 | 0.483358 | PASS | 否（布局不同） |

0 GiB 原点已测：PASS。

| 布局 | 预算GiB | 均值(s) | 相对原点节省(s) | 相对原点改善% | 测量别名 |
| --- | --- | --- | --- | --- | --- |
| 0gib_greedy | 0.000000 | 2.020146 | 0.000000 | 0.000000 | 0gib_joint/resource_lp |
| 8gib_greedy | 8.000000 | 1.897118 | 0.123029 | 6.090081 | 8gib_joint/resource_lp |
| 16gib_greedy | 16.000000 | 1.820268 | 0.199879 | 9.894259 |  |
| 16gib_joint | 16.000000 | 1.811470 | — | — |  |

1 秒资源采样（包含同设备其他进程；采样最大值不等于精确峰值）：

| 节点 | 窗口内样本 | 同时总HBM最大GiB | 较窗口前样本增加GiB | 新增OOM kill |
| --- | --- | --- | --- | --- |
| node27 | 686 | 83.828995 | 65.543015 | 0 |
| node28 | 685 | 115.360165 | 0.000000 | 0 |
| node29 | 686 | 99.445187 | 81.543072 | 0 |

关联窗口：recorded coordinator event envelope; initial preflight may precede it。主机间 wall clock 误差未校准。

测量边界：

- one observation per policy/target; differences are exploratory, not a statistical ranking
- same budget means equal permitted extra HBM plus equal charged DRAM base; actual selected HBM is also reported
- target allocations, runtime memory and unrelated owners coexist; high-water verification is separately flagged
- identical physical layouts share measurements; aliases do not increase sample counts
- zero and negative improvement points are retained
