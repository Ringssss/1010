# 最小资源测量后的规划与执行接入

编号保持 `v0.1.23.3/task2`。这里复用 `ClusterWorker`、`Cluster.transfer`、`restore_plan`、`interleave`、`layout_ledger` 和 `bind_real_cache`，不建设新求解器或服务框架。主 agent 负责新容器、端口、GPU 进程、部署和精确清理；本目录的 CPU 注册命令不调用 SSH/GPU。

`build_contract.py` 用已有 `physical_xml` 重建并核对冻结输入，生成新的 28/30 小子图，保留全部来源 SHA。旧 27/29 地图不改。旧 GPU2↔GPU0 资格、新 GPU0↔GPU4 的 1MiB 资格分别记录；它们不认证新 worker communicator，也不自动提供当前独占、任务预算或生产 profile。

两手工布局为 `source_concentrated`（两份 HBM 缓存都在底本所在 node28）和 `target_local_distributed`（每份缓存邻近自己的目标）。后者就是强简单 nearest-target 布局，明确记录相同 decision signature 和 `no_solver_delta=true`，不重复执行相同布局，也不把对照解释为联合优化算法贡献。两布局使用相同底本、目标、本次 2×16MiB 块集合、缓存物理字节、32MiB 分块和 rank-interleaved 顺序。

每节点 HBM/DRAM 任务预算各 2GiB。现 worker 每节点固定 64MiB HBM 暂存，因此 HBM catalog cap 为 `2GiB-64MiB`。本次集中布局的 node28 核心 HBM 账本为缓存 32MiB + 目标 16MiB + 暂存 64MiB = 112MiB；另核实际 PyTorch allocated/reserved 与 free guard。驱动 context 的物理峰值仍需父 agent 的 host 前后证据，代码不把 catalog 账本标成完整物理峰值验收。底本在 node28 共 32MiB DRAM；生成第二份 16MiB 合成块时核心 DRAM 为48MiB，digest tensor 与 tobytes 同时存在时核心 DRAM 上界64MiB，Python/运行时成本另记。

新副本 `mode=replica` 必须全量 SHA 后才能 publish；保持 `Worker.complete` 原正确性门禁。输出同一 controller monotonic clock 的 `block_buffers_filled_s`（全部同步复制完成）与 `published_collection_ready_s`（全 SHA + publish 后），准备成本单列。`model_weights_ready_measured=false`，没有真实引擎绑定时不产生完整模型权重可用指标。新 `controller_timing` 只追加时间字段，不改变原 transfer 相位顺序。

有限多路径工作点使用如下字段：

```text
request.workpoint = {workload_id, operation, path_bytes, concurrency, schedule,
 resource_path_ids, resource_incidence, bytes_budget_by_resource}
measurement = {directed_paths, workpoint, per_path_rates_Bps,
 runtime_config_id, runtime_config_fingerprint, availability_context_id,
 measured_at, source_refs, status, admission, timing}
physical.directed_paths[path_id] = {src, dst, src_tier, dst_tier,
 resource_ids, runtime_config_ids, source_refs}
shared_resource_contracts[唯一rid] = {resource_id, mode: finite_measured_workpoints,
 measurement_ids, source_refs}
```

严格准入要求 exact workload、预算、物理 incidence、配置、时效与原 owner/exclusive 门禁全部匹配。联合证据整组返回，不拆成每条路径 capacity；有限工作点不能声明 `capacity_Bps`。共享负载的观测可用 `observed_workpoint_score` 留存精确同工作点的 controller 窗口，但返回 `formal_profile_admitted=false` / `performance_ready=false`；微测 whole-payload 不自动成为 32MiB 生产执行器 profile。

CPU 命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/minimal_calibration/integration/test_integration.py
python3 codex_coding/src/v0.1.23.3/task2/resource_map/audit/test_planner_negative.py
python3 codex_coding/src/v0.1.23.3/task2/minimal_calibration/integration/build_contract.py --out codex_coding/results/v0.1.23.3/task2/minimal_calibration/integration_contract_r05
```

结果目录必须全新，已有 r01/r02 原件保留。`run_session.py --spec ... --out 新integration_*` 仅生成 launch plan。父 agent 按计划 env、`unset_env=[ROCR_VISIBLE_DEVICES]`、现有 exact-owner supervisor 启动两个新 worker，建立独立 log/dump 目录（dump 与 input 分文件）；34500 为两 host 各自 worker RPC，29970 为 rendezvous。逻辑 GPU 均 0，物理 GPU 分别 0/4，由 selected HIP/CUDA mask 及新资格绑定，不叠加 ROCR mask。

真实 `--execute` 必须引用新路径 qualification SHA、两个当前 guard SHA（默认最多 300s）、容器/boot 与两新 worker session。入口先核新 session 空状态后才 mesh，再 synth/adopt、建立两布局、恢复、全量验证并逐项 retire 自有 prefix。失败保留原 error，清理失败继续处理其余自有副本并保存日志；不 signal 或借用旧 keeper。实际新 communicator 的 IB/HCA/XML/BDF 由父 agent 与独立审查读取本 worker log/dump 验证。

实际大小由 `spec.block_sizes_mib=[16]` 明确约束，64/256MiB 仅 CPU 候选、不执行；原64/256/512MiB CPU注册结果保留。两个源、randint 临时 tensor 与运行时基线不同于 A 单源微测，不借 A 的峰值外推完整生产上界。8GiB container cap 是外层保护；采样值在 kernel memory.peak 缺失时仅为下界。

父启动监督入口为 `supervise_worker.py --plan 当前launch_plan.json --node node28或node30 --out 全新integration_worker_nodeXX_r01`，startup≤60s、lifetime≤300s；外层 native timeout 应比 300s 加清理和取证更宽。该脚本复用冻结 `runtime_gate_r03.py` 的 proc/boot/exact-cleanup，记录 child PID/start/PGID/SID/argv/boot/PIDnamespace 与 plan SHA。完成控制端测量后，父在各 supervisor 输出目录写 `STOP.json={"plan_sha256":对应launch_plan文件SHA}`，由仍持有 child 的 supervisor 做精确 TERM/KILL 与组空验收。STOP 不按旧 PID signal；无 marker 则300s deadline保留failed状态并清理。原 worker 不改，不能调用旧 process.py 不完整的 stop 作为本任务 cleanup。

最终实际两阶段：先 `run_session.py --execute --mesh-only` 建新mesh，再复用原 `warm(pairs=[[0,1]])` 的1float广播创建 lazy pair communicator，固定准备耗时独立保存，此阶段零synth。父在warm后采当前guard，逐worker填写 `baseline_guard_ref`（启动前原cgroup/GPU底本）与 `post_mesh_guard_ref`（warm后当前），新spec标 `mesh_initialized=true,pair_warmed=true`。之后同入口无 `--mesh-only`，先核当前-底本的runtime真实增量+16MiB应用buffer上界≤原2GiB，再决定是否synth；不足时保存 `budget_rejected_before_synth` 且源分配0。完整物理峰值未知保持false，不借静态app小于2GiB认证runtime总预算。
