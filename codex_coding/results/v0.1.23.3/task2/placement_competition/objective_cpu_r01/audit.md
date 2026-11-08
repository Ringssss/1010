# 多合法保存位置竞争：目标与执行语义审计

日期2026-10-10；开发号v0.1.23.3/task2。范围为源码只读审计与小型CPU符号检查，产品、GPU和远端均未改动。`checks.json`保留原service scorer的完整timeline与来源SHA。此文服务于新增位置竞争契约，正式文章由主agent维护。

## 1. 位置竞争的数学条件

定义内容块b的合法位置集合L_b，页面取整占用a_b，选择x_bl∈{0,1}。位置预算约束为Σ_b a_b x_bl≤C_l，全局预算为Σ_bl a_b x_bl≤C；每份副本各计一次物理占用，不能按唯一内容去重。每个必要目标仍须按identity/version/tp/off/n从底本或已选候选完整覆盖；允许的底本和路径对所有策略完全相同。

“有多个合法位置”不足以形成竞争。至少需某处l的需求超过C_l、内容的替代位置代价不同，并且差别最终改变整体主目标或最小占用。若两个pool各容纳两块，而两个刚性内容分别偏好不同pool，柔性内容可各占剩余一格，则好位置并未稀缺，不应称为位置竞争反例。预登记的竞争构造让两个刚性内容共同依赖pool0；柔性内容可让出pool0，另一个内容可使用pool1。底本若绕过该刚性约束，须重算“依赖”而不能只以缓存路径速率宣称它成立。

最小二内容二位置整块例中，P在S0/S1速率a/b，Q在S0/S1速率c/d。等大块s、每pool一格，直接路径、两个source/target pairs独立且无其他瓶颈时，顺序最近先占需要a>b或确定性tie偏S0。其成本T_first=s/min(a,d)，交叉为T_cross=s/min(b,c)，故交换严格改善恰当且仅当min(b,c)>min(a,d)。脚本精确核对了1..4的256组正整数速率。该式在source/target复用、共享NIC、启动费、后备分流或不同大小块时需要替换为共同队列评分，不能直接沿用。

## 2. 既有paired究竟何时解决

`frozen_layout.strong_simple`的两个paired controls从空集开始，只添加singleton或两个不同tp且同(off,n)的候选；密度按正整体收益/新增分配字节排序。它保留每个复制位置的独立机会，但没有删除、替换或交换已选候选。

若竞争只涉及上述两内容、两格，cross pair符合bundle资格，初始空集到cross有严格正收益，且cross按控制的密度/tie规则胜过更早可行addition，则已有paired addition可直接选对，无需新MILP。不能简化为“所有两位置问题都已解决”：单候选P@S0可能先有正收益被选入并占满稀缺位置；下一步addition即使包含P@S1也不会释放P@S0。四目标max的平台期也可能使全部singleton/pair初始收益为零。

因此强对照应明确分为：原strong_simple；简单瓶颈均衡；位置感知的one-swap/two-exchange参考。交换参考须按实际分配副本删除/加入，保持底本与完整覆盖，重新检查global/location预算，并采用与其他方法相同的route/order/service scorer接受严格改善。该局部搜索不读取穷举答案，也不自动等于全局最优。文档中“分配/成对交换可解”是数学简单方法的能力，不能据此宣称原paired control已经实现交换。

## 3. 三种评分分开

固定布局X后，原`restore_plan(... resource_lp)`选择合法源与字节分配，再进行quantum取整。其`estimated_transfer_s`为实际取整ops的资源字节负载/容量最大值；启用`serial_target_execution`时还包含每target按孤立路径速率相加的服务负载。它是冻结容量模型评分；取整前`bandwidth_lower_bound_s`与取整后评分不同。该LP不同时保证source-ready、target-ready、local前序与共享硬件时间因果。

原`service_cost.estimate_execution`对同一ops列表使用：d_j=first_pair_j+operation_j+n_j/(10^9 warm_GBps_j)，start_j=max(ready_src,ready_dst)，end_j=start_j+d_j，然后两个worker都更新至end_j。pair warm由同session/epoch的状态决定，request/dispatch只在共同规定的层次计入。它会给同source、同target以及兼任两角色的同worker建立顺序。它不读取op.resources，因此不能给不同worker pairs共享NIC建立动态服务。

