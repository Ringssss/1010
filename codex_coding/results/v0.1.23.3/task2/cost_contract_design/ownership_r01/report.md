# 成本归属与最小计时契约只读审计

开发编号：v0.1.23.3/task2；2026-10-11；范围为现有源码、已登记计划与文章。没有修改产品、启动 GPU、SSH 或新实验。本文给主 agent 的统一设计提供源码边界，不提出新的实验矩阵。

## 1. 主结论

应先固定两本账：**同一时钟上的实际请求延迟分段账**，以及**执行依赖图中的服务成本账**。前者按相邻边界闭合；后者只加入已识别的服务项，通过原有 worker/source/target 依赖组合。controller、endpoint、worker、op、chunk、设备事件是嵌套或并行观测，不能直接全部相加。当前首操作 A 阶段证明首差存在，但状态门未过，因此保留 first/op/state 标签，统一 thread-first/request-first 系数仍为未识别，不给每块重复收费。

现有代码可直接给出 controller 到 filled/published 的完成窗、worker/op 的复合完成窗、逻辑字节与验证结果；不能直接分离裸 SSD、裸 DMA、精确 RPC 网络、collective 等待或唯一的首操作内部机制。`weights_ready` 是独立的模型侧边界，不能用 replica `published` 替代。

## 2. 当前窗口的实际归属

| 当前字段/事件 | 实际包含与取样位置 | 账目处理 |
| --- | --- | --- |
| `Cluster.transfer` 的开始 | 已获取 controller lock、复制/检查 plan 后，才采 `started_ns`；规划与等待 controller lock 均在它之前。 | 只称 transfer 窗；用户请求总窗需另从发起请求前开始。 |
| `inventory_rpc_s` | inventory 或 reused-epoch 检查，到 `inventoried`；reused 分支也有 CPU 工作。 | 该段不能因没有 RPC 就视为 0。 |
| `prepare_rpc_s` | `inventoried` 后含 tx UUID、worker-plan/body 建设、active 登记、并行 prepare RPC，到所有响应收齐。 | 并非纯 RPC；宜新命名为 controller prepare 阶段并保留旧字段映射。 |
| `prepare_s` | `prepared-started`，已含 inventory 与上述 prepare 段。 | **不能再加 inventory/prepare_rpc_s**。 |
| `execute_wall_s` | 所有并行 execute 请求的建立、提交、HTTP、worker执行、响应编码/读回、解码、executor退出，到所有响应收齐。 | 同 controller 时钟的整体完成窗；各 endpoint duration 是子观测，不能求和加进来。 |
| `all_targets_filled_ns` | 所有 execute RPC 已返回后；随后才检查 expected/actual 字节相等。 | controller 观察到的填充完成，含响应尾部；不是最后一个 GPU copy 的精确硬件结束点。验收失败时不得当成功边界使用。 |
| `verify_and_publish_s` | 字节核对、所有 source/target 全 SHA、校验结果合并、complete RPC、必要 SSD commit、Catalog 发布/live done 文件，到响应收齐。 | 不应整体归因传输或 publication；目前 verify 没有单独 RPC telemetry。 |
| `all_targets_published_ns` | 所有 complete 响应收齐。 | 同 controller 的 publication 观察点；并不证明模型已经发出 weights_ready。 |
| `coordinator_wall_s` | `ready-started`。`finish` 在此之后执行，默认 `retain=False` 时函数返回还等待 finish。 | cleanup 不在此字段中；如用函数返回作总延迟，需单列 finish。 |
| worker `execute_s` | `_execute` 中 set_device 与 phase 检查之后起表；含 record_targets、pipeline建设、op循环、记录/末尾 live事件。return字典计算时结束，之后还执行 finally pipeline drain；opt-in ExecuteProfile退出也在它之后。 | 不是 handler 总服务；两个 set_device、profile进入/退出、HTTP解析/锁等待/响应序列化均不能默认包含。 |
| op `elapsed_s` | 源/目标查字典之后起表；含 begin log、profile begin/end、全部 chunk读取/切片/提交/同步；record.append 的 elapsed 取样后还有 end log。 | end log 属 worker record 外成本；end log自身传入的 elapsed 已晚于 record elapsed，不与 record当成相同取样。 |
| `sync` | 每 serial chunk `torch.cuda.synchronize(self.dev)`。 | 已在 op 内，覆盖此前异步工作的等待且为 device-wide；不能把设备事件时长再加一次。 |

