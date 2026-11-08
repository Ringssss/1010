# Stage Summary

## 2026-10-23 补强：完整过程证据链已展开

用户要求参考DSV4详细记录，新增online_cost_calibration_detailed_20261023专题，代码和原件由两子agent独立核查。首次/后续、冻结/留出、T/D/W分解、七段账与rank内部并行窗口分别展开；数据仍为10/12。计时起点前parked evidence/gate/command明确窗外，未来planner完整请求比较应统一纳入必要前置项。新history详细版快照保存，旧快照不覆盖；功能/预测结论和下一步规划收益方向保持。

## 2026-10-23 当前：本域校准完成，恢复后讨论规划收益

用户明确授权上下文归档，当前history与固定快照已更新，旧467行GLM交接另存pre_archive备份。10/23仅本地文档/证据核验，未新增硬件实验或现场检查。最新主线10/12：两节点三GPU完整Qwen70.33GB四请求同controller，prime3.024893s单列，cal2.903039s唯一outer=.423580s；remote预测4.572579/实4.581127s、near1.212516/1.290400s，误差−.187%/−6.036%，73项独审通过。旧12路径服务点不变，全部正确性/物理park/资源及退出已于10/12通过。

范围为同controller首次完整恢复后的固定模型/尺寸域；首次和复杂域另验。下一步应同预算比较强简单与既有规划器选择的真实weights_ready，计入在线规划准备成本。当前未启动该实验，停止微项校准。组件与既有三节点synthetic、历史GLM真实多节点证据分开，优先复用已有底座。下文“整体未过”等是此前阶段快照。

## 2026-10-12 当前：成本八请求完成，分层资格明确

首读docx/experiments/2026-10-12_cost_ledger_calibration_report.md（18.34/记录49）。GPU0整卡占用上界54GiB拒绝，0bootstrap/0cal；新GPU4域nativeworld1完成6校准+2新session留出，512MiB测量+192MiBbootstrap。计时/SHA/回收过，C2独审最终138+资源169过。三项worker候选留出误差+.45/−6.16%，完整filled改善低于冻结观察尺度而未保留；不是复制失败。实际filled8.2–10.3ms/worker.43–.76ms、published118–313ms受全SHA主导，非liveTready。下一步用既有原件定位控制段迁移，不追加矩阵/solver。旧模型/产品/history保持。

## 2026-10-11 当前：先建立成本项与可辨识实验，再回规划

用户最新要求优先于下方直接回layout建议。总计划18.33/记录48，新docx/plans/2026-10-11_cost_accounting_and_identification_plan.md为首入口。三agent只读审计/CPU矩阵/理论审查，资源界、DAG、请求层与时间所有权明确；N/K/C秩4是设计资格，不是新硬件结论。最多6校准+2条件留出尚未执行，A状态unidentified/B跳过保留。下一步建设最小计时覆盖层与CPU资格，再由主协调硬件；无新GPU/SSH/产品模型修改/全历史归档/history覆盖。

## 2026-10-11 当前：首操作A实机通过功能，状态未识别，B跳过

计划18.32/报告47，first_op_validation actual_A_r01唯一8请求完成（非待实施）。R2非log首差CPU Ffresh225.434/Pfresh30.440、Fpretouch233.962/Ppretouch187.743us；P实际持久同birth仍首慢，F−P不等价、P目标/顺序交互超预登门。独立gate unidentified/S5us，按协议止A，不启动B、不改阈值。首差存在不等统一thread/request模型成立；同A三拟合仅诊断，frozenfalse/预测0，旧bounded保持。

80MiB含bootstrap逻辑SHA/208MiBpayloadcopy，所有cell线程/对象/SSD/九进程清理，旧13owner12container135source/XML保持。执行211、独审164/59/4项过，采样预算内，完整peak未知。首读analysis_result_A_r01与review_actual_A_r01。下一步不自动补B或无限微调，回同预算合法layout的可辨差量/真实主指标；本文以下A8+B8待执行均历史快照。

## 2026-10-11 当前：FluidCKPT主体与论文组件进度重新对齐

用户明确Janus仅为组件源码参考，按Fluid缺口选择性迁入有价值实现，非Janus拓扑改造/引擎互通/替换底座。AGENTS、专题与计划18.31已落实。新系统概览docx/articles/2026-10-11_fluidckpt_paper_system_goal_components_and_status.md为首入口：已有对象/资源图/模型/规划/执行接管/评测六部分，重点是复杂拓扑同预算决策收益。

历史真实QwenTP2恢复及正确输出已通；最新三节点六GPU是合成TP4块恢复，未升级当前拓扑完整模型资格。算法已有而当前复杂场景同预算收益未认证；有限域预测通过、首操作A8+B8未执行。后续先状态验证和计费契约，再同任务两布局→真实模型收益。此次仅口径/规则/说明更新，无实验或源码迁入。

