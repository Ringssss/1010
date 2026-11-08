# 多层保存结构契约首次 CPU 结果

日期2026-10-10，开发编号v0.1.23.3/task2。结果来自唯一冻结的三节点六GPU/TP4八块合同，无参数扫描、GPU、SSH、payload分配或产品改动。

合同内部hash：`dbdae0f8dfc7c87455f5bc8f810903fb531f9c1f236d20a6b7fd679aeba9f874`。标准schema与247结构检查通过。此处通过的是manifest、位置预算、路径incidence和给定覆盖witness；不表示算法对照、物理或成本准入。

| 阶段 | HBM限额 | DRAM限额 | witness实际HBM/DRAM/SSD来源 | 无SSD时DRAM必要量 | 无SSD时HBM必要量 | SSD逻辑来源下界 |
|---|---:|---:|---|---:|---:|---:|
| C0 | 8q | 12q | 8q/0/0 | 0 | 0 | 0 |
| C1 | 4q | 12q | 4q/4q/0 | 4q | 0 | 0 |
| C2a | 4q | 6q | 4q/4q/0 | 4q | 2q | 0 |
| C2b | 4q | 6q | 4q/4q/0 | 4q | 2q | 0 |
| C3 | 4q | 3q | 4q/3q/1q | 4q | 5q | 1q |

q=4MiB为整数账本单位，总需求32MiB；没有实际分配。这些必要量不应解读为策略已选或必有性能收益。C3无SSD条件的HBM必要量5q超过实际4q、DRAM必要量4q超过实际3q，证明无SSD覆盖不可行；每块仍保留固定合法SSD来源。C3witness的1q SSD来源达到字节覆盖下界，未证明时延最优或实际磁盘读1q。

104候选、104路径在全部五阶段保持。每块12个可选HBM/DRAM位置加1个固定SSD来源；完整SSD为n0:4q/n1:2q/n2:2q，总8q，并非每node各8q。零配额位置依然可见，由预算拒绝。全部六HBM位置都在至少一阶段能容纳q；C2a/b/C3的w1和w3实际分别供给另一节点rank1和rank0内容，两个helper都起保存与恢复作用。

C2a与C2b预算及witness完全相同，仅共享资源关联不同；C2b与C3 HBM/共享关联相同，仅DRAM配额下降。路径含GPU copy engine、node PCI、NUMA DRAM及link、SSD queue、host staging、node NIC与worker资源。remote DRAM/SSD默认按已有GPU staging通信原语作资源假设，速率、有限队列和物理绑定仍unknown；不将同名资源容量相加。

SSD底本、暂停HBM/DRAM副本和恢复target8q分别记账。保守HBM已知项C0为16q、其余为12q，未计runtime/staging/transition，也未给原地alias抵扣；完整物理峰值仍unknown。Linux page cache、共享NFS/本地SSD归属另待协议，当前2GiB/node历史上限未改变。

实际命令：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/multitier_scenarios/contract/build_contract.py --out codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r01
```

自然rc0。`structural_checks.json`保留247项及逐阶段byte账本；`provenance.json`保留来源SHA。有限参考只检验给定witness，未枚举2^104布局、没有新solver，未调用原joint/strong_simple。scope是一个固定TP4 processed snapshot，冷checkpoint转换与任意未来target尚未实现。独立审查结果使用另目录保存，不覆盖本次输出。
