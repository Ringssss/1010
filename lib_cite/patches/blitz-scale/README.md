# BlitzScale 最小分发机制 HIP 适配

冻结上游为 `594e91127714349c183d935ac68d7b0eb63fc1ed`。原始 checkout `lib_cite/blitz-scale/` 未修改；`lib_cite/blitz-scale-hip/` 是该 commit 的独立 detached worktree，用作可复现抽取输入。本轮采用源码忠实抽取/port，没有完整移植 CUDA 服务，因此不产生虚假的全服务 HIP patch。

产品代码：`main_lib/fluidckpt/benchmarks/blitz_adapter.py`。运行与验证入口：`codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/`。实际身份为 **BlitzScale 分发机制 HIP 适配**，不是原版 live-serving 系统。

| 原始组件 | 保留的决策/行为 | 本轮范围与适配 |
|---|---|---|
| `steersman/mod.rs::record_model_loaded/unloaded` | machine/model 缓存、loaded 集合、最后 load 时间；unload 不刷新 TTL 时间 | 只登记完整 TP 逻辑实例的 leader，避免把不同 TP 内容的 GPU 当作互换源；首次观察有效 generation 时登记 load |
| `machine_has_cache/find_one_rank_within_cache` | 非空 loaded 集合或 TTL 未到期；空集合即使 TTL 仍有效也无法形成 GPU 发送源；busy semaphore 不选作 local 源 | 缓存状态不能凭 TTL 伪造物理 HBM 源；来源 generation/epoch 由现有 prepare 检查 |
| `assign_nvlink_chains` | 每机第一个可用缓存源，并复用于该机后续目标 | 原始 Rust HashSet 迭代无确定顺序；port 明确使用登记顺序。当前两主机 S3 无目标本机 HBM 缓存 |
| `exec_blitz.rs::span_max_rdma_sending_replicas` | 非 unidirect 分支：normal Decode → shutting Decode → shutted Null，过滤 RDMA busy | 单完整活跃源；不引入 EPDT、fastest、LP 或带宽取源 |
| `rdma_bcast_scale_n_decode` | source vector `pop()`、完整 TP 源组与目标组 | 当前一个 target，自然退化为按 TP rank 的 P2P；不能据此报告完整广播性能 |
| `service.cc::RdmaBroadcast` | `src_ranks[tp_rank]` 加上 `dst_ranks.begin()+tp_rank`、步长 `tp_size` | 两 rank 内容不同：0→2，1→3。真实传输使用现有 HIP/RCCL 后端 |

本轮不覆盖 CPU 参数池来源策略、SSD loader、Tanz 多 lane 矩阵、多目标流水链、逐层提前服务、请求/SLO 控制器。无合法完整 HBM 源或选定源路径未验证时明确 `not_supported`；失败传输由现有 abort/lease 清理处理，没有自动替换 baseline 或虚构 CPU 后备。`ModelLoader::load_model_gpu` 存在本机 H2D 路径，单独知道它不等于已忠实移植整个参数池取源策略。

CPU oracle 从冻结源码直接抽取：Steersman 仅移除 serde/tracing，保留真实 Rust `Instant`、集合与原子计数；source selector 取原始表达式；TP chain 取原始 C++ 循环并配最小 request accessor。测试覆盖 TTL、空缓存无 sender、semaphore、source 顺序和 TP stride 等。原始 CUDA/RDMA/Tanz 服务不在该 CPU oracle 的验收范围内。

oracle 结果：`codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_cpu_gate_r01/oracle_retry_r01/oracle.json`；CPU 测试：同目录 `tests_r01/tests.json`。首构建因本地解包 Rust 未设置动态库搜索路径失败，原始日志保留于 `oracle/rust_build.log`，修复后的重试独立记录。Rust 工具链仅解包在该结果目录，未安装到全局。

所有 GPU 执行由主 agent 统一排队；本说明和 CPU 注册不表示 GPU 功能/性能通过。
