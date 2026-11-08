# target-only recover_target.py 追加只读审查

审查入口是 `codex_coding/src/v0.1.23.3/task2/baseline_loading/serverless/recover_target.py`，不是 recovery_target.py。主agent已经启动 `serverless_target_recovery_r01` 后，本次只读当前实现，不编辑或重跑。

确认的关键行为：

- 原 `.process.pid.json` 记录的PID和start_time被检查。存在且非Z，或start_time不符均拒绝；不signal原PID。新run仅使用新process spec、log和process.py的PID/start_time保护停止新进程。
- 仅恢复node29 GPU0/1原target_b。新handoff phase与command独立，模型启动选项和其余template.env保持。FLUIDCKPT_TIMEOUT改为900s，新命令与metrics/phase随新run记录；原session文件不覆盖。
- Cluster.transfer返回的verification.operations在内存是int键；代码先用原op_id，再兼容str，归档S2 JSON的键类型与运行时不同已正确处理。
- 全逻辑70,329,718,112 bytes先完成真实transfer全SHA，逐ranktarget SHA再对冻结S2 SHA。生成使用exact参考。cluster.finish释放keeper IPC映射后才release exporter。
- 回收比较为生成后ready inventory→release后inventory的HBM free delta，要求至少rank weights减256MiB；不把恢复前“没有engine”与恢复后“新parked engine及KV/context”当相同free基线。engine release_begin/allocated与release_end/allocated/free独立记录。
- overlay只在生成、完整SHA、protected metadata、leases、无active transactions和物理权重回收全通过后登记新target_b command/spec/parked状态；原输入session不写回。该恢复是functional setup，不是Store新增性能样本。

只读排除一项配置疑问：现fixture容器自身LD_LIBRARY_PATH已包含DTK/HYHAL/RCCL-plugin/UCX/mpi等路径；exec_target不source `/opt/dtk/env.sh` 没有在这些路径上造成缺失，不需要改当前启动配置。

已交负责agent与父agent的非当前阻塞边界：overlay的slot新process_spec有正确登记，但继承的`new_engine_processes`/`basic_owned_processes`仍是原node29旧spec；未来若使用cleanup_bootstrap全平台入口会漏新engine，须用slot ownership/spec定向清理或明确overlay登记列表。旧`cat /proc/PID/stat`的stdout为空只证明该probe没有返回stat，当前root容器ENOENT路径正确；复用时宜区分ENOENT与权限/其他失败。当前脚本不会据此signal旧PID，故没有扩大清理范围。

首Store full失败的原pending历史保留。父agent另外记录目标exporter全部退出、仅自有adapter容器移除与keeper保留于`serverless_full_r01_cleanup/identity_before.txt`和`remove.txt`。汇总单列这份后续清理记录，不将首轮失败改成passed，也不把已移除adapter继续算pending。恢复成功和后续Store full r02仍须独立验收。
