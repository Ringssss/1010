# 真实 keeper 小权重块桥接

固定 `v0.1.23.3/task2`。入口 `run_bridge.py` 复用存活的四 worker、完整 Qwen DRAM 底本与 `Cluster.transfer` serial，不启动模型、loader 或 forward。单 A 为 rank0/worker0→worker2，单 B 为 rank1/worker1→worker3，pair 使用同两路径。每正式配置各一次、默认每流 1GiB、32MiB chunk；正式前一次 pair 32MiB 全 SHA 预热，两路径均预热。所有 target 归属独有 `bridge_*` run ID，保留源 manifest/identity 与合法 extent，分别验 SHA、lease、publish 和退役。源不迁移、不释放。

父 controller 必须在 node27 执行，沿现只读工具在两节点采集保护、PID 和日志；它通过 SSH/dockerexec 在 node29 当前 owned 容器调用同文件 `--transfer-only` 子入口，子入口只执行 transfer/HTTP，不调用假定 node27-local 的 SSH helper。HTTP execute receiver 的两个调用因此有同主机/boot_id monotonic 包络。它包含 RPC dispatch/response，是初步性能口径；worker records 的 elapsed 与原日志 wall 时间另留原始证据。现 keeper 的 `record_targets` 仅为完整 live target 发 monotonic，小 replica 无该事件；`worker_true_monotonic_window=null`，不声称 kernel 并发、不套微 probe 的 0.85 门槛、不称 `T_ready`。Cluster coordinator 时间含阶段后 inventory 与200ms idle计数采样，receiver RPC 包络不含这些观测。正式 execute 前/后按每 HCA/port 保留接收端原始计数与独立 idle 窗口；只在实际 initlog 可确认端口时归因，同口不叠加。

目录保护复用只读校准 inventory，独立读取实际 keeper PID 的白名单环境及其注册日志/`NCCL_DEBUG_FILE`，保留完整日志与 SHA。不从隔离 probe 推定当前实际 HCA/GDR。环境未设置 `FLUIDCKPT_EXECUTE_PROFILE_DIR` 时不启 profile；无热 patch、无重启。结果会保留 keeper catalog 的 run-owned `RELEASED` 描述符，只从保护签名中排除这些已退役条目；未归属 run 的条目严格保留。

CPU 检查：

```bash
python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/test_bridge.py
```

登记（无 SSH/HTTP/GPU）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/run_bridge.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/bridge_registration_r01
```

GPU 执行由主 agent 选择未使用 run ID、登记并在 node27 运行同一入口，加 `--execute`、`--protect-session` 指向旧22worker会话。每流 target 至多1GiB、每卡额外2GiB准入 guard，保留现 staging64MiB。HBM physical free前后容差128MiB，配合 keeper allocated/reserved严格保护，不能漏掉1GiB临时target。HTTP timeout 默认180秒、上限300秒、core0。失败及重试独立目录；任何 case 失败即停止后续配置，只尝试清理该 run 的 target。源码仅冻结本驱动直接依赖，不纳入同时建设的场景适配模块；运行前后验证这些文件和session/legacy输入SHA。
