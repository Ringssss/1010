# 拓扑资源最小校准

编号固定 `v0.1.23.3 / task2`。本目录测量暂停权重保活研究所需的最小路径事实，不建设新优化器，不改 keeper，不重建 mesh，不加载模型。只读检查和默认登记入口都不启动传输；GPU 实验由主 agent 统一运行。

## 可复用能力和隔离范围

- `topology/discover.py` 提供 HIP logical GPU→PCI BDF→NUMA、PCI ancestor、NIC/HCA 身份。
- `profiler.numa.HostBuffer` 提供严格 mbind、触页、HIP 注册和页位置采样。
- 生产 `ClusterWorker._execute` 在每个 worker 上按 op 顺序同步执行；不同 worker 对可独立前进，共享 worker 的不同流串行。`double_buffer` 只做单操作内流水，不等价于任意流并发。
- 校准复用相同 `torch.distributed.broadcast(..., backend='nccl')` RCCL 通信算子；新建临时四 rank 控制组和 `[0,2]`、`[1,3]` 两个通信组。其独立 GPU buffer 不含生产 worker 的 staging/copy、目录与调度。因此只能产生 `isolated_pair_rccl_broadcast` 路径观察，不能直接成为 `keeper_serial` 的完整速度 profile。
- 复用 `process.py` 的独有 spec、PID/start_time 登记和进程组精确停止。所有新进程 spec、PID、日志和结果均在新 `calibration_*` 目录；不创建/删除容器，不修改 keeper catalog，不访问 `/health`，不执行生成或 parked 引擎命令。

## 固定矩阵和预算

| case_id | 路径 | 目的 |
| --- | --- | --- |
| network_single_a | node27 GPU0→node29 GPU0 | 第一条隔离网络流 |
| network_single_b | node27 GPU1→node29 GPU1 | 第二条隔离网络流 |
| network_pair | 上述两条流同时 | 共享端到端路径影响 |
| h2d_node27_single_a / single_b / pair | node27 NUMA 近端 DRAM→GPU0/1 | 当前源节点 host/PCIe 控制 |
| h2d_node29_single_a / single_b / pair | node29 NUMA 近端 DRAM→GPU0/1 | 目标节点 host/PCIe 控制 |

每配置一个观察。每流复用 256 MiB synthetic GPU buffer，预热一次 256 MiB，正式观察 16×256 MiB=4 GiB；16 个调用不计作16个性能样本。每个 H2D rank 最多一个 256 MiB registered host buffer，先 mbind/触页并核对采样页全部位于指定 NUMA。SHA 校验按 8 MiB 分块，临时 CPU 副本保守上限 32 MiB/rank。每节点 host 准入按 576 MiB+2 GiB guard，GPU 物理 headroom 必须至少 2 GiB。context/communicator/runtime 开销单列观测，不塞入 synthetic buffer 字节数。

所有配置先完成一次预热；之后把接收 GPU buffer 归零并同步，避免预热或预填的正确内容掩盖正式搬运 no-op。正式计时从受控 Gloo barrier 后开始，每次 CUDA stream synchronize 完成后记录区间。最后一遍完整 buffer SHA 与期望内容、网络 source SHA 一致。校验仅声明最终 synthetic allocation 完整，不声称逐16次独立全量验证。

当前主 agent 只读 inventory 显示两机 GPU0 的 NUMA 为0、GPU1 为3，`shca_0` 位于 NUMA0（PCIe 5×16）。因此 A/B 路径的 GPU—NIC 亲和性不同；单流 A、单流 B 分开比较，不能把两条流当作同拓扑同速率。H2D 按每个实际 GPU 对应 NUMA 注册本地 host buffer，不把 GPU ordinal 当作 NUMA identity。

## 命令

CPU 验证（无 Torch/GPU 导入）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration/test_calibration.py
```

只读 inventory（主 agent 可另加 `--protect-session` 指向旧 mesh session；旧 session 原来可访问的14个端点需前后保持，其余失联端点单列）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration/inventory.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_inventory_r01
```

登记 protocol、端口和4个具体进程命令；执行前供主 agent 审查：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration/run_calibration.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_register_r01 --port 33960
```

主 agent 统一执行时使用另一个全新目录，并加 `--execute`：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/calibration/run_calibration.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01 --port 33960 --execute
```

默认 HCA `shca_0`、OOB interface `ib0`，独立 rendezvous `node27:33960`，collective timeout 90s、每 rank/驱动 deadline 600s，所有 launcher/rank core dump 设0。`--matrix network|h2d` 可仅执行已登记子矩阵；异常必要确认时 `--reverse` 反转所选 case 顺序并使用新 run，不覆盖旧结果。是否确认和选择哪个子矩阵由主 agent 决定。

## 输出与解释

