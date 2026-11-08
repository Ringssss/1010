# 基础场景历史证据离线审计

开发编号：v0.1.23.3/task2。证据类型为 historical_reference；新增GPU恢复与性能样本均为0。原run、方法身份和失败边界保留，历史结果不命名为Janus。

## S1–S5覆盖

| 场景 | 状态 | 已有证据 | 最小缺口 |
| --- | --- | --- | --- |
| S1 目标HBM完整权重 | covered_mechanism | 完整Qwen HIP IPC两循环已通过，是历史硬件适配机制证据。 | 原版SGLang daemon未验收；引用不能记为新样本。 |
| S2 CPU保活恢复 | covered_common_executor | e6 0GiB额外HBM用完整DRAM底本恢复；e4强简单与joint同决策，e5无新增恢复。 | 此为Fluid共同执行器历史host恢复，不改名Janus warm，也未比较Janus原版。 |
| S3 活跃复制 | unmeasured | e1独立keeper owner与e3局部HBM缓存不构成活跃服务实例D2D复制。 | 固定活跃源、服务干扰与源保护协议后才有功能/性能证据。 |
| S4 零散空间 | partial_related | 已有8GiB分散块及0/8/16GiB HBM曲线；完整65.5GiB DRAM底本始终保留计费。 | 未测聚合DRAM不足/物理位置容量不均导致完整副本不可行；不支持广义多层容量紧张主张。 |
| S5 共享资源 | partial_verified_software_dependency | 同一worker跨目标串行参与的有序依赖已验证；e3远端来源负载不同。 | 软件依赖和时间关联可核对；物理出口争用、运行顺序或冷/热效应尚未分离。 |

## e3来源与指标

集中/分散等权均值分别为2.538151092/1.931096664s，差值0.607054428s（23.917190%）。这是四个历史单次配置的描述性差值。

| 布局/目标 | T_ready(s) | execute(s) | 命中HBM(GiB) | DRAM(GiB) | 来源worker字节(GiB) |
| --- | ---: | ---: | ---: | ---: | --- |
| concentrated/target_a | 1.277976811 | 0.844249250 | 4.000000 | 61.499654 | 0: 32.749827, 1: 32.749827 |
| concentrated/target_b | 3.798325372 | 3.318748010 | 8.000000 | 57.499654 | 0: 36.749827, 1: 28.749827 |
| distributed/target_b | 2.624750835 | 2.138061846 | 8.000000 | 57.499654 | 0: 32.749827, 1: 32.749827 |
| distributed/target_a | 1.237442493 | 0.769558524 | 8.000000 | 57.499654 | 0: 32.749827, 1: 32.749827 |

A集中/分散命中4/8GiB HBM，因此A不是同来源流量的纯拓扑对照。B两组均为8GiB HBM+57.499654GiB DRAM；source0/source1从36.749827/28.749827变成32.749827/32.749827GiB。B变化集中在execute。逻辑总字节/execute仅代表整组逻辑吞吐。

## worker依赖与rank局部阶段

### concentrated/target_a

| worker | 角色/操作数 | execute(s) | 局部elapsed总和(s) | 连续操作组（src→dst/kind/op范围/秒） |
| --- | --- | ---: | ---: | --- |
| 0 | {'both': 131} | 0.765792 | 0.759872 | 0→0/local_hbm:hbm/0–15/0.025475; 0→0/local_dram:dram/16–130/0.734397 |
| 1 | {'both': 131} | 0.840886 | 0.834772 | 1→1/local_dram:dram/131–261/0.834772 |

| rank | 本机execute→copy(s) | copy→publish(s) | publish→ready(s) |
| --- | ---: | ---: | ---: |
| rank0 | 0.765613 | 0.080957 | 0.004936 |
| rank1 | 0.840548 | 0.004620 | 0.007348 |

### concentrated/target_b

| worker | 角色/操作数 | execute(s) | 局部elapsed总和(s) | 连续操作组（src→dst/kind/op范围/秒） |
| --- | --- | ---: | ---: | --- |
| 0 | {'source': 147} | 1.637234 | 1.630601 | 0→8/network:hbm/0–15/0.148237; 0→8/network:dram/16–130/1.287619; 0→9/network:hbm/131–146/0.194745 |
| 1 | {'source': 115} | 3.311915 | 3.306242 | 1→9/network:dram/147–261/3.306242 |
| 8 | {'target': 131} | 1.439301 | 1.429866 | 0→8/network:hbm/0–15/0.139266; 0→8/network:dram/16–130/1.290600 |
| 9 | {'target': 131} | 3.308061 | 3.297668 | 0→9/network:hbm/131–146/1.627734; 1→9/network:dram/147–261/1.669934 |

