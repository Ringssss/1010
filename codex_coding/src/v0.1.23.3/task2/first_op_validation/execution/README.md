# 首操作状态实验执行覆盖层

编号保持 v0.1.23.3/task2；唯一协议为 A 八请求及条件通过后的独立 B 八请求。这里只实现实验薄层，产品代码、baseline、旧冻结结果不改。GPU/SSH/部署由主 agent 统一执行；本子 agent 仅做本地 CPU 回放与包建设。

`worker.py` 复用上一轮 cached inventory worker 与原 HTTP handler。诊断借现有 `prepare` 端点提供 cell open/close 和只 CPU observer 控制。cell open 用原 Catalog.reserve/allocate 分配 A/B/scratch，计入预算并持有真实 lease；每个普通请求仅一次借用对应未消费 BUILDING 目标，后续 prepare/verify/publish/finish 生命周期仍为原实现。没有 FILLED→PREPARED 重置。A/B/scratch 同时存活且地址范围不重叠，cell close 才归还 lease 与精确 retire。

独立 prepare actor 在两个请求间保持同 birth。fresh 写 scratch；pretouched 写该请求目标，均 8MiB copy + device sync。F 每请求创建并 join actual executor；P 跨两请求保持同 actual executor，R2 后 join。F/P 使用同 Actor 队列，actor 不获取 HTTP handler 已持有的 worker.lock，因此没有跨线程锁等待死锁。executor 首 copy 前仅执行明确计时的 set_device；view/log/query没有预热调用。两轮后才组装 tensor/stream/memory 元数据，全部留在 worker execute/controller/full 计时内。

实际 worker 独立 CPU observer actor 使用同 wall/threadCPU clock 与原 logger，测 64 个样本，保留原值与波动尺度；不在被测 executor 上运行，不从 GPU 观测减除。

本地入口：

```bash
python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/runner.py --cpu-replay --out codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_cpu_NEW
python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/runner.py --cpu-replay --experiment-stage B --out codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_cpu_B_NEW
```

CPU 输出实际执行 canonical schema 与真实生命周期 trace；torch 为设备替身，所有复制原语由同覆盖层执行。CPU 测量不可用于 GPU 模型拟合。bootstrap 比较冻结的原 SHA 元数据，同时独立全字节比较 CPU 模式；八测量目标使用真实 CPU SHA256。`mode_file.py --path` 在只复制单个文件的 `python -I` 环境验证，CLI 完全 stdlib，并以 `open('x')` 拒绝覆盖。

主 agent 的只读现场准入、A 部署及执行入口：

```bash
python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/prepare.py metadata --out codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_metadata_NEW
python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/prepare.py build --experiment-stage A --session first_op_runtime_A_NEW --out codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_prepared_A_NEW
python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/prepare.py stage --execute --prepared PREPARED_A --out codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_stage_A_NEW
python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/execution/session.py --execute --plan PREPARED_A/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_actual_A_NEW
```

现场准入复用 `local_slope/execution_repeat_prepared_r01` 的完整旧资产链；具体六 worker 和九 supervisor、6GiB DRAM/2GiB HBM 每 node、STOP/TERM/owner 精确清理沿上一轮纠正实现。source bootstrap 16MiB 另计，八请求测量复制 128MiB、准备干预 64MiB、目标逻辑 SHA 64MiB。

A 独审通过且模型分析准许 B 后，使用独立新 session；B build 必须传 `--experiment-stage B --prior-prepared PREPARED_A --prediction FROZEN_PREDICTION`。源码未变时新包 assets 为空，仍独立生成 mode/plan/output；拒绝覆盖冻结 A 源码。模型 schema `first_op_model.v1` 的八个预测键按 B 反序固定，开测前校验并写入 preregistration，B 每条记录保留 key/ref/冻结预测行，起止核 SHA；旧 bounded 正式预测保持 null。

canonical 为 `measure/stage_measurements.json`，schema `first_op_validation.v1`。每 request 保留两 op 的相邻 wall/threadCPU ns 边界与完整 flow。`timing_components` 精确闭合 worker execute：op 总时长、set_device、record 外 endlog/gap、postdiagnosis、handler create/queue/wait/join 和其余 worker 工作；`controller_components` 的 query/prepare/full_other 加 controller execute 精确闭合 full。R1 fixture 分配计 prepare，P R1 create/P R2 join 按实际所在请求计费。不同机器只组合 duration，不相减跨机 timestamp。所有失败/重试独立 revision，不覆盖原件。
