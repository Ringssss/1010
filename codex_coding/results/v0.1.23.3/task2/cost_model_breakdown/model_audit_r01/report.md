# 三节点六GPU多层恢复：成本模型适用性审计

结论：现有breakdown与已实现执行机制在结构上相容；本轮没有成本模型预测值，不能判定绝对预测准确度。资源容量模型、有状态串行服务模型、controller目标填满窗口是三个不同层次，必须分开对照。实际执行的是冻结来源见证，不是模型规划结果。

审计只读本地原件和产品源码，未执行SSH/GPU、未修改产品或旧结果；新产物是audit.py、audit.json和本文。`audit.json`保存117项原件一致性检查及SHA，全部通过；这些检查不是性能样本。

## 1. 实际调用链与当前参数

`runner/run.py:416`创建`Cluster(endpoints, {}, executor='serial')`；`cases`经旧adapter的`execute_phase → compile_phase → Cluster.transfer`执行。runner没有调用`estimate_execution`、`restore_plan`、`solve_joint_request`或拟合函数，也没有加载service_model。`adapter.py:237`明确设置：

```text
strategy = frozen_witness
estimated_transfer_s = null
bandwidth_lower_bound_s = null
resources = []
scope = current-source serial witness; no rate model or runtime prediction
```

四case原件与源码一致，restore各8个4MiB操作。不存在当前六worker适用的容量profile、service系数或绝对prediction；这些字段应继续为空，不能以实际case耗时倒填“预测”。功能资格binding仅记录路径/epoch/有效期等，并未把一次资格耗时变为正式模型参数。

旧`costcal01/costval01/requestcal01/requestselected01/requestcurrent01/service_model.json`有真实历史参数，但五者topology_id都是`16a91c017048061d3b821b07f9e1be2a5045608aa533df727346cf456eeeba0a`，与当前六worker不同。历史场景是node27/28/29、八worker、Qwen TP2，并非当前28/30/27六worker；仅有`2:hbm:2->6`和`4:dram:2->6`两条路径。schema1使用32/256MiB；schema2使用128/256MiB，operation原始拟合值为负后钳零，诊断公开。它们不覆盖当前23路径、SSD/目的DRAM、当前4MiB操作及当前RuntimeWorker诊断控制成本，原模型的topology检查会拒绝直接使用。

旧`layout_validation/review_preflight_r01/predictions.json`是28/30两worker、16MiB HBM布局的路径角色与弱定性预言（A最短、D最长，B/C未知），没有绝对服务系数。旧64MiB微测还存在原语不同及预算失败，不能把有效吞吐直接搬到当前恢复模型。

## 2. 资源容量/流分配模型究竟估什么

`planning.restore_plan`在固定有效副本布局上按目标coverage断点形成区域。每区域需求为`d_j`，来源/路径选择的字节分配为`x_jp≥0`。固定恢复LP为：

```text
min T
sum_p x_jp = d_j                         每区域覆盖
sum_(j,p uses r) x_jp ≤ C_r T            每资源容量
sum_(j,p has path k) x_jp ≤ B_k T        每定向路径容量
sum_(j,p to w) x_jp / B_p ≤ T            可选serial_target_execution
```

GB/s实现换算采用十进制1e9，LP用MB缩放。它是给定时限下可分流覆盖的容量LP，可用cut/流量下界理解；实现不是任意中转图的通用max-flow。候选仅为当前合法副本直接到目标的路径，没有relay、编码或任意重分片。LP分配随后按quantum取整、余数归主来源，重新计算最大负载时间。`bandwidth_lower_bound_s`是连续LP值，`estimated_transfer_s`是取整后资源负载最大值，并可加目标串行服务需求下界。

`multi_joint.joint_scenarios`再加入布局二元变量`y_c`、`x≤d y`、4KiB对齐tier/location/shared-location预算，各互斥恢复场景有独立`T_s`。目标是加权`sum_s w_s T_s / sum_s w_s`或`max_s T_s`；不是多个恢复请求同时竞争的调度器。selected布局再用restore_plan重算，不会因MILP成功获得finite执行时序保证。

当前`path_for`实现的资源如下：

