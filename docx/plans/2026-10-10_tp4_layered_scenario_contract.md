# TP4分层场景契约与部署可行性

日期：2026-10-10。开发编号：v0.1.23.3 / task2。父计划[18.22](2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)，正式记录[报告37](../experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)。本阶段交付CPU场景契约、已有策略加瓶颈均衡对照及物理部署可行性；真实模型绑定和GPU运行是后续独立关口。

## 1. 要回答的问题

四个必要目标恢复时，位置预算和共享出口分别如何改变整体完成时间与值得占用的缓存空间？在相同底本、候选和空间机会下，已有strong-simple、瓶颈均衡与joint是否出现有意义的决策差异？

采用理论文档中的耦合机制，逐层改变一个关键条件。C0验证充足位置预算；C1验证多目标和位置配额；C2保留C1主场景的所有内容、候选与配额，仅引入两个来源出口共享。无需得到joint胜出才算完成，能准确识别同解、平台期和无效缓存就是结果。

## 2. 内容与逻辑角色

- 四个逻辑rank r0–r3，每rank两个4MiB块，共8块，总需求32MiB。
- 每rank的两个块来自同一完整8MiB DRAM底本；4份底本分布在4个逻辑source workers，底本总32MiB。
- 四个目标使用4个不同target workers。source与target角色分离，共8个symbolic workers；同一target的操作顺序必须一致，不把来源共享导致的等待遗漏。
- 每块有一个在自身目标HBM的候选，共8个。所有case保持完整候选集合，即便某处cap为0，也以不可行约束拒绝而不是改变某策略的候选机会。
- 底本始终合法可用，候选可以不选满预算；不提供任意relay、压缩、组播或远端新增副本候选。

这是四rank字节区间的synthetic契约，不是32MiB真实TP4模型。原topology_cases.validate_case硬编码TP2，不改其旧资格规则；新增本地CPU契约需独立核四rank/完整coverage/目标distinct，直接复用已有通用planner函数的能力。

本轮本地候选集合适合隔离缓存分配和共享资源影响。远端HBM存放的选择需要以后在同一契约下同时给所有策略增加相同候选，并证明它改变待研究决策。

## 3. 场景表

空间单位u=4MiB。允许预算与实际选用空间分别记录。

| case | 目标位置允许缓存units | 全局上限 | 来源出口 | 与前一层的变化 |
|---|---|---:|---|---|
| C0_local_full | [2,2,2,2] | 8u=32MiB | 四独立symbolic出口 | 简单充分对照 |
| C1_balanced_control | [1,1,1,1] | 4u=16MiB | 同C0 | 可改善所有目标的对称平台期控制 |
| C1_uneven | [2,1,0,1] | 4u=16MiB | 同C0 | 位置分布不均，r2无缓存额度 |
| C2_shared | [2,1,0,1] | 4u=16MiB | s0/s1共享U；其余不变 | 同C1_uneven，仅出口共享关联变化 |

为复用现path_for按node构造network资源的方式，CPU中C2可能合并两个source的symbolic node标签。必须提供稳定role-resource映射，检查NUMA内存、target入口、worker出口、合法路径类型没有额外被合并；这是图中的反事实配置，不是对真实节点或默认XML的修改。

所有带宽/服务系数显式symbolic、无实测速率。CPU对照的公用假设为一块remote服务一个单位时间、local服务四倍速。未绑定实际硬件的capacity字段保持unknown，理论系数不能写入正式成本profile。

## 4. 强对照与公平性

| 策略 | 规则 | 用途 |
|---|---|---|
| base_only | 只使用完整DRAM底本，额外HBM为0 | 判断缓存是否真正改善主目标 |
| local_capacity_fill | 按冻结rank/block顺序填合法本地候选 | 简单充足容量与使用空间对照 |
| existing_strong_simple | 原singleton/paired与joint_greedy控制，原共同评分 | 保留已有baseline，不削弱复制/来源机会 |
| bottleneck_balanced | 单候选前进；以global评分和降序target串行服务向量作字典序改善，ID破tie | 允许global零收益但瓶颈向量改善，越过平台期 |
| existing_joint | 原joint_scenarios，same候选/预算/底本 | 联合选择对照 |
| finite_reference | 枚举合法subset并固定同路由政策，取相同评分最小及最少占用 | 小规模参考，不冒作普通在线baseline或任意路由全局最优 |

策略选择时同时记录global主目标、各target服务、缓存实际分配和所用操作。瓶颈均衡为独立可执行的参考规则，不读取枚举答案；只提高非关键rank可能增加占用而不改善模型完成时间，必须标为对主指标无收益甚至被base_only空间支配。

原LP提供资源负载/每target服务下界；service_cost提供worker顺序评分但不能自动覆盖disjoint pairs的硬件争用。两种分数分列，C2不能以无争用DAG时间替代sharedcut。所有结果尚非真实T_ready。

## 5. 预算账本

| 类别 | 已知应用量/状态 |
|---|---|
| 完整DRAM底本 | 32MiB，所有策略相同 |
| 恢复目标 | 32MiB |
| 额外HBM cache | C0≤32MiB，其他≤16MiB；实际值按策略记录 |
| 原执行器staging | 8 workers×64MiB=512MiB |
| Worker运行时DRAM/GPU上下文 | 每物理节点真实多进程聚合未测 |
| 校验临时内存、并发通信组 | 阶段峰值需单独核 |
| 当前任务实验增量预算 | 保持2GiB/node DRAM与HBM，不自动提高 |