| rank | 本机execute→copy(s) | copy→publish(s) | publish→ready(s) |
| --- | ---: | ---: | ---: |
| rank0 | 1.436343 | 1.882269 | 0.007869 |
| rank1 | 3.305855 | 0.013993 | 0.007214 |

### distributed/target_b

| worker | 角色/操作数 | execute(s) | 局部elapsed总和(s) | 连续操作组（src→dst/kind/op范围/秒） |
| --- | --- | ---: | ---: | --- |
| 0 | {'source': 131} | 2.130928 | 2.124606 | 0→8/network:hbm/0–15/0.274172; 0→8/network:dram/16–130/1.850434 |
| 1 | {'source': 131} | 2.128318 | 2.121723 | 1→9/network:hbm/131–146/0.273521; 1→9/network:dram/147–261/1.848203 |
| 8 | {'target': 131} | 2.129542 | 2.120435 | 0→8/network:hbm/0–15/0.267717; 0→8/network:dram/16–130/1.852718 |
| 9 | {'target': 131} | 2.126346 | 2.118209 | 1→9/network:hbm/131–146/0.267435; 1→9/network:dram/147–261/1.850775 |

| rank | 本机execute→copy(s) | copy→publish(s) | publish→ready(s) |
| --- | ---: | ---: | ---: |
| rank0 | 2.127557 | 0.010276 | 0.009736 |
| rank1 | 2.125100 | 0.012228 | 0.009705 |

### distributed/target_a

| worker | 角色/操作数 | execute(s) | 局部elapsed总和(s) | 连续操作组（src→dst/kind/op范围/秒） |
| --- | --- | ---: | ---: | --- |
| 0 | {'both': 131} | 0.763873 | 0.757870 | 0→0/local_hbm:hbm/0–15/0.025737; 0→0/local_dram:dram/16–130/0.732133 |
| 1 | {'both': 131} | 0.761510 | 0.755752 | 1→1/local_hbm:hbm/131–146/0.025591; 1→1/local_dram:dram/147–261/0.730160 |

| rank | 本机execute→copy(s) | copy→publish(s) | publish→ready(s) |
| --- | ---: | ---: | ---: |
| rank0 | 0.763607 | 0.004720 | 0.006908 |
| rank1 | 0.761319 | 0.007830 | 0.006712 |

## 最小已验证约束及后续界限

每个worker按全局有序op列表串行完成其参与操作；共享worker的跨pair操作不能同时推进。

集中B中source0先完成对target8的操作，再进入target9的HBM；target9之后才能继续自身DRAM操作。这是来源共享导致的执行先后依赖，不需要假设物理网络争用。worker0/1与target8/9的局部elapsed和copy完成/发布等待形成可核对证据链。所有操作记录与计划次序、角色、字节逐项匹配。

候选取数评分至少应识别参与worker的累积负载及跨目标先后依赖；仅独立pair带宽/target串行不能描述共享source gate。

先冻结B的总HBM/DRAM字节、合法来源和chunk/executor，仅改变source0承担第二目标HBM块的布局；反序一次区分顺序效应，必要时第二次。新增实机由主agent预登记调度。

反序不保留差异或强简单策略同决策时保留负结果并采用简单方案；不新增自动复制/通用排程器。

未决原因：local elapsed包含collective等待、传输、落地与同步，不能拆成独立链路带宽。 旧record无每op开始/结束绝对戳，不能恢复跨主机重叠或精确硬件出口利用率。 单次固定顺序仍与布局变化混杂，物理NIC/PCIe/NUMA争用及热状态未独立控制。

仅同一host monotonic_ns求差；worker本地elapsed只局部汇总；不减未校准跨主机wall/monotonic钟。

## 复用与完整性

e4为2个独立实测+6别名；e5为decision_only/unchanged，0新增GPU恢复；e6为8个独立实测+4别名。引用、别名和本次离线审计均不增加性能样本数。S3未测，S4仅局部覆盖。

sources.json保存五个历史run的全部文件SHA256/长度及审计前后相等检查。原结果没有覆写。e3_worker_records.json保留逐worker操作次序、角色、局部elapsed及计划来源；summary.json保留完整派生证据。
