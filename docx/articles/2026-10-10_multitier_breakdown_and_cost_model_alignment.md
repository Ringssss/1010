# 三节点多层恢复：时间组成与成本模型对齐

日期：2026-10-10；编号：v0.1.23.3/task2；对应计划18.27、正式实验记录42。本文分析上一轮唯一六GPU实机session，不增加GPU实验、不修改产品或原数据。

结论是：容量覆盖、真实路径和worker执行依赖已有实证支持；现有带宽/服务模型尚未在这张新图上正式校准。时间组成能解释观测，但解释观测与运行前准确预测是不同证据。一个仅用先前单路资格记录的事后同尺寸诊断显示，执行段误差约−22.6%至+19.6%，而含约62ms查询的filled误差缩到约±4%；后者不能单独证明布局选择可靠。

## 1. 实际时间账目

每组32MiB合成TP4式字节、四个8MiB replica目标、各运行一次。所有数字单位ms。

| 配方 | 库存查询 | prepare RPC | execute | 目标填满 | 校验和发布 | 发布完成 |
|---|---:|---:|---:|---:|---:|---:|
| SSD_only | 63.287 | 5.508 | 63.390 | 132.188 | 62.394 | 194.581 |
| C0：32MiB HBM | 63.132 | 4.491 | 5.290 | 72.915 | 26.971 | 99.886 |
| C1：16MiB HBM＋16MiB DRAM | 62.373 | 4.391 | 4.685 | 71.450 | 25.358 | 96.808 |
| C3：16MiB HBM＋12MiB DRAM＋4MiB SSD来源 | 61.842 | 5.071 | 14.138 | 81.052 | 30.510 | 111.562 |

在当前原生事务窗口内：

\[
T_{filled}=T_{inventory}+T_{prepareRPC}+T_{execute},\qquad
T_{published}=T_{filled}+T_{verify+publish}.
\]

计时从Cluster.transfer内的load_begin到同controller单调时钟的filled/published；浮点与ns事件的微秒差异保留，表中总计统一ns。外部可信gate、编译binding、前置inventory和live模型handoff不在这个窗口。不能把这一窗口命名为完整模型启动时间。

库存查询是获取当前位置、内容/版本、容量与运行状态；prepare RPC是预留目标、检查epoch/generation和获取源租约；execute包括实际读入、复制、通信、同步与等待；校验发布包括全字节SHA和状态发布。prepare合计约67ms，其中约62ms实际是inventory，不是GPU分配耗时。

每组最慢inventory RPC的transport_and_server为61.127–62.468ms，反序列化最多约0.207–0.262ms。可见JSON反序列化不是这62ms的主要解释，但transport与server内部尚未拆开。当前RuntimeWorker.inventory每次查询实际BDF、library、proc/cgroup等诊断，这只是明确的待验证因素；没有新增profile，不据源码认定根因。

## 2. execute不是各条链路时间简单相加

controller execute与最大worker本地execute分别为：

| 配方 | controller execute | 最大worker execute | 差值 |
|---|---:|---:|---:|
| SSD_only | 63.390 | 59.633 | 3.757 |
| C0 | 5.290 | 2.062 | 3.228 |
| C1 | 4.685 | 2.745 | 1.940 |
| C3 | 14.138 | 11.411 | 2.726 |

最后一列仅是两个时长统计的残差，不能直接命名为网络/dispatch的精确耗时。不同worker的RPC到达时刻、执行开始和结束不同；各worker elapsed含同步和对端等待。这里未做跨主机时间戳相减。

C3的明显长尾位于node27的w4/w5：w4先恢复自己的HBM块b4（0.372ms），再接收来自w2的DRAM块b5（3.129ms），最后从SSD读取并转发b7（7.719ms）。w5先恢复本地DRAM块b6（0.968ms），随后在b7接收操作中记录9.982ms。b7两端是同一操作的重叠窗口，不能把7.719和9.982相加，也不能把任一个数字称为纯SSD读取时间。

b7实际经过SSD文件/HTTP服务→主机buffer→w4 GPU暂存→同节点P2P/IPC→w5目标。现日志没有分别测每一腿。w4必须先完成前面的接收才能转发另一块，说明布局改变的既有每条路径成本，也有谁在等待谁。SSD_only还存在源worker同时承担前序接收与后续SSD供数的串行链；把三块SSD或多GPU的带宽相加会漏掉这种依赖。

## 3. 我们已有的三层模型

### 3.1 容量与带宽模型

空间约束检查每个位置/共享池的副本占用，内容覆盖保证各目标拿齐不同字节。C3需求32MiB、HBM＋DRAM只能覆盖28MiB，至少4MiB来自SSD；这一容量必要条件在实机得到直接支持。

共享资源容量的基本约束为：

\[
\sum_p a_{rp}B_p\le C_rT.
\]