## 2026-10-11 当前：A8+B8状态规律方案与Janus复用审查

计划18.31/报告46；本轮仅设计和本地源码审查，无GPU/SSH/移植/模型改动。新正式方案first_operation_state_validation_plan固定8MiB F/P实际线程×fresh/prepare预写目标，scratch匹配process干预；先8请求，支持明确预言才新session8独立留出，C0常数/C1共享request/C2firststate同计时拟合，主指标没增量选简单。固定size不拟BW，旧bounded两copy域外null。

新Janus文章明确：缓存/生命周期/D2D/rolling/NUMA已有，NPU hooks实装、generic缺项fatal/远端NPUonly，pool内释放非外部free；最高EPDT单源有地址数/TP/layout前提，源锁D2D后释放再fallback。现Fluid底座和Janus式机制已有，应保留，必要时仅segment/extent/身份/lease/目标commit adapter，复杂拓扑新增收敛规划层。原版海光移植未验收，不是仅加图；四线程池轮转与首操作状态协议有关，参数不跨runtime照搬。

## 2026-10-11 当前：负斜率诊断发现请求首操作主机成本

计划18.30/报告45，新article2026-10-11_local_hbm_first_operation_cost.md。local_slope首八请求native负斜率未稳定复现（16MiB1.037ms长点保留），diag负差主要新eventbegin仪表。最后同handler同ptr双轮4MiB .346575→.089062ms、16MiB .289744→.123432ms；first尺寸差−56.831us来自log/切片/copy提交，second差+34.370us主要同步，source_slice CPU首/次下降77.210/61.481us。支持首op host成本影响尺寸表象，尚非具体库函数/旧每点唯一根因；真实torch2.10 header排除fullslice bypass。

两会话所有新权重/SSD/进程清理，旧资产保持；最终review_final_r01 121refs核过，首阶段142checks、repeat78checks。旧模型未改，GPU不再增加。下一步先模型契约分首操作/后操作/字节状态成本、避免新首项与旧service双计，暂缓直接layout对照；旧单op常数只经验近似，不能外推多op或纯带宽。详细入口local_slope/execution_repeat_result_r01/interpretation.md和review_final_r01。

## 2026-10-11 当前：有限域模型已获独立迁移实测，下一关是同任务布局合同

计划18.29/报告44，bounded_cost/model_frozen_r01只用旧训练冻结e98b...；local4–16MiB单op服务常数.280796ms、带宽null，remote/SSD复用原fit，共享request/dispatch各一次，published预测null。actual_r03是唯一有效会话，两新配置各1：local服务.280796/.268654ms(+4.52%)；mixed worker13.613298/14.133330(−3.68%)、execute16.287744/17.285672(−5.77%)、fullfilled35.162678/37.902339(−7.23%)；实际published88.079182ms。64MiB全SHA，review_final_r03 96项/48引用过，decision_ready=false。stage命名冲突及两次GPU准备脚本失败保留，零holdout，不调模型。

全部新VALID/used/tx/SSD清零，九supervisor成功/childTERM组空，旧资源/XML保持，无GPU后台实验。三path源内容/目标不同且全经w4，排列同分，不能直接充当placement收益；本次execute差约1ms，不用于亚ms细排序，也非未来误差界。下一步先同内容/target/HBM-DRAM预算的两合法layout合同和CPU评分，优先粗SSD替代差量，复用现强简单/交换/joint，不扩solver/全路径扫描。首读execution_result_r03、model_decision_r01和review_final_r03。

## 2026-10-10 当前：查询开销已定位，完整成本模型尚未识别

计划18.28/报告43及breakdown article第8节为最新。cost_calibration/execution_actual_r02完整通过，r01监控误拒active事务的失败与精确SSD清理单独保留。新实验覆盖层仅按PID缓存库路径/CDLL，所有动态信息fresh，原产品/旧worker不改。查询中位数60.576→6.190ms；36MiB同源cached/legacy各一次，完整filled156.026→42.207ms、校验发布228.260→114.392ms，execute仍约23ms。改善来自两次查询，不是placement或链路提速；replica非live模型可用。

三路径9训练+仅local3次规定补测+2组合，含bootstrap200MiB全SHA。本地HBM4/16MiB均值.381/.277ms仍为负斜率，原fitter拒绝整体模型；冻结model/prediction=null，不能报准确率。最终review_final_r01状态evidence_review_passed_model_unidentified，查询/功能/清理通过，absolute_prediction_ready=false。所有新source/target/SSD退役，9supervisor成功/组空，旧资产保持，无活跃GPU实验。下一步先讨论有限范围local固定项/分段成本与独立验证，避免扩大重复/solver。正常增量，不改history。

