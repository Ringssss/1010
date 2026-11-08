# localHBM 负斜率：最小原路径分段诊断

本目录只追加实验覆盖层，保留旧产品、训练原始结果和有限域冻结模型。开发编号维持 `v0.1.23.3/task2`。GPU、SSH 与 stage 实际执行由主 agent 统一协调。原三 quiet 任务容器、六 worker、九 supervisor、原 XML、64MiB staging、32MiB serial chunk 与 warm24/45均复用；只有w4执行入口换实验子类，其余五worker与三SSD服务保持原入口。只生成原 source0 的同16MiB synthetic DRAM→HBM bootstrap，核旧schema和bootstrap全部字节SHA相等。

第一阶段固定八请求：native `[4,16,16,4]MiB`，然后 diagnostic 同序；每mode/size两次，每次独立新目标ID及精确大小HBM target。全部使用同一新source identity/generation/epoch，source_offset=0，目标为flat uint8 replica。请求矩阵及全部plan先于首请求落盘，全部字节SHA验证后精确回收目标。无拟合、系数更新、solver、额外尺寸扫或默认后续干预。

native调用未改的原 `ClusterWorker._execute`，调用前不构view、不查stream、不record event；线程元数据在该方法返回后采样。prepare阶段两模式均记录target分配、指针、stride与memory快照；不计入原op service。native原_execute起点在 begin log 前，source/target字典查找在其前；set_device仍在原stamp前。

diagnostic展开单本地分支，保持source flat slice→target flat slice→`copy_(non_blocking=True)`→device sync，但略过原land的分派检查，并增加计时观察。host按相邻monotonic_ns/thread_time_ns边界分 begin log、source slice、target slice、event begin record、copy submit、event end record、sync；残差零仅是区间账目闭合，不能证明仪表成本为零。GPU event夹窗包含提交/host空隙，不是纯kernel时间。事件在prepare阶段预建、record并device synchronize，这是一项额外前序干预；不能声称两模式同步前序相同。execute结束后的metadata/event读取耗时独列，它在HTTP/controller窗内而在worker execute/service窗外，不当controller dispatch。两mode分块产生时间漂移混杂，差量只做诊断。

CPU回放使用真实旧source/bootstrap字段和原Cluster.transfer/native Worker生命周期；CPU bytearray double做全字节复制比较，旧SHA是元数据oracle，不是CPU pattern的SHA。CPU event/clock值不能当GPU结果。r01曾缺numpy，保留失败；r02改为标准库double后完整bootstrap+八请求+exact release通过。本机缺torch；GPU入口只在主已冻结任务容器执行，不安装新依赖。

早期只读metadata preflight：

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py preflight --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_preflight_r01
```

本地回放、冻结后由主审查并stage/session：

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/cpu_replay.py --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_cpu_replay_r02
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py build --session local_slope_runtime_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py stage --execute --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/session.py --execute --plan codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_actual_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/analyse.py --actual codex_coding/results/v0.1.23.3/task2/local_slope/execution_actual_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_result_r01
```

紧急STOP只写冻结计划的九标记，精确身份清理由原supervisor/helper完成：

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/stop_owned.py --execute --plan codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stop_r01
```

结果不覆盖，失败/修订使用新编号。原XML与默认mapping、原keepers/owners、node29均保持。每node6GiB DRAM/2GiB同HIP HBM保守上界；采样不能证明连续瞬时peak。单worker隔离仅作为后续由实际phase触发的选择，本包不实现或默认启动。

首次修订准备包为 `execution_prepared_r02`。主的stage_r01在node28成功append八源后因旧bounded helper路径gate失败；其他node未append、mode-file未创建、GPU0。新增本目录helper并修prepare引用，CPU gate+实际partial-stage衔接检查通过。r02冻结按真实node状态生成prior spec，保留旧snapshot。完整证据与修订见 `execution_stage_failure_r01/report.md`。

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/mode_file.py --self-check --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r01 --partial-stage codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_cpu_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py build --session local_slope_runtime_r01 --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r01 --partial-stage codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r02
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py stage --execute --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r02 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r02
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/session.py --execute --plan codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r02/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_actual_r01
```

最新包是 `execution_prepared_r03`：stage_r02实际helperCLI又因顶层common→主控脚本依赖不在容器内失败，node28已append九task源，其他节点未append，模式文件/GPU均0。修订使普通mode-file CLI仅stdlib；仅host selfcheck lazy import主控。CPU r02只复制helper到独立目录，以python -I真实CLI验证创建和noclobber通过。prepare按实际changed before/after SHA递推partial状态；不扩大远端旧依赖。详见 `execution_stage_failure_r02/report.md`。

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py stage --execute --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r03 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r03
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/session.py --execute --plan codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r03/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_actual_r01
```

第一阶段actual_r01已完成八请求，解析见execution_result_r01。主随后明确批准最后两个干预请求：4/16MiB各1，同HTTP handler对同source/target地址连续两轮相同slice/copy/sync。仅追加repeat_worker/runner/prepare/session四源，旧stage1九源SHA保持。两轮中只有相同序列的日志、host wall/threadCPU边界、slice/copy/sync；ptr/view metadata、stream、memory诊断只在两轮之后。无CUDA events、prepare事件初始化、额外prewarm或同步移出窗。record.bytes及logical SHA仍n，actual_copy_bytes=2n，总service/request/filled包含两轮与轮间日志开销，不是优化基准。第二轮还改变target first-touch/cache，source_slice CPU下降只检验首操作host项，不能独立解释copy/sync。

CPU replay用旧source0字段与原native生命周期，CPU bytearray两轮全部比较，实际copy序列为bootstrap16MiB、4、4、16、16MiB。logical SHA含bootstrap36MiB，实际copy含bootstrap56MiB。最新冻结execution_repeat_prepared_r01/session local_slope_runtime_r02、CPU验收execution_repeat_cpu_r02。这两个请求后不再增加GPU采样。

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/repeat_runner.py --cpu-replay --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_cpu_r02
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/repeat_prepare.py build --session local_slope_runtime_r02 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_prepared_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/repeat_prepare.py stage --execute --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_prepared_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_stage_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/repeat_session.py --execute --plan codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_prepared_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_actual_r01
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/repeat_runner.py --analyse --actual codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_actual_r01 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_repeat_result_r01
```