即同一网卡、内存池或PCI通道承担的总工作量，不能超过它在时间T内能提供的服务。固定布局后的实现是路径容量LP，外层multi_joint加入离散放置预算。它提供理想容量与流量下界，并不自动包含有限块的先后依赖、首次通信、RPC、分配、校验等时间，也不是任意中转图的通用最大流实现。

本轮六pair/23路径证明功能可用，尚未测共享资源容量；当前path_for仍使用node级network出入口、worker入口/出口、来源NUMA、SSD与store_network聚合。它不会自动从物理图生成各HCA/PCI容量。目的DRAM也被统一记GPU入口的旧抽象，以及SSD本地HTTP/CPU落地和跨worker GPU暂存的区别，都要在接入三层成本时对齐。

### 3.2 已有状态服务DAG模型

已有service_cost.py定义每操作：

\[
d_i=\alpha_{p_i}+B_i/\beta_{p_i}
  +\mathbf1_{cold\ pair}\gamma_{p_i}.
\]

然后按实际操作顺序，取源和目标worker都就绪时开始：

\[
s_i=\max(R_{src},R_{dst}),\quad e_i=s_i+d_i,
\quad R_{src}=R_{dst}=e_i.
\]

最终取最晚完成者，再加请求/dispatch经验项。这正是C3“先接b5，再发b7”需要保留的结构。互不相交的worker对可以并行，因此不能把全部op时长求和。该模型当前仍未校准同时争用同一HCA/PCI的动态降速；独立容量下界与DAG下界取max也不等于证明存在达界调度。

### 3.3 用户主指标

最终权重可用时间还需明确在线规划/库存查询、prepare、执行、必要发布及模型handoff的起止。实验全SHA成本与生产必需工作应分别标记，再用所有方法一致的边界测量。带宽模型、execute模型和T_ready不能直接用同一实测总数验证。

## 4. 当前是否真的启用了预测

没有。实际runner创建Cluster(endpoints, {}, executor='serial')，执行冻结witness；四组计划的estimated_transfer_s、bandwidth_lower_bound_s均null，op resources为空。它未调用estimate_execution、restore_plan或joint求解，也未加载service模型。因此原GPU运行没有可计算正式预测误差的事前预测值。

历史五份service_model属于旧node27/28/29八worker、Qwen TP2场景，仅两条路径，拟合32/256MiB或128/256MiB；当前是另一六worker拓扑、23路径与4MiB块。原topology_id检查会拒绝直接套用。另一个旧两worker布局记录只给定性预言，不能变成当前绝对系数。

路径键已核实正确：非HBM目的层加:dram后缀。23资格与四组保存/恢复的方向、源/目标层、4MiB、chunk、executor、worker epoch一致，没有目的tier碰撞导致的虚假资格。

## 5. 使用先前单路数据的事后诊断

为检验模型结构的迁移能力，子agent固定以下规则：只用23条单操作资格记录的target/both elapsed作为同4MiB服务查表值；依原estimate_execution递推worker依赖，dispatch取资格残差中位数3.092ms。filled预测另加资格inventory中位数62.051ms与prepare RPC中位数4.531ms。四组case的耗时不参与拟合。

这是GPU实验结束后计算的诊断，不是原运行前冻结预测。单点无法同时辨识固定成本和带宽；operation/first-pair/request的占位不代表这些成本实际为零，禁止外推其他尺寸或冷pair。published未校准，不作预测。

| 配方恢复 | execute预测(ms) | execute实测(ms) | 执行误差 | filled误差 |
|---|---:|---:|---:|---:|
| SSD_only | 60.476 | 63.390 | −4.60% | −3.88% |
| C0 | 4.094 | 5.290 | −22.60% | −3.07% |
| C1 | 4.775 | 4.685 | ＋1.94% | −0.13% |
| C3 | 16.902 | 14.138 | ＋19.55% | ＋3.00% |

误差=(预测−实测)/实测。约62ms公共控制项让总窗相对误差变小，但不能证明数据服务或布局选择已经足够精确。C0/C1预测执行排序与单次观察相反，差距本来就很小、n=1不认证真实优劣；它提示不能仅用filled误差几个百分点作为规划器决策可靠性的验收。

这项诊断的限制包括：每路径单尺寸单样本；单操作请求与八操作请求不同；target elapsed包含启动时差/等待，之后再递推DAG可能重复计等待；资格目标4MiB而正式rank目标8MiB；SSD文件、缓存和组间状态未完全控制。资格先于cases由同步controller调用与成功命令证明，各transfer时间只在自身clock_domain内计算。

## 6. 最小下一步

先确认约62ms库存请求的实际构成和观测成本，同时固定主T_ready边界。然后复用fit_path/fit_request_model，只对会改变当前布局决策的关键路径补两个warm尺寸；需要区分请求/操作成本时，再补同总字节不同操作数对照。参数冻结后，用一个未参与拟合的尺寸或混合布局验证执行与主指标，并单独检查排序/是否改变决策。

