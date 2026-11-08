# 多层场景最小串行执行适配

开发编号 `v0.1.23.3/task2`。此目录把冻结合同的104候选和C0/C1/C2a/C2b/C3见证转换成原产品的描述符、保存事务、恢复事务和分阶段账本。另有`SSD_only`完整底本恢复资格recipe，它不修改原合同或五个研究配置。默认不选择新布局，不调用新solver，不填写速率。

## 实际接口与执行边界

`adapter.py`可用系统`python3`离线运行。无binding输出全部机会、各配置的保存/恢复所需路径和账本，`execution_ready=false`。本目录没有GPU启动或SSH入口。

```bash
python3 codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/adapter.py \
  --output codex_coding/results/v0.1.23.3/task2/multitier_scenarios/adapter_projection_NEW
```

`compile_phase(contract,binding,phase_id,run_id,current_rows,stage)`只编译当前存在来源的原生plan，使用`validate_plan`审覆盖；保存后必须刷新inventory，才能编译恢复。原`restore_plan`可接收同形态的replica/target/worker描述符，但需要外部已测profile；默认见证执行不调用它，不用合成带宽填入profile。`placement_plan`/`frozen_layout.bind_real_cache`仅有旧HBM账本或DRAM/HBM预算，因此复用其“冻结选择→实际Catalog绑定”的流程与产品API，而不向旧接口强塞SSD或另写planner。

保存来源固定为当前活owner持有的完整SSD底本，目的为见证中的HBM/DRAM副本。恢复来源为已经成功保存、校验并发布的见证副本与必要SSD块，目的为四个新的8MiB HBM replica target。全局op次序一致，各worker仍由原serial执行器运行；不声明所有链全局串行，也不声明跨worker真实争用已测。该target模式认证完整字节可用接口，不自动执行live模型handoff，不能称完整模型`T_ready`。

`execute_phase(cluster,...,trusted_admission=...,journal_path=...)`复用`Cluster.transfer(verify_before_ready=True)`，保存与恢复分别返回同钟filled/published时间以及全SHA；实际runtime资格和端点启动由外部主协调器负责。未提供可信gate、不可写新journal、worker身份变化、缺当前底本、未测必要路径、缺预算账本时均拒绝。所有拟建ID在transfer前落journal。异常携带`AdapterExecutionError.journal`，文件保留已完成阶段、失败位置及精确清理集合。`cleanup_owned`只处理本run的HBM/DRAM拟建ID，缺项跳过；检查当前身份/租约/事务，再核八个底本generation/epoch/VALID状态，回验清理结果。不得全量retire一个model identity，不调用底本owner.close。

## binding输入及可信来源

binding JSON是编译输入，不是独立物理实证。`evidence_refs`的SHA只防止引用内容漂移；`verified`/`admitted`状态本身不能证明机器、路径或预算事实。正式执行还必须注入主协调器审查后的`trusted_admission`函数。该gate须独立校验允许的node27/28/30及container/boot/PID-start/真实GPU BDF/NUMA/HCA、活SSD mount/owner和当前session、选定路径实际全SHA结果、最新GPU/runtime guard及每node聚合预算；不得认可本目录`cpu_api_fixture`、示例或合成报告为实机证据。callback返回`status=admitted,contract_hash,phase,run_id`表示调用者完成上述审查。该物理准入生成器属于下一关，当前交付不包含现成实机准入。

必须字段如下；[CPU fixture产物](../../../../../results/v0.1.23.3/task2/multitier_scenarios/adapter_cpu_r02/binding_cpu_fixture.json)给出完整结构例子，其purpose及path/admission证据只适用于CPU测试。

