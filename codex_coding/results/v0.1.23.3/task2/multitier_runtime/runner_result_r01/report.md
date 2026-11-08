# 三节点六GPU多层恢复：一次实机结果

三台机器六个GPU worker完成23条必要路径资格，并完成SSD_only、C0、C1、C3各一次完整32MiB恢复。八个4MiB SSD底本按node28/node30/node27的16/8/8MiB发布，四个8MiB目标的每个逻辑字节均通过SHA256；C3的两个helper实际跨节点供数。

本报告只读解析session `multitier_runtime_r02` 的原件，开发编号 `v0.1.23.3/task2`。载荷是合成processed TP4 schema字节。物理路径、字节可用和生命周期已执行；研究结论范围为每配置n=1的功能观察。完整模型handoff、solver策略差异、统计性能结论和完整物理峰值留待后续独立评估。

## 同钟时延

单位ms。prepare含inventory与prepare RPC；execute为全部worker执行返回的控制端墙钟窗口；filled/published均从同一monotonic_ns起点计算；verify+publish含全SHA、完成与发布。保存计时从已有完整SSD底本建立选定快层副本；初始合成与NUMA分配在该窗口之外，SSD底本构建保留独立transfer时间戳。

| 配置 | 阶段 | inventory RPC | prepare RPC | prepare合计 | execute | filled | verify+publish | published |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SSD_only | save | — | — | — | — | — | — | — |
| SSD_only | restore | 63.287 | 5.508 | 68.795 | 63.390 | 132.188 | 62.394 | 194.581 |
| C0 | save | 61.573 | 4.945 | 66.518 | 61.970 | 128.490 | 62.694 | 191.184 |
| C0 | restore | 63.132 | 4.491 | 67.624 | 5.290 | 72.915 | 26.971 | 99.886 |
| C1 | save | 61.303 | 11.395 | 72.698 | 63.871 | 136.571 | 60.585 | 197.156 |
| C1 | restore | 62.373 | 4.391 | 66.764 | 4.685 | 71.450 | 25.358 | 96.808 |
| C3 | save | 61.278 | 10.930 | 72.209 | 31.043 | 103.253 | 59.679 | 162.932 |
| C3 | restore | 61.842 | 5.071 | 66.913 | 14.138 | 81.052 | 30.510 | 111.562 |

恢复inventory RPC约61.8–63.3ms；C0/C1的filled窗口主要包含这部分控制请求。prepare合计包含inventory，不代表GPU分配耗时。当前窗口来自带RuntimeWorker诊断的功能入口；该inventory每次还采集HIP BDF、sysfs NUMA、进程/cgroup和通信诊断，诊断负担是后续可检验的因素，现有数据尚未分离其成本。

正式四配置共用时钟域 `controller/m03r2n07/735377/4823e950d68d4d26b000c045d131d8b2`。SSD_only没有快层保存操作，表中为“—”。资格路径来自另一个独立控制进程，时钟域 `controller/m03r2n07/730862/ffe91dcbb75844fab19252bbfdc54c92`；其时间戳单独保存，没有与正式控制进程相减。

## 暂停空间与实际恢复来源

| 配置 | 暂停HBM MiB | 暂停DRAM MiB | 恢复HBM来源 MiB | 恢复DRAM来源 MiB | 恢复SSD来源 MiB | 目标HBM MiB | 已知HBM项 MiB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SSD_only | 0.0 | 0.0 | 0.0 | 0.0 | 32.0 | 32 | 416.0 |
| C0 | 32.0 | 0.0 | 32.0 | 0.0 | 0.0 | 32 | 448.0 |
| C1 | 16.0 | 16.0 | 16.0 | 16.0 | 0.0 | 32 | 432.0 |
| C3 | 16.0 | 12.0 | 16.0 | 12.0 | 4.0 | 32 | 432.0 |

