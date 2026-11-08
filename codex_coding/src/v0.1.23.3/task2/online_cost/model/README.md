# 完整在线模型接口

开发编号v0.1.23.3/task2。这里只建设CPU代价假说/冻结/独立验收；GPU由主agent统一启动。主指标是同一controller时钟从请求之前到完整模型全部权重可用确认，SHA与正确输出在结束之后验证。当前代码和CPUfixture不代表实测通过。

协议应先登记C0_mixed完整校准（1GiB HBM前缀→每rank16GiB连续remote→其余local）、Hnear/Hremote两种不同来源分配留出。所有完整模型plan保持同内容、同target/rank layout、相同预算、1GiB extent quantum及相同ordered target extents/N/K/C；路径校准各512MiB/1GiB原生32MiB chunk。C0不能用空live目标发READY。

## 1. 路径模型输入

使用Python API `build_model(context, workers, samples, shared_samples, source_refs)`。返回未校准完整外层的模型。`source_refs`每项为绝对路径与实际文件sha256。

`context`字段：

- `executor`: `serial`或`double_buffer`，实际执行配置。
- `chunk_bytes`: 33554432；本轮原生serial。
- `control_mode`: 实际controller配置字符串。
- `load_state`: `warm`；只校准实际已暖化pair。
- `clock_domain`: 同一controller实际时钟域字符串；`clock_scope`固定`single_controller`。
- `worker_BDF`: worker数字字符串到fresh实际GPU BDF的映射。
- `worker_physical`: 按worker顺序列表，字段`worker/node/gpu/numa`。
- `path_context`: 实际path_key到`endpoints:[src,dst],operation_kind,source_tier,source_numa`的映射。
- `shared_resource_paths`: 实际资源ID到两个确认共享的remote path_key。
- `operation_count_interval`: 本轮`[66,66]`。
- `full_model_plan_workload`: 直接用`predictor.plan_workload(C0_plan)`生成；所有完整plan须完全相同，来源分配不参与该signature。

当前两节点绑定为编号0/1=node27 GPU4/5、2/3=node30 GPU4/5，NUMA为4/7/4/7；六条需要路径是`0:dram:4->0`、`1:dram:7->1`、`0:hbm:4->0`、`1:hbm:7->1`、`2:dram:4->0`、`3:dram:7->1`。编号/NUMA以fresh实际workers为准，不能为了套此表重写硬件事实。本轮在源构建后的资源/物理暂停检查处停止，实际路径系数、C0外层残差与留出预测尚未产生，不能把CPUfixture作为实测资格。

`samples[path]`恰好两行，格式为：

```json
{"nbytes":1073741824,"service_s":0.1,"passed":true,"verified_bytes":1073741824,"evidence_kind":"actual_path_calibration","config_sha256":"digest(context)","session":"actual_session"}
```

`service_s`为该单op源/目标原生record elapsed_s的最大值，不能用HTTP初始化或SHA窗。数据是effective operation服务，包含分块与实际loop/sync，不称裸带宽。两点非递增时拒绝，保留失败；原fit_path负截距规则保留原始diagnostics。

`shared_samples[actual_resource]`一行通常够；必须有真实绑定与实际并发证据：

```json
{"total_bytes":2147483648,"service_s":0.2,"flow_count":2,"paths":["actual_remote_path0","actual_remote_path1"],"path_bytes":{"actual_remote_path0":1073741824,"actual_remote_path1":1073741824},"overlap_confirmed":true,"resource_binding_confirmed":true,"passed":true,"evidence_kind":"actual_shared_calibration","config_sha256":"digest(context)","session":"actual_session"}
```

共享`service_s`使用同一controller并发execute段内原生最大worker execute_s，排除外部RPC dispatch/SHA；原controller完整窗也保留。共享模型采用实际paired concurrent/max isolated同字节两腿服务的比值，只乘到同offset/n、连续remote阶段的两条remote腿，再复用原workerDAG。C0 remote阶段在HBM前缀之后、local余量之前；Hremote全部余量remote。实际完整C0与Hremote须另保存paired phase overlap证明。未知capacity不能填成标称速率。

## 2. 完整模型外层校准

`state`为当前`new_state(workers)`加：`fresh_binding_verified:true,load_state:"warm",config_sha256:digest(context),warm_pairs:["src:dst",...],source_lineage:{replica_id:{worker,tier,numa,identity,generation,source_epoch}},worker_BDF:context.worker_BDF,binding_source_refs:[{path,sha256}]`。source_epoch须等于当前source worker epoch，来源ID/身份/generation由prepare实际核准。

