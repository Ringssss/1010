# localHBM 负斜率：独立原件核查

44 项原件算术、路径、lineage、时间顺序与冻结系数检查通过。状态为 `prior_origin_review_passed_mechanism_unidentified`。仅本地只读分析，无 GPU、SSH 或模型修改。原始 `execution_actual_r02` 与 `execution_actual_r03` 的 JSON/SHA 引用见 `qualification.json`。

| r02 顺序 | 字节/请求 | both-record(ms) | worker execute(ms) |
|---|---|---:|---:|
| p0 small | 4MiB/1op | .329504 | .378205 |
| p0 large | 16MiB/1op | .240403 | .280804 |
| p0 batch | 4MiB/4op | .338995/.083381/.077341/.072791 | .712510 |
| p1/p2 全部之后，p0 batch retry | 4MiB/4op | .258584/.089302/.091441/.073051 | .638549 |
| p0 large retry | 16MiB/1op | .219493 | .273913 |
| p0 small retry | 4MiB/1op | .333784 | .384496 |

单 op `large−small` 在原序和规定反序分别为 −.089101ms、−.114291ms。反序排除了“只有第一个配置冷启动”这一简单解释，但没有消除每次新 HTTP handler、分配尺寸与复制长度同时变化等条件。batch 两次的首个 1MiB op 也明显慢于其后三个 op；op 位置、线程运行状态、source offset 与目标对象同时改变，这只能支持进一步区分，不能直接命名根因。

原 `service_s` 对单 op 是 `both-record.elapsed_s`；对 batch 是最长 record，而非四条 record 之和。`target_s` 则是目标 worker 的整段 execute。拟合中两种窗口不得替换。共有 controller 请求库存、prepareRPC、校验/发布都在单 op record 之外。

## 路径与边界

全部单 op 使用同一个 16MiB source HBM 副本、同 epoch/generation/identity、source_offset=0；目标均为新 exact-size HBM replica，off=0。32MiB chunk 大于 4/16MiB，因此两尺寸各只有一次 `copy_(non_blocking=True)` 和一次 `torch.cuda.synchronize(self.dev)`。local 分支直接 source slice → `land` 的 target slice → copy，没有 staging、RCCL broadcast、SSD read 或 live HbmTarget scatter。

原 op stamp 先于 `self.log(cluster_op_begin)`，日志实现包括 JSON 构造、事件列表追加、`print(..., flush=True)`。record 窗含 begin log、source slice、target slice、copy API 和 synchronize；record 字典构造中的 timer 前工作也含在窗中。end log 位于 record 窗外，但含在 worker execute。源/目标 lookup 在 op stamp 之前；set_device、phase转换、record_targets 位于 worker execute/record 的不同外层边界。replica record_targets 仅迭代检查，没有 live metric 输出。

`prepare` 的 identity hash、reserve、`torch.empty(n)` 分配不在 op stamp 内，但分配状态可能影响随后首次复制。fixture manifest 的 t0=1MiB、t1=其余字节对本分支只是元数据；源码没有按 t0/t1 分段复制。没有原始 ptr、stride、storage offset、stream、handler TID 或 GPU 事件数据，不能仅凭源码断言真实硬件 DMA/kernel 的类型或 allocator 地址重用。

## 冻结模型能说明的范围

原四个单 op service 均值 .280796ms 是经验近似。新 r03 8MiB .268654ms 验证一次新尺寸的总 record 插值，没有证明 4–16MiB 内物理服务恒定、任意后续请求的波动上界、负斜率来源，或控制项在路径间相同。本次保持 frozen 模型 `e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`，local bandwidth/byte slope 均 null。

## 最小诊断的审查条件

stage1 保持同六 worker HTTP 流程、w4 同 source16MiB、fresh exact-size target、cached 和原 chunk，native [4,16,16,4] 后 diag 同序。两 mode 分块会混入时间漂移；native/diag 差量只能报告仪表扰动加漂移的观察，不是精确仪表开销测量。

diag 保留原 host 总 record 口径，并在同一 worker 单调钟拆 log、source/destination slice、copy API、sync；thread CPU 和 wall 各自成对。额外 timer、event record、事件 elapsed 读取与元数据组装必须说明处于哪些窗内，未覆盖开销计入 residual。GPU event 预建与初始化在窗外，两个 event 之间仍可能包含 host 提交间隔；其 elapsed 是设备队列上的窗口，不称纯复制/DMA 时间，更不能替换 host 完整 interval。

阶段比例能定位观察差量出现在哪里，不能直接证明阻塞来自操作系统、线程局部 HIP、GPU 缓存或特定 copy engine。若 diag 显示首调用特有成本，再由主决定最多两个 same-handler warm 对照，明确 handler、目标分配容量与真实 source 复制长度的控制因素。若改变日志、预构 view、固定目标容量或预 warm，各对照须逐项列明唯一变化及未消除 confound。当前没有尺寸边界扫描的依据，也不回调冻结模型。

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/local_slope/review/audit_origin.py --output codex_coding/results/v0.1.23.3/task2/local_slope/review_origin_r01`。该输出目录首次创建后不可覆盖；重新检查使用新 revision。