完整SSD底本恒为32MiB，已知HBM项包含六worker的384MiB原serial staging、32MiB目标及暂停HBM副本。runtime、驱动、注册、主机读取buffer、pagecache与过渡分配需进入实际空间账本，表中已知项对应部分分配，完整物理峰值尚未测得。

## SSD服务I/O窗口

下表均为各SSD服务实测counter差值，单位MiB；kernel read/write来自服务进程/proc/self/io。qualification窗口包含小载荷source bootstrap、已选路径和SHA读入。每配置窗口包围save+restore，含源端全SHA再次读取。

| 窗口 | 节点 | HTTP读取 | kernel读取 | HTTP写入 | kernel写入 | fsync提交 |
| --- | --- | --- | --- | --- | --- | --- |
| qualification | node28 | 72 | 48.0039 | 24 | 24 | 24 |
| qualification | node30 | 36 | 24 | 12 | 12 | 12 |
| qualification | node27 | 24 | 16 | 8 | 8 | 8 |
| base_build | node28 | 16 | 0 | 16 | 16 | 16 |
| base_build | node30 | 8 | 0 | 8 | 8 | 8 |
| base_build | node27 | 8 | 0 | 8 | 8 | 8 |
| SSD_only_save_and_restore | node28 | 32 | 32 | 0 | 0 | 0 |
| SSD_only_save_and_restore | node30 | 16 | 16 | 0 | 0 | 0 |
| SSD_only_save_and_restore | node27 | 16 | 16 | 0 | 0 | 0 |
| C0_save_and_restore | node28 | 32 | 32 | 0 | 0 | 0 |
| C0_save_and_restore | node30 | 16 | 16 | 0 | 0 | 0 |
| C0_save_and_restore | node27 | 16 | 16 | 0 | 0 | 0 |
| C1_save_and_restore | node28 | 32 | 32 | 0 | 0 | 0 |
| C1_save_and_restore | node30 | 16 | 16 | 0 | 0 | 0 |
| C1_save_and_restore | node27 | 16 | 16 | 0 | 0 | 0 |
| C3_save_and_restore | node28 | 32 | 32 | 0 | 0 | 0 |
| C3_save_and_restore | node30 | 16 | 16 | 0 | 0 | 0 |
| C3_save_and_restore | node27 | 16 | 16 | 0 | 0 | 0 |

SSD底本初始构建的kernel读取为0，随后四配置服务读取各64MiB，其中逻辑传输32MiB、源端SHA校验再次读取32MiB。buffered读取协议与POSIX_FADV_DONTNEED保持原实现；这些kernel计数刻画服务I/O，不构成可重复的冷盘读时延。

分阶段HTTP读取量根据实际plan与原verify_run的SSD源端读取计算，并与实测窗口总量核对；这一拆分为推导值。原件缺少save/restore边界处的kernel counter采样，因此kernel两阶段归属在JSON中保留null。

| 配置 | 阶段 | node28传输/SHA MiB | node30传输/SHA MiB | node27传输/SHA MiB |
| --- | --- | --- | --- | --- |
| SSD_only | save | 0/0 | 0/0 | 0/0 |
| SSD_only | restore | 16/16 | 8/8 | 8/8 |
| C0 | save | 16/16 | 8/8 | 8/8 |
| C0 | restore | 0/0 | 0/0 | 0/0 |
| C1 | save | 16/16 | 8/8 | 8/8 |
| C1 | restore | 0/0 | 0/0 | 0/0 |
| C3 | save | 16/16 | 8/8 | 4/4 |
| C3 | restore | 0/0 | 0/0 | 4/4 |

## 完整底本、NUMA与helper见证

