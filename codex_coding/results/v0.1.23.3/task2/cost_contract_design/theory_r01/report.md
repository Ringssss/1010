# 成本契约理论审查：先分清可观测账目、预测聚合与资源界

日期：2026-10-11；开发编号：v0.1.23.3/task2。本报告为设计审查，读取现有代码与原实验报告，新增GPU/SSH/产品改动为0。目标是确定最小、可验收的成本契约，再决定必要测量与后续规划入口。

## 1. 审查结论

FluidCKPT已有三种互补对象：资源负载优化代理、按实际worker顺序构造的执行依赖模型、完整请求的分层计时。下一步应固定各自的量纲、计时边界、状态域和拒绝规则。微观首成本尚未识别，不影响保留首/后操作标签和当前有限域经验预测；它也不构成增加统一thread/request首项的依据。

当前最重要的测量缺口是“指定执行形状下的聚合成本能否迁移”，以及“组合依赖和完整权重可用窗口能否由事前预测解释”。首次内部机制、裸HBM带宽、全路径容量矩阵，均不应自动成为下一轮工作。

## 2. 三层对象与数学合同

| 对象 | 输入和输出 | 可作的判断 | 当前实现边界 |
|---|---|---|---|
| 资源工作量约束 | 布局/流量、每资源工作量与容量，输出负载时间或LP目标 | 找潜在瓶颈、构造资源模型下的优化下界 | planning.py与joint.py；多数路径率是端到端有效率，当前输出首先是profile条件下的代理值 |
| 可执行依赖预测 | 明确ops、执行器、状态和服务时间，输出排程/关键路径 | 预测已支持计划的执行时间、比较可辨差量 | service_cost.py按source/target worker ready递推，支持serial；独立共享资源争用未进入递推 |
| 完整请求预测与实测 | inventory/prepare/dispatch/transfer/commit等边界 | 权重何时可用、模型误差来自哪一段 | controller/worker和实验覆盖层；filled、published及live weights_ready应有各自定义 |

设操作o的逻辑字节为n_o，资源r的工作量为w_or=a_or n_o，可信容量上界为C_r。资源负载必要条件是

`T >= L_r = sum_o(w_or)/C_r`，故`L_resource=max_r L_r`。

只有C_r是对应工作量定义下的可信物理容量上界、a_or覆盖必要流量，且时间窗一致时，这才是实际时延的数学下界。孤立测得的`N/execute_wall`或有效拟合斜率不是物理容量上界：加入主机固定项会令其偏小，并发/缓存/不同操作形状会改变它。用此类率求出的LP最优值应称“资源模型下界/优化代理”，不能据此断言实际计划必然更慢。

固定计划的依赖预测采用`finish_o=max_{p in pred(o)} finish_p + service_o`。现代码以“同worker参与的上一操作”为pred，令`start=max(ready[src],ready[dst])`，两端ready共同更新。它表达当前单worker串行、同一op列表的组织，允许不相交worker对前进。不同worker但共用HCA/PCI/DRAM控制器时，该DAG本身不约束重叠；需要真正决定候选差量时才加入对应资源预约或实测争用函数。

资源界和服务DAG消耗的是同一传输工作，不能相加。资源界作为必要约束或诊断值保留；执行预测由实际排程产生。把`max(resource_bound, DAG)`写成一个分数，也只得到两种约束的组合，并不自动生成可执行资源排程。

现joint MILP最小化上述流量资源代理，service_cost估计器并未自动接入其目标。因此“已有service模型”与“joint已按完整时延优化”是两个不同的实现状态，后续接口应明确评分对象。

## 3. 字节与驻留资源合同

逻辑恢复字节为每目标所需extent的覆盖量；若同一内容写两个目标，两个目标各自计入逻辑恢复量。一个远端op有source/target两条记录，逻辑n只计一次，不能把两端记录各n加成2n。操作覆盖量、最终逻辑SHA量、实际payload复制量和硬件资源工作量应分别输出。

本地HBM copy至少涉及源读n与目标写n。若容量C采用“HBM总读写字节/s”，则该资源工作量约为2n；若C来自copy有效`n/time`，该率的校准约定已经折叠读写，两者必须配套，不能再乘2。cache、staging、collective和SSD host materialize会产生额外流量，其a_or依执行路径定义；未测部分标unknown，不能把逻辑n当所有资源的实际流量。

现planning.path_for对每个命中resource累计n，反映简化的payload工作量约定。特别是local_HBM源读负载和网络路径的staging读写尚未显式分开。保持该约定即可解释现代理；需要物理界时，先补资源字节约定，再校准相同约定的容量。

