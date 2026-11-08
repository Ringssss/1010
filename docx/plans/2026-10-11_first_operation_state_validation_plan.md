# 首操作成本：8MiB区分实验与独立留出协议

日期：2026-10-11；编号：v0.1.23.3/task2。状态：A阶段8请求完成且执行资格通过，状态门为unidentified，B按预登记规则跳过；结果见总计划18.32、实验记录47。原设计阶段为18.31/记录46。下文保留事前协议，不表示B仍待自动执行。原设计与引用索引见[设计证据目录](../../codex_coding/results/v0.1.23.3/task2/first_op_validation/design_r01/design.md)。Janus复用结论另见[专题说明](../articles/2026-10-11_janus_retention_reuse_audit.md)。

用户要求“先设计实验看看首操作成本是不是客观规律，如果是建模考虑”。本协议先检验当前localHBM执行路径是否存在**随可观察生命周期重置的首操作成本**，再检验状态项是否降低独立execute/full预测误差。结论作用域为冻结版本、路径、线程/目标/日志条件；该实验不建立普适物理定律。

唯一主协议为A训练/区分8请求，条件通过后B独立留出8请求，共至多16请求。每cell一个双请求block，A/B各一次，总两个block；不默认五轮重复。4/16MiB、logger扩展矩阵、真实model-runtime和Janus本体运行均留待后续明确任务。

## 1. 依据与待区分因素

报告45原1MiB×4首op .338995/.258584ms，后三op约.073–.091ms。最新native4=.253934/.172842ms、16=.238223/1.037124ms，旧尺寸负差未稳定迁移，长点必须保留。同handler双copy的source_slice CPU从90.991→13.781us、74.411→12.930us，直接支持主机首次执行差额；copy/sync同时改变目标first-touch/cache。

产品使用ThreadingHTTPServer，handler持worker.lock直接调用execute/_execute，所以旧请求边界与实际执行线程首次调用共变。set_device在op record前，begin日志在record内，end日志在record外但worker execute内。原bounded模型local .280796ms包含首日志/切片/提交，request .050530ms只为execute−record残差，不能再把首项叠上去。

| 假说 | 可证伪预言 |
| --- | --- |
| H_thread | 同实际线程跨R1/R2后首主机差额消失；新线程即使写预写目标，差额仍恢复。 |
| H_request | 持久线程R2第一个op仍慢，该请求第二op暖；与fresh执行线程相近。 |
| H_target | 持久暖线程写fresh目标时copy/sync差额保持，在预写目标下降；source_slice CPU未必随之变化。 |
| H_observer | 差额集中在logger/计时调用，主机view/submission没有对应结构；作用域需绑定实际观测mode。 |

预写干预识别的是目标写入历史/cache联合因素，不能继续细分页提交/分配池/驱动函数。实验也区分process已初始化与actual执行线程未使用；不会从首op推断具体TLS函数。

## 2. 固定条件与真实状态证据

w4=node27 GPU0、localHBM→localHBM replica，固定16MiB同SHA source，连续uint8，source_offset=0，n=8MiB，serial、32MiB chunk、每copy一次device synchronize，无GPU event，无live scatter。沿现有repeat/native复制原语及source lineage、prepare、Catalog、校验、budget gate、supervision、精确退出，cached inventory和warm24/45保持；不新增remote/SSD/coldpair性能矩阵。

F：每个请求新建actual执行线程，完成后join，同请求两copy在该线程执行。P：一个actual执行线程连续服务R1/R2，之后join。两者使用同一单任务队列/返回/序列化，禁止并发；记录thread create、enqueue/pop、wait/join，实际完整窗包含它们。handler不得持worker.lock等待一个还要获取同lock的执行线程。覆盖层必须由源码/CPU协议检查证明复制原语保持；不新增GPU anchor请求。

线程创建时赋birth UUID；记录process epoch/PID/start、handler birth、actual executor birth、native TID、线程存活/退出/join、request ID、thread累计op序号。OS TID/ident复用不能证明同一存活线程。预赋token进入op原始记录，详细ptr/stream/meminfo和字典组装在两轮之后，开销留在实际worker/controller/full窗。

每cell两个8MiB目标A/B同时存活且地址不重叠，R1写A、R2写B。每请求连续两copy同目标，最后每目标全SHA一次。诊断buffer lease由本任务薄层管理；禁止把产品FILLED事务重置PREPARED。正常产品生命周期不改。两个copy均纳入完整请求，不能把第二copy暖时间当首次恢复收益。

