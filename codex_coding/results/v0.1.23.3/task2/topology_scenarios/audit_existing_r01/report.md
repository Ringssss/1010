# 拓扑校准前独立证据审查

开发编号：`v0.1.23.3 / task2`；本报告只读取本地材料，新增GPU样本为0，未发SSH/HTTP请求。输入前后SHA记录见`existing_evidence.json`，全部保持。

## 可复用证据与禁止混用项

| 证据 | 可复用 | 不能据此宣称 |
| --- | --- | --- |
| 当前`serverless_target_recovery_r03/session_overlay_owned.json` | node27/node29 GPU0/1、四endpoint、源rank identity/extent/epoch、完整DRAM及最新目标进程控制文件 | `local_dram=35/local_hbm=80/peer=35/network=20`等数值是实测；profile原注明provisional |
| `paths01/network_pairs.json` | 32MiB成功传输是历史路径可用性证据，保留失败pair记录 | 旧8worker编号可直接用于当前4worker；可用性等于带宽校准 |
| `costcal01/service_model.json` | serial执行器的first-pair、per-op、chunked effective byte service建模方式 | 两条旧路径`2:hbm:2->6`、`4:dram:2->6`能代表当前0/1→2/3；33.13/27.49GB/s是容量上界；已有并发共享链路校准 |
| 9月23日`profile_node27.json`及heldout/longlived诊断 | NUMA绑定验证方法、fresh worker避免profile污染、单流/并发分开测量的设计 | 184条旧单流与历史5轮默认配置自动继承为新实验授权；本地8卡矩阵证明跨机NIC或交换网络资源独立 |
| e3逐op审计、S5-S正反控制 | 固定操作多重集合/每目标内部次序、worker串行依赖和发布等待证据，公平共享rank_interleaved执行改进 | 1.882s发布等待或S5-S约40.69%/21.93%是物理fabric争用或自动placement/MILP收益 |
| native/Blitz/Store成功路径 | 完整Qwen TP2内容、输出、生命周期门槛及原版/适配身份 | 122.27/2.80/131.12s构成同预算算法排名；局部微probe能替代这些完整恢复验收 |

旧profile的GPU0/1 NUMA映射碰巧与当前源记录相同（0/3），仍不能沿用有效容量：日期、进程绑定、工作负载、后端、共享负载与执行器均须重新绑定。当前应用worker在全局有序操作中执行同步RCCL/copy；isolated-pair校准允许的并行关系可能与它不同。

## 最小实验排除能力

父agent已在计划18.7预登记九配置：network单a/单b/并发，以及node27、node29分别H2D单a/单b/并发，每配置一次。两节点H2D分别覆盖源host供给与目标host亲和路径。它们可排除“仅把两条逻辑路径单流速率直接相加就能预测并发”的假设；还可发现当前backend/NIC绑定错误、并发没有真正重叠及内容/清理缺陷。结果若正常叠加，只是这一配置未观察到显著共享限制；结果若下降，只支持同配置有效供给受限，不能唯一定位为某switch/NIC/NUMA controller饱和。

receiver同主机单调时钟上的完整同步区间可以证明传输调用处于并发状态。分块区间含等待与排队，必须明确是host-visible已同步调用区间；它不是GPU kernel活动时间的直接测量，不能以外层HTTP/RPC窗口重叠替代。若需要严格设备执行重叠，应增补实际device事件或trace再作该层表述。用同目标clock domain避免跨主机相减；起始barrier、warmup和哈希计时位置一致。

network端口计数必须依据实际RCCL日志选择HCA/port，不只依据环境变量或OOB接口。IB `port_xmit_data/port_rcv_data`单位为4字节；网卡statistics的rx_bytes/tx_bytes已经是字节。两者不能合并，也不能把所有HCA的计数重复求和；按实际绑定port分别对照payload，报告背景流量/计数窗口。counter delta与传输字节相符是路径支持证据，并不单独证明这个端口达到容量。

## 公平性与停止清单

- 固定overlay/hash、worker/node/GPU/NUMA、boot_id、transport、chunk、bytes、预热与校验位置；单流与并发使用完全一致流定义。
- 速率统一decimal GB/s，预算统一bytes/GiB。总搬运量除以共同makespan；分流速率可列，但不能把不同计时窗口的速率和当成实际聚合吞吐。
- 实测速率仅为effective service/cost估计；严格`D/C`物理下界要求有独立容量上界证据，当前单流/并发点没有提供它。
- pre/post受保护原14keeper与当前4keeper的目录、epoch、lease、事务、parked进程身份保持。probe独有PID/starttime必须退出；实际free/allocated/reserved、残留事务与owner检查要有观测，空cleanup错误列表不足以证明已清理。
- 同预算场景分别计完整DRAM、额外HBM、目标分配、staging与建立成本；位置上限和总预算同时验证。未来互斥目标权重0.5是synthetic预登记，不伪装实测需求。
- 允许双方相同来源、远端、IPC和共用执行改进；原版/硬件适配/机制对照身份写清。同有序操作仅记decision alias，不新增性能样本。
- 无真实overlap、NIC路径不确定、保护漂移或清理失败时停止该性能结论。异常/歧义才考虑第二次；失败与重试新run保留，不默认五轮。
- 校准执行器与keeper_serial不一致时，物理S5在应用层仍为pending。freeze可以保留functional_provisional离线预览；不能宣布应用预测已校准。
- 候选同布局同来源同顺序时停止完整恢复比较。微probe信号需先改变真实候选决策、核对应用可执行性，再进入e4/e5完整模型验收。

## 初步建设判断

**reuse：**现有权重目录/内容身份、完整DRAM、fastest/LP/成组greedy、rank_interleaved、逐op同步、SHA/输出与生命周期验收。

**minimal：**九配置校准的绑定/局部时间/端口计数证据；当前overlay与代码指纹；同预算场景冻结和独立离线门槛。若校准暴露争用但现有路径模型已足够，先配置化复用；只有改变决策且影响主指标才补一个共享资源约束或评分项。

**defer：**通用异步执行器、全面多流/relay/collective合成、新MILP体系、自动复制、全系统移植、e7/e8。微benchmark并非整模型收益证据。

运行命令：`python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/audit/inspect_existing.py --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/audit_existing_r01`。