## 2026-10-10 当前：成本模型与breakdown对齐

计划18.27/报告42，新增article2026-10-10_multitier_breakdown_and_cost_model_alignment.md。原实际4recipe预测null/空profile，未用模型，旧八worker两路径参数不适用。只读审计解释约62ms查询和C3先接再发SSD的worker依赖；容量/覆盖成立≠共享带宽或时延精度成立。资格同4MiB lookup事后诊断execute误差−4.60/−22.60/+1.94/+19.55%，filled约±4%被共同控制项压低，细排序仍不可靠。prediction_r03独立算术/15SHA过、r02跨controller域断言失败保持；模型117检查/evidence27refs。无新硬件实验或产品改动，下一步先计时口径与控制段，再关键路径最小校准/独立组合，复用现模型。

## 2026-10-10 当前：三节点六GPU三层恢复实机已通过

计划18.26/报告41，三节点计划12节。actual_r01/sessionr02一次真实运行：六BDF/NUMA触页注册、六必要pair、23路径、SSD32MiB按16/8/8、SSD_only/C0/C1/C3四rank32MiB全SHA均过。filled/published约132/195、73/100、71/97、81/112ms，inventory约62ms；不同budget手工witness、replica非live，外部gate/模型handoff不在窗，不报solver/model收益。最终review_final_r02纳103SHAop/412MiB与同HIP绝对采样upper，DRAM采样/保守HBMupper在cap，完整peakunknown、28absolute256margin未证。9supervisor成功/child计划TERM组空/3容器pids1/noOOM/旧13PID12container保持。产品仅synth TP参数最小兼容修复，原XML/通信不改。下一步是主指标诊断与同预算布局/共享工作点，已通基础设施不重建。

## 2026-10-10 当前：真实绑定与node27新单卡突破

计划18.25/报告40，三节点计划第11节。mapping_binding_r03选28GPU0/1、30GPU4/5、27GPU0/4，D0/D1=0/3、4/7、0/4，本地NVMe目录由mount实际确认，避开已知oldkeeper/vLLM。27新boot旧D不存在，三个旧容器exited；主新隔离6GiB容器一次GPU0/BDF09 1MiB全SHA过，进程19.9906s包含init，child/组空/noOOM/旧资源不变。CPU adapter原API79与失败journal7过，native测试源码缺snapshot已严格逆推exactSHA，最终独立核两断言差异。新6GiBDRAM/2GiBHBM每node聚合是本场景新预算，旧2GiB小实验失败保持。六worker/NUMA/网络/三SSD完整恢复仍待运行，成本/modelT_ready未知。无产品/原XML/全历史归档修改。

## 2026-10-10 当前：三节点六GPU与多层保存场景

计划18.24/报告39，docx/plans/2026-10-10_three_node_multitier_scenario.md为可读入口。四目标两helper/六DRAM池/三SSD逻辑位置、32MiB processed需求与完整SSD底本、104候选路径，五阶段隔离容量/位置/共享变化。C3快层仅28MiB，至少4MiB逻辑SSD来源；pagecache使物理磁盘量未知。两helper见证跨node供给。contract_cpu_r02 249结构项、review_final_r01 247独立项过；r02补384MiB条件暂存与SSD两腿，未实际分配。复用现SSD/Worker/CPUowner/planner，三node六worker/真实SSD/预算/成本/model均未准入；原2GiB/node不变，GPU/SSH0，无solver优势结论。后续先实机映射与必要工作点，再同预算强对照。编号v0.1.23.3/task2，正常增量，无上传/全历史归档。

## 2026-10-10 当前：多位置竞争研究入口通过

计划18.23/报告38，multiple_placement_competition计划。原r03两个被DRAM支配位置导致researchgate失败，保留；由1<v<12/7先验推导后只跑v3/2 active_r01。8candidate/121subset使用见证齐，同16MiB最快/单步均衡11/12、交换/joint5/6同解，无joint超强exchange收益。event容量和原LP/fastest witnesses一致；95独立checks、objective531通过，均symbolic。六role物理部署/原2GiB/node未准入，GPU0、产品无改。主动子agent长期约定已写AGENTS，未上传/全历史归档。

## 2026-10-10 当前：TP4分层合同与空间—成本对照

计划18.22/报告37，2026-10-10_tp4_layered_scenario_contract.md为可读入口。contract_cpu_r03四层C0/C1equal/C1uneven/C2shared与现strongsimple/新均衡/填充/joint离线完成，完整256/81/36/36subset/Pareto。C1不均base-only同主分且零空间，C2共享后8MiB可降资源分4→2，strong/joint同解；均为symbolic评分非硬件时间。feasibility_r03多worker原2GiB/node未准入，旧keeper不能拼session；metadata-only两端身份正常、目录未新刷。review_final_r02独立44+4+28项过，GPU/产品改动0，无上传/全历史归档。

