# Baseline源码能力与最小方法适配

开发编号v0.1.23.3/task2。本轮只做源码核查和CPU一致性验证，新增GPU性能样本0。

ServerlessLLM忠实复用整模型pinned命中/磁盘等待估价与迁移候选排序，标记decision_only_not_runtime。输入model_size为bytes、带宽bytes/s、waiting与输出为秒；需上游store与硬件状态。原版miss分支为waiting+model_size/disk，不加入PCIe项；hit分支为model_size/pcie。TP2模型必须在节点整模型语义中合计两rank字节；本子模块不制造任何逐rank传输计划。

固定目标只有一个合法候选时可复用估价，但没有位置决策增量。未实现原版多层加载后端、迁移、缓存驱逐与运行时调度。

BlitzScale源码已有机器缓存/TTL、TP与NVLink/RDMA广播能力。缓存metadata命中不证明物理完整来源；HashSet首源没有稳定tie-break。当前TP2两rank内容不同，不直接构成同内容broadcast。当前单目标串行copy缺广播/relay状态、源组映射和逐层服务接口，故不硬造分发适配。单目标walzer_Tanz路径max_lane_num受目标逻辑实例数限制，不提供多接收者扩容优势。

S4完整DRAM后备仍合法，8GiB不能保住65.5GiB完整HBM副本；该结论只限制完整HBM/IPC机制。Janus式warm要求目标本地完整host，不等于原版Janus没有其他恢复路径。S5本轮软件worker串行观察不推断物理fabric共享。

| 方法 | 身份 | S1 | S2 | S3 | S4 | S5 |
| --- | --- | --- | --- | --- | --- | --- |
| sglang_original | original_system | conditional | conditional | conditional | conditional | conditional |
| janus_original | original_system | not_adapted | not_adapted | not_adapted | not_adapted | not_adapted |
| blitzscale_original | original_system | not_adapted | not_adapted | not_adapted | not_adapted | not_adapted |
| serverlessllm_original | original_system | not_adapted | not_adapted | not_adapted | not_adapted | not_adapted |
| sglang_ipc_hip | hip_adaptation | applicable | infeasible | infeasible | infeasible | conditional |
| janus_host | mechanism | infeasible | applicable | conditional | conditional | conditional |
| janus_d2d | mechanism | infeasible | applicable | applicable | conditional | conditional |
| fastest | mechanism | applicable | applicable | applicable | applicable | conditional |
| resource_lp | mechanism | applicable | applicable | applicable | applicable | conditional |
| blitzscale_distribution | mechanism | not_adapted | not_adapted | not_adapted | not_adapted | not_adapted |
| serverlessllm_locality | mechanism | not_adapted | not_adapted | not_adapted | not_adapted | not_adapted |

各单元具体合法来源、后备、缺口、准确commit、源码行号与历史证据路径见summary.json；applicable是机制表达能力，原版/策略未适配和历史GPU证据各自保留。

CPU校验：通过；冻结源码与工作区：通过。

停止条件：固定目标及同一完整host来源不能证明ServerlessLLM选址增量；不继续以nearest/fastest替名。BlitzScale若要比较分发，先限定完整TP源组与可执行transfer后端；否则保持not_adapted。
