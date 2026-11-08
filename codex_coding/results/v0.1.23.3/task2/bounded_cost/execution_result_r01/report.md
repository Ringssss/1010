# 有限成本独立会话：执行结果

会话 `bounded_cost_runtime_r01` 状态 `failed`，supervisor 会话总窗 19.565762s，含初始化与 STOP，不是恢复延时。新正式请求 0 次，训练/查询 AB 均为 0。

失败：`RuntimeError('controller failed 1')`。`mesh_controller/stderr.txt` 首次 `Cluster.inventory()` 报 `worker identities do not match endpoint order`。startup 的六个 `worker` 均为 null；mesh 尚未执行，编号需由 mesh 初始化。新 runner 把原先 mesh 后的 Cluster 级 inventory 提前，触发调用顺序错误。

未 warm、未建 synthetic fixture、未迁移绑定模型、未冻结两预测、未执行 holdout。误差、request clocks、SHA 成绩均为空，不能将原训练或 CPU 预测代入。清理的 Cluster 级 final inventory 同样因未初始化 worker rank 而失败，原 journal 保留该错误。

| node | DRAM 250ms 采样峰 B | startup同HIP HBM绝对上界 B | quiet pids | exact退出 |
|---|---:|---:|---:|---|
| node28 | 3253780480 | 675282944 | 1 | True |
| node30 | 3261640704 | 675282944 | 1 | True |
| node27 | 3281494016 | 675282944 | 1 | True |

共享 cgroup 仅按 node 统计一次；HBM 不把 sysfs baseline 减 HIP 数据。startup 上界和 DRAM 采样不能证明完整连续峰值。九 supervisor rc0、九 child planned STOP→TERM/-15、PID absent/组空；各 node quiet pids1，旧 owner/container、原 XML 与全部 frozen assets 前后保留。详见 summary 的逐进程与保护核验。

当前 decision_ready=false，未验证布局排序、共享容量或 live 模型 T_ready。解析脚本只读取本地原件，无 SSH/GPU/信号/远端写。