## 2026-10-10 当前：必要性理论与算法边界

计划18.21/报告36，articles/2026-10-10_joint_planning_necessity_and_boundaries.md主文完成；可分top-k证明、位置稀缺、max平台期、共享cut三反例。原strongsimple三个control在4target理论平台期选空8s，原joint4prefix5s，均衡简单参考也5s；16subset离线与独立27数学/14方法项通过。全部理论参数，无GPU/远端/产品改动。真实多HCA并发、预算和净收益仍需映射验证，不能将图大当必要性；不自动扩当前2GiB/node配额。

## 2026-10-10 当前：预算受限决策差异负gate

计划18.20/报告35。已复用现成strongsimple/joint/serviceDAG，2case×3symbolic×9合法subsetCPU完成；受限同target28/caps16+16三个成本区间simple与joint同代价，不为等价编号交换追加GPU。独立52项通过/scopeaudit4项，真实成本与主指标未新测。已有DAG评分不重造，managed只重评current/base/MILP的集成边界明确。两同primitive单op+holdout契约prepared-only，GPU0；只读28/30身份/保护/端口正常，旧pending不动。无产品修改/上传/全历史归档。

## 2026-10-09 当前：四布局地图/时间认知验证

计划18.19/报告34。ABCD各一次2×16MiB HBM，实际路径与roles/order/fullSHA/同预算/退役均过；filled4.203/5.813/5.669/6.908ms，A最短D最长支持预言。published49.881/69.844/67.461/46.628ms不同排序，校验/控制不等于传输。反向payload与选定IB/GPU-NIC链通过，不升级整图容量或绝对模型。最终layout_validation/review_final_r02/qualification.json独立116项过。GPU执行及STOPrc0，外层localtarfilter失败保留、collect-only_r02补收；PID214/217退出、预算观测在2GiB内/完整peak未证。主线暂止于认知验真，未上传GitHub/全历史归档。

## 2026-10-09 当前：最小NUMA成本与产品块恢复

计划18.18/报告33。新组合28GPU0/NUMA0→30GPU4/NUMA4完成实际IB/gdr0资格；local64 1.471ms、remote64因果窗5.494ms/fullSHA通过，但remote源观测DRAM超过2GiB约1.88MiB，保留失败。正式profile与完整峰值未就绪。产品r02复用原Worker/Cluster.transfer、先mesh+pairwarm再查预算，2×16MiB同预算布局实机通过：filled5.456→3.956ms，verified发布69.625→43.227ms，SHA/副本退役通过；主要差在校验发布，非完整模型T_ready或solver增量。nearest-target与分散布局同解。最终保护/路径/资源审核先读报告33后续小节。没有全历史归档/history改写。

## 2026-10-09 最终：28/30自有XML与IB小通信完整闭环

计划18.17/报告32.12。新fresh init/copy两端通过，旧15s总预算偏紧；新统一runtime_gate_r03/d1ae使用initcopy60/NCCLinit60-total90。最终probe_nccl_2830_node28_r03/node30_r03在GPU2/BDF55↔GPU0/BDF09，经IBext_v8/shca_0:1/gdr0，全1MiB SHA、GPU+NIC物理PCI/NUMA/affinity、自有XML加载、barrier/退出/旧保护全部通过。修了日志cudaDev/rank/dev映射及vendorGPU/NET空属性严格派生解析，复用已有海光插件（原失败/raw保持）。30root满通过私有shm小资产绕行，未清理或改默认XML。仅选定路径功能通过，带宽/共享竞争仍待校准，27/29旧pending保持，未全模型迁移或性能优化。

## 2026-10-09 最新：node28满盘状态变化，保活内存与磁盘已分清

计划18.16/报告31：14:02同boot/两容器连续，根盘86%、余59.97GiB；自有SizeRw合计163.64MiB、共同镜像逻辑32.21GiB单计。GLM keeper unique memfd273.88GiB为共享权重内存，另/data自有837.30GiB模型/实验文件在不同磁盘（54%使用、余784.54GiB）。宿主json日志权限不足、昨日满盘根因未完全归属。本轮未清理/停止/GPU；28可重新核运行准入，不能沿用根盘仍满或直接认GPU健康。27/29 pending保持。

## 2026-10-08 最新：27/28/30替代范围已调查，fresh运行仍未就绪

