# 最小成本测量入口

开发编号保持 `v0.1.23.3/task2`。本目录只建设本次节点 28/30 的小测量与只读资源检查；创建容器、GPU 启动、run ID、现场清理由主 agent 统一执行。

## 已完成的只读刷新

`refresh_host.py` 通过 stdin 复用冻结的 `fresh_host.guard`，增加 CPU、NUMA、全部 GPU 普通 sysfs、KFD 数字 `vram_*` 和根盘观察。不读取 `vramtbl_*`，不调用 HIP/SMI，不访问 27/29。结果保存于 `minimal_calibration/measure_refresh_r01`。2026-10-09 10:46:42 UTC，两原功能容器身份持续、pids=1/noOOM；28 两旧容器、13 个 exact owner、六目录 SHA 保持；30 根盘 available=123,299,876,864B。

`owner_scope_host.py` 对 30 两个 zero-VRAM exact owner 读取命令/cgroup、容器身份与限定可见设备环境。两者属于外部 `/glm-5.2-int` 的 vLLM TP4 控制进程，环境读取权限拒绝，保持 unknown。GPU4–7 无非零 VRAM owner 仅构成候选，不能证明整个 DRAM/PCI/NIC 路径独占。结果保存于 `measure_owner_scope_r01`。

## 隔离环境准备

`build_setup_plan.py` 已生成 `measure_setup_plan_r01`：冻结 wrapper 复用原 `fresh_host.py`，仅新增 SYS_NICE capability 与 `main_lib/fluidckpt/` 源码白名单。两端独立 stage `/tmp/fluidckpt_three_node/minimal_measure_r01_nXX`、8GiB container、private IPC、1GiB shm，代码日志和小元数据可入 shm；payload 为匿名 NUMA mmap/GPU tensor，不落 shm 文件。原 XML 内容保持，只有新容器 manifest 的输入路径重绑定。

主 agent 上传 wrapper/spec 后执行 `python3 measurement_host.source.py setup NODE.spec.json`，创建后冻结准确 CID/StartedAt，再调用 `guard`。runtime_r03 仍按原合法目录保存新 `three_node_recovery/probe_*` 的 1MiB init/copy/XML 资格；本目录通过引用链接原件。新候选是 28 GPU0/BDF09/NUMA0 与 30 GPU4/BDF85/NUMA4，旧 GPU2↔GPU0 功能资格不能直接转移。

## 测量边界

`measurement_driver.py` 支持 `local_numa_h2d` 与 `remote_numa_h2d_rccl_broadcast` 两个微测 primitive，目前有限 workpoint 为 64MiB 与 256MiB、单流、一个完成样本。最初拟 512MiB，因资源余量缩减为 256MiB；没有执行 512MiB。它复用 runtime_r03 的精确 owner、60s 初始化准入、bounded child 监督、退出清理以及实际 BDF/XML/IB/HCA 日志验收，复用 `HostAllocation(numa_registered)` 的 mbind 和 256 页实际驻留验证。GPU 初始化、communicator setup、host 分配/触页/注册、pattern 准备、warmup、搬运和完整 SHA 分列。全量 SHA 按 8MiB 分块读取，避免一份完整验证副本。

远端计时在 receiver 本机 monotonic 时钟上，从发送 request token 前开始，source 收到 request 后才 H2D，随后 broadcast payload，receiver GPU sync 后结束，因此包含请求开销、source H2D、RCCL 传输和目标完成。各机器只计算自身持续时间，不跨机器相减。

该微测使用整 payload tensor/broadcast，**不等同**产品 `ClusterWorker._execute` 的 32MiB chunks、64MiB staging、receiver land 路径。输出永久保留 `production_executor_equivalent=false`、`planner_profile_eligible=false`、`formal_performance_ready=false`；产品 controller 的真实块恢复、完整权重正确性和同预算布局比较另由规划/执行接入任务完成，不能用本微测代替。

`build_measurement_plan.py` 接收父创建的两端 CID/StartedAt、当前前置 guard 与新资格引用，生成两大小/两端 local 及 remote 两 rank 的有限 configs。先 `--validate-config` 可纯 CPU 检查配置；正常入口需要父实际启动。before/after guard 及布局预算须与测量结果一起验收。

CPU 验证命令：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure/test_measurement_cpu.py
```

4 项通过，包括有限 workpoint 拒绝、NUMA resource ID 绑定、未知/共享范围边界和跨 chunk 全内容 SHA/尾字节损坏检出。结果引用 `measure_cpu_validation_r01/summary.json`。源码后续修订须增量部署并冻结新 SHA，不能覆盖早期 setup bundle 或测量 raw。

## 首轮 64MiB 现场结果与预算状态

`measure_local64_node30_r01`：NUMA4 实际 256/256 页，全 64MiB SHA `98dc891b...` 通过，H2D 完成 1,471,437ns。`measure_remote64_node{28,30}_r01`：源 NUMA0 实际 256/256 页，两端相同全 SHA、实际 IB/XML/GPU/HCA 路径和自然退出均通过；receiver 单时钟因果链完成 5,494,220ns。这些为单样本条件观察，初始化和建立成本单列。

本机 `memory.peak` 文件不存在，20ms `memory.current` 采样不是完整物理峰值证明。远端 64MiB node28 相对本次前置 guard 的已观测 cgroup 增量为 2,149,457,920B，超过本轮 2GiB 预算 1,974,272B，记录为 `failed_observed_budget`；node30 为 2,097,999,872B，采样内但完整峰值未定。不得以初始化后的基线、payload 大小或共享页 RSS 口径替换该预算失败。

`measure_driver_deploy_r02` 已冻结有限 256MiB 配置与保守静态应用账本：NumPy 各 dtype 临时合计上界 128MiB、校验 8MiB、证据采样 64MiB、元数据 1MiB，源 app DRAM 上界 457MiB、每端 app HBM 上界 257MiB；这些不含 runtime/共享库页。当前 r02 仅准备，因 64MiB 远端实际预算失败，须先由主 agent 明确后续 runtime+app 预算与准入，再启动新的 256MiB 条件观察。旧 64MiB raw、feda 源码和失败预算原件保持。
