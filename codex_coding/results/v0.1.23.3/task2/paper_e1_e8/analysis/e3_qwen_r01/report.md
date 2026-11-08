# e1–e6 实验分析快照

原始运行只读；本报告不替代正式实验总记录。正的节省表示加速，负值表示变慢。

输入在分析期间保持不变：PASS。

## e3 / e3_c02_qwen_r01

执行状态：`passed`；科研状态：`exploratory_primary_metric_evidence`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e3/e3_c02_qwen_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

有效测量：4/4；引用别名：0；预算合规：PASS；清理：PASS。

| 布局 | 目标 | 取数 | 预算GiB | 实占缓存GiB | T_ready(s) | 生成 | 全量校验 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| concentrated | target_a | resource_lp | 8.000000 | 8.000000 | 1.277977 | PASS | PASS |
| concentrated | target_b | resource_lp | 8.000000 | 8.000000 | 3.798325 | PASS | PASS |
| distributed | target_b | resource_lp | 8.000000 | 8.000000 | 2.624751 | PASS | PASS |
| distributed | target_a | resource_lp | 8.000000 | 8.000000 | 1.237442 | PASS | PASS |

| 对照 | 对照均值(s) | 候选均值(s) | 节省(s) | 改善% | 完成 | 同布局 |
| --- | --- | --- | --- | --- | --- | --- |
| distributed_vs_concentrated | 2.538151 | 1.931097 | 0.607054 | 23.917190 | PASS | 未通过/不完整 |

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