pretouch由独立prepare线程在对应请求prepare内复制8MiB到目标并sync；该线程birth与F/P executor不同，executor首次任务前不能调用view/copy/log预热/GPU查询。fresh对照也由同prepare线程复制8MiB到等大scratch并sync，以匹配process级初始化，同时保证对应目标从未写入。所有cell相同A/B/scratch分配，target用empty，禁止zero/init偷写fresh。prepare线程、地址/持有者、分配代际和写入历史均有执行证据。

这个scratch控制意味着全部测量都在进程已做同字节copy之后；本协议识别该前提下的thread/request/target状态，process完全首次GPU初始化保持域外。sourcebootstrap是共同输入建立成本，生命周期总账另列。

## 3. 唯一主矩阵与请求数量

每cell开始新建executor generation。R1/R2为顺序请求，单请求两轮间不插入SHA、inventory、stream/meminfo采样。P在R1到R2之间必须证明同birth仍活；F在R2必须新birth。单block初始A/B均未写，只有指定prepare干预改变目标状态。

| cell | executor | target初态 | R1 → R2 | 判别 |
| --- | --- | --- | --- | --- |
| A1/B1 | F | fresh | prepare写scratch→A两轮；写scratch→B两轮 | 首请求+首线程+firsttouch基准。 |
| A2/B2 | P | fresh | prepare写scratch→A两轮；写scratch→B两轮 | R2线程暖而目标fresh。 |
| A3/B3 | F | pretouched | prepare写A→A两轮；写B→B两轮 | 新线程写已经完成预写目标。 |
| A4/B4 | P | pretouched | prepare写A→A两轮；写B→B两轮 | R2线程暖且目标已写。 |

A阶段冻结顺序1,4,3,2；B新worker session、新source绑定、新target/allocation/线程birth，反序2,3,4,1，控制过程暖化/顺序漂移。每阶段四cell×两请求=8请求，16测量copy=128MiB；pre-touch/scratch另8copy=64MiB，执行器payload合计192MiB。sourcebootstrap另列。最终SHA八target×8MiB=64MiB，不能称16轮各独立SHA。两阶段上限16请求/32测量copy256MiB+16干预copy128MiB=384MiBpayload，两个bootstrap另列。

活跃fixture为source16MiB+A/B16MiB+scratch8MiB=40MiB，加现有staging/reserve/进程实际占用做gate。仍沿每node6GiB DRAM/2GiB HBM和旧应用reserve；主统一run/GPU，旧owner/container/XML不动。

## 4. 因素读数、时钟与观察者处理

R2第一轮：thread effect fresh=A1−A2，thread effect pretouched=A3−A4；target effect F=A1−A3，target effect P=A2−A4。两个thread effect之差为交互。分别计算source_slice wall/CPU、target_slice、copy_submit、sync、非同步host合计和service。P的R2.op1−op2检验request重置；R1.op1−op2对齐历史观察。

记录相邻wall/threadCPU边界、原始ns、op/service总窗、两轮间/endlog/bookkeeping、set_device、thread dispatch/join、prepare干预、postdiagnosis、controller query/prepare/execute/filled/published。set_device在record前仍计入实际worker外层。完整请求起于controller fresh inventory之前，含prepare预写/scratch、dispatch和最终日志/诊断；跨机器只用本地duration，controller全部同monotonic clock。device-wide sync表示该状态下完成，纯DMA/裸带宽未识别。

主协议保持相同logger与clock instrumentation；CPU空clock/相同结构dummy log各做一小段成本检查，单列且不从实测减除。若主要差额只在logger，结论写实际执行器观察策略的首成本。现阶段不增加GPU日志on/off请求；是否改日志属于后续任务。cross-runtime（如Janus C++）不借用本协议系数。

CPU affinity/NUMA、torch线程数、logger后端、process累计初始化历史、background owner、预算gate、source ptr/版本、target分配历史保存。sync无论快慢和所有长点均保留。

## 5. 三候选：同一A拟合集、相同成本定义

固定8MiB只识别固定/状态项，**不能识别字节斜率或带宽**。以下三候选均在A同一record数据上校准，所有原始首/后轮保留；两copy完整service使用sum并包括已定义bookkeeping边界。每请求k=2，F/P与轮次才提供可识别状态变化。

| 候选 | 结构与训练规则 |
| --- | --- |
| C0 bounded式常数 | 每op服务常数p，训练A全部op的平均；两copy服务2p。是相同新计时mode下的常数对照。 |
| C1强简单共享request | 首op=p+r，后op=p；每请求服务2p+r，跨请求总按请求数收r。用全部A同一非负最小二乘拟合p/r。 |
| C2显式state | 每op服务p+h·q_i；q_i是实际executor birth首次op（若A支持request重置，则q_i按request first并认定与C1同构）。使用全部A同一非负拟合p/h。 |

