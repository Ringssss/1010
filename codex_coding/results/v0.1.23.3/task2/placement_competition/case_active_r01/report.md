# 有限内容—位置竞争：活性修订实例

单次run为`case_active_r01`，保留原`case_cpu_r03`及其两个位置无效的失败。修订之前先保存`case_active_preregister_r01/derivation.md`、`route_witness.md`及`auditrefs_final.json`：活性要求v>1，显式顺序队列与交换队列产生差异要求v<12/7，冻结v=3/2。只将r2/r3的pool1速率由0.5改为1.5；候选、底本、容量、worker、执行顺序和baseline规则不变，没有扫描参数。

所有速率为声明的符号系数。四rank各4MiB，两个HBM池各8MiB，总额度16MiB；八个合法保存候选，每块可放两个池；完整DRAM底本始终参与所有取数选择。底本16MiB、恢复目标16MiB、缓存至多16MiB、6worker×64MiB staging预留384MiB分别记账；runtime和物理峰值未知。没有GPU、SSH、payload分配或产品修改。

14项构建检查通过，容量合法subset仍为121。八个候选均至少在一份合法布局的共同原resource_lp路由中实际被使用，修复了原契约的候选活性不足。

| 策略 | 原service队列主分数 | 资源下界 | 实际缓存MiB |
|---|---:|---:|---:|
| 完整DRAM | 2 | 1 | 0 |
| 容量感知最快 | 11/12 | 2/3 | 16 |
| 原strong_simple | 2 | 1 | 0 |
| 单步均衡 | 11/12 | 2/3 | 16 |
| 均衡加两副本交换 | 5/6 | 1/2 | 16 |
| 原joint | 5/6 | 1/2 | 16 |

最快先保留r0@pool0、r1@pool1，然后r2@pool0、r3@pool1。此时pool0的稀缺好位置先被较flexible的r0占用，r3使用较慢但仍快于DRAM的pool1替代。交换在不改变实际16MiB的情况下，把r0移至pool1、r3移至pool0，r2/r3使用pool0两块额度。父事前登记的简单交换规则完成这一步，与joint得到完全相同的布局、路由和队列，不支持joint相对强交换的增量。

两个预登记`route_witness`分别调用原fastest整块路由与原service成本，完成分数11/12、5/6；它们不是baseline替换或从运行结果选择最好的路线。本实例原resource_lp的实际整块来源与这两份见证一致；未出现影响该对照的取整路线差异。非关键rank的LP tie风险在事前推导中已明确，不以这一实例外推原路由器普遍无误。

主分数取每target最后receiver操作完成时间的最大值，并核原service总完成一致；兼source的target0/1在后续发送时的worker最终ready不当作其自身权重ready。资源下界与给定worker顺序完成分列：交换资源下界0.5，而接收rank0后再由pool0发送r2/r3，使实际声明队列为5/6。是否满足所有共享资源容量由独立事件审查认定，不能仅以资源下界推定可执行。

固定共同路由的有限服务参考在允许预算0/4/8/12/16MiB时最佳为2/2/1/1/5⁄6，其中12MiB额度下最佳仅需8MiB。全121布局、资源Pareto、服务预算参考、原strong所有controls、所有方法的完整plan/queue/timeline、两见证均在`comparison.json`。字段`cache_bytes_above_finite_minimum_at_no_worse_primary_cost`为继承的资源评分空间差，服务主指标的空间参考应读取`finite_service_reference`。

这些是固定声明速率、零setup、既定worker队列的synthetic CPU比较，不是物理测量或完整模型权重ready结果；物理容量、成本、runtime和模型binding资质仍false。原负例与本必要修订的因果、参数变化和停止条件分别保留，不以负gate终止用户任务，也不继续改变参数制造joint优势。
