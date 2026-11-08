# e1–e6 实验分析快照

原始运行只读；本报告不替代正式实验总记录。正的节省表示加速，负值表示变慢。

输入在分析期间保持不变：PASS。

## e1 / e1_c02_qwen_ipc_r01

执行状态：`passed`；科研状态：`baseline_functional_gate_passed`。

原始结果：[summary.json](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e1/e1_c02_qwen_ipc_r01/summary.json)。

生成正确性：PASS；完整性：PASS。

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
- CPU preservation checks metadata, not a post-run byte rehash
- disk fingerprints alone do not certify code already loaded into persistent processes