若要验证共享带宽/最大流层，再增加一组已知共享出口的单路与并发对照，区分执行等待和实际容量竞争。不能用现有串行资格或C3恢复时间替代这个证据。建设优先复用现服务模型与执行器，不扩104路径扫描或新增求解器。

## 7. 本轮证据入口

- [实际breakdown独立解析](../../codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/evidence_r01/report.md)：原件计时、逐op双端/worker顺序、SSD读量、27引用SHA。
- [模型源码与调用审计](../../codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/model_audit_r01/report.md)：117一致性检查、真实公式、旧模型适用域及当前预测缺席。
- 预测诊断的最终路径与独立算术复核见正式实验记录42；中间结果全部保留。本轮未新GPU、SSH或修改旧产品/结果。

最终诊断为[prediction_r03/report.md](../../codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/prediction_r03/report.md)，同目录summary.json保留每op查表和DAG，arithmetic_check.json独立闭式复核四恢复DAG、21误差字段与15引用SHA。计算调用原estimate_execution，但单点有效速率只编码4MiB服务时间，包含该观察的固定/等待成本，不是测得物理链路带宽，也不构成正式schema1/2拟合。r01数字保持，r02因误加完整clock_domain相同断言而失败，r03只修顺序证据；无参数调优或硬件重跑。

## 8. 后续实测更新：报告43（2026-10-10）

本节更新前文“库存根因待测/校准待做”的状态，原report42事后诊断与结果保留。

实际六worker分段确认：重复查找/加载HIP动态库是查询的主要固定负担。仅按PID复用库路径和句柄，BDF/NUMA/HCA/proc/cgroup/原Catalog等全部fresh，两次整体查询中位数60.576→6.190ms，库段减少55.290ms，与controller减少54.386ms相符。这是实验诊断层开销的修正，不是数据路径带宽提高。

同内容36MiB混合请求中，完整request→filled从legacy156.026降到cached42.207ms，request→published从228.260降到114.392ms；execute23.286/23.142ms几乎不变。该新总窗从前置gate/inventory前开始，包含两次fresh inventory；库存减少112.118ms解释filled减少113.819ms的约98.5%。这是固定cached→legacy各一次的配对观察，原32MiB/内层事务数据不与它混比。

三关键路径按4/16MiB与等4MiB分4op实际校准；localHBM两次均值4MiB=.381ms、16MiB=.277ms，规定补测后仍不能辨识正斜率。原拟合器诚实拒绝整套模型，冻结model与prediction均null，没有完成正式预测准确性验收。remoteDRAM与SSD代理的有效service斜率可识别，但不能把两条局部结果称完整模型。

这提示下一步只需处理小本地快路径的模型形式与适用区间，再用独立样本检验；不应为了得到正带宽而挑点。当前测量平台已能明确减少控制开销并完整记账，完整模型校准、共享容量和布局收益仍分别验收。

完整证据：[库存分段](../../codex_coding/results/v0.1.23.3/task2/cost_calibration/inventory_actual_report_r01/report.md)、[实际校准及配对对照](../../codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_result_r01/report.md)、[独立验收与模型未识别状态](../../codex_coding/results/v0.1.23.3/task2/cost_calibration/review_final_r01/report.md)。首run监控把正常active事务当失败，已最小修正并保留失败/精确SSD清理；完整r02全SHA及九进程退出通过，无新增第三轮GPU调参。

## 9. 2026-10-11 更新：有限域模型已有独立实测

正式计划18.29/报告44及结果bounded_cost/review_final_r03为最新证据，补充第8节“整套仿射模型未识别”的后续。原负斜率结果没有修改；新模型只把local4–16MiB单op改为旧四点service均值.280796ms，带宽仍null，remote/SSD原fit不变，request/dispatch各一次。模型仅旧训练冻结，两个新配置的预测在首请求前保存，原36MiB组合不参与拟合。

8MiB local服务实际.268654ms，预测误差+4.52%；三个path各8MiB的mixed最大worker预测13.613298/实际14.133330ms（−3.68%），controller执行16.287744/17.285672ms（−5.77%），完整filled35.162678/37.902339ms（−7.23%）。实际published88.079182ms，校验字节/对象成本未建模故预测null。mixed纯service仍不能把含等待的两端窗口相加验证，逐op和controller时钟边界保持。

这说明有限域模型能够接受先验新会话检验。每配置一次、64MiB全SHA、96项独审通过，不等于同预算布局排序或共享容量认证。三当前path内容/目标不同且全经w4，排列同分；本次约1ms执行低估不支持直接信任亚ms候选差异，也不是未来误差界。下一步先建立同内容、同target、同HBM/DRAM预算的两个合法布局合同并CPU评分，关注粗SSD替代差量；只补决定选择的必要路径，复用已有规划器，不追极低拟合误差。

新实验脚本的快照同名、mesh前rank检查、live/JSON键格式三问题及两次零holdout准备失败均保留，最终actual_r03为唯一有效会话；详见报告44。所有新进程/权重/SSD已清理，旧资源保持，产品和冻结模型未事后调参。
