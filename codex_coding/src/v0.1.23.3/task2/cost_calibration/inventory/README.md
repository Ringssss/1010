# 库存查询分段诊断

此目录是 `v0.1.23.3/task2` 新会话的实验覆盖层。旧 `multitier_runtime/runner/runtime_worker.py`、产品与历史运行结果保持原件。使用原 RuntimeWorker 的分配记录方法和 native.main HTTP handler；不新增 HTTP 框架。新增独立诊断锁不替换原 POST 锁。

`worker.py` 兼容旧 worker 参数，新增 `--inventory-mode-file PATH` 与 `--allow-static-cache`。模式文件严格是 `{"mode":"legacy"}` 或 `{"mode":"cached"}`；由统一执行器只在本任务新文件上原子替换。未指定文件默认 legacy。cached 需显式 flag，实际启动/切换由主 agent 协调。

`/inventory?compact=1` 保留原 inventory、runtime_identity、host_allocations 的全部字段，只新增 `inventory_diagnosis`。其中 schema_version=1；sequence 为该 worker 本会话请求序号；worker_inventory_start_ns/end_ns/elapsed_ns 在该进程的 monotonic_ns 时钟域内；segments_ns 分出 mode_file_read、native_inventory、library_resolution、cdll_load、hip_bdf、hca_metadata、proc_identity、cgroup_metadata、pci_metadata、environment_and_rss。segment_sum_ns 和 residual_ns 校验分段守恒；残差包含Python结构组装、锁等待及计时调用开销，不能冒称纯 dispatch。native_inventory 原样调用，Catalog、epoch、HBM free/allocated/reserved、NUMA内存与 readiness 来源持续 fresh。

worker 内计时到原始 inventory 和 identity 字段全部形成；新增诊断字典组装与 HTTP serialization 在其后。controller 外层完整库存请求计时必须保留，包含所有 worker 原字段、计时字段传输和反序列化；不得从 controller 窗减去 worker 时间戳。此覆盖层新增 timer 和模式文件读取的观测成本应在结果中注明。

## 冻结假说与对照

源码显示每个 inventory 都重复 `ctypes.util.find_library('amdhip64')` 和 CDLL。这只是待验证假说。先在当前实际任务容器做 legacy 两次，分别记录六 worker 分段及 controller 完整库存窗；仅当 library_resolution/CDLL 是主要负担时，再启 cached 两次。最多每模式两次，n=2 不作统计认证。缓存不是预先从计时窗外初始化：legacy 已在该窗内完成解析并持有句柄，cache_origin_sequence 明记初始化来自哪次请求；cached 首次无可用cache时按原路径计费。

候选只复用当前 PID 的库路径和 CDLL 句柄；不缓存 BDF、PCI NUMA、HCA端口、boot/PID/start、cgroup path/namespace/current/max、环境/RSS或任何 native 库存。每次 cached 仍执行 HIP BDF。若查库是主因，预测 worker 库存下降应与 legacy library_resolution/CDLL 时间的减少相符，controller 最慢 worker 窗随之缩短；若不符先修正假说。除时变数值外原字段结构与静态身份应一致；动态值需继续合法且fresh，不能用速度中位数更小单独宣布根因成立。

`probe_find_library.py --out NEW_JSON --iterations 2` 是纯 stdlib CPU 原语探针，无 torch import、CDLL 或 HIP调用。可以在实际容器运行，也可本地运行，但两者均不能替代实际 worker 分段。`cpu_check.py --out NEW_JSON` 以 AST 提取原 runtime_identity 做字段对照，并用假 HIP/sysfs验证 cached 后cgroup、RSS、PCI仍刷新与PID变更重建；不启动 GPU。
