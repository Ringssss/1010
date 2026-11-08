# 首次执行前补充：显式整块路线见证

两个在推导中已写明的布局分别为：顺序满缓存`r0/pool0,r1/pool1,r2/pool0,r3/pool1`；交换满缓存`r0/pool1,r1/pool1,r2/pool0,r3/pool0`。两者均实际16MiB、各pool8MiB，并保留完整DRAM后备。

同一次修订实例CPU脚本另调用已有`restore_plan(strategy="fastest")`、原rank-interleaved与service_cost，生成这两份预登记布局的整块路线和前序。期望11/12及5/6，与主合同原resource_lp实际结果分列。它们是route_witness，不替换baseline、joint或strong内部目标，不从结果中选较好路线。

原resource_lp对非关键rank的分流和4MiB取整可能偏离显式路线。按唯一实例逐op记录这类差异，若公式条件未满足，不把差值归因于布局收益，不修产品或重跑挑tie。需独立事件资源负载审查，物理模型资格仍false。
