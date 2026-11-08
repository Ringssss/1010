# 两节点单发送端r01实际失败独审

本轮完整70,329,718,112B捕获/全SHA/固定参考输出/物理暂停与两NUMA远端源构建通过；12条路径校准原件全部全SHA、资源门与临时目标退役通过。模型拟合按冻结协议失败，C0/Hnear/Hremote均0，整体10%误差未知，原host失败不改判。

明确失败为`fit_path: invalid or non-increasing calibration samples`。远端worker2 NUMA4→worker0的512MiB最大native op记录为73.126384ms，1GiB为66.851419ms；它们为两个worker记录取max，不相加。该观察不满足正斜率假设，不能解释为负物理带宽，也不能唯一指认首调用、缓存、RCCL或调度机理。失败时engine_parked=true、active_transactions为空、无resource_gate失败引用。

helper常驻guard共1280个0.5s配置样本，最低实际free33,478,934,528B、最高Torch reserved69,206,016B，均通过live阈值；不是连续峰值证明，完整driver/RCCL本任务归属unknown。原生Catalog终结used/transactions全空，四个准确自有supervisor final_owned_closure通过，最终两node pids.current=1/quiet通过，cleanup_failure=null。整体host rc1来自校准失败，不能把回收成功改写为完整实验成功。

建议新r02事前登记每path1GiB预热，随后实际操作尺寸805,120,688B/1GiB各一次，用正实测operation成本表替代两点正斜率门。表仅按path+尺寸索引并复用worker依赖DAG，是操作级聚合经验假设；没有逐layout常数、wholelayout记忆、物理BW解释或跨未知尺寸外推。C0只校准同一个共同outer残差一次，Hnear/Hremote提前一起freeze且各自整体10%门。保留r01原件、失败事实与全部12行校准；新run/源码快照/资源准入独立登记。