保活空间分别记录稳态驻留、prepare/迁移/restore峰值和旧代lease持有量。容量合同包含目标、source、staging、scratch、运行时reserve及迁移期间同时存活的旧/新副本。当前采样最大值是“观测最大值”，完整连续peak仍未知。逻辑字节正确、采样未超额和峰值预算证明应分别验收。

首操作A的字节例子：测量copy128MiB，准备干预64MiB，bootstrap16MiB，共208MiB执行payload；八最终目标SHA64MiB，加bootstrap逻辑SHA16MiB为80MiB。两轮共写同一目标，最终SHA不提供每轮独立验证，也不代表208MiB全部硬件流量。

## 4. 主机段、异步提交与同步的边界

当前serial worker每chunk执行source read/view、land或staging copy、broadcast及device synchronize。op record还包括begin logger；end logger位于record之后、worker窗之内。set_device位于worker原生execute计时前，而A覆盖层另有完整外层窗。预测/校准必须绑定具体timer schema，不能把不同schema的残差直接合并。

对于异步copy，submit调用的wall区间与设备执行可以重叠。设排队时刻e、设备前驱完成时刻d_prev、设备工作d、主机进入sync时刻s，则简化完成关系为`d_done=max(e,d_prev)+d`，sync返回与`max(s,d_done)`及其实际主机开销有关。一次copy的wall不是`submit_wall + 完整device_duration + sync_wall`。

有连续host边界时，`record_wall=sum(互不重叠host wall段)+显式gap`可以精确记账；copy_submit和sync是wall段名称，不能分别重命名为“纯CPU提交”与“纯设备带宽”。threadCPU帮助识别主机工作，但跨线程wall不能sum为请求时延，CPU与wall也不是可加项。device-wide sync还可能等待同设备既有工作。

最小预测优先使用已定义的完成服务聚合`S_o(n,state,timer_schema)`，其内部包含主机与设备重叠。只在确需解释设备瓶颈时加设备事件或trace，并独立确认仪表改变的完整wall。事件区间含设备空档/host延迟的情形应保留，不能直接用于裸带宽。

## 5. 状态与成本参数合同

状态至少保存process/session/epoch、实际executor birth与累计op序号、handler/request身份、source内容/代际、target分配/写史、stream、通信pair、chunk、logger/仪表模式和NUMA等。通信pair warm与执行线程warm是不同状态；现warm_pairs按无序pair和incarnation维护，进一步使用方向条件时应单独登记证据。

首操作A八请求的nonlog首/次CPU差均为正；P R2 fresh/pretouched仍为30.440/187.743us。F−P第一op差为198.243/47.420us，P目标/顺序交互为−196.682us并超事前门。合同保留`first_state=unidentified`，拒绝统一thread/request首项；B已按协议跳过，不在本设计重新启动。

旧bounded local单op4–16MiB的0.280796ms是含首主机工作的有限域经验聚合，byte slope为null。新增首项必须替换聚合中的相同组成；再把首项加到0.280796ms上会双计。旧worker request residual0.050530ms来自record外差額，controller residual2.674446ms来自controller−maxworker，不能把它们重新解释为首切片/TLS成本。

后续可考虑简单聚合`T_worker(N,k | fixed_scope)=R_scope+k P_scope+b_scope N`。这里R只是指定作用域下的聚合截距，能够包含未识别的首次执行差額；它没有thread/request重置机制的因果含义。只有独立留出支持字节与op数迁移，才按该形式预测；固定N/k的实验仅支持工作点常数。固定k=2时，A的C0/C1总预测均为411.619125us，首/后拆分没有产生新的请求总成本区分力。

负斜率或负截距须保存原值和所有长点，作为形状/状态/噪声不相容的证据。非负约束可作为另一个明确候选，但不能通过clamp把失败拟合转成已识别物理参数。现service_cost.fit_path/fit_request_model具有截断或聚合throughput回退，设计验收应同时查看raw诊断与独立误差。

## 6. 请求时延与独立账目闭合

权重可用主指标采用同一controller时钟或有明确时钟转换证据的`max(weights_ready)-min(load_begin)`。当前all_targets_filled是controller收齐execute回复的时刻，包含返回/收集开销；published还含verify/complete，live引擎接管另有真实语义。filled可作固定synthetic工作负载代理，不能直接赋成live模型weights_ready。