此前单worker所在隔离容器运行时DRAM增量约2GB，可用于保守规划投影。不同进程可能共享库页、页缓存，也可能增加mesh/通信分配，故不能把单worker cgroup峰值简单相加称作硬件占用下界。当前未取得在原2GiB/node预算内启动8workers的资格。

## 6. 硬件映射的准入要求

当前可取证范围优先28/30，原27/29的pending状态不触碰。静态GPU/NUMA/HCA结构与单pair功能资格不能代替新多worker资格。三类可能映射应分别审核：

1. 两节点，各4个worker的完整source/target角色分布；须核所有GPU、出口/入口共享、容器总量及原预算。
2. 三节点角色拆分；不能把当前未恢复节点当已可用，也不能只把source/target分摊到节点就忽略共享资源变化。
3. 复用已有keeper或合并角色；只有session/epoch/worker编号/内容布局/保护与执行DAG确实相容才可等价，不热改旧服务，不以改变理论问题来凑预算。

本轮不创建新容器、不启动GPU、不清其他服务或修改原XML。物理部署报告应给角色表、来源证据、预算投影以及仍需验证的精确条件，deployment_ready和GPU_launch_command保持false/null直到条件通过。

## 7. 验收与停止条件

- 四rank、两个块/rank、区间不重不漏，四目标不同，底本完整。
- C0→C1仅改变允许预算；C1_uneven→C2保留需求/候选/预算/路径服务，只改变预登记出口共享关系。
- 所有算法使用同一可行域、评分口径、来源机会；参考穷举和均衡策略分开。
- 同报主目标与实际占用；同代价块置换、solver tie、非关键target改善不能算主指标收益。
- 数学/CPU通过、硬件静态取证、运行准入、成本预测、真实模型binding分别标记。
- CPU同解不触发重复策略GPU实验；物理预算未通过不扩预算试跑；未知并发容量不补为标称速率。
- 原结果和失败均保留，源码/合同哈希可追溯；不上传GitHub或全历史归档/history覆盖。

## 8. 本轮输出入口

机器合同、CPU决策和证据放`codex_coding/results/v0.1.23.3/task2/tp4_scenarios/`，脚本在同版本`src/.../tp4_scenarios/`。正式报告37将记录最终版本、检查结果和部署可行性；本节交付状态待完成后追加。

## 9. 已交付的离线结果

最终机器契约为`tp4_scenarios/contract_cpu_r03/contract.json`，内部合同hash为`6cbcada0097df02f9d298dda01f24ee3321778c8c821f785b515ca6e92f91575`。comparison.json保存完整策略步骤、真实候选ID、路径/资源/worker依赖队列、缓存账本和所有合法subset。C0/C1均衡/C1不均/C2分别有256/81/36/36个合法subset。r01保留初始输出，r02补空间判据，r03修复两个symbolic source合并node时的GPU编号冲突；原件不覆盖。

下表时间是归一化CPU资源模型评分，不是毫秒实测；缓存是逻辑额外HBM字节。

| 配置 | 不缓存评分 | 达到最低资源评分的最少缓存 | 原strong-simple | 瓶颈均衡/容量填充 | 原joint |
|---|---:|---|---|---|---|
| C0_local_full | 2 | 32MiB / 0.5 | 0MiB / 2 | 32MiB / 0.5 | 32MiB / 0.5 |
| C1_balanced_control | 2 | 16MiB / 1.25 | 0MiB / 2 | 16MiB / 1.25 | 16MiB / 1.25 |
| C1_uneven | 2 | 0MiB / 2 | 0MiB / 2 | 16MiB / 2 | 0MiB / 2 |
| C2_shared | 4 | 8MiB / 2 | 8MiB / 2 | 16MiB / 2 | 8MiB / 2 |

C0/C1均衡复现strictpositive边际规则的平台期，简单均衡与容量填充已解决。C1不均中r2无缓存资格且始终决定完成下界，其他rank的缓存改善不降低主目标；不缓存占用更少。C2共享出口使base-only从2变4，缓存U组至少两个块使共同出口负载下界降到2，原strong-simple和joint都做到8MiB。该层说明资源地图可以改变有价值的缓存决策，却尚未产生joint相对强对照的增量。

这些数字比较的是固定原resource_lp路由政策和字节取整后的下界评分。有限执行还受local前序、网络竞争与启动时序影响；相同resource分数的不同操作队列可能有不同实际完成时间。worker DAG分数单独列示，它不计算disjoint pairs共享硬件时的动态服务。真实成本/并发/model-binding全部保持未就绪。

部署可行性最终在`tp4_scenarios/feasibility_r03/deployment_feasibility.{json,md}`，绑定最终合同r03；元数据原件仍在feasibility_r01。两节点8worker按历史单进程观测投影约8.1–8.2GB新增DRAM/node，超过原2GiB/node规划额度；共享页与多进程通信的净效应仍未测，因此不是物理下界。8GiB容器limit也不等于任务额度。旧keeper的session/rank集合与本fixture不同，不能拼入新会话。

独立总验收为`review_final_r02/qualification.json`：合同44项、依赖4项、部署28项通过；交付方部署自检11项通过。中间审查原件保留，最终资格为CPU准备通过、物理部署未准入。没有新GPU样本或新增产品算法，新的瓶颈均衡实现仅为版本实验脚本内的可复现参考。