v0.1.23.3/task2，计划18.15/报告30，当前入口three_node_recovery/inventory_qualification_r02/report.md。28/30静态清单完成；27新KFD采集D child4037472/start477235183仍pending（controller已回收），28实际Dockerroot根盘available0阻断隔离容器，30隔离容器成功但唯一fresh init在Torch导入2.096s后set_device到总15s超时，child13精确TERM后退出。未copy/XML/带宽，next为30配置/环境只读差异与28存储可写条件；旧29两pending保持，本轮无29调用。当前用户切换范围覆盖下方旧的等待node29路线。详见next_step最新段，未全历史归档/history覆盖。

## 2026-10-08 最新：服务端入口仍拒绝，现场采集命令已准备

计划18.14/报告29。两次串行11/12.11网络SSH均约7.5ms明确Exceeded MaxStartups，未认证、server采集未执行；本机own SSH/mux和目标三IP TCP22为0，不能排除server残留/其他来源。官方机制可能global/per-source，未取得实际server配置/负载不能唯一归因。console_readonly.py已完成5CPU/语法验证，待node29已有会话或控制台执行，失败count=null不伪装0；不改系统/网络/XML。旧两个29PID未清理证明、runtime/performance保持false。结果sshd_investigation_r01/report.md和access_review_r02/report.md。

## 2026-10-08 最新：远端异常集中node29，取得SSH拒绝文本

计划18.13/报告28。同27/28/30数据网SSH正常对照，29管理网不可达、数据网ping/TCP通但SSHbanner/keeper无响应；从28交叉复现。29备用11.211.3.29返回Exceeded MaxStartups，是一次未认证SSH连接准入拒绝证据，实际连接来源/阈值及HTTP根因未定。node28模型NFS小读正常，项目/DTK/hyhal本机存储，不能统一归因node27本机HIP失败。结果connectivity_nodes_r01/cross_alt_r01与dependency_check_r01；旧两个29PID仍pending、运行/性能仍未就绪。未改原XML/sshd/网络或启动GPU。

## 2026-10-08 最新续作：初始化边界已定位，继续阻止性能规划

计划18.12/报告27，v0.1.23.3/task2。resume_control_r02确认node27原keeper/源进程身份连续与旧own退出，node29SSH/HTTP仍不可用（数据ICMP通/管理ICMP失败）。本机local_xml_node27_r01在前置HIP/SMI查询失败，零通信rank；独立metadata对照all-visible在hipInit10s报警、0/1在库加载阶段SIGSEGV，阶段和因果边界分开，两child退出/core0。修正历史RELEASED误拒，CPU16过；完整70.33GB底本目录/lease前后一致窄保护通过，未重读权重或完成全部postflight。最新resource_map/resume_map_r02只追加这些失败/状态证据，原物理/rate不变、XMLruntime/性能仍false。远端两个oldPID继续pending，不能启动新GPU或把控制健康当新GPU运行时健康。

## 2026-10-08 最新：资源地图第一版完成，运行验收受node29控制异常阻塞

v0.1.23.3/task2，计划18.11/报告26节。最终resource_map/build_r04包含16GPU16NUMA8HCA88唯一桥和物理/runtime/availability/measurement分层，查询与严格准入接口已建，自有每node完整XML静态通过，原厂文件不动。新XML startup未取得实际RCCL图；源Gloo2/4clients后node29 SSH/HTTP超时、ICMP可达。source own已退出，remote PID21883/start475875814与21901/start475875846经两次精确cleanup仍未证，见xml_recovery_r02/summary.json。最后完整保留服务检查是resource_map_preflight_r02，当前postflight缺证，不判损坏。structure_ready=true、runtime/performance=false；不能拿12:00资源快照当live。恢复节点控制后第一步核账本/keeper，再谈新运行；最终audit只认证地图/状态记录的正确性。

## 2026-10-08 最新：准确拓扑已认证，性能对照未完成

v0.1.23.3/task2，正式计划18.10/报告25节。镜像默认XML和unset后vendor mapping均把实际NUMA3的GPU1置NUMA1；empty system version2显式输入经四rank图验证与所选设备sysfs一致。unset/empty实际non-GDR，原XML实际A-GDR/B-off，物理正确与能力/速度分开。两startup通过；两正式组第二组全卡free下降176MiB超128MiB阈值且两机有其他vLLM每卡约50GiB，停止剩余性能组，不排列速度优劣。探针均退出，keeper/底本保护通过，生产配置/profile未改。结果入口topology_alignment/provenance_r01/report.md和各probe/postexit/summary.json；下一步先查活跃GPU owner与共享负载，再补同条件测量。

## 2026-10-07 最新：keeper桥接和T2薄runner整模型功能通过

