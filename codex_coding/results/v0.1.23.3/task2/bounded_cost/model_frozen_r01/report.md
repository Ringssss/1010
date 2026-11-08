# 有界 warm 模型冻结

最小成本模型已冻结；本结果是 CPU 模型建设与接口资格，独立新 session 的性能验证由执行/审查分工继续负责。

冻结文件 `frozen_model.json` SHA256 为 `e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`；模型内容 digest 为 `3eaac58b63756c6fb0328eec4e6168195fba23467c5da744d843325d27cb61fb`。来源仅旧 r02 原训练 9 请求与事前 local 3 重复，原数据/原 null 模型和负斜率未改。旧 36MiB 两组合和 validation 文件未读取用于拟合。

local4→4 单操作只支持 4–16MiB，四个原单操作服务时间算术均值 0.280796ms；其 byte slope/GBps 为 null，负 raw slope 保持。本地观测范围 0.219493–0.333784ms，仅四点包络，没有统计置信区间或新组合误差保证。1MiB×4 只是训练锚点，模型拒绝该预测。每 path 每请求最多一次。

remoteDRAM2→4 与 SSD4→5 逐字段保留旧 fit_path 系数，有效服务 10.187298/0.918383GB/s、固定项 0.370791/2.953640ms。只 warm、serial、32MiB chunk、cached；没有冷 pair、共享容量或裸硬件带宽认证。

共有 worker request residual 0.050530ms（六聚合单操作 residual 全部保留）和 controller residual 2.674446ms（九聚合训练中位数）各加一次。原 DAG 的 source/target ready 递推原样保留；将隔离 target 服务迁移到混合请求仍是需实测检验的假说。filled 控制段约 18.874934ms 只是原九训练中位数粗迁移基准，服务/worker/execute/full 误差必须分别报告。SHA/发布字节对象成本尚未识别，published 正式预测 null。

API 位于 `codex_coding/src/v0.1.23.3/task2/bounded_cost/model/predictor.py`：`load_frozen` 核 SHA/digest；`bind_session` 绑定 fresh 物理、配置、source lineage 与实际 warm24/45 保存证据；`predict` 拒绝不受支持的域/配置/身份。绑定只增加当前 incarnation 和证据，系数不变。实际 BDF、NUMA、HCA、通信环境、source SHA 与 warm 回复资格由执行器核查，预测器不能用布尔声明自行认证硬件。

验证命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/model/freeze.py --output codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/model/cpu_check.py --frozen codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/frozen_model.json --output codex_coding/results/v0.1.23.3/task2/bounded_cost/model_cpu_r01
```

最终 `../model_cpu_r01/qualification.json` 为 30 项 CPU API 检查通过，涵盖全部四原 local 点、旧双路径系数、混合闭式依赖、共享项一次计费、null 发布预测、域边界和 fail-closed。CPU fixture 的 local8MiB/mixed3×8MiB execute 预测约 3.005772/16.287744ms，只是离线接口算术，没有实际测量资格。CPU 迁移证据明确 `CPU_FIXTURE_NO_GPU`。

保留 draft_r01/draft_r02 和各自 CPU 原件；draft_r01 后因主审明确 published null 修改了 predictor，故其来源 SHA 已过期，不作正式冻结结果。draft_r02 与最终冻结字节一致。全过程无 GPU/SSH、产品修改、原数据覆盖或系数扫描。decision_ready=false，独立性能及排序资格尚待实测/独审。
