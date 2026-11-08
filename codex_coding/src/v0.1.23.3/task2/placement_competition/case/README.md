# 有限内容—位置竞争 CPU 契约

开发号保持 `v0.1.23.3/task2`，只运行一份事前冻结的 synthetic 四 rank 输入。每 rank 为一块4MiB，所有块都能在两个HBM池保存；各池额度8MiB、共享全局额度16MiB。允许复制，同一 rank 两个副本分别记账；不强制用满额度。4KiB为分配记账粒度，既有路由器按4MiB量子整块取整。

pool0/1与target0/1复用worker；target2/3独立，完整DRAM底本按rank奇偶存于worker4/5，两source共享符号U出口。完整底本16MiB、目标16MiB、额外缓存至多16MiB、6×64MiB staging预留384MiB独立计账；runtime与物理峰值未知，实际payload分配为0。该6worker图与原8worker TP4图的跨角色前序不同。

冻结速率（4MiB/符号时间）：local4；pool0到rank1/2/3为4/4/4；pool1到rank0/2/3为3/0.5/0.5；各DRAM后备1；网络每node出口/入口容量4，GPU出口/入口和DRAM容量100。r0/r2/r3的优质需求三块大于pool0两块额度；r0的替代3严格快于r2/r3的替代0.5。因此存在内容对稀缺位置的竞争。所有数字为声明系数，无实测来源。

原`evaluate/resource_lp`、`layout_ledger`、`strong_simple`、`joint_scenarios`、`service_cost.estimate_execution`均复用，产品不改。所有策略具有同一底本、八个候选及预算，采用原rank-interleaved路由队列；额外保存不强制成为取数来源，完整后备随时参与路由。

新简单策略规则在机器合同`baseline_rules`中逐字冻结。最终balanced/exchange以原服务函数的worker前序完成分数为主，以每target最后receiver操作结束时间的降序向量、实际缓存字节为次级；不能把兼source的worker最终ready算作该target权重ready。exchange仅检查删除1–2个已选、增加0–2个未选的可行邻域，反复接受严格改善，允许remove1/add2；没有读取有限枚举答案。原strong与joint内部目标保持，所有strong control提案共同服务重评单独输出。

枚举全部256个subset，其中121个满足容量，是有限布局参考而非baseline。参考固定原resource_lp路由和取整，没有搜索任意路由、操作顺序或新增执行器。服务分数和资源下界分别报告；共享资源在给定队列中的可达性由独立审查核验，物理并发容量、模型binding、实际权重可用时间仍未知。

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/placement_competition/case/build_case.py --out codex_coding/results/v0.1.23.3/task2/placement_competition/case_cpu_r03
```

结果目录不允许覆盖。同解、被后备抢走服务、空间支配与负结果均保留；不通过改参数寻找joint赢，不启动GPU/SSH。
