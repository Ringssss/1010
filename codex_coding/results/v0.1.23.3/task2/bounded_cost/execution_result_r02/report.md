# 有限成本独立会话：执行结果

会话 `bounded_cost_runtime_r02` 状态 `failed`，supervisor 会话总窗 48.923171s，含初始化与 STOP，不是恢复延时。新正式请求 0 次，训练/查询 AB 均为 0。

失败：`RuntimeError('controller failed 1')`。没有完成正式测量请求；具体阶段与原异常保留在 mesh/measure journal，误差与正式请求 SHA 成绩不补造。

| node | DRAM 250ms 采样峰 B | startup同HIP HBM绝对上界 B | quiet pids | exact退出 |
|---|---:|---:|---:|---|
| node28 | 3470200832 | 675282944 | 1 | True |
| node30 | 3974598656 | 675282944 | 1 | True |
| node27 | 4260007936 | 675282944 | 1 | True |

共享 cgroup 仅按 node 统计一次；HBM 不把 sysfs baseline 减 HIP 数据。startup 上界和 DRAM 采样不能证明完整连续峰值。九 supervisor rc0、九 child planned STOP→TERM/-15、PID absent/组空；各 node quiet pids1，旧 owner/container、原 XML 与全部 frozen assets 前后保留。详见 summary 的逐进程与保护核验。

最终 Catalog 无有效副本，used/transactions 清零，三 SSD files=0；journal 的 own 对象按 exact epoch/identity/range 回收。


当前 decision_ready=false，未验证布局排序、共享容量或 live 模型 T_ready。解析脚本只读取本地原件，无 SSH/GPU/信号/远端写。