| 字段 | 内容与检查 |
|---|---|
| `contract_hash`, `purpose`, `valid_until` | 精确冻结hash；purpose为physical或cpu_api_fixture；过期拒绝 |
| `evidence_refs` | 名称→本地绝对path/sha256；存在性与内容hash检查。语义由可信gate核实 |
| `workers` | 顺序0–5，logical_worker w0–w5；node/gpu/numa、当前epoch/session、endpoint、capabilities、各tier的Catalog budget、64MiB staging。两worker同node，三node不同，六物理GPU不同 |
| `manifests` | 每rank真实processed TP4 meta/index/identity；identity用产品函数重算；packed字节连续且各8MiB。当前规模是冻结小资格场景，扩大模型必须另建合同 |
| `ssd` | n0/n1/n2→HTTP endpoint、storage_node、current_owner_session、backing_evidence_ref；SSD服务位置不能用GPU proxy位置代替 |
| `base_ids` | b0–b7→当前活worker Catalog中底本ID，校验VALID、完整extent、alloc、epoch/generation及SSD endpoint/storage_node。不根据盘上文件名恢复owner |
| `paths` | 仅已需要资格的原生path_key→status/serial/source_epoch/target_epoch/valid_until/evidence_ref；SSD还核endpoint/storage_node，覆盖HTTP腿及后续GPU腿。104机会不要求全扫，未测来源不可用于当前plan |
| `phase_admission` | 每配置→admitted/evidence_ref/valid_until、runtime_staging_inclusion=`included`或`separate`、budget_accounting_complete。gate复算各node聚合cap与可用余量，不拿6个worker预算简单相加当物理准入 |

新的聚合预算由本任务主协调器批准，适配器不硬编码旧2GiB。`budget_accounting_complete`指各项有界估算与postwarm可用额已经审查，不要求首次运行前已完成全流程峰值实测；`physical_peak_measured=false`仍保留。暂停容量是冻结布局约束，GPU target、64MiB/worker staging、runtime/通信/注册、SSD读取临时buffer/pagecache、保存和过渡峰值分别计。历史runtime含staging时不得再重复加384MiB。C0已知HBM项448MiB，其他研究配置432MiB；它们不是完整物理峰值或DRAM预算。SSD_only对应416MiB已知HBM项，完整底本一直32MiB。

## SSD生命周期

真实`RemoteExtent`初始化必调用`/create`，服务用`open('xb')`；不能把同ID已存文件当open-existing恢复。`commit`是文件fsync，未提供重启manifest恢复或掉电认证。`close`调用`/delete`，所以完整底本必须保留当前owner/session。默认适配器不创建SSD底本，也不清理它。若owner退出、epoch改变或只有文件而没有当前Catalog，编译拒绝。double_buffer不接受当前SSD tensor=None来源，本适配器固定serial。

## CPU验证

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python \
  codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/cpu_api_check.py \
  --output codex_coding/results/v0.1.23.3/task2/multitier_scenarios/adapter_cpu_NEW

/home/zhujianian/miniconda3/envs/sglang/bin/python \
  codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter/journal_api_check.py \
  --output codex_coding/results/v0.1.23.3/task2/multitier_scenarios/adapter_journal_cpu_NEW
```

第一入口实际调用产品`Cluster.transfer/verify/finish`和`ClusterWorker.prepare/_execute/land/verify_run/complete/finish`、Catalog及wire。CPU tensor实际搬运并全SHA核验每配置四target全部32MiB。仅CUDA设备操作、GPU分配/暂存、NCCL广播和worker控制HTTP替换为CPU mock；暂存mock仅4MiB，真实64MiB预留由源码和账本验证，不能把mock内存视为GPU峰值。三本地CPU HTTP服务及`RemoteExtent`文件协议真实运行，每份底本为4MiB，全部八文件在测试停止服务后保留。服务是本地普通文件，结果不是SSD介质性能或冷I/O资格。第二入口只mock transfer/Catalog元数据，注入恢复部分发布后失败，审journal及精确清理，不能当native传输证据。

r01原API六阶段已完成后，预期open-existing负路径导致FileExistsError/RemoteDisconnected，测试未捕获该异常而失败，原failure/log/文件保留。r02修正异常种类及journal/清理边界后79项通过；单独journal验证7项通过。所有结果仅CPU接口资格，physical/performance/实际模型ready均false；没有GPU/SSH/远端操作、没有速率样本、未修改产品代码。
