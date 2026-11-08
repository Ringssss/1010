# 三节点六 worker 实际运行：部署与监督复核

本轮只读复核实际 session `multitier_runtime_r02`。父运行控制链 75.6088 秒完成；9 个监督器返回0，三节点收集通过。SSD_only、C0、C1、C3各恢复32MiB完整SHA通过。

| 节点 | DRAM观察最大 GiB | DRAM余量 MiB | 同HIP口径HBM观察上界 GiB | HBM绝对上界余量 MiB | 终态cgroup MiB |
|---|---:|---:|---:|---:|---:|
| node28 | 4.9247 | 1101.1 | 1.7793 | 226.0 | 14.99 |
| node30 | 5.0522 | 970.5 | 1.4336 | 580.0 | 15.24 |
| node27 | 4.0283 | 2019.0 | 1.7266 | 280.0 | 12.97 |

DRAM按同一个容器cgroup计费，两个worker和SSD监督器的采样合并取最大，不能相加。每个监督器299次、约250ms采样；所有节点观察值加256MiB均低于6GiB。kernel没有memory.peak文件，观察最大是采样下界。

HBM重新用HIP total−free口径计算整个两卡绝对占用，postwarm值加同口径运行free下降得到上表观察上界；其中也包含已有占用。三节点均小于2GiB。node28上界余226MiB，因此这个绝对上界不能独立证明256MiB任务增量余量。父原准入在28/30使用sysfs used扣除旧基线，它与HIP total−free不是同一计数口径，只保留为增量估计，原始准入与运行结果不改写。node27基线扣0是保守绝对占用。2秒HBM监测也不能证明完整物理峰值。

9个child由匹配STOP触发精确TERM退役，child返回−15；没有KILL、超时或身份变更。不是9个child自然返回0。独立收尾读取确认这些PID不存活且原process group为空，三容器保留sleep进程，pids.current=1，oom/oom_kill/max均0。旧容器、node28的13个保护owner、原XML/default mapping及源码stage哈希保持。host原厂XML此前即不存在，记None；不推断容器namespace中该原厂文件的保护状态。

六worker日志均记录加载各自任务input XML，未见默认mapping fallback。w0至w4实际NET channel观察IBext_v8、shca_0:1/IB、ib0；w5只有必要的同node45 pair，实际channel为P2P/IPC，不当作跨nodeNET证据。对raw dump沿用已验证dump_parse_view，仅在本目录生成派生XML，删除GPU/NET匿名空属性token，原件保持。六份GPU BDF/完整PCI链/NUMA/affinity/gdr0与所选HCA链验证通过；每份是某个必要pair的局部rank dump，不能冒称全部15个可能pair路径均实测。部分C日志被交错输出及退役截断而无COMPLETE行，精确START设备身份结合成功mesh/warm/路径数据确认，不补造日志。

源码SHA与真实stage/mesh/qualify/cases记录一致。本报告是部署、已观察预算和精确清理证据；完整peak、冷磁盘与真实模型T_ready仍未认证。结构化入口：`operations_result.json`；`w0`至`w5.transport.json`及对应parse_view文件提供实际设备路径原件引用。
