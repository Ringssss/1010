# 有限成本独立会话：执行结果

会话 `bounded_cost_runtime_r03` 状态 `completed_pending_independent_review`，supervisor 会话总窗 48.450669s，含初始化与 STOP，不是恢复延时。新正式请求 2 次，训练/查询 AB 均为 0。

| 请求 | service 实测/预测 ms | execute 实测/预测 ms | execute误差 | full filled 实测/预测 ms | filled误差 | published实测 ms |
|---|---:|---:|---:|---:|---:|---:|
| local_novel8 | 0.268654/0.280796 | 3.347495/3.005772 | -10.21% | 23.309653/21.880706 | -6.13% | 46.963841 |
| mixed_novel8 | 组合未作可比单值 | 17.285672/16.287744 | -5.77% | 37.902339/35.162678 | -7.23% | 88.079182 |

误差为（预测−实测）/实测。full filled 是旧训练控制中位数的迁移假说；published 正式预测为 null，校验/发布的字节与目标数成本未建模。mixed 每 op 含等待，不能将 source/target 窗相加冒充纯数据 critical path。

| 请求 | max worker ms | max worker预测误差 | 请求前置query ms | inner query ms | prepareRPC ms | verify/publish ms |
|---|---:|---:|---:|---:|---:|---:|
| local_novel8 | 0.311994 | +6.20% | 5.278811 | 5.254530 | 3.799831 | 23.653308 |
| mixed_novel8 | 14.133330 | -3.68% | 5.282151 | 5.702867 | 3.633559 | 50.175703 |

| 请求 / op | worker / role | 实测 elapsed ms | byte数 |
|---|---|---:|---:|
| local_novel8 / 0 | 4 / both | 0.268654 | 8388608 |
| mixed_novel8 / 1 | 2 / source | 1.131250 | 8388608 |
| mixed_novel8 / 0 | 4 / both | 0.265984 | 8388608 |
| mixed_novel8 / 1 | 4 / target | 0.732100 | 8388608 |
| mixed_novel8 / 2 | 4 / source | 12.914913 | 8388608 |
| mixed_novel8 / 2 | 5 / target | 13.885566 | 8388608 |

两请求及两 bootstrap 共 64MiB 全字节 SHA。每次请求的两个 preregistration 与原模型/迁移绑定 SHA 都保持，freeze_saved_ns 早于首个 request_begin_ns；完整请求、内层 transfer clock 均采用 controller 单调钟。原 worker duration 只作时长比较，不与 controller 绝对时间戳相减。

| node | DRAM 250ms 采样峰 B | startup同HIP HBM绝对上界 B | quiet pids | exact退出 |
|---|---:|---:|---:|---|
| node28 | 3483189248 | 675282944 | 1 | True |
| node30 | 4011237376 | 675282944 | 1 | True |
| node27 | 4409393152 | 675282944 | 1 | True |

共享 cgroup 仅按 node 统计一次；HBM 不把 sysfs baseline 减 HIP 数据。startup 上界和 DRAM 采样不能证明完整连续峰值。九 supervisor rc0、九 child planned STOP→TERM/-15、PID absent/组空；各 node quiet pids1，旧 owner/container、原 XML 与全部 frozen assets 前后保留。详见 summary 的逐进程与保护核验。

最终 Catalog 无有效副本，used/transactions 清零，三 SSD files=0；journal 的 own 对象按 exact epoch/identity/range 回收。


当前 decision_ready=false，未验证布局排序、共享容量或 live 模型 T_ready。解析脚本只读取本地原件，无 SSH/GPU/信号/远端写。