源码锚点：[controller请求计时](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/control.py:20)、[并行call_all](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/control.py:58)、[transfer起点](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/control.py:129)、[filled/verify/complete](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/control.py:155)、[finish在publication窗之后](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/control.py:183)、[execute/profile包裹](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:203)、[worker/op/chunk边界](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:217)、[HTTP handler](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:393)、[profile退出输出](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/executor/profiling.py:48)。

`request.telemetry.transport_and_server_s` 从 body 序列化后到 response.read 完成，包含 Request对象建设、网络/服务器等待、server执行与编码，不能称纯网络。`serialize_s`/`deserialize_s`也是其所在 endpoint 窗的子段。`call_all`每阶段新建 ThreadPoolExecutor，最后还等待 shutdown；controller阶段−max endpoint/worker只可称 residual，不能唯一解释为 dispatch。

## 3. request、op、chunk 与设备重叠

serial 路径每个 worker 依 plan 顺序遍历，只执行自身参与的 op。每个 op 逐 chunk 处理，每 chunk末同步，所以同 worker op顺序和 chunk顺序可作为实测串行依赖；不同 pair 可以独立推进。remote同一个 op 的 source/target elapsed 含共同 collective 和不同的前序等待，因此两端不能相加。target窗口里的等待若已吸收到拟合 service，再由 source/target ready DAG 收等待，就会重复计算。

服务账建议维持已有 `start=max(ready[src],ready[dst])`，每个逻辑 op 只安放一次服务节点。若同预算对照只需要同状态的实际整体服务，可以使用有限域有效完成成本；若要迁移到不同前序，则等待与内禀服务必须分开识别，不能把原 elapsed 当作已去等待的内禀成本。最大 worker duration 与 controller总体duration也不是精确关键路径拆分，异步启动/结束偏移仍在残差里。

double_buffer在单 op内重叠 copy/comm、两slot复用；`host_slot_wait_s`、move末stream drain、trace events全在 op完成窗内。trace event区间在同 GPU上可重叠，source/receiver的各自网络窗也可重叠；取 union/依赖链用于解释，不能用 event总和预测墙钟。move末已有drain，worker finally另有drain，后者仍是实际return路径成本，即使常常很小也不预设为0。

出处：[原DAG](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/service_cost.py:67)、[串行worker顺序](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:232)、[pipeline slot等待/事件依赖/drain](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/executor/pipeline.py:31)。

请求项必须说明 owner：controller每次请求、每个worker handler一次、每个实际executor birth一次、每个pair incarnation首次、每个op一次、每个chunk一次。它们是不同基数；不能统一叫 first。现 `fit_path` 已注明 chunk overhead折入有效字节斜率，直接另加chunk固定项会双计；需替换系数定义并补识别条件。[fit_path](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/service_cost.py:16)

旧 bounded local service已含首日志、切片/提交/sync，`request_s`只是target worker−record的残差，`dispatch_s`是controller−max worker residual。新first项不能叠旧service。固定字节/固定chunk数下，per-op与per-chunk固定项同构；固定每请求2copy下，C0/C1总service同值，无法独立决定request收费规则。[旧冻结残差定义](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/bounded_cost/model/freeze.py:60)

此外 A诊断覆盖层的 `execute_s` 包含actor create/queue/join；record `elapsed_s`包含双copy间/endlog；与原产品record/execute定义不同。保留 `measurement_mode`，不能跨mode直接合并拟合。[A覆盖层外窗](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/worker.py:220)、[A双copy record](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/worker.py:274)

## 4. SSD真实腿与归属