| 底本块 | rank | 偏移MiB | 字节MiB | 完整SHA256 |
| --- | --- | --- | --- | --- |
| b0 | 0 | 0.0 | 4.0 | f4198c06e96bcf0f61b2593b4570cd0f30204cb3c77f6c557881c057380693ed |
| b1 | 0 | 4.0 | 4.0 | 9e7c1f005a8356a090c2aca8b712dcfd9416cc454227fbc4b9750e36ad95a5bd |
| b2 | 1 | 0.0 | 4.0 | d7e4b7c90e9c0e70ca77fd914249e5ae3ec1cbf95fb70e5b473fdf2c22ab2170 |
| b3 | 1 | 4.0 | 4.0 | da61a8812d11df944b6aa6bee7524a5876f661781ff72efd4df2ecd25cd87cf9 |
| b4 | 2 | 0.0 | 4.0 | ca7af8703636305b1924309fc3d46c1240a39b3cea976593543b7aaac25e1072 |
| b5 | 2 | 4.0 | 4.0 | 091857f4d5a7b8a150fc92d2e226210d8e213a4163ea2f30af3f108eabf2db7d |
| b6 | 3 | 0.0 | 4.0 | 6dd36c55ad72e1d7d1ab1454d7e1861062715ac9e6996701f27e9b82a0348327 |
| b7 | 3 | 4.0 | 4.0 | eb2eb0345c676fb57798f710a43ce7a39cfa2d7faad7c670c13610441552f43a |

八SSD extent的原native complete执行文件fsync后发布Catalog。保存与恢复的每个op都与对应权威底本SHA一致；四target均为[0,4MiB)、[4MiB,8MiB)完整覆盖。当前owner贯穿四配置，相关证据认证本次活owner生命周期。

| worker | 节点 | GPU | 实际BDF | NUMA | 4MiB触页/256样本/注册 | 历史host分配条目 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | node28 | 0 | 0000:09:00.0 | 0 | 通过 | 14 |
| 1 | node28 | 1 | 0000:36:00.0 | 3 | 通过 | 1 |
| 2 | node30 | 4 | 0000:85:00.0 | 4 | 通过 | 11 |
| 3 | node30 | 5 | 0000:b5:00.0 | 7 | 通过 | 1 |
| 4 | node27 | 0 | 0000:09:00.0 | 0 | 通过 | 7 |
| 5 | node27 | 4 | 0000:85:00.0 | 4 | 通过 | 5 |

| 配置/块 | helper位置 | target worker | 来源MiB | 实际路径 |
| --- | --- | --- | --- | --- |
| C3/b1 | w3 node30 GPU5 NUMA7 | 0 | 4.0 | network |
| C3/b2 | w1 node28 GPU1 NUMA3 | 2 | 4.0 | network |

NUMA表引用实际mbind、触页、256页采样与HIP注册记录；历史分配条目用于说明曾成功分配的内容，当前驻留状态以最终Catalog为准。helper同时有实际source角色执行记录、4MiB计数与对应底本SHA，C3剩余b7由node27 SSD提供。

## 精确清理与可追溯性

每配置结束只退役该case的optional/target，八个底本在四次cleanup后均保持VALID且lease为0。run结束后按显式许可退役本轮完整SSD底本，最终Catalog和transactions为空，unadopted_shards为空；三节点九个本任务worker/SSD进程及其process group均absent/empty，任务端口已释放。隔离容器保留一个idle init进程，pids.current=1。

| 节点 | 本任务group | 任务端口 | 容器pids | 清理后cgroup MiB |
| --- | --- | --- | --- | --- |
| node28 | 空 | 已释放 | 1 | 14.992 |
| node30 | 空 | 已释放 | 1 | 15.238 |
| node27 | 空 | 已释放 | 1 | 12.969 |

独立解析核对103个完整SHA op，累计核验逻辑字节412MiB，每个op分别哈希source与target。完整输入SHA、实机加载source SHA、各阶段原时间戳和23条资格明细见同目录summary.json。解析器 `/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/summarize_actual.py`，SHA `9b39f540c7b9f3c92e8186874b71895545a21bcd64f7f2b694b4b8373c066a35`。