`protocol.json` 固定9配置、字节、资源上限、拓扑 ID 和 session SHA；`rank_N.process.json` 是启动命令与端口映射；`rank_N.process.pid.json` 是独有 PID/start_time；`rank_N.json`、`rank_N.log` 和 `rank_N.nccl.*.log` 保留原始证据。`inventory_before.json`、`inventory_after.json` 保留前后资源和保护对象。

`summary.json` 的 `schema_version='topology_calibration.v1'`；主要字段：

- `workers`、`topology_id`（复用 path_catalog.topology_id）、`session_overlay_sha256`。
- `calibration_executor='isolated_pair_rccl_broadcast'`、`application_executor='keeper_serial'`。
- `observations[].case_id/kind/node/mode/flows/overlap/actual_nic_payload_evidence`。
- 每 flow 的 `src/dst/node/gpu/numa/bytes/buffer_bytes/iterations/receiver_start_ns/receiver_end_ns/chunk_intervals/clock_domain/completion/receiver_reset_before_timing/correctness/source_sha256/warmup/nic_snapshot_before/nic_snapshot_after/nic_counter_delta/idle_background_counter_delta/service_s/empirical_effective_GBps`；`clock_domain` 为接收 host 的 hostname/boot_id/monotonic_ns。源侧 timing 独立保存，禁止跨 host 相减。
- `binding` 区分请求配置、日志中 `NET/IB : Using [0]shca_0:1/IB ; OOB ib0`、实际 `Channel ... via NET/IB...`。变量配置和可见 NIC 不能代替使用证据。
- `resource_admission`、`before_after_preservation`、`cleanup` 同时给 `passed` 和 `status`。保护核对目录身份/generation/epoch、事务与 keeper allocated/reserved、原 PID/start_time、parked 命令文件；不通过重新读原模型内容或 forward 检查保活状态。
- `cleanup` 必须具备每个启动 handle 对应的 PID 注册和实际退出证据，并核对物理 HBM 前后 headroom（容差128 MiB，其他进程变化仍须审查）。
- `code_sha256/code_sha256_after/code_preservation` 冻结本探针及直接执行依赖（discover、HostBuffer、process runner、path_catalog、复用 CPU runtime_support），不冻结其他 agent 的场景/audit代码；`input_sha256/input_preservation` 冻结 session overlay、protocol 和4个自有进程 spec。
- `shared_resource` 只描述独立微 probe 的 `observed_end_to_end_shared_path`；`application_executor_contention_verified=false`。`profile` 不填未经生产执行器校准的容量或未知路径。

IB `port_xmit_data/port_rcv_data` 原始单位4字节；网卡 sysfs tx/rx_bytes 单位1字节。delta 要求同 boot/clock、时间递增、计数非负不回卷。实际 HCA port 的接收字节需至少覆盖该 case 全部 receiver 的累计载荷；同一共享计数器取快照区间并集，不能把双流读取的同一个计数累加。计数快照位于预热之后、校验之前，但包含 barrier/control 和所有其他端口用户。每 case 额外0.2s idle baseline 估计背景；不把它说成完全排除后台通信。

共享 gate 要求实际 binding、保护/回收、完整校验、接收完成同步调用区间重叠≥较短流80%、实际端口载荷计数通过。退化标志使用 `independence_completion_ratio=max(single_A_elapsed,single_B_elapsed)/pair_makespan`，其中 `pair_makespan=max(end)-min(start)`；≤0.85 为探索信号，并列出各流 slowdown 与 receiver start skew。两流字节固定相等，已有不同 GPU—NIC 亲和性的单流时长各自作为基准，避免不等速独立流被误判共享。双流总字节除共同 makespan 的聚合速度、再除隔离速度之和的比值仅作描述，不参与共享 gate；隔离速度之和不是物理共享容量。idle 背景估计超过5%只记 conditional。没有退化也保留 conditional 负结果。以上窗口不能证明 device kernel 同时活动，也不能仅凭退化判断 NIC 饱和；NIC/PCIe/NUMA/端点等具体根因保持未定。一次观察不能形成稳定统计排名，实测速率不是真实物理最优上界。

## 当前验证

共享 gate 还要求 receiver start skew≤较短 matched single duration 的5%；大 start skew 即便通过80%overlap和0.85完成比值，也只记 conditional，避免启动偏差被误判共享资源退化。这个门槛在GPU实验开始前由独立 CPU 反例确定。

首版CPU20项契约检查、py_compile、3个 CLI `--help` 已通过，日志保留 `codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_cpu_r01/unittest.log`；共同 makespan 和不等速独立流修订后的22项结果为 `calibration_cpu_r02/unittest.log`；增加 start-skew 反例后的23项结果为 `calibration_cpu_r03/unittest.log`。本建设子任务没有启动 GPU 搬运、重启 mesh、修改目录或清理既有进程。实际 inventory/校准由主 agent 统一运行并独立审查结果。