`calibrate_outer(model, calibration)`的完整C0输入：

```json
{"evidence_kind":"actual_model_online_calibration","full_sha_passed":true,"exact_reference_output_passed":true,"all_target_weights_usable":true,"config_sha256":"digest(context)","plan":{},"workers":[],"state":{},"load_begin_ns":1,"all_weights_ready_ns":1000000001,"source_ref":{"path":"/absolute/C0.json","sha256":"actual_sha"},"session":"actual_session","clock_domain":"actual_controller","clock_scope":"single_controller","shared_phase_overlap_confirmed":true,"verification_position":"after_all_weights_ready","verified_bytes":70329718112,"resource_gate_passed":true,"cleanup_passed":true}
```

开始必须早于必要inventory/planner/resume RPC；末尾必须包括实际填满、布局绑定、设备完成和引擎ACK。外层仅取完整在线减建模DAG的残差一次，负值拒绝。它是共同残差假说，留出验证其迁移，不单独强制50µs等微段门。full SHA与输出正确性在ready之后检查。

## 3. 协议、预测冻结与留出

`protocol.json`：

```json
{"schema":"online_cost_protocol_v1","registered_wall_ns":1,"max_relative_error":0.10,"verification_position":"after_all_weights_ready","model_weights_ready_required":true,"independence_mode":"new_request_current_incarnation","scenarios":{"Hnear":{"logical_bytes":70329718112,"validation_samples":1,"context":{"weight_identity":"actual_weight_identity","target_signature":"actual_target_signature","allowed_budgets":{},"comparison_group":"same_content_target_budget","clock_domain":"actual_controller","plan_workload":{},"layout":"Hnear"}},"Hremote":{"logical_bytes":70329718112,"validation_samples":1,"context":{"weight_identity":"actual_weight_identity","target_signature":"actual_target_signature","allowed_budgets":{},"comparison_group":"same_content_target_budget","clock_domain":"actual_controller","plan_workload":{},"layout":"Hremote","requires_shared_overlap":true}}}}
```

预算必须在采集前填实际冻结ledger。`inputs.json`为`{calibration_run_ids:[],calibration_incarnation_digests:[],scenarios:{Hnear:{plan,workers,state},Hremote:{plan,workers,state}}}`。digest使用evaluate.digest。完整model为calibrate_outer返回JSON。在任何留出前同时冻结两预测：

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/model/freeze.py --protocol /absolute/protocol.json --model /absolute/model.json --inputs /absolute/inputs.json --output /absolute/frozen_prediction.json
```

留出`observations.json`为`{Hnear:[row],Hremote:[row]}`，每行：

```json
{"scenario":"Hnear","context_sha256":"digest(protocol.scenarios.Hnear.context)","session":"actual_session","run_id":"unique_actual_holdout_request","incarnation_sha256":"digest(state.incarnations)","load_begin_wall_ns":2,"fresh_binding_verified":true,"all_target_weights_usable":true,"exact_reference_output_passed":true,"verification_position":"after_all_weights_ready","full_sha_passed":true,"verified_bytes":70329718112,"resource_gate_passed":true,"cleanup_passed":true,"clock_domain":"actual_controller","load_begin_ns":1,"all_weights_ready_ns":1000000001,"source_refs":[{"path":"/absolute/raw.json","sha256":"actual_sha"}],"evidence_kind":"actual_model_online","shared_phase_overlap_confirmed":true}
```

本轮允许同engine/session当前登记incarnation的独立新请求。训练run ID不能留出复用；所有预测须在最早留出之前冻结，当前源/状态仍核验。若另登记`fresh_session`，额外要求session和incarnation与训练分离，但不由本轮自动推定跨会话迁移资格。

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/model/evaluate.py --frozen /absolute/frozen_prediction.json --observations /absolute/observations.json --output /absolute/independent_validation.json
```

每次观测独立要求`10*abs(predicted_ns-actual_ns)<=actual_ns`，恰好10%通过。不平均消掉失败，不回填留出；排序单独列示；用同10%尺度生成的区间重叠时abstain，它是分辨率约定而非未来置信区间。准确率合格不能自动批准很小差值的排序或未测拓扑。输出使用文件独占创建，保留失败与重试原件。

## 4. CPU验证

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/model/cpu_check.py --output codex_coding/results/v0.1.23.3/task2/online_cost/model_cpu_new_unique_run
```

CPUfixture所有实测字段只是结构测试，不产生GPU证据或完整模型资格。