把请求预测组织为带依赖的边界图：inventory/必要plan→prepare/lease/分配→worker dispatch→传输完成→必要commit/handoff→weights_ready。处于load_begin后的准备/预热/线程创建/元数据工作全部计入；预先规划等前置工作应明确其生命周期成本，避免通过移窗制造时延收益。SHA是否在ready之前由既有产品正确性语义决定，同一对照两侧保持相同规则。

独立账目验收至少包含三条：

1. 从原始相邻时间边界构造不重叠区间，核验顺序、包含关系、无漏窗/重复归属；worker总体timer须与段边界独立记录。规定timer量化/插入成本容差，不能先定义other=total−known再把等式成立当作通过。
2. 用controller同钟记录inventory/prepare/execute/verify/commit区间和请求总窗，RPC边界独立核对；跨机器仅比较本地duration。并行RPC取完成makespan，不sum所有worker或两端op elapsed。
3. 未解释残差独立输出为`unknown_wall`。`controller_execute−max(worker_duration)`可辅助误差定位，但混合launch偏斜、网络回复与调度，不能直接命名为纯dispatch，也不能作为其恒定性的证明。

账目闭合证明测量覆盖一致，预测准确要求冻结参数/状态规则在新请求前给出数值，再与实测比较。独立holdout如果用该请求实测控制残差回填完整预测，会使完整窗精度失去验收意义。

## 7. 现有经验聚合与可解释成本的对应

| 已有量 | 已有证据 | 契约含义 |
|---|---|---|
| local0.280796ms | 原四单op范围0.219493–0.333784ms；独立8MiB0.268654ms | 单op有限域经验服务，不能外推多op/新dispatch或物理带宽 |
| remoteDRAM10.187298GB/s、SSD0.918383GB/s | 旧fit_path的chunked完成服务率，fixed分别0.370791/2.953640ms | 有效byte斜率的倒数，含相关chunk组织；不认证物理容量 |
| mixed worker预测13.613298ms、实测14.133330ms | 一次独立新会话 | 组合实际偏差0.520032ms，尚非误差上界 |
| mixed controller预测16.287744ms、实测17.285672ms | worker差0.520032＋controller残差差0.477896ms | 两种误差来源可分账；残差项迁移需独立验收 |
| mixed fullfilled预测35.162678ms、实测37.902339ms | 再多控制差1.741733ms，总差2.739661ms | 完整窗百分比不足以覆盖service/execute误差 |
| A完整filled20.432–25.749ms | 同16op诊断中的postmetadata0.664–1.279ms | 微段几十us差量价值须在完整ready窗检验 |

mixed的w4 SSD source12.914913ms和w5 target13.885566ms存在重叠且后者含等待，两端不能sum；纯混合服务实测保持null。端到端published46.963841/88.079182ms已经实测，但正式预测仍null，因为校验/发布成本未校准。

## 8. 最小实验序列与停止条件

以下为条件式测量菜单，先完成成本契约，不进入新的布局/solver计划。每配置一次，确有歧义且能改变结论时再一次；所有新实验仍由主agent另行资源协调。

**步骤0：零GPU重放账目。** 复核旧bounded与A原件的独立timer边界、逻辑/payload/resource字节、first_state标签和拒绝域；记录已测经验量与unknown。先判断下一目标是原单op有限域、op数扩域，还是共享资源组合。旧A停止条件继续生效，首机制不自动追加实验。

**步骤1：仅为必要的N/k扩域做最小识别。** 固定一个确需使用的路径、native执行器/logger、chunk、目标形状与可观察生命周期，每请求写不重叠目标extent；避免A的“两轮覆盖同址”替代正常恢复。若仍只需要旧单op域，复用现有限资格。若需要`R+kP+bN`，三个训练工作点足以形成满秩设计：

| 训练/留出 | 总逻辑N | op数k | 每op字节 | 回答 |
|---|---:|---:|---|---|
| train1 | 4MiB | 1 | 4MiB | 基础工作点 |
| train2 | 16MiB | 1 | 16MiB | 同k的有效字节差量 |
| train3 | 16MiB | 4 | 4MiB×4 | 同N的op数差量 |
| holdout1 | 8MiB | 1 | 8MiB | 独立session尺寸迁移 |
| holdout2 | 8MiB | 2 | 4MiB×2 | 独立session分段迁移 |

