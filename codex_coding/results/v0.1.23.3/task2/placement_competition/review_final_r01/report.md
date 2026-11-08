# 多来源放置竞争独立最终审查

开发编号：v0.1.23.3/task2。最终输入 case_cpu_r03；初始参数登记 case_cpu_r01。原件 r01/r02/r03、supplemental 失败均保留。审查未导入 planner 或求解器，没有重跑 solver 矩阵。

CPU 合同、队列、独立成本、Pareto 和既有准入状态共 79/79 项通过。研究门槛单独判定为**未通过**：r2/pool1、r3/pool1 的速率 0.5 低于完整 DRAM 后备 1，在全部 121 个合法 subset 的共同取数政策中均未使用。每块两个合法候选已成立，每块两个有意义来源尚未成立。

## 独立证据

- 从 8 个候选重新构造全部 121 个可行 subset，包含同内容两份副本；未强制每内容只选一份。
- 对 121 个 subset、6 个策略和原 strong-simple 3 个 control 共 130 行逐条核内容覆盖、候选来源、路径/共享资源、配额、重复计账、worker 前序与 receiver 完成时间。
- 用精确分数累积操作负载，并按 source/target ready 重算队列。pool0/1 后续作为 source 的尾时间没有误写为其自身目标 ready。
- 按事件区间累计实际同时服务的 resource/path rate，所有本场景 DAG 时间线都满足预登记容量。因此这些是零启动、恒定服务、冻结资源模型下的合法时序 witness；不代表任意操作顺序或路由的有限执行最优，也不代表硬件实测。
- independently checked objective 的两 helper 得 2、pool/target 复用得 2，以及 local-first+shared U 的两个独立评分均 2 而共同合法时序为 3。一般不能把资源下界与 DAG 的 max 直接当可达时间。

## 结果与边界

资源下界 Pareto 为额外 cache 0MiB/评分1、16MiB/评分0.5；共同固定 worker DAG Pareto 为 0MiB/2、8MiB/1、16MiB/5/6。允许预算 0/4/8/12/16MiB 时，最佳共同评分依次是 2/2/1/1/5/6。8MiB 的参考布局在空间—主评分上支配最快顺序策略和单添加均衡策略的 12MiB/1。

交换均衡与原 joint 选择完全相同的 16MiB 布局，评分同为 5/6；联合求解没有相对这一强对照的增量。三个内容 r0/r2/r3 争两个高质量 pool0 槽位，r0 的 pool1 替代更好，存在稀缺分配结构；r2/r3 的第二位置目前仍没有决策作用。共享 U 在本参数下不决定这个收益，不能归因为共享出口优化。

原 paired/单步强对照、均衡、交换和原 joint 内部规则分别明确，统一由既有 service_cost 评估。r02 修正同速 local 优先，r03 修正共同主评分/receiver 时间口径；二者均没有改参数、内容或预算。r03 hash 是本修订 run 的前置合同；首轮参数登记证据使用 r01，不能冒称 r03 是首次求解前的 hash。

## 物理状态与交付判断

旧两节点 8-worker 与 4-worker 复用方案都未准入，4-worker 方案另改变依赖。当前新 case 是 6-role symbolic 契约，没有物理部署映射或多 worker 峰值证据。保持每节点 DRAM/HBM 各 2GiB 原预算，不把共享页线性投影称作下界，不借用旧 keeper 会话资格。

physical_budget_admitted=false；physical_rate_ready=false；model_binding=false；real_T_ready_measured=false；GPUcommand=null。审查 GPU/SSH/远端写/信号/产品变更均为零。

本关结论为 `passed_cpu_correctness_research_gate_failed_physical_unadmitted`。CPU 计算可复查，研究所需的每块多来源竞争仍有缺口。保留负结果和固定参数；新增结构若有独立依据，须在计算前重新登记，不通过参数搜索制造 joint 胜出。