第三种为真实完整权重可用T_ready：须用同一实际执行队列同时满足所有worker前序、物理资源容量、启动与同步、实际byte coverage与目标绑定。当前未建立共享服务校准、部署准入或真实模型绑定，不能把前两种评分改名为该指标。

对同一symbolic孤立服务模型，worker DAG可以作为位置竞争的共同CPU排序目标；记录所有策略与合法subset的同一resource_lp路由、rank_interleaved order及原service reeval。这仅比较冻结路由政策下的布局，不是对任意route或order的有限执行最优。如果某布局使用共享U，而共享服务动态未知，则同时报告容量负载评分和worker DAG；不以任一单分数冒称实际更快。

## 4. 排队的最小证据

`checks.json`直接调用原scorer得到：两个helper workers4/5分别服务四target中的两个，每op孤立服务为1单位，则worker DAG=2，timeline开始为[0,0,1,1]；四path独立max=1漏掉source排队。若helpers还共享容量1的U，4单位网络字节负载要求至少4单位，但原scorer仍只给2，不能把不同workers当不同NIC。

pool0/pool1兼任target0/target1时，本地操作先各耗1单位，随后两个pool作为source分别服务target2/target3，DAG=2。后两操作即使目标不同也须等待前一本地操作。同一worker角色复用改变了原TP4的八独立roles问题；本契约四target加两DRAM sources为六workers，需明确重新定义。

更小的local-first共享U检查证明max(resource_bound,DAG)也不够：target0/1各先执行1单位local，再各需1单位网络；不同source4/5均跨容量1的U。资源负载=2、target串行服务=2、孤立worker DAG=2，但所有network工作在t=1之后才能开始，故T≥1+2=3。脚本保存local[0,1]并行、remote0[1,2]、remote1[2,3]的显式可行witness，达到该固定队列下界。这个witness是一次数学检查，不是新增通用scheduler，也不认证任意布局的执行最优。

## 5. 公平性与既有接口

| 接口/策略 | 公平使用方式 | 明确限制 |
|---|---|---|
| frozen_layout.evaluate | 全策略相同base/candidates/budget、共同resource_lp与interleave | 当前返回资源评分，不是service主目标 |
| strong_simple所有controls | 不只重评其资源winner；应保留各control提案并做共同service重评 | 原选择规则仍优化资源评分；不得静默称为service-aware greedy |
| joint_scenarios | 原MILP提案共同route/order/service重评、记录status/gap/fallback | 原optimal只针对容量模型；非service最优 |
| managed.solve_joint_request | 当前/base/bandwidth-MILP在同service model与cold state下重评 | 默认不纳入strong/均衡/交换，route仍由LP生成 |
| one-swap/two-exchange参考 | 相同可行域、复制计费与共同route/order/service评分 | 实验参考；局部改进并非完整最优 |
| finite subset reference | 穷举合法subset后统一路由政策评分，同时列最低分最少cache | 非在线baseline；非任意route/order执行oracle |

多个未来互斥场景用共同layout、各自完整restore plan，再按原aggregate_cost做expected归一化或worst。四个同时必要targets必须放在同一plan聚合容量与依赖，不能将它们拆成互斥场景独立求max或平均。在线规划耗时、cache build成本与驻留量分别记录，规划是否落入恢复窗由统一计时边界决定。

## 6. 最小后续需求

| 必须冻结/检查 | 最小交付 | 本轮边界 |
|---|---|---|
| 内容与legal positions | block identity/tp/off/n、每位置独立candidate ID、完整DRAM底本、page rounding | 新candidate不可成为preview运行时source |
| 稀缺性与替代性 | caps与超额需求、刚性/柔性的共同评分条件、pool0空位被抢的实际decision | 不以多个位置数量代替竞争证据 |
| worker角色 | 六worker表、兼pool target0/1、各op的source/target前驱 | 不引用旧八独立pair平台期作等价结论 |
| 共同评分 | 同route、quantum、rank_interleave、executor/chunk、cold/warm state与service coefficients | symbolic结果不写入正式实测profile |
| shared resources | 稳定resource ID与path incidence、容量出处和服务重叠假设 | node合并只为symbolic U，不自动代表实机共享HCA |
| 强baseline | 原paired、均衡、one-swap/two-exchange与固定policy subset参考 | 不能只比nearest-first造联合优势 |
| 实际执行 | 同时满足queue+shared incidence的有限执行记录和独立路径/组合holdout | 当前shared dynamics仍未知 |
| 空间与部署 | base/target/cache/staging/runtime/transition peak分列、node额度与准入 | 原2GiB/node不因理论扩大 |