该5请求模板是最多一个新增路径的最小参数/留出结构，可按实际必要字节等比例替换。固定观测作用域仍需真实birth/target写史资格。训练3点代数为`b=(T2−T1)/(12MiB)`、`P=(T3−T2)/3`、`R=T1−P−b·4MiB`，所有T使用同一聚合边界。现fit_request_model采用另一种等总字节batch定义，实施时明确选择，不能混用字段含义。三训练点仅是刚好可识别，独立holdout是准入必要条件。

若训练状态明显不同、b不可分辨、raw项不合理，保留限定形状常数/unknown并停止该参数分支；不通过新增firsttouch/TLS参数或删长点救模型。若只需要指定N/k工作点预测，可以用该工作点经验聚合，省去这个5请求模板。

**步骤2：只测目标组合一次。** 单路径聚合通过后，选一个部署确需的最小多op请求，预测其worker关键路径、controller execute和完整filled/ready，冻结后执行一次。相同worker串行组合检验累积，真正不相交worker的组合检验并发；两者按目标组织择其一，不能用全经w4的排列声称检验了共享并发容量。若该请求跨未经支持路径或首状态，先保持预测null。

**步骤3：仅在争用决定下一选择时补一对对照。** 选择同byte/路径的两条传输，独立完成与共同资源重叠各一次，保持真实执行计划相同且只改变允许重叠关系，观察完整makespan和各参与worker。用已有单路径资格作隔离参考前提是状态/schema一致。结果只认证该共同资源与并发形状；资源不同或实现不能重叠时，无此测量需求。

每步的准入同时检查执行资格、完整SHA/生命周期/清理、账目闭合、事前预测与新会话迁移。service误差、worker误差、controller误差、主ready窗口误差各报绝对/相对值；MAE/最大误差在n=1–2时是观测摘要，不冒充置信界。

主指标验收在测前指定有决策意义的绝对误差预算δ_ready，以及该阶段拟辨的差量Δ；要求独立主窗口误差小于δ_ready，且δ_ready明显小于|Δ|。例如将来要辨1ms差量，现一次execute约0.998ms偏差提示尚无足够余量；10ms级差量可优先验证必要粗聚合，但仍需合法同任务与实测。不能依据结果事后放宽δ，也不能把训练残差当未来误差界。

复杂候选只有同时改善独立worker/execute与主ready预测、且改善超过记录的仪表/控制变化尺度才准入；若与简单聚合等效，采用简单聚合。局部拟合好而完整ready无改善，停止细化首机制，保留测量发现和有限域成本。成本合同通过后才另行定义同内容/目标/预算的选择问题，本报告不把成本校准通过当成布局或论文性能收益。

## 9. 来源与本轮交付

本报告的数学关系为基于执行语义的自有推导，属于建模与验收常识，未命名为新的研究贡献。读取以下现有来源，原数据与模型未写回：

- AGENTS.md、docx/next_step.txt、docx/debug_philosophy.md。
- docx/plans/2026-10-11_first_operation_state_validation_plan.md。
- docx/articles/2026-10-11_local_hbm_first_operation_cost.md。
- main_lib/fluidckpt/cluster/service_cost.py、planning.py、joint.py、worker.py、control.py、path_catalog.py。
- codex_coding/results/v0.1.23.3/task2/first_op_validation/analysis_result_A_r01/report.md、execution_result_A_r01/report.md。
- codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/report.md、model_decision_r01/report.md、review_final_r03/report.md。

另已审查docx/plans/2026-10-11_cost_accounting_and_identification_plan.md。其L1–L5设计[1,N,K,C]经独立Fraction高斯消元确认秩4，六个校准请求含唯一重复锚点、两个新session留出请求的方案具备四项聚合辨识条件。第8节5请求模板是省去独立chunk项时的更简单候选，不能与统一矩阵叠加执行。若确需32MiB边界外形状，采用统一N/K/C矩阵即可；L4的chunk项仍按该策略下聚合增量解释。

已向主agent反馈：现controller的filled是“收齐执行回复后确认目标搬完”，包含回复与收集开销；C2完整filled需另行事前冻结prepare/controller/诊断段预测规则，或明确保持粗迁移假说；主ready误差预算应在留出前与拟辨差量一起登记。该计划未由本agent修改。

本轮验证为本地python3 stdin只读检查：Fraction消元确认统一矩阵秩4与简单模板秩3，报告UTF-8可读且状态/unknown/主指标误差字段存在。无硬件性能验证。本轮仅新增此报告；没有新增性能样本、运行脚本或产品模型。待主审核对契约取舍后，实施任务应按所选最小分支另行冻结接口与实验预期。