正式计划18.9/报告24节。bridge_real_r01真实1GiB单A/B/pair RPC45.165/59.922/69.974ms，内容/租约/物理free与保护通过；窗口非worker绝对monotonic，物理归因不升级。冻结r08读取外部layout/caps/目标/commoninterleave已接runtime真实ID，T2指定固定复制布局功能smoke2.827939181s通过全SHA/输出/清理；physical8GiB但命中6GiB。CPU强simple/joint三场景同解，profile仍provisional。最终keeper_bridge/audit_final_r01 366项通过；当前会话/底本保留，无新增solver或重复策略GPU。

## 2026-10-07 最新：双流原因拆分完成

正式报告23节、计划18.8。G0同NIC混合GDR复现237.870ms；G1拆NIC且B启GDR98.194ms；G2拆NIC但B非GDR205.220ms≈自身独立预期204.996ms。已分离路径互扰与B自身GDR效率，未唯一定位NIC/PCIe/CPU微组件。runtime XML/sysfs不一致已取证，未改生产配置。最终`topology_scenarios/diagnosis_audit_final_r02/report.md`263项通过；G1/G2原failed为日志可见性，后置derived补证通过、新GPU0。所有路径前缀为codex_coding/results/v0.1.23.3/task2；最新保留现场未变，下一步仍公平路径机会下的生产桥接。

## 2026-10-07 最新：拓扑阶段1/2完成

九配置各一次校准通过：网络独立预期168.256ms、并发235.604ms（ratio0.714147），本地H2D两机ratio0.991993/0.978973；隔离共享路径压力有证据，生产S5-P归因与整模型收益未建立。三场景最终为`topology_scenarios/scenarios_freeze_probe_r08/manifest.json`，独立`audit_final_r02`390项通过。契约/CPU计划可用，完整runner/预测未就绪；下一步只补应用桥接、布局/caps/schedule薄适配和同复制机会强简单placement。所有路径前缀为`codex_coding/results/v0.1.23.3/task2/`；有效运行会话仍为recovery_r03 ownedoverlay，保护与回收通过。正式记录第22节。

## 2026-10-07 当前：真实baseline加载分支完整通过

开发编号v0.1.23.3/task2。vendor native122.266963s、Blitz单目标TP策略HIP适配2.804379s、真实Store HIP131.121135s均完整Qwen TP2 SHA/输出/回收验收。最终表为`codex_coding/results/v0.1.23.3/task2/baseline_loading/summary_r02/revised_export_r01/report.md`，正式报告第21节记录全部控制修复。新当前现场为`baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json`，旧目标PID失效；4keeper/DRAM和2parked引擎保留，临时容器已清理。三项初始来源/格式不同，不计算算法速度比；原版整系统及物理拓扑/前台/动态缺口继续按计划门槛处理。本轮history-chat未改。

## 2026-10-03 最新：四节点生命周期已真实通过

最终入口为`docx/articles/2026-10-03_lifecycle_results.md`与`codex_coding/results/lifecycle/final03/analysis.json`。A→B/B→A/远端A到首正确token分别观察到3.75×/2.29×/3.11×改善；适配HIP IPC的resident命中仍更快。A已实际迁到node28 DRAM和node30 SSD，本地A权重HBM/DRAM均释放。源码无漂移，全部校验通过，自建进程已清理。下方“仅规划/未验证”条目是历史；严格全系统内存预算、并发/中途召回和算法增量收益仍待研究。

## 最新续作：2026-09-29 managed04（优先于下方历史快照）

`v0.1.23.2/task2`单模型单目标联合控制层已通过真实验收：400个全模型候选、MILP gap=0、
14动作紧预算切换、提交后中断续作、发布目录恢复。单卡上限放开到8 GiB，选4+4 HBM＋1+1 DRAM；
临时副本预算0。42项相关CPU测试通过。

旧/新加载2.034551/1.722079s，传输1.379845/1.221305s；每布局1次，均全量70.33 GB＋3条生成通过。
源码指纹无变化，清理成功，测试GPU已释放。用户授权停止的3个agentx容器保持停止，其他服务未动。

新增现实约束：GPU空闲不代表本地NUMA容量足够。node28 GPU2底本从不足的NUMA2改放NUMA3；
GPU3仍用NUMA1，路径重新校准。NUMA预选和全部TCP占用检查已接入启动链。

必读：过程文档附录I；`codex_coding/results/task2/cluster/managed04/managed_analysis.json`。
后续仍有多目标、底本迁移、通用崩溃恢复、在线反馈及严格SGLang保活对照，不能称全部七步全范围完成。

## 当前快照：2026-09-29，v0.1.23.2 / task2

最新归档动作：根据用户对信息量的反馈，参照v0.1.19.10进一步补齐过程文档函数级实现、状态机、逐op证据、失败推理链及下一步代码落点；原始结果未覆盖，产品能力和性能结论没有新增。

