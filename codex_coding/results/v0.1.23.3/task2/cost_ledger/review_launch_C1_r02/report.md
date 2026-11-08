# C1启动源码与CPU资格独立审查

211项通过，输入为 `execution_prepared_C1_r01`、`execution_cpu_r04` 与 `analysis_predeclared_r03`。本审查无GPU启动或远端调用；物理准入由主agent重新检查。

验证固定六请求顺序及N/K/chunk矩阵、96MiB独立bootstrap、单node27/GPU0/rank0且无SSD/pair warm、有限生命周期与6GiB/2GiB任务上限。原135项部署源码SHA保持，新10项执行源码仅追加；CPU实际回放时冻结的每项执行源码与最终launch版本逐一一致。原bounded模型不作为新模型输入，原XML输入保持且dump与输入分离。

最终分析协议/源码引用及固定绝对误差门一致；原生copy loop未重写，完整 `super().execute(body)` 父窗与仅替换原生模块引用的clock proxy符合合同。远端worker在仅冻结源码包、仅torch stub的隔离环境下CLI成功，mode helper使用真实stdlib且重复创建拒绝覆盖；prepare/runner/session/cpu_check按本地controller角色检查其完整本地依赖。

前一次 `review_launch_C1_r01` 保留两个审查器自身路径失败：相对output配合隔离cwd导致worker路径重复，mode basename未满足严格namespace。修正仅限review/audit_launch.py，本次新目录重跑通过。

启动 readiness：CPU/源码/包可进入主agent现场准入。该PASS不认证HBM性能或连续资源峰值。C1完成后需独立raw/resource/模型审查才能授权C2；最多八请求、通过前预测资格未知，published/live保持null。