## 7. 仍开放的研究问题

这一固定小case可检查稀缺位置、跨role排队是否改变保存决策，并证明某个具体简单规则的损失。若交换参考已达到同分最少空间，则采用该参考足够，不能据此主张建设新solver。更一般的问题是：多内容合法性、多重共享cut、位置配额、布局转换峰值与有限queue共同限制时，资源感知局部方法仍损失多少，现有joint提案能否弥补，以及真实收益是否覆盖规划/布局建立开销。

目前还未验证不同GPU/HCA组合的实际capacity、多个worker在原预算的准入、共同资源动态服务、真实model T_ready与在线开销。共享争用未知时无需新增solver；先冻结路径/资源/队列接口，再以同primitive校准与独立holdout验证代价。在此之前所有CPU最优结论都附固定候选、固定路由政策和symbolic评分范围。

运行命令：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/placement_competition/objective/check_objective.py --out codex_coding/results/v0.1.23.3/task2/placement_competition/objective_cpu_r01
```

结果：8/8检查通过，包含256个精确速率条件。无新增求解器、GPU、SSH、产品变更；原件目录不可原名重跑覆盖。

## 8. 最终case_r03共同复评与可达范围

后续冻结输入`case_cpu_r03/{contract,comparison}.json`已由`objective/reevaluate_case.py`独立重新调用原resource_lp、rank_interleave与service scorer。结果`objective_case_r03/reevaluation.json`通过520项检查，121个合法subset、6个policy与3个原strong控制的ops、共同分数及全部声明资源容量逐一核对。

本case与上面一般共享U反例的参数不同：DRAM source4/5共享U的cap为4单位，两个source各自服务速率1，同时总速率最多2；pool0/1的资源cap为4，单worker顺序使各自不会被多target并发重复计速。独立检查从每条DAG timeline的全部start/end端点划分区间，假定每op在整个服务区间按其冻结path rate匀速，逐区间聚合相同resource incidence的流量；所有policy/control/subset均不超容量。这提供了该零setup、固定duration、固定route/order符号模型下的可行witness。因此当前case的DAG评分可达，仍不是实测容量、真实GPU时间或任意路由/顺序全局执行最优。上面的local-first共享U容量1反例继续有效，不与此case混用。

最终强参考接受key已经统一为原service整体分数、各target最后receiver操作end的降序向量、实际cachebytes。兼pool的worker在完成自己target后可能继续为其他target发送，其最后source ready时间不能当作自己target完成；独立输出逐target_finish供审阅。原strong/MILP内部目标保持原实现，三个原strong提案都共同重评为2。

| 额外缓存预算 | 该固定路由政策下最低worker DAG | 达到最低分的最少实际缓存 |
|---:|---:|---:|
| 0MiB | 2 | 0MiB |
| 4MiB | 2 | 0MiB |
| 8MiB | 1 | 8MiB |
| 12MiB | 1 | 8MiB |
| 16MiB | 5/6 | 16MiB |

r03交换参考与原joint选择`r0/r1@pool1、r2/r3@pool0`，资源评分1/2、worker DAG5/6，16MiB缓存。均衡与容量最快参考均为1且用12MiB；8MiB的固定policy参考已为1，故两者在当前空间—主目标下被支配。原strong与base同为2、0MiB。该case证实柔性内容让出稀缺位置的决策结构，交换参考已解决，没有原joint相对交换参考的新收益证据。

独立复评初次`objective_case_r02`在引用SHA序列化时失败：相对输入路径传入绝对ROOT的`relative_to`。失败metadata保留，原stderr未另存；改为显式resolve输入路径后`objective_case_r02_retry_r01`通过514项，最终r03通过520项。未覆盖任何原结果或调整case速率/caps。最终复评命令：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/placement_competition/objective/reevaluate_case.py --input codex_coding/results/v0.1.23.3/task2/placement_competition/case_cpu_r03 --out codex_coding/results/v0.1.23.3/task2/placement_competition/objective_case_r03
```
