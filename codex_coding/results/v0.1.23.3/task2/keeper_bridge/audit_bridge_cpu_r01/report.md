# Keeper 桥接冻结输入与CPU选择独立审查

状态：failed；独立检查282项；新增GPU样本0。

本文件仅核验冻结契约的canonical hash、原始及冻结input hash、冻结源码和case hash。不构成应用恢复或物理共享资源验收。

失败：bridge/warmup_pairs_exact_plan, bridge/single_A_exact_plan, bridge/single_B_exact_plan, bridge/pair_exact_plan。

T1_simple_control：strong_simple/joint完整布局与有序操作同解=True；复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。

T2_uneven_budget：strong_simple/joint完整布局与有序操作同解=True；复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。

T3_shared_resources：strong_simple/joint完整布局与有序操作同解=True；复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。

## 生产 keeper 路径桥接

仅引用三个正式小extent配置各一次观察，另有32MiB pair预热；审查新增GPU样本0。小extent以合法replica目标走原Cluster.transfer serial，不构成完整模型T_ready或算法增量。

| 配置 | 正式payload B | receiver RPC包络 ms | worker接收端execute ms |
| --- | ---: | ---: | ---: |
| warmup_pairs | 67108864 | 5.220720 | 3.123637/3.010227 |
| single_A | 1073741824 | 45.164698 | 43.701250/0.018390 |
| single_B | 1073741824 | 59.922063 | 0.014190/58.582705 |
| pair | 2147483648 | 69.974211 | 68.239830/66.144799 |

共同窗口为node29同host/boot的execute HTTP RPC包络，包含dispatch、response；不等于设备kernel占用或worker同步monotonic窗口。阶段coordinator时间含200ms空闲采样、inventory与日志落盘，均标instrumented；prepare、全SHA验证和publish不混为copy时间。

隔离微probe采用synthetic GPU buffer、独立pair communicator与重复broadcast；应用桥接读取实际DRAM、源HBM staging、broadcast、目标land并每chunk同步。因此微probe36.46GB/s与0.85判据均不直接搬入应用服务模型。当前RPC时间没有提供可信worker共同窗口；生产执行器争用、S5-P和共享成本项保持未定。完整模型或同预算选源实测出现决策及T_ready差异后，再判断是否需要新增共享成本项。

NIC计数仅在execute前后读取，每HCA/port只计一次；idle窗口估计背景，不能排除突发其他用户流量。实际运行环境和完整初始化日志独立保留，GPU tensor接口不自动代表GDR。
