# 完整模型在线恢复执行入口

开发编号 `v0.1.23.3 / task2`。本目录复用 FluidCKPT 原生 SGLang 加载/生命周期、Cluster、HIP IPC/RCCL、Catalog 和精确 owner 监督器，执行真实 Qwen TP2 捕获、暂停、校准与独立恢复。主指标是同一控制器在必要库存/规划/恢复请求之前计时，至全部模型恢复 ACK 后结束；全量 SHA、确定性32-token输出、暂停和回收在计时后。

## 当前配置与实测状态

本轮确定的范围为 node27 GPU4/5 两个目标，以及 node30 GPU4/5 两个源 helper，逻辑 worker 0–3，CPU NUMA 分别4/7/4/7。每个 processed shard 为35,164,859,056B，完整模型70,329,718,112B。每 rank 保留1GiB HBM前缀、完整本地 DRAM 底本、一个 helper DRAM 副本；所有可用与未使用的持久副本均计费。完整请求固定每rank33个1GiB逻辑extent，末尾805,120,688B，两rank共66个交错操作与2096个32MiB chunk。

目标节点 task DRAM192GiB、容器 hard224GiB、每目标GPU HBM48GiB；源节点 task DRAM96GiB、容器 hard128GiB，两源GPU HBM合计2GiB（含两个运行时及staging）。预算与物理余量在主 agent 冻结的 plan 内核验，不由脚本自动提高。线程环境在 worker、引擎、控制器导入之前设置 OPENBLAS/OMP/MKL/NUMEXPR/RAYON 各1及 TOKENIZERS_PARALLELISM=false，容器 pids512 保持。

r01 因未限制的64线程 OpenBLAS池达到pids512而失败。r02 已走到真实捕获、参考输出、暂停请求、helper和cache构造，但最终资源门失败；尚未完成任何完整C0/留出在线校准。r02额外SHA包装重复导入同一IPC令牌，暂停后引擎allocated没有下降。当前修正通过CPU契约检查；主 agent 的32MiB IPC探针因其他团队GPU负载被资源门拒绝，修正后的物理释放和在线预测精度均未实测。本轮是两个节点的有限场景，不声称三节点、全部路径或SSD域资格。详细记录：[在线验证报告](../../../../../../docx/experiments/2026-10-12_online_ready_cost_validation_report.md)。

## 启动与有限阶段

`metadata_probe.py` 只读有界配置/index与文件stat，不导入Torch或读取权重payload；`prepare.py`/`build_launch_plan.py`冻结新容器、只读模型挂载和准确角色命令。`bind_plan.py`绑定新的 container ID、StartedAt、boot、BDF、预算及资源证据。`orchestrate.py`由主 agent 执行阶段与精确清理；默认注册/计划输出不启动GPU。旧容器、旧模型底本与历史运行不覆盖。

`controller.py`在新node27任务容器内执行以下阶段，使用该容器的真实handoff/metrics与单机控制器时钟。每个输出目录必须新建；`--execute`需要已批准并绑定fresh admission的plan；`--deadline-s`为30–3600秒的进程内阶段上限。

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/controller.py --plan /absolute/launch_plan.json --action init --out /absolute/execution_runtime_NEW/phase_init --deadline-s 300 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/controller.py --plan /absolute/launch_plan.json --action bootstrap --binding /absolute/execution_runtime_NEW/phase_init/init.json --out /absolute/execution_runtime_NEW/phase_bootstrap --deadline-s 2700 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/controller.py --plan /absolute/launch_plan.json --action calibrate --bootstrap /absolute/execution_runtime_NEW/phase_bootstrap/bootstrap.json --out /absolute/execution_runtime_NEW/phase_calibrate --deadline-s 1800 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/controller.py --plan /absolute/launch_plan.json --action validate --bootstrap /absolute/execution_runtime_NEW/phase_bootstrap/bootstrap.json --calibration /absolute/execution_runtime_NEW/phase_calibrate/calibration.json --out /absolute/execution_runtime_NEW/phase_validate --deadline-s 1800 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/controller.py --plan /absolute/launch_plan.json --action cleanup --bootstrap /absolute/execution_runtime_NEW/phase_bootstrap/bootstrap.json --out /absolute/execution_runtime_NEW/phase_cleanup --deadline-s 300 --execute
```

这里的 `/absolute/` 必须是任务容器内 `codex_coding/results/v0.1.23.3/task2/online_cost/` 下准确的当前运行路径。init在4个worker启动后调用原生mesh与必要pair warm；随后启动原SGLang launcher的fresh auto/capture引擎，再调用bootstrap。bootstrap捕获完整真实权重、SHA与参考输出，物理暂停通过后构造helper与HBM前缀。calibrate使用6条必要有向路径的512MiB/1GiB两点及真实双流1GiB并发，再执行C0_mixed：HBM前缀→每rank连续16GiB remote→local余量。calibrate同时冻结Hnear/Hremote预测；validate分别执行HBM前缀后全local与全remote的两个新请求。各完整留出相对误差要求≤10%，无留出回填。准确率、排序与正确性分别记录。

## IPC、暂停与失败证据

产品 `capture_regions(..., verify_full_sha=False)` 默认保持原同步D2H后发布行为。实验worker显式启用True，在唯一原始HbmTarget生命周期内执行完整SHA，成功后发布VALID；不重新消费同一导出令牌。`full_sha_enabled`、`capture_s_scope`与publication明确包含SHA的bootstrap窗口。captured标记在验证和映射释放之后产生；新replica的原全SHA发布门保持，只有同步完成的完整live目标使用计时后SHA。

每次资源门失败前保存全部worker/卡的实际数据、node/card/BDF、upper/cap/margin与违反项；bootstrap的capture/reference/park/helper/cache及库存分别落检查点。物理暂停门要求每rank引擎allocated减少量和同卡实际free增加量各≥90%完整权重字节数，全部前后事实保存；在helper建设前与每次online恢复后核验。parked标志或release_end事件不能代替该门。失败不伪造.done、不退役带lease或未知消费者的来源；主 agent 先结束精确引擎，再结束本任务worker监督器。

后续资源恢复时先fresh资源检查并完成小IPC捕获/释放探针，再在新会话执行上述完整序列。r01/r02失败及所有原始结果保持不可覆盖。当前[IPC修正报告](../../../../../results/v0.1.23.3/task2/online_cost/execution_ipc_lifecycle_fix_r01/report.md)与`execution_core_cpu_r05/qualification.json`提供35项CPU检查，不作为GPU性能或释放验收。