| 分支 | 实际腿 | 当前可观察与未识别 |
| --- | --- | --- |
| SSD源、本worker目标 | file read→HTTP response→worker bytearray/frombuffer CPU tensor→land(HBM/DRAM/live/SSD)→device sync。 | SSD owner.read整体可测；裸盘、HTTP、内存构造未分开。 |
| SSD源、不同worker目标 | 同上CPU读入→source GPU staging copy→pair broadcast→target land→sync。 | source.elapsed含读/暂存/通信等待，target.elapsed含等待source读/暂存；不能把两端相加。 |
| SSD目标 | GPU或CPU src→`tensor.cpu().numpy().tobytes()`→HTTP PUT→服务file write→响应；complete时另commit/fsync。 | 写入缓冲完成与持久化边界不同，不能把execute filled称持久化。 |
| SSD SHA | verify_run对source/target执行额外extent.read，每个CHUNK再HTTP读/CPU hash。 | verify读取不属于restore payload；HTTP逻辑read计数不等于物理disk read。 |
| SSD prepare | RemoteExtent构造时create，SSD service truncate。 | create成本归prepare，不加入每chunk读取项。 |

`ssd_store`使用buffered IO、fsync、POSIX_FADV_DONTNEED；DONTNEED是建议，不是page-cache冷态证明。`/proc/self/io`只在/status采样，整请求前后差额可能同时含恢复和SHA，缺少阶段窗口时不能分摊为纯恢复IO。当前double_buffer直接取`source['tensor']`，SSD source为None；不得按tensor双缓冲假定SSD路径已可用。

出处：[worker SSD读取与staging](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:246)、[land分支](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:192)、[RemoteExtent读写](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/memory/ssd.py:22)、[SSD service read/write/commit](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/ssd_store.py:31)、[SSD额外SHA读](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:299)、[complete中fsync](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:323)。

## 5. filled、published、live weights_ready

replica FILLED只表示完成op和同步，Catalog发布还需要全内容校验；SSD publication另需commit。live HbmTarget在prepare中导入真正模型storage，scatter可能将一个chunk拆成多个tensor copy，不能从连续replica推断其host提交成本。[IPC导入](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/keeper.py:107)、[live scatter](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/keeper.py:124)

live complete先发`target_ready_publish`事件，再逐target写`.done`原子rename；所以该事件本身甚至不是done文件已发布的准确结束点。模型侧`_wait_done`每5ms轮询，收到文件之后才退出加载路径；`target_filled`在模型侧含文件通知延迟。通用measure wrapper还会在original load_model返回后device sync再发weights_ready；生命周期resume additionally执行device sync、TP CPU barrier、清offload tag后发weights_ready。

模型启动/恢复总账应保留：load_begin→模型布局/分配→export/handoff→keeper prepare/execute→done发布→模型观察done→必要模型后处理/sync/TP barrier→weights_ready。哪些串行/并行应按实际依赖保存，不能把完整controller transfer再和包含其等待的模型 `load_begin→weights_ready`相加。不同rank局部duration的max没有共同触发/响应边界时，也不能直接当跨机全局T_ready。

源码：[complete事件与done顺序](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:334)、[done原子写](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/keeper.py:154)、[模型5ms轮询](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/sglang_hook.py:205)、[模型target_filled](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/sglang_hook.py:308)、[加载weights_ready](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/measure.py:36)、[生命周期weights_ready](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/lifecycle.py:70)。

measure.summarize现只接受单host，主动拒绝跨host monotonic相减；新多host合同应明确由同controller触发与观测所有模型rank完成，或仅报告各rank同域duration与未识别全局窗。[当前时钟拒绝](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/measure.py:53)

## 6. 最小timer schema（设计建议，不是已实现字段）

统一原始span：`run_id, request_id, tx, span_id, parent_span_id, op_id?, chunk_index?, worker?, role?, clock_domain, begin_ns, end_ns, phase, measurement_mode, status`。clock_domain包含host+boot与clock种类，process_epoch/PIDstart另存；不同domain只能比较本地duration，不能直接减时间戳。每项标记`inclusive`或`exclusive_partition`；parent只用于嵌套归属，parallel lane/dependency单列。必要host分段另存thread_cpu_begin/end_ns，不把wall−CPU直接命名为网络/锁/GPU等待。