模型结构事前登记，A决定哪个状态有证据；系数、状态规则、拒绝域和B全部预测在B首请求前冻结。C1/C2参数数量相同；C0有更少参数。target若有独立大作用且三候选解释不足，保留残差和state_unidentified，停止admission，不能现场增加参数救预测。

旧bounded model SHA/.280796ms等原件保持，只作历史参照。旧域每path至多1op、单op4–16MiB，当前两copy诊断与新分派属于域外，正式旧预测必须null；C0不能命名为“旧模型已经通过扩域”。4/16校准与8尺寸留出若后续做，必须独立session和新目标/线程birth，不能拼旧不同计时边界的字节系数。

record内新首项已经替代训练服务的组成，禁止再叠旧含首项.280796ms；原.050530ms只为旧execute−record残差。C0/C1/C2使用相同A观测的record外成本：set_device、thread create/dispatch、endlog/bookkeeping、postdiagnosis；request/controller/filled共同段只由A冻结，禁止用B实测控制段回填。线程create等模式差异只能用事前F/P规则预测并由A拟合，不能把它们默认0。

服务、worker execute、controller execute、request→all_targets_filled分层评分。正式full预测保留prepare预写/scratch和所有诊断；published如果无独立校准则预测null。local单worker按原依赖规则组合；此处没有新的共享容量/remote/SSD参数。

## 6. 停止与模型准入

A结束先做完整资格和因素判断：实际birth、prepare隔离、目标未写/已写、原语/字节、SHA、时钟账目、source lineage及清理必须通过。CPU空clock开销给出观察者尺度，host证据依赖CPU差额与wall互相支撑；单次block不提供统计显著性或误差界。

- A无可见一致状态结构、只出现不能分离的target/observer交互，或差额与计时/调度噪声同尺度：止于8请求，结论state_unidentified，不开B。
- A若支持thread/request边界的明确预言，冻结三候选和B预测后进入B。B是一次独立复现/留出；总数至多16请求，不再补第三block。
- B同因素对照不保持预言，或新状态系数不能迁移：拒绝新state模型，保持当前有限域经验模型和未定原因。
- B按相同case逐项列绝对/相对误差、MAE、最大误差及phase残差。C2只有同时降低C0/C1的worker execute与full重要误差，并且误差减少超过A/B所见共同控制/观察者变化尺度，才进入完整成本模型。service改善而execute/full不改善，留作计时契约发现，不升级规划成本。
- 首状态按request计而与C1同构，或C1/C2同样准确，采用强简单共享request；不为state名称保留复杂度。target残差大时报告下一次需要的独立状态条件，不自动扩大矩阵。
- 用户主线仍是保活空间—权重可用时延；布局价值需将来同内容/target/预算的合法strong-simple/瓶颈均衡/交换对照。当前首状态验证不认证placement/solver收益。

所有失败/重试新run记录，原始长点不删。每阶段结束own target/source精确释放、Catalog/used/tx/SSD归零、supervisor/child group空；旧资产和拓扑XML保持。

## 7. 实施前审查与本轮交付

后续子agent只建设必要覆盖层/CPU协议检查，主agent统一资源并验收。CPU检查至少覆盖F/P真实生命周期、prepare线程隔离与scratch控制、target lease、产品状态机不被重置、时钟闭合、未知状态/旧模型域外拒绝、SHA与清理。remote CLI在仅部署文件的隔离目录检查，吸取报告45mode helper漏依赖经验；独立launch审查后才由主启动冻结阶段。

设计阶段交付本文件与[证据索引](../../codex_coding/results/v0.1.23.3/task2/first_op_validation/design_r01/evidence_refs.json)，当时无新GPU/SSH、运行脚本、产品或旧模型改动。当前实施记录统一见报告47，不将预登记计数当作已测样本。4/16MiB、实际模型runtime first状态、Janus本体复用/跨框架校准皆另任务；如需外推先补对应生命周期与资源合同。

依据：AGENTS/next_step/debug philosophy；计划18.30、报告45、first_operation文章；local_slope repeat原件/源码/最终独审；worker.py执行与HTTP路径；service_cost.py现有DAG/request模型；bounded_cost冻结模型/独立验证报告；范围指南与baseline路线图。完整路径与SHA见同目录证据索引。
