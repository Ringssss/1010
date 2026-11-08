# 有界 warm 成本模型

本模块只做 CPU 离线冻结和预测，编号为 `v0.1.23.3 / task2`。不修改产品、原训练结果或原失败模型，不启动 GPU/SSH。

## 模型与范围

每请求最多三个操作，每路径最多一次，每操作 4–16MiB。路径固定为 localHBM `4:hbm:0->4`、remoteDRAM `2:dram:4->4`、SSD `4:ssd:0->5`；serial、32MiB chunk、cached 诊断配置固定。1MiB×4 的训练请求保留为独立资格锚点，本模型不预测 1MiB 操作或四操作请求。cold pair 拒绝；冷启动开销为 null，不把 warm-only 约束写成测得的零开销。

local 的服务固定项取原 4/16MiB 单操作各两次、共四个原始 service_s 的算术均值，约 0.280796ms。原负字节斜率保留；byte slope 和 bandwidth 为 null。四点最小/最大 0.219493/0.333784ms 只是观测包络，不是置信区间或新组合误差保证。

remoteDRAM 和 SSD 逐字段复用原 `fit_path` 的 service_s 拟合结果：固定项约 0.370791/2.953640ms，有效服务率约 10.187298/0.918383GB/s。这些参数含执行原语、同步、HTTP 和 staging 成本，不称物理带宽。隔离 target record 的时延可迁移到依赖组合，是待独立验证的假说；组合接收记录可能含等待。

共有 worker request residual 只由六个聚合单操作的 `target_s-service_s` 构造，中位数 0.050530ms，全值保存在冻结包。范围约 0.047410–0.055871ms；将不同路径残余近似为一次共有项是显式假说。batch 行的 service_s 是最大单操作 record，不是全部 record 总和，不用于该残余拟合。controller residual 取原九聚合训练行的中位数 2.674446ms；它是 controller-minus-maximum-worker 残差，不宣称精确 dispatch 分解。

沿用既有 `ready[src]/ready[dst]` DAG 递推服务段，其后共有 request 和 controller residual 各加一次。filled 粗基准额外加原九训练请求控制段中位数约 18.874934ms，适用性待验证；服务、worker、execute 与完整 filled 的误差必须分列。SHA/发布字节及对象成本未识别，正式 published 预测为 null，旧 verify median 仅保留在训练诊断。decision_ready 恒 false；没有共享容量或 solver 收益资格。

## 冻结来源

`freeze.py` 只读旧 `cost_calibration/execution_actual_r02/measure` 的 training、training_repeats、fit_input_observed、原 frozen_model 和 final_inventory；后者只提供物理角色映射。训练 9 请求与事前 local 3 重复的每项聚合重新核对。旧 validation 和两次 36MiB 组合文件不读取、不调参。冻结包包含原文件 SHA、旧 schema2 失败、本地负斜率及所有原 request 残余。拟合/预测源码 SHA 也入 provenance；任何源码改变需另建结果目录重新冻结，禁止覆盖。

## 接口

`load_frozen(path)` 校验模型 digest 和所有来源文件 SHA，返回含 `artifact_ref` 的冻结包。`bind_session(frozen, workers, attestation)` 不改系数，只绑定通过实际 fresh gate 的新 incarnation。`predict(plan, workers, bound_model, state)` 返回 `estimated_service_s`、`estimated_worker_s`、`estimated_execute_s`、粗 `estimated_full_filled_s`、null `estimated_full_published_s` 和逐 op timeline。

迁移证据先保存 JSON，再以 `dict(saved, evidence_ref=file_ref(path))` 传入。保存内容必须具有以下字段，允许额外完整实测证据：

- `physical_gate_passed/config_verified/source_lineage_verified=true`，由执行器实机核查负责；CPU 布尔值本身不构成硬件认证。
- `control_mode='cached'`、`executor='serial'`、`chunk_bytes=33554432`。
- `worker_physical=[{worker,node,gpu,numa}, ...]` 与冻结六角色完全相同；`worker_incarnations=[[session,epoch], ...]` 与当前 workers 完全相同。
- `warm_pairs` 含 `2:4`、`4:5`，对应当前 session 实际 warm 成功证据；`model_source_ref=frozen['artifact_ref']`。
- `source_lineage` 按三个 path 为键，各值含 `replica_id/identity/generation/source_epoch/worker/tier/numa`，由 fresh 实测源生成并核 SHA。预测 op 的源四身份字段及 local_hbm/network/peer 原语须相符。

执行器必须独立保存 BDF、NUMA、HCA、通信环境、setup/来源 SHA、资源预算、warm 回复的实际核查证据。预测器检查这些外部资格的成功声明与不可变 evidence 文件相符；它不代替物理核查。已有 `new_state(workers)` 可直接提供 state，成功 warm 后再加入两 pair；旧 epoch state 不可沿用。

## CPU 检查

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/model/freeze.py --output codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/model/cpu_check.py --frozen codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/frozen_model.json --output codex_coding/results/v0.1.23.3/task2/bounded_cost/model_cpu_r01
```

CPU fixture 明确标为无 GPU，仅验证训练来源、已有系数、闭式依赖递推、request/dispatch 一次计费，以及对尺寸、batch、cold、映射、epoch、来源、原语、配置、系数和证据修改的拒绝。其预测不冒称实机冻结或已验证性能。