| 资源 | 路径进入条件 | 重要边界 |
|---|---|---|
| `in:dst_worker` | 所有路径 | 通用GPU入口系数；目的DRAM也使用该抽象，未逐腿展开 |
| `out:src_worker` | 非同worker | 通用GPU出口；没有显式HBM读取池容量 |
| `dram:src_node:src_numa` | 来源DRAM | NUMA来源共享带宽；不是完整CPU/NUMA拓扑 |
| `net_out:src_node`、`net_in:dst_node` | 跨node | 每node聚合网络容量，不区分HCA/NIC/PCIe共享域 |
| `ssd:storage_node`、`store_network:storage_node` | 来源/目的SSD | SSD与HTTP存储腿的聚合抽象；未显式拆HTTP→GPU暂存→网络→落地 |
| `path:key` | 每路径 | directed_path_rates优先，其次kind:tier或kind默认值 |

该模型假设profile中的持续带宽与共享容量真实且适用、合法来源覆盖完整；用流体负载下界近似。它未计first-pair激活、固定操作/请求成本、按worker顺序等待、当前库存诊断、目标分配、校验/发布、pagecache与source重复读取。即便每资源cut均满足，也未证明当前serial有限块调度能同时实现所有容量下界。当前实机没有共享容量校准；不能把六pair/23路径全SHA升级为这些资源参数已认证。

具体接入假设还需对齐：同worker SSD→DRAM保存可以是CPU字节读取/CPU落地，其实际数据腿不一定消耗GPU ingress；现`path_for`仍统一加入GPU入口资源。实际跨workerDRAM/SSD路径则可能经过GPU staging。这些区别必须从执行原语拆清后再绑定profile，不能认为旧HBM目标资源表自然覆盖三层双向保存/恢复。此为规划成本接入的限制，不否定现已通过的全SHA功能；本轮不修改产品。

## 3. 有状态service DAG模型究竟估什么

`service_cost.estimate_execution`复用实际plan操作顺序，维护每worker就绪时间`R_w`以及按session/epoch绑定的warm pair集合：

```text
d_o = operation_p + n_o / (1e9 × warm_GBps_p)
      + first_pair_p × I(src≠dst且无向pair尚未warm)
s_o = max(R_src, R_dst)
e_o = s_o + d_o
R_src = R_dst = e_o
pair在该操作后转warm
estimated_worker_s = max_w R_w + request_s
estimated_execute_s = max_w R_w + request_s + dispatch_s
```

这与`Worker.execute`按全局op顺序过滤出本worker参与操作、每chunk落地后同步、不同无交集worker可独立前进的语义吻合，不是把全部操作全局求和。源端也可能先做自己的目标或其他发送，因此source/target依赖可改变关键路径。该DAG没有在disjoint pair之间额外建立共享HCA/存储资源容量边；同一资源并行竞争时会需要真实组合参数或调度约束。使用单路径有效rate可以吸收隔离状态下的多腿服务，但不能因此保证组合无争用。

schema1以两种warm size的service_s线性拟合byte slope与固定operation；cold-small残差给first_pair，dispatch取样本中位数。schema2用warm small/large target_s得到斜率，再以相同总字节的多操作批量分离operation和request，中位request/dispatch只添加一次；原始负值钳零并保留fit_diagnostics。

评分严格检查chunk、serial executor、topology_id、worker incarnation以及全部路径系数。topology_id只含worker/node/gpu/numa；额外的传输插件/HCA/SSD设备/缓存状态仍需由校准合同限定。warm pair按无向worker pair建模，实际本轮01/02/03/12/24/45已经激活；不能默认new_state全cold，也不能把旧session的warm状态拿来用。

`managed.solve_joint_request`可用service_model重评current/base-only/带宽MILP三个提案，但默认以`new_state(workers)`按cold评分；不会自动同步实际warm状态，也不是全部layout/route的service全局优化。强简单对照尚未自动进入该三提案集合。当前runner完全绕过此入口，因此没有任何“service模型选出本次布局”的证据。

## 4. path_key目的tier和同size资格样本