当前在“已有完整系统，开始将联合规划实验原型建设为控制层”的阶段。用户要求开发编号固定，不随压缩递增。当前模型为Qwen3.6-35B-A3B TP2，GLM适配问题不是前置阻塞。

已完成：主动NUMA注册DRAM保活；处理后权重capture与真实恢复；单机多副本；三节点八阶段；compact控制面；可选双缓冲正确性/失败重试；profiling；有限候选单场景联合MILP和同预算placement实测。

最新证据：旧/新布局传输1.361813/1.255519s，完整加载2.133549/1.812675s。每种一次；完整窗口15%差异含准备变化，更直接搬运改善7.8%。两次70.33 GB与生成检查通过，placement01源码指纹/清理通过。

关键边界：固定node28完整底本、目标node29、额外8 GiB HBM＋2 GiB DRAM、每目标HBM最多4 GiB、36候选。不能说已证明单卡8 GiB集中劣于均分，或已获全局/多目标最优。

待实施：统一资源需求预算、全模型候选、执行器一致成本、联合规划接口、迁移峰值与覆盖保护、恢复时快速LP、预测反馈。先单目标完整闭环，再多目标。最新用户消息仅要求归档交接，没有启动新GPU实验。

必读：根`history-chat.txt`、v0.1.23.2完整过程文档、`docx/plans/2026-09-29_task2_joint_planning_control_plan.md`及placement01分析。最新实验资源已清理；下一次重新preflight，通常每方案一次、必要时两次。

## 历史快照：2026-09-27（下方保留，非当前状态）

## Current Stage

`v0.1-init-project` —— **规范化建设 + 平台缺陷阻塞下的等待**。

两条线并行：

1. **工程线（已完成本轮）**：把散落在 `/home/zhujianian/fluidckpt` 的成果按规范迁移进
   `fluidckpt_zjn` 仓库，确立 `main_lib/`（核心库）/ `codex_coding/`（脚本与结果）/
   `docx/`（理解）/ `code_building/`（过程）四层归属。
2. **技术线（被阻塞）**：GLM-5.3 在本硬件上无法启动，导致三节点 max-flow 保活实验
   与 SGLang 保活端到端对比无法产出有效数字。

## Completed Work

### 平台缺陷定性（最重要的产出）

- **证明 GLM-5.3 的崩溃与 FluidCKPT 无关**。用完全无 hook 的纯 `sglang.launch_server`
  （`FLUIDCKPT_MODE=""`，进程 cmdline 与日志双重确认）复现了**完全相同的**崩溃：
  21:03:43 启动 → 20:34:28 报 `KERNEL VMFault`，共 17 次，栈完全一致。
- **机制完整追踪**：
  - `is_hcu()` 为 True（`gfx936` 在支持列表内）
  - `is_hcu_native_fp8_supported()` 要求字面量 `gfx938` → 恒为 False
  - → `use_fp8_index_k_cache` 恒为 False
  - → `index_k_buffer_dtype = bfloat16`
  - → `_use_hcu_bf16_index_cache()` 为 True
  - → `_get_q_k_bf16` → `lightop_attention.fuse_layernorm_rotary_embedding` 崩溃
  - **NSA 两条 HCU 分支调用同一个 `_get_q_k_bf16`，所以翻任何 flag 都绕不过去**
  - `lightop_attention` 是 Hygon 厂商 pybind11 builtin，无签名、无 env 开关
- **横向证据**：同一硬件上 Qwen3.6 用 triton attention backend **成功 fired up**（2026-09-22 日志），
  证明平台本身健康；GLM-5.2 与 GLM-5.3 从未成功启动过。

### 传输能力测量（可直接用于规划器）

- 成对单流：node27↔node29 **25.7 GB/s**
- N 流聚合（8 rank/节点）：**205.8 GB/s**（64 MiB 消息时 230）
- 单链路：48.5 GB/s
- node27 本地：H2D **46 GB/s**、D2H **47 GB/s**、本地 NVMe **2.5 GB/s**、NFS **0.27 GB/s**
- **per-tensor 广播 vs bucketing**：256 次广播 = 25.43 ms / 1.32 GB/s / 99.3 µs per broadcast；
  同样字节打成一个 bucket 广播 = 0.91 ms / 36.73 GB/s → **27.8×**。
  99.3 µs 这个每广播固定开销就是 SGLang 逐张量远程加载慢的机制。

### Core dump 事件（本轮排障）

- `/home` 被 32 个 core dump 写满（**203 GB**，单文件 17.6–22 GB）。
- 全部对应三次已知崩溃（17:47、17:49、20:34），**32 个 core 与 32 个日志 PID 双向零差集**。
- 清理后 `/home` 从 1.3 G 恢复到 204 G。
- 清单归档于 `codex_coding/results/logs/core_inventory/core_list.txt`。

