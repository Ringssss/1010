# 最终位置竞争契约：共同执行目标独立复评

输入为`case_cpu_r03/contract.json`与`comparison.json`；来源SHA和原service timeline见`reevaluation.json`。开发号v0.1.23.3/task2；全程CPU，无产品变更、GPU或SSH。

520项检查通过：121合法subset、6policy、3原strong控制均重新调用原resource_lp→rank_interleave→estimate_execution，核对ops、资源与DAG评分。每条timeline以事件端点分区间、在区间内聚合全部共享resource incidence的匀速path服务，声明capacity全部满足。故此case的DAG在零setup、固定时长、固定route/order符号模型下有可行witness；一般共享NIC动态、真实硬件和完整模型T_ready仍未校准。

| 策略 | worker DAG归一化分数 | resource评分 | cache MiB |
|---|---:|---:|---:|
| base_only | 2 | 1 | 0 |
| 原strong | 2 | 1 | 0 |
| capacity_aware_fastest | 1 | 1 | 12 |
| bottleneck_balanced | 1 | 1 | 12 |
| balanced_exchange | 5/6 | 1/2 | 16 |
| 原joint | 5/6 | 1/2 | 16 |

交换与joint同布局：r0/r1放pool1，r2/r3放pool0。其target0/1/2/3最后receiver完成时间分别为1/3、7/12、7/12、5/6；其中target0虽完成于1/3，同worker0仍为别的target发送，因此不能取worker0最终ready作为target0完成时间。

0/4/8/12/16MiB允许预算下，固定policy subset参考的最低DAG分别为2/2/1/1/5⁄6；最低分最少实际cache分别0/0/8/8/16MiB。8MiB即可达到1，因而fastest/均衡的12MiB在空间—主目标上被参考支配。交换参考已达到16MiB固定policy参考的最低分；本case不支持新joint增量或新solver必要性。

评分语义、候选合法性、paired addition与真交换区别、最小需求表和未定研究项见`../objective_cpu_r01/audit.md`。独立8项语义probe还包括两个helper服务四target的source排队，以及max(resource,DAG)不能保证一般shared queue可达的local-first反例。
