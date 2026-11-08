# 成本账本执行覆盖层

开发编号保持 v0.1.23.3/task2。产品源码与已有结果不修改。C0 的 CPU 回放使用真实 native HTTP handler、Catalog 和 prepare/execute/verify/complete/finish/retire 方法，torch 与 NUMA/runtime 后端使用明确标记的模拟接口；不能作为硬件系数或速度证据。

主 agent 已批准 node27 GPU0、world1/rank0 的最小拓扑。native 初始化及64MiB staging 保留，不启动SSD或remote warm。源为完整96MiB synthetic TP4元数据副本；这不是 live TP4模型。每个请求使用新、未写目标，serial32MiB原复制循环不修改。C1 顺序 L3、L1、L4、L2、L5、L3_repeat；C2仅在冻结模型和独立原始/资源审计均通过后，以新session执行V2、V1。最多八请求。

`protocol.py` 是机器可读协议和状态合同，`runner.normalize()` 是raw与分析字段映射。bundle的`schema_version=cost-ledger-v1`，`measurement_mode=native_serial_thread_scoped_noevents_v1`，configuration包含固定STATE及measurement_mode，state_contract为STATE。样本保存case/session/request/target ID，`actual`保存实际N/K、chunk_mib、copy/sync/land次数和字节；`measured`保存service_ns、worker_ns、controller_execute_ns、full_filled_ns、published_ns、request_return_ns、raw_service_ns。service_ns仅保留原record.elapsed_s求和取整数；worker_ns是完整原execute方法独立外窗，包括set_device及return/finally。raw_service_ns来自保留原monotonic返回值的相邻ns采样，不要求它和另一次float采样精确相同。

每个raw请求包含worker_ledger、controller_ledger、flow、source_lineage、gate、cleanup。worker原始primitive spans来自当前执行线程的set_device、begin/end log、land、copy_、synchronize成功返回；source_slice_and_loop包含切片、循环和hook工作，不能命名纯切片。copy_span是主机提交窗，sync是剩余完成等待，不能称裸DMA。parent与不重叠partition独立记录，partition的unattributed_gap不指认原因。CPU时间与GPU事件不引入本模式。

真实HTTP线程出生与代际由原ThreadingHTTPServer.process_request_thread入口记录。目标allocate出生代际及之前/之后写入史、源epoch/generation及bootstrap写史保留。metadata和大字典在原execute方法外组装，其开销仍在controller及完整请求内。每请求完整开始早于fresh inventory、预算检查、plan建设和journal写入，filled/published均为controller观测边界；finish在published之后单列。weights_ready为null。

同一组ns分区整数精确闭合。原float表示取整误差与另次时钟采样边界差分开报告；1µs不用于吞掉set_device或日志空隙。两个时钟domain不直接相减。本地CPU原件在execution_cpu_r04，55项检查通过；独立review另有215项检查。

运行命令由主 agent 在现场fresh准入后执行，下面目录必须不存在。C1准备、stage、preflight、session分别使用`prepare.py build/stage/preflight`与`session.py --execute`。build必须传入最终分析`--predeclared`。C2另传`--prediction`、`--calibration-review`和最新C1 `--prior-prepared`；gate.py会重算C1冻结，核对所有源/原始引用、newsession、非CPU证据和独立`C1_raw_resource_model`资格。CPU模型始终不能授权GPU启动。

每node聚合6GiB DRAM/2GiB HBM，运行时/staging计入；每次GPU准入另保留256MiB应用margin和请求所需目标字节，bootstrap前reserve192MiB。保留旧owner/container/原XML及mapping，只发精确本任务STOP。measure期间不并发HIP inventory monitor；supervisor主机cgroup采样、请求前后sameHIP取样保留，连续GPU峰值未知。

监督与artifact collection复用已有operations helper；它们要求独有`multitier_runtime/operations_process_<session>_<key>`和dump目录。最终原件收集到新cost_ledger/execution_*目录；这个跨目录约束是已有基础设施要求，不覆盖旧run。所有child使用ulimit-c0。失败和重试使用新目录，journal仅在本次新建run内更新。

`cpu_gate_check.py`是冻结之后新增的CPU协议检查工具，不属于已冻结/已部署测量资产；它检查失败门、篡改、旧session、CPU模型GPU拒绝及C1rank0→C2构建。它使用明确标记的synthetic计时fixture和仅限测试的CPU gate入口，不提供硬件准入或预测准确率。