状态与字节元数据：`executor_birth, handler_birth, thread_op_ordinal, request_op_ordinal, pair_incarnation/state, target_allocation_generation/write_history, source_generation/lineage, logger_mode, profile_mode, executor, chunk_bytes, logical_restore_bytes, submitted_copy_bytes, verified_bytes, http_read/write_bytes, kernel_io_delta_scope`。unknown显式null，状态观测与收费系数分离；身份记录和dict/json写出放在测量循环之后，实际外窗继续包含其开销。

最小必选边界：

1. Controller同ns钟：request_begin（任何fresh query之前）→inventory_end→gate_end→plan_end→transfer_begin→prepare_begin/end→execute_begin/end（filled_observed）→verify_begin/end→complete_begin/end（published_observed）→finish_end→request_return。如有阶段间工作，保存gap；不能仅靠不同调用time.monotonic与monotonic_ns混合要求整数精确闭合。
2. Worker服务：handler_body_read/parse、lock_wait、handler_set_device、execute_method_enter、executor_set_device、op序列、record/postdiagnosis、finally drain、profile_exit、execute_method_return、reply_encode/write。最小性能mode先保证execute_method外窗与op相邻raw边界；HTTP详分只有RPC残差影响决策时再开启，全部与旧mode分开。
3. Serial op/chunk：op_begin→begin_log_end→每chunk source_read/view_end→stage_submit_end→broadcast_return→land_submit_end→device_sync_end→op_record_end→end_log_end。本地路径用not_applicable或空span，不能将不存在的branch计为已测0；残余bookkeeping用相邻gap。提交段可能含已执行GPU工作，不命名纯host开销。
4. SSD需要解释时：owner.read整体；service同域file-open/seek/read/response段以request/chunk correlation关联；write与commit单独。HTTP client窗与server窗是嵌套诊断，仍不相加。
5. Live模型：同rank load_begin、layout_end、export_done、done_observed、postload_end、sync/barrier_end、weights_ready；controller另保存观测all_model_weights_ready边界。filled/published/live语义分别输出，缺少真实live运行则`model_T_ready=null`。

闭合只要求同一父窗的相邻exclusive分段之和等于父窗；父子inclusive不参与同层加总。失败单独保留不完整span与状态；未到达filled/published/weights_ready的边界null。observer成本单列尺度不从主指标扣除。

## 7. 可辨识状态与设计约束

| 类别 | 当前结论 | 规划接口处理 |
| --- | --- | --- |
| 同controller请求分段、填充/发布窗 | 可观察，现已有多数组成但gap/verify/finish需统一边界。 | 使用真实语义标签；共同时钟只闭合其自身分段。 |
| isolated op有效完成成本 | 可在冻结path/executor/bytes/chunk/状态/mode域内使用。 | 不能称物理带宽；跨mode/超域拒绝。 |
| 旧request/dispatch residual | 有实数且可复算，但机制混合、移植是假说。 | 不作为精确控制开销或首项；不与包含它的总窗重复加。 |
| first/thread/request/target收费规则 | A门未过；首差存在而统一规则未识别。 | state标签保留，系数null；不能用diagnostic拟合救准入。 |
| byte/per-chunk斜率 | 当前固定8MiB/单chunk无辨识；旧负斜率保留。 | 保持未识别；不能把op固定项和chunk固定项同时任意拟合。 |
| 纯SSD IO、纯DMA、collective等待分离 | 当前复合elapsed不提供唯一分解。 | 给复合腿有限域资格，底层项null；要新增参数须说明能改变哪个布局决策。 |
| shared HCA/PCI/NUMA容量、并发竞争 | 当前孤立/串行证据未识别容量与竞争模型。 | 原DAG复用但无contention保证，不把单流rate相加。 |
| real model weights_ready | 当前replica计时不能替代；真实模型与rank barrier另有契约。 | 无live证据时null，不以published冒领。 |

设计输入已读：AGENTS.md、docx/next_step.txt最新A8停止条目、debug_philosophy.md、first_operation_state_validation_plan与local_hbm_first_operation_cost文章。验证方式仅源码逐行核对与只读rg；没有新增tests或运行硬件程序。后续统一计划宜先明确主指标/模式/成本owner/未知参数及准入域，再决定是否需要最小额外校准。
