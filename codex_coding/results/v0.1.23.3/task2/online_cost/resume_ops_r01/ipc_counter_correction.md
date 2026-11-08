# IPC 探针计数器解释修订

实际 once/twice r02 都记录 Torch 2.10.0；此前源码讨论用的是 2.8 参考实现，不能直接充当当前构建的内存布局证明。重新核对 [PyTorch v2.10.0 MapAllocator.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.10.0/aten/src/ATen/MapAllocator.cpp)：`map_alloc_alignment=64`，`RefcountedMapAllocator::data()` 返回文件映射起点之后64字节的数据区。导出 refcounter 索引相对于 allocator 数据区，旧 `pread(offset*8)` 在索引0时读到 allocator header，不能解释为 IPC slot 值。

once/twice 原始文件不修改；其中旧 `known=true`、signed/unsigned=1 是未验证 IPC slot 的 header 读取，语义标签为 `allocator_header_not_validated_IPCslot`，IPC 数值计数未知。一次导入后 producer retained0B、同一 token 两次导入后 retained33,554,432B 的真实 Torch allocated 差量保持有效；它支持本小型受控场景存在重复消费后的保留差异，不证明完整模型唯一原因或直接测到 counter 下溢。

只修改未来探针 `probe_ipc_counter.py` 与 CPU 检查：`counter()` 默认 known=false、measurement_performed=false，仅保存凭证名和 exported_refcounter_index 元数据；没有 SHM open/pread，没有 numeric signed/unsigned。无实际构建布局验证时不恢复数值读取。原2GiB资源门、32MiB负载、同一导出令牌、一次/两次 raw import 和进程清理保持。

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/check_ipc_probe_cpu.py --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_ipc_cpu_resume_r01`。7项通过，其中把 os.open 替换为直接失败，验证未校验 slot 不访问 SHM；未导入 Torch、未启动 GPU/SSH/Docker。没有追加 GPU 实验。

主 agent 已在这项 probe 修订之前冻结 r03 的138项资产。该 snapshot 包含原 b2b7429... 探针，但完整模型工作流不执行该独立探针；r03 原快照与 hash 保留，不原地更新。本修订独立生效于未来探针，不追改实际 r02 结果或延迟 r03。
