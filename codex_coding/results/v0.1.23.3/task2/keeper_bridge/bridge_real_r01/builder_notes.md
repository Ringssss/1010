# 真实 keeper bridge：建设侧只读核查

本文件仅汇总主agent运行后的只读核查，独立验收以audit输出为准。原始summary/transfer/case文件保持不变。

`bridge_real_r01/summary.json` 和 `transfer_summary.json` 均为passed。32MiB/flow pair预热、正式1GiB单A、单B、pair各一次均全SHA通过，源lease在prepare/execute/complete三门均存在，finish后归零；独有target已退役。四card physicalfree delta均0，目录/epoch/allocator/staging、parked精确PID/命令、可访问旧keeper保护通过，直接依赖及输入SHA前后相同。

| 正式case | payload/flow | node29 receiver RPC包络ms | target worker操作elapsed ms |
| --- | ---: | ---: | --- |
| 单A，worker0→2 | 1GiB | 45.164698 | A 43.646280 |
| 单B，worker1→3 | 1GiB | 59.922063 | B 58.526945 |
| pair，两条原路径 | 各1GiB | 69.974211 | A 68.169260；B 66.077948 |

pair RPC overlap为67.641297ms；同node29/boot_id clock，但这是execute HTTP调用包络。worker绝对monotonic操作窗口仍为null，不能把这些RPC overlap当成kernel占用或硬件服务能力。coordinator阶段总耗时含保护观测和idle采样；微恢复不称T_ready，不据此宣布整模型收益或硬件共享主因。

实际精确PID环境四keeper均有`NCCL_IB_HCA=shca_0`、`NCCL_TOPO_FILE=/usr/local/built-in-508-topo-input-tj-default.xml`、`NCCL_NET_PLUGIN=librccl-net-shca.so`，均未设置`FLUIDCKPT_EXECUTE_PROFILE_DIR`。完整keeper注册日志及SHA保存在两节点runtime_before/after JSON；actual端口与A/B模式应依据其中匹配通信组的初始化连接记录独立认证。各case的receiver_counters.json保留所有HCA/port的正式execute及200ms idle窗口，不将共同端口计数重复叠加，也不把counter覆盖等同链路打满。

建设验证：六项CPU契约通过，日志为同目录树`bridge_cpu_contracts_r01/cpu_tests.log`；两源文件py_compile通过。本子agent未发GPU操作、未启动额外实验、未改存活worker或冻结基线。