产品`path_catalog.path_key`已经把非HBM目的tier写入key后缀，例如`0:ssd:0->0`是SSD→HBM，`0:ssd:0->0:dram`是SSD→DRAM。旧adapter直接import此函数，没有自建漏tier版本。

逐原件核对23资格key与source_worker/source_tier/source_numa/target_worker/target_tier，全部一一对应；四case所有操作均能匹配相同目的tier、4MiB的资格proof。没有同key覆盖不同目的tier的bug，也不能为了凑样本合并这些key。

这些资格耗时可作为独立的同size路径观察，支持有限离线lookup/DAG回放；它们并未识别`operation_s`、byte slope、first_pair_s和request_s各项。资格每请求1个op/1个target，case每请求8个op/4个target；直接把资格`execute_wall_s`当可相加的纯service会把一次dispatch请求成本重复8次。应至少从worker/receiver观测分离RPC dispatch，保留真实warm状态和目的tier，再以八操作组合作为holdout。一次样本不能给出统计置信、尺寸外推或正式容量。

## 5. 主指标、控制与校验边界

`Cluster.transfer:129`的controller同钟事件从其内部inventory开始，到六worker execute返回时填满，再到全SHA/complete发布：

```text
T_filled(transaction) = inventory_rpc + prepare_rpc + execute_wall
T_published(transaction) = T_filled + verify_and_publish
```

filled只证明四完整HBM replica内容已填满；published再证明全SHA与Catalog发布。此前外部trusted_gate（也会查询库存/预算）、binding检查、编译、前置库存读取以及恢复调用的上层入口均未计入。save阶段从SSD底本构建cache，单列为夹具构建成本，不能计作真实暂停时保留原地权重成本。finish/后续清理不属于目标填满边界。

如主指标定义为请求后processed权重可用时间，必须先冻结所需起点及权重可用终点：外部必要gate与编译/目标准备是否在窗，发布是否为使用前必须条件，真实使用方的权重接入是否已完成。现有filled/published是可用的两条分段证据；不能直接重命名为经过完整使用方验收的model T_ready。无需因这个缺口展开新模型/推理基线，仅补清楚当前主指标的必要边界。

| case | inventory(ms) | prepare RPC(ms) | execute(ms) | filled(ms) | published(ms) |
|---|---:|---:|---:|---:|---:|
| SSD_only | 63.287 | 5.508 | 63.390 | 132.188 | 194.581 |
| C0 | 63.132 | 4.491 | 5.290 | 72.915 | 99.886 |
| C1 | 62.373 | 4.391 | 4.685 | 71.450 | 96.808 |
| C3 | 61.842 | 5.071 | 14.138 | 81.052 | 111.562 |

约62ms inventory与5–63ms execute的分离说明纯带宽项不会解释完整小数据窗口。RuntimeWorker附带运行诊断，其具体开销原因尚未独立profile；源码只能提出候选，不能把62ms确定归因某函数。service模型现有dispatch也不等于这一库存/prepare/SHA窗口，直接与filled比较属于边界错配。

## 6. 可用证据、缺项与最小下一关

可用：真实来源层次/SSD补齐、同tier同size路径功能、worker顺序、每操作角色和SHA、controller控制/execute/校验分段、一次组合holdout。结构上支持“资源下界不含finite等待”“HBM/DRAM缓存绕过SSD路径”“serial DAG允许disjoint worker并行”。它没有给出当前rate/资源容量与stateful系数，绝对准确度和共同预算算法增量均unknown。

最小范围是在既有原件上完成同size、同tier、同executor/worker状态的路径lookup回放，并明确其独立参数来源和预测终点；这只检验固定八操作schedule的执行段，不能冒称全T_ready模型或shared容量。之后只有该主指标需要时，才补独立的操作/请求分离或必要shared组合，不新增solver、不扫描104候选，不将本轮case观测回填成预测，不自拟误差阈值。

复核命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/cost_model_breakdown/model_audit/audit.py --out codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/model_audit_r01
```

该目录已存在，不可原名重跑。原始资料：三节点计划第12节、正式报告41、operations_actual_r01/qualification及cases、review_final_r02/qualification；SHA见audit.json。脚本不导入torch、不访问服务。
