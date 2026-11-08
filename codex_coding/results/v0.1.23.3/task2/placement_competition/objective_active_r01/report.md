# 两个有意义位置门槛：唯一修订配置的独立目标复核

开发号v0.1.23.3/task2；只读源码审计和CPU检查。输入冻结为`case_active_r01/{contract,comparison}.json`，结果见`reevaluation.json`与`revision_scope.json`。原r03和此前目标报告完整保留；本报告补充并收窄其研究资格。

## 1. 保留原负结果，修复候选活性契约

原`case_cpu_r03`的r2/r3@pool1虽然合法，但路径速率0.5被完整DRAM后备速率1支配。独立统计全部121个原共同LP实际队列，两个候选从未使用，未满足每块两个有意义保存位置的门槛。旧目标报告的容量与队列核算仍有效，其“多位置竞争已验证”只能限定为六个有效候选，不能当作八候选全活性结果。

本次事先推导非支配条件v>1及两个指定完整缓存队列的交换改进条件v<12/7，冻结唯一v=3/2。独立比较合同：内容、候选、底本、角色与全部配额相同；仅r2/r3@pool1两条directed rates由0.5变1.5；其他profile字段完全相同，4项修订检查全过。没有参数扫描或产品改动，也未以joint胜出为目标。

## 2. 共同路由与显式见证均通过

531项独立检查通过，包括121合法subset、6policy、3原strong controls和两个预登记fastest路由witness。各条队列重新调用原resource_lp或对应原fastest、rank_interleave与原estimate_execution，再以全部start/end端点划分区间、逐resource聚合匀速path服务。每条队列满足声明capacity，八个候选各在至少一个合法共同LP队列中被真实取用。

此处“真实取用”仅指CPU方案的actual ops选择，尚非GPU运行。DAG提供零setup、固定时长、固定route/order、声明capacity模型下的可行timeline，不表示真实硬件性能或任意route/order的全局最优。

两个预登记完整缓存布局在共同resource_lp与独立fastest witness中的来源完全一致，服务时间也一致：

| 完整缓存布局 | 实际cache | 共同LP的worker DAG | fastest witness |
|---|---:|---:|---:|
| 顺序最快：r0/r2@pool0、r1/r3@pool1 | 16MiB | 11/12 | 11/12 |
| 柔性让位：r0/r1@pool1、r2/r3@pool0 | 16MiB | 5/6 | 5/6 |

同一layout的共同LP与fastest时间差均为0；本次没有非关键rank tie或4MiB量子取整导致来源退回DRAM的偏离。因此指定两布局之间1/12的符号服务差确实来自保存分配及相应同worker排队。共同LP的资源评分分别为2/3、1/2，继续与11/12、5/6的可达队列时间分列。

原strong保持空缓存，service=2；容量最快与单候选均衡选择顺序布局，均为11/12、16MiB；真删除/加入的交换参考与原joint选择让位布局，均为5/6、16MiB。交换参考已经解决本例，未出现joint相对该强对照的增量，不支持新solver必要性。

## 3. 空间—服务曲线与剩余问题

同候选、原LP路由政策下，0/4/8/12/16MiB预算的最低DAG分别2/2/1/1/5⁄6，达到最低分所需的最少实际cache分别0/0/8/8/16MiB。曲线展示缓存是否值得占用空间；不应只比较16MiB满缓存的两个布局。

这一唯一修订满足每块两个有效位置的有限契约，支持“柔性内容应让出稀缺好位置”的特定决策解释。一般多重共享cut、路径/顺序联合优化、真实多worker资源准入、独立cost holdout、布局建立开销和完整模型T_ready仍未完成。本case共享DRAM出口cap4高于两个source合速2，其动态争用不会绑定声明timeline；不能用此结果替代共享NIC竞争实验。

独立复评命令：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/placement_competition/objective/reevaluate_case.py --input codex_coding/results/v0.1.23.3/task2/placement_competition/case_active_r01 --out codex_coding/results/v0.1.23.3/task2/placement_competition/objective_active_r01
```

来源SHA、全部容量峰值、实际source IDs、逐target最后receiver完成时间和两路由对照均保留机器结果。新GPU=0、SSH=0、产品变更=0；原r03负结果未覆盖。
