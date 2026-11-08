# A 阶段启动独立资格

状态 `launch_cpu_and_source_closure_passed`，53项通过。对象为 `execution_prepared_A_r02`，launch plan SHA `5315e50b24ab6c1ba830de01f961019ab72205d2dec995db73e354040ab2771f`。本审查没有 SSH、GPU 或远程写入；现场资源准入及 A 启动由主 agent 执行。

新8个实现文件 live/base64/冻结 SHA 一致，三节点原部署资产全部保留。六worker、九有限寿命supervisor、预算、旧模型引用与原拓扑输入保持；仅w4进入首操作薄层。单copy原语仍为连续uint8 source slice→target slice→nonblocking copy→device sync；固定8MiB、两copy、32MiB chunk，没有GPU event或模型带宽拟合。

CPU A `execution_cpu_r03`、CPU B `execution_cpu_B_r02`通过真实覆盖层和 native prepare→execute→verify→complete→finish。独立raw资格分别150项通过（`review_cpu_r03`、`review_cpu_B_r02`）。trace独立核对16测量copy、8干预copy、正常PREPARED→FILLED→READY→finish、actual F/P线程出生/存活/join、prepare与执行线程隔离、A/B/scratch同时保活不重叠、最终SHA与清理。CPU每stage含bootstrap共208MiB payload、80MiB逻辑SHA；bootstrap使用历史metadata oracle且另做全byte CPU pattern比较，测量target使用真实CPU SHA。CPU不认证硬件时间或状态规律。

启动闭包按实际执行角色检查。远端w4 worker在仅部署文件的隔离project执行真实import/`--help`，只stub第三方torch（本机没有torch），产品与其余依赖全部真实加载；mode_file在同隔离包中使用纯stdlib真实CLI并证明duplicate拒绝。runner、prepare、session和cpu_replay是本地主控入口，在本地完整源码闭包执行真实`--help`。supervisor沿用上轮冻结实现与部署SHA，未作新实现变更。主核session→parent.controller为本地subprocess调用，没有远端runner调用。

两个实现审查问题已修复：worker_other被外层闭合计算覆盖会丢账，现保留原内部差额；observer最初在controller使用StringIO，现用w4独立CPU actor的原生日志与paired clock采样。最后CPU后仅journal把模拟计数与GPU计数分列，未变复制原语或计时定义；最终部署包冻结此修订。完整worker/controller/full分层闭合，R1分配/prepare、F/P创建/dispatch/join、set_device、endlog/gap和postdiagnosis均保留。

分析源码和规则已经冻结，三候选同A全部op数据同成本定义，shared项按mode×ordinal由A冻结，B只接受8个预登记key/ref，旧模型和published预测null。独立A门先审资格，再重算CPU/wall非log状态差与target交互；没有清楚结构即止A，不启动B。模型准入与可见状态规律分别判定。

`review_launch_r01`保留过期prepared与审查脚本自身相对路径/SSD检查失败；`review_launch_r02`保留把local controller误纳入remote closure的scope失败。修订根据实际session调用角色，未新增远端依赖以掩盖缺失。reviewer初版语法错误保留`review_cpu_r01/failure.json`，修订CPU检查全部通过。失败记录不覆盖。

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/review/audit_launch.py --prepared codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_prepared_A_r02 --cpu codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_cpu_r03 --cpu-b codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_cpu_B_r02 --output codex_coding/results/v0.1.23.3/task2/first_op_validation/review_launch_r03`。isolated commands、SHA与检查逐项见同目录JSON。
