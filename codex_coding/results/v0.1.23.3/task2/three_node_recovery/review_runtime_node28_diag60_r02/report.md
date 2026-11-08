# node28 fresh 初始化通过：独立23项验收

28在原private容器、原cap/LD和线程配置下，fresh GPU初始化通过60秒诊断期限；实耗18.637711728s。实际BDF `0000:55:00.0`，fresh HIP free67,427,631,104B，超过2GiB+1MiB门槛。child64/start485400521自然rc0，无signal，之后absent、组空。前后原keeper实际HIP free67,429,728,256B，差0；host/container memory.events的max/oom/oom_kill均0。

Torch导入1.823252434s，set_device14.4236846s，BDF查询0.073339455s，HIPfree查询0.000137601s；仅导入与set_device已超过原总15秒。新副本d5331fa2相对原927f仅三行非NCCL期限记录/调用改为60，worker、清理和数据路径保持。源码副本、新run和旧15秒失败分别保留。

因此当前应更新为“28 fresh runtime初始化在该配置通过，原15秒预算不足”；不再把此前失败当永久GPU不可用、deadlock，或证明缺某个IPC/cap/LD配置必然不能初始化。成功发生在较长期限及此前多次尝试之后，低层耗时原因和历史重试的影响尚未通过单独机制实验拆分；不扩大到所有GPU或节点。

新旧容器完整身份、13旧owner/worker/keeper和六目录hash持续，lease0/事务空；原源码和完整权重内容边界保持。copy、XML和性能本次未执行。23项独立证据/保护检查通过；r02显式报告runtime_passed=true、diagnostic60_runtime_passed=true、original_15s_admission_passed=false。r01的runtime字段曾表示原15秒准入，r02澄清该命名而不改原结果。

验证入口：`review_runtime/review28.py --diagnostic60 --before .../admission28_guard_before_diag60_r01 --run .../probe_node28_diag60_r01 --after .../admission28_guard_after_diag60_r01 --out .../review_runtime_node28_diag60_r02`。审核只有本地原件读取，无SSH/GPU。
