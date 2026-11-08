# 最小 warm 校准执行入口

本目录复用 `multitier_runtime` 的原六 worker 映射、三任务容器、三 SSD 服务、serial API、supervise、STOP 和 exact collector。仅追加新源码和新 session 输出；原阶段源码及输入 XML 的 SHA 必须匹配，不新建容器，不更新产品。

主 agent 执行 `prepare.py build` 生成本地追加包，再审查后执行 `prepare.py stage --execute` 与 `session.py --execute`。子 agent 不启动 GPU。固定 session 为 `costcal_runtime_r01`，结果位于 `cost_calibration/execution_*`；九 supervisor 子进程输出仍放原监督器认可的 `multitier_runtime/operations_process_costcal_runtime_r01_*`，随后收集到新 actual 结果目录。

原 mesh 与 pair 2→4、4→5 预热完成后，进行无 payload legacy/cached 库存查询各两次。只有库解析/加载占 legacy 总窗至少一半、下降解释至少七成 controller 总下降且精确身份保持，才选 cached。mode 文件是新 session 参数，通过独立 `mode_file.py` 原子替换；immutable 源清单不包含 mode 文件。

校准只有 localHBM 4→4、remoteDRAM 2→4、SSD 4→5，每条单 op 4/16MiB，以及同总 4MiB 的四个 1MiB op。若某路径 target 4/16MiB 非正斜率，仅对该路径三配置反序各补一次，完整保存全部样本，用两次算术平均重新拟合；仍不识别则冻结 null model 并保留失败。不得改变尺寸、挑选样本或根据 holdout 误差调整参数。

`fit_path` 与 `fit_request_model` 调用原实现。后者的强制 cold 输入仅存在单独 `fit_inputs.json`，标 `observed=false` / `synthetic=true` / warm 引用，不算实测 cold；模型 gamma 未测、约束为零，并拒绝未预热 pair 和不同 worker incarnation。原实现的负项及 clamp 均保留。

唯一 holdout 内容为上述三路径各 12MiB，顺序 4→4、2→4、4→5，保留 w4 先接后发依赖。模型与确定性 plan/prediction 在正式请求起点前写盘冻结 SHA；请求窗仍包含 fresh inventory、预算 gate、重新构 plan、prepare、execute、SHA 与发布。冻结模型仅检验选定模式一次；如果 cached 被准入，再固定 legacy 同内容一次作查询开销配对对照，目标 ID 独立。固定顺序不能排除顺序效应，不报告统计显著。

预算以共享 cgroup 绝对值（每 node 只记一次）和同 HIP API 的 selected GPU `total-free` 绝对保守上界核查，禁止从 sysfs baseline 减 HIP 样本。postwarm 与每请求准入预留 64MiB 新应用上界及 DRAM 256MiB；运行中采样完整峰值仍未知。最大已知 live 源/holdout 目标共 52MiB 位于 node27，allocator 缓存纳入 fresh HIP 保守上界。

所有源、目标和 SSD extent 由原 exact_release 按 epoch/generation/lease/身份回收；异常也回收 journal 已登记对象。最后九 STOP、原 collector 检查 child/PID/group 退出，quiet 容器保持，保护旧 PID/容器和原 XML。

CPU 接口验证：`python3 .../execution/cpu_check.py --out .../cost_calibration/execution_cpu_r01`。它检查原计划验证器接受 mixed DAG 和四 op offset、warm 仿射参数/请求项恢复、非正斜率不伪造模型，无 GPU 或远端写入。
