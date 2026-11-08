# e1–e6 实验分析快照

原始运行只读；本报告不替代正式实验总记录。正的节省表示加速，负值表示变慢。

输入在分析期间保持不变：PASS。

## e4 / e4_c01_qwen_r01

执行状态：`passed`；科研状态：`incomplete_or_failed_matrix`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json)。

生成正确性：PASS；完整性：未通过/不完整。

有效测量：2/-2；引用别名：6；预算合规：PASS；清理：PASS。

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
