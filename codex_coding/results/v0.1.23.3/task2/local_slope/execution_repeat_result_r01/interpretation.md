# 最后两请求：首次host操作已实测，原负斜率不是带宽证据

同一HTTP handler、相同source/target指针和相同n的两次复制，source_slice thread CPU显著下降：4MiB 90.991→13.781us，16MiB 74.411→12.930us。这直接验证了本实验执行路径的首操作host耗时，且第二次slice的两尺寸CPU耗时接近。它不定位PyTorch/HIP内部哪个函数，也不把旧四个训练点各自约0.1ms差量精确归因给某个内部函数。

|尺寸|first round us|second round us|source_slice first→second CPU us|copy_submit first→second CPU us|
|---|---:|---:|---:|---:|
|4MiB|346.575|89.062|90.991→13.781|83.372→17.920|
|16MiB|289.744|123.432|74.411→12.930|70.671→18.500|

首轮16−4为−56.831us，第二轮变为+34.370us。首轮负差主要来自begin_log −27.930us、source_slice −17.081us与copy_submit −12.760us，device_sync反而16略大+1.690us。第二轮的正差主要来自device_sync +35.400us，source_slice/target_slice/copy_submit差均小。各phase thread CPU与wall接近，首轮负差的主项落在host执行窗口，不能解释成“拷更多字节使HBM数据传输更快”。第二轮copy/sync还受到目标first-touch/cache状态改变，不能据此独立拟合物理带宽或认证纯GPU复制曲线。

两轮没有CUDA events、prepare事件初始化、额外同步或stamp前prewarm。每轮日志和hostphase次序相同；中间只有原op结束日志、clock/list记录和下一轮，不查询ptr/stream/mem_get_info或组诊断dict。metadata在两轮之后，4/16MiB其耗时0.720820/0.552647ms仍计在HTTP/controller内，不能省略为优化收益。整个logical op service为0.518927/0.484826ms，含两轮与轮间日志/计时开销；它不是旧单copy service的直接替代值。

线程分别为Thread-27/TID6480和Thread-37/TID6499，同请求两轮线程证据相同。source指针140341248262144在两请求相同；4MiB两轮target140341220999168，16MiB两轮target140341225193472，均contiguous/stride1。地址采样在两轮之后，保留两轮view引用确认是相同allocation，无轮间metadata预热。

建模建议是增加首操作host/runtime状态这个变量，再区分每op固定项与字节相关的完成时间。可表示为 `T_local = H_first(request/thread/runtime state) + Σ A_op + Σ C_bytes(n, target first-touch/cache state) + observation/control`，首个op也计自身copy固定项；该式只是结构建议，没有新系数。必要的host首项随新HTTP线程可能再次发生，不等于collective pair已经warm便消失；原first_pair_s是通信状态，不能复用成本项。日志/测量器和元数据成本需单列，其公共或服务位置按实际窗口计费。

旧4/16MiB样本的负beta缺乏物理带宽意义，强行取绝对值、clamp成正带宽或挑点都不成立。对当前4–16MiB单op有限域继续保留旧冻结固定service .280796ms及bandwidth=null，domain/新HTTP/fresh exact-target条件明确；8MiB独立验证仍是其既有证据，本轮没有改写参数、重拟合或外推尺寸。若未来比较更细布局，需要基于真实请求/线程/目标状态约束的新训练与独立验证；本轮不进行。fullfilled/published的控制成本、验证成本和live模型T_ready仍各有自己的窗口，不能把second round当端到端恢复收益。

本会话logical测量SHA20MiB，实际调用copy payload40MiB，另bootstrap16MiB：最终逻辑SHA合计36MiB、调用copy payload合计56MiB。SHA在两轮之后核逻辑n字节；没有在首轮之后插入独立SHA，避免轮间改变状态，故不能声称两轮各独立全SHA。actual_copy_bytes是两次copy请求的payload字节，不是已经识别的HBM总线流量。

会话48.671220s含初始化/mesh/两请求/STOP，不是恢复延时。九supervisor rc0，三node collection通过；最终VALID replicas、所有used/transactions与三SSD files均0。DRAM monitor采样峰28/30/27为3482435584/3979849728/4228812800B；27请求gate DRAM4283543552B、同HIP HBM绝对上界1826619392B高于monitor，所以不能把monitor当完整峰值。已记录gate/monitor均在每node6GiB/2GiB预算，额外64MiB保守HBM reserve仍有余量；连续瞬时peak未知。保护旧owner/container/XML及精确child清理资格由最终独审给出。

来源：本目录summary.json的两条raw_ref对应原请求；execution_repeat_actual_r01/measure/measurements.json、budget_monitor.json、budget_evidence.json、measure/final_inventory.json及summary.json。stage1原路径含1.037124ms长点保持，其负斜率未稳定复现；本轮只增加了两请求的可区分首host状态证据，结束后无新GPU采样。
