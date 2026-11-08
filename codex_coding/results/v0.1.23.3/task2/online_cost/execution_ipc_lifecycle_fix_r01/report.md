# IPC 捕获包装修正与证据保存

开发编号 `v0.1.23.3 / task2`；日期 2026-10-12。本报告对应 r02 失败后的本地源码、日志及 CPU 修正，不是新的 GPU 实验结果。

r02 两个引擎 rank 的暂停前 allocated 均为 36,496,468,480B，暂停后均为 36,496,468,992B，释放量为 −512B。两个 rank 同时出现超过 1000 个 IPC 内存块进入 limbo 的警告。`release_end` 和控制器 parked 标志不能据此证明权重已从 HBM 释放。最终资源门未保存具体失败卡与数值，这部分旧记录仍未知，不能回填。原始来源与指纹见同目录 `diagnosis.json`。

本轮实验包装的错误是：原生 capture 已经导入并释放 IPC 映射，额外 SHA 包装再次用相同导出元组构造 HbmTarget。当前复制的 Torch header 使用无符号计数；上游实现为一次导出设置计数 1，并在每个独立接收 storage 析构时减 1。重复消费同一元组因此可能产生 −1，在生产者无符号读取下成为正的大数。该错误与实际 limbo 警告及未释放的完整权重一致。来源：[生产者计数与 limbo](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/csrc/CudaIPCTypes.cpp)、[接收 storage 析构](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/csrc/StorageSharing.cpp)。本轮没有直接读取 r02 共享计数器，也没有完成修正后的 GPU 释放验证。

产品改动限定在 `main_lib/fluidckpt/cluster/capture.py`：增加默认 False 的 `verify_full_sha`，启用时在原始 HbmTarget 的生命周期内校验完整 CPU/引擎内容，成功后才发布 VALID。实验包装启用该选项，释放原始映射并同步收集 IPC 后才写 captured 标记。原默认路径保持同步 D2H 后发布；`full_sha_enabled`、`capture_s_scope` 与 publication 字段明确新的计时含义。没有修改模型布局、加载器或预算。

执行覆盖层现在在资源门抛错之前保存全部 worker、全部卡、实际总量/空闲量/上界、预算、余量与具体违反项；失败文件拒绝覆盖。Bootstrap 在捕获/SHA、参考输出、暂停、helper 转移及库存、cache 转移及库存各步保存检查点。Helper 建设前及每次在线恢复后，均要求每 rank 引擎 allocated 减少量和同卡实际空闲量增加量各达到完整权重字节数的 90%，全部前后事实保留；事件标志不代替物理门。

`execution_core_cpu_r05/qualification.json` 的 35 项检查通过，覆盖单次映射、SHA 错误精确回收、默认行为、无符号计数反例、完整资源失败证据落盘、90% 边界和 r02 的 −512B 反例。结果是 CPU 契约验证，`actual_model_online_qualified=false`。

主 agent 的 32MiB IPC 探针在构造 producer tensor 与 consumer 之前被资源门拒绝：GPU4 整卡已用上界 34,833,694,720B、空闲 33,869,004,800B，Torch allocated/reserved 为 0。其他团队已有负载占用当前目标资源；该负载启动于 r02 失败之后，不能解释 r02 的旧资源门。探针来源为 `execution_ipc_probe_once_r01/producer_init.json` 与 `failure.json`。不再尝试 GPU，也不调整预算。完整 C0 和两个留出请求均未完成，在线预测精度仍未知；资源恢复后先验证小 IPC 释放，再运行新的完整模型会话。
