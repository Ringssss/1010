# localHBM 负斜率独立审查收口

最终状态 `local_slope_evidence_review_passed_first_operation_host_cost_observed`。原件44项、修订启动114项、首八请求142项、两请求启动33项、两请求实际78项分别通过；复核汇总121个引用SHA保持。检查覆盖存在重叠，不能把检查项数当样本数。报告45.6/45.7的phase表、尺寸差分解、两轮总窗和filled/published均与raw一致。

旧反序负斜率仍是原始事实。新native序列没有稳定复现，1.037124ms的16MiB慢点保持；instrumented序列的大部分尺寸差来自新增event记录阶段。最后无event的两条同handler重复复制实测source slice首→次CPU下降77.210µs/61.481µs，first-round尺寸差−56.831µs、second-round+34.370µs，说明首操作host成本可以盖过正尺寸完成增量。具体内部初始化机制、旧各点唯一原因与native慢点原因没有认证。

实际安装header排除了单一维fullslice跳过slice的源码解释；本路径没有按manifest分裂、额外chunk/live scatter。两轮copy/sync还同时改变目标first-touch/cache状态，第二轮不是正常首次恢复服务或优化收益。固定模型保持经验近似和原SHA，后续若拆出首操作项，须从旧含首操作的service中拆除对应部分，避免双计。

两会话分别逻辑全SHA96/36MiB，payload copy96/56MiB，总计逻辑132MiB、payload copy152MiB。后一个物理复制计数含重复写入，不能称总HBM流量；重复复制的逻辑SHA只检两轮后的最终内容。主窗始终保留首轮、两轮间隔与遥测，未减出“净服务”。

两个会话九进程统一STOP/TERM、全部PID/组退出、有效Catalog/used/事务/SSD清零，旧资产/容器/owner/XML保护通过。预算采样均合格，完整连续peak未知。无新增GPU请求，原模型不回调，同预算layout排序与live模型T_ready仍未认证，decision_ready=false。

旧部署两次失败及审查scope缺口、自有审查字段错误均保留于对应revision；最终入口不混用已superseded的launch资格。完整原件与SHA见 `qualification.json`，阶段细节见 `review_actual_r02/report.md` 和 `review_repeat_actual_r01/report.md`。