### 仓库规范化迁移（本轮主线）

- 核心库 21 个 `.py` → `main_lib/fluidckpt/`（2768 行，6 子包）
- 脚本 23 个 → `codex_coding/src/{bench,transport,diag,serving,tests}/`
- 结果 32 个 → `codex_coding/results/{bw,mesh,diag,model,plans,profile}/`
- 精选日志 8 份 → `codex_coding/results/logs/`
- 4 篇 findings → `docx/articles/`（按 `YYYY-MM-DD_topic.md` 改名）
- 全仓库旧路径 `/home/zhujianian/fluidckpt` 零残留；`py_compile` 全通过；`pytest` 5 passed

## Verified Facts

1. GLM-5.3 在 gfx936 上崩溃是 SGLang 自身 NSA path 缺陷，FluidCKPT 无法影响。
2. 跨节点 RCCL 需要三个条件：`NCCL_NET_PLUGIN=librccl-net-shca.so`、容器
   `--ulimit memlock=-1:-1`、**只用 collectives**（跨节点 send/recv 坏：
   `ncclSocketInit: connecting to address with family 0`）。
3. `/dev/shm` 每节点仅 252 GiB，而单节点 8 个 TP rank 合计 352 GB →
   **per-node tmpfs 持久化不可能**，因此选择 keeper 内 pinned DRAM。
4. `torch_memory_saver` 未安装 → SGLang 的 `--enable-weights-cpu-backup` 路径不可用，
   **我们的 keeper 是唯一的保活机制**。
5. GLM-5.3 = 78 层 + MTP 1 = 80 layer group，256 专家 top-8，hidden 6144，
   fp8 e4m3 block [128,128]，tp_size=16，141 safetensors，703.7 GiB，118,629 张量，
   其中 58,776 个（8–16 MiB）占 **97.9%** 字节。
6. HIP IPC 跨进程权重交接可行且零拷贝（producer 20.2 µs/handle，consumer 174.6 µs/handle，
   consumer 峰值 HBM 0.01 GiB）。
7. SGLang 用 `mp.set_start_method("spawn", force=True)` 启 TP worker，所以 launcher 的
   `__main__` 会在每个子进程重新 import → **import 期装的 hook 会在全部 16 个 worker 里生效**。

## Current Hypotheses

- **`keep2` 神秘死亡**：假设为 **ENOSPC**。它跑在 19:25–19:35，而 17:47–17:49 两批 16 个 core
  已把 `/home` 写到 100%；`keep2_resume.log` 是 **0 字节**（创建成功、写入失败），
  符合「worker 起不来被回收」形态（`resource_tracker: 8 leaked semaphore objects`）。
  *未验证*：没有直接证据表明当时写入确实返回 ENOSPC。
- **`base2` 崩溃时间 1852 s** 为何比 `vanilla1` 长：不确定是采样位置差异还是内存压力差异。

## Do Not Repeat

1. **不要再怀疑 GLM-5.3 的崩溃是 FluidCKPT 引入的。** 已用纯 SGLang 复现证伪。
2. **不要试图用 `--kv-cache-dtype` 或其他 flag 绕过 NSA crash。** 该 flag 只改
   `use_fp8_index_k_cache`，而 NSA 的两条 HCU 分支都调用同一个 `_get_q_k_bf16`。
3. **不要再试 `lightop_attention.fuse_layernorm_rotary_embedding` 的替代签名或 env 开关。**
   它是 pybind11 builtin，无 Python 可见签名，无 env toggle。
4. **不要建议走 `--enable-weights-cpu-backup`。** `torch_memory_saver` 未安装。
5. **不要建议用 `/dev/shm` 做 per-node 权重持久化。** 252 GiB < 352 GB。
6. **不要假设跨节点 send/recv 可用。** 只支持 collectives。
7. **跑 GPU 实验前不加 `ulimit -c 0` 是自找麻烦。** 一次崩溃 = 180 GB。

## Recommended Next Step

| 方向 | 说明 | 阻塞点 |
| --- | --- | --- |
| A. **转向可启动模型验证保活链路** | 用 Qwen3.6（已验证能 fired up）跑通完整 7 步保活，拿到端到端数字 | 无（推荐） |
| B. 等 SGLang/Hygon 修 NSA path | 需要 vendor 侧修复 `gfx936` 的 fp8 index cache 支持 | 不可控 |
| C. 继续规范化收尾 | `docx/cites` 首篇、`BACKUP_SCOPE.md` 改写、git 提交 | 无 |

**建议 A**：保活链路的价值不依赖具体模型。用 Qwen3.6 打通 capture → save → spread → drop →
restore 全链路并计时，同时并行准备 SGLang 保活对比，等 GLM-5.3 可用时直接换模型即可。
