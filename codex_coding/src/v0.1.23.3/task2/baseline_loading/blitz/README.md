# 最小 BlitzScale TP 分发运行入口

固定开发编号 `v0.1.23.3/task2`，上游 `594e91127714349c183d935ac68d7b0eb63fc1ed`。当前只测 S3：node27 GPU0/1 的一个完整 Qwen TP2 活跃源，分发至 node29 GPU0/1 的一个停放引擎。方法身份为 **BlitzScale 分发机制 HIP 适配**；原版 live-serving、广播性能和 CPU 参数池后备未覆盖。完整来源映射见 `lib_cite/patches/blitz-scale/README.md`。

运行器继承既有 `ScenarioRun` 的 preflight、活跃 IPC 源建立、恢复 RPC 线程、源/目标停放与清理，保留原底本和无关目录。源的建立沿用既有恢复基础设施，单列 HBM 构建、IPC attach、源驻留时间和 byte-seconds。**测量阶段取源**由本 adapter 独立决定，不调用最快源/LP 规划器。active 源的两次确定输出用于存活校验，未测持续流量干扰。

主指标为目标主机本地时钟 `max(weights_ready)-min(load_begin)`；协调器从请求到全部 ready ack、首正确 token 另列。在线 inventory/plan、prepare/传输、后置全量 SHA、生成和清理均可追踪。目标发布后先做逐字节 SHA 和32 token确定输出，再结束校验映射、停放消费者，最后退役源 owner。失败记录独立输出目录，禁止覆写旧 run。

CPU oracle 与测试（绝不访问 GPU）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/upstream_oracle.py --out codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_cpu_gate_r01/oracle_retry_r01 --rustc codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_cpu_gate_r01/toolchain/root/usr/bin/rustc
python3 codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/test_blitz.py --oracle codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_cpu_gate_r01/oracle_retry_r01/oracle.json --out codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_cpu_gate_r01/tests_r01
```

GPU 功能验收（仅由主 agent 排队启动；移除 `--execute` 只注册）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/run_blitz.py --session codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --out codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_s3_r01 --execute
```

无完整活跃源时结果明确不支持已移植范围；不静默当作其他方法运行。清理由父类生命周期协议完成，RPC 未结束或 detach 未证明时保留 owner 并记录诊断状态。每个 rank 内容身份、generation/epoch、传输路由、源码 SHA 和 source protection 可在 summary/plan 中核对。
