# 本地成本可识别性设计：固定产品32MiB分块，最多8请求

日期：2026-10-11；编号：v0.1.23.3/task2。本轮为设计与CPU代数验证，新GPU、SSH、产品修改均为0。

## 1. 当前结论与待回答问题

首操作A已经结束，状态门为`unidentified`，B为`skipped=predeclared_A_state_gate_failed`。本设计独立于该已关闭协议，不补B、不复跑A，也不把其C1/C2诊断系数升级为已识别首项。

成本在当前产品中的待识别变量是：总逻辑传输字节N、实际操作数K、实际分块与完成同步次数C，以及明确观察到的请求/线程/目标状态。原小尺寸每操作一个chunk时K=C，不能同时辨别每操作项和每chunk项；固定8MiB、每请求两copy还不能辨别字节斜率，C0/C1请求总成本也相同。

本次最小设计只问：**在冻结的native HTTP分派、新执行handler、未由本任务写入的HBM replica目标、serial32MiB执行配置下，一个按N/K/C计费的局部经验合同能否迁移到新N与新K？** 首状态机制仍未知；满秩只解决变量之间的代数混淆，不解决状态、缓存、尾块尺寸和顺序交互。

## 2. 产品计时和成本合同

源码入口为`main_lib/fluidckpt/cluster/worker.py`的`Handler.do_POST → execute → _execute`。原handler在`worker.lock`下直接执行；不使用首操作实验中的F/P队列、重复copy或FILLED复位。`execution_chunk()`仅支持serial32/64MiB，本设计固定32MiB，不增加1/4MiB chunk能力。`_execute`每chunk执行source slice、target slice/land、copy和device synchronize；每op还包括begin日志、记录与end日志等。localHBM同worker无需broadcast。

按同一worker时钟分别记：

- `service = sum(native record.elapsed_s)`：每op begin日志、chunk loop与同步完成，保留原产品record边界。
- native `execute_s`：含record外op-end日志、bookkeeping与状态发布；native `_execute`开始前的set_device另列，不能归入该返回值。
- **主拟合窗 `T_worker` = 外层包住native `execute(body)`方法的wall**：包含该方法内set_device、所有op、bookkeeping和return；只增加入口/出口host clock。原返回`execute_s`并非此完整窗。Handler之前的set_device、锁等待和reply在RPC/控制残差中。
- worker `/execute`外层完整窗：含Handler的set_device/锁等待、execute内set_device、JSON和reply；以外层单调时钟记录，不能把`execute_s`当整个RPC。
- controller execute、inventory/prepare、`all_targets_filled`、验证发布窗：复用`Cluster.transfer`同一controller单调时钟。SHA、complete/finish在填满后；published仍不是live权重可用时延。

主候选对上述`T_worker`使用`T = a + b·N + p·K + g·C + residual`，per-op service并行保留为诊断。N以字节保存、MiB仅用于表格及数值缩放。a是外层execute方法边界内条件化的请求共同残差，**不是已证明的request-first/thread-first成本**；b是有效字节服务斜率；p是native每操作边界成本；g是source/target切片、提交、同步及计数观察的共同chunk计数项。g不能命名为纯sync、DMA或硬件带宽成本。

显式假设：上述项在冻结状态合同内近似可加；每chunk项不随尺寸/尾块独立变化；每op项不依赖顺序或其chunk数；未观察到的首执行状态不会系统地随N/K/C改变。A没有证明这些假设，本矩阵与留出负责尝试否定它们。异步copy后的sync含设备完成、驱动/host与设备其他工作等待。

同时报告少参数对照`a+bN`及`a+bN+pK`。全部使用同一训练原件与计时边界。保留无约束原系数；负项、零附近不可辨项不靠截断制造物理解释。拟合新模型时以该完整worker合同替换旧service与worker残差组合，不能叠旧`.280796ms`、`.050530ms`或A的首项；旧bounded模型和remote/SSD系数保持原资格。

## 3. 最小矩阵与精确秩

每请求一个目标、操作按连续logical offset分割同一冻结96MiB source的前N字节，不重叠、无重复覆盖。K是执行实际参与op数，`C=sum(ceil(op.n/32MiB))`，不能用`ceil(N/32MiB)`替代。

| ID | N(MiB) | 按执行顺序的op尺寸(MiB) | K | 实际chunk向量(MiB) | C | 用途 |
| --- | ---: | --- | ---: | --- | ---: | --- |
| T1 | 32 | 32 | 1 | [32] | 1 | 字节/固定对照 |
| T2 | 64 | 64 | 1 | [32,32] | 2 | 与T1比总量 |
| T3 | 64 | 32+32 | 2 | [32];[32] | 2 | 与T2比K、N/C相同 |
| T4 | 64 | 48+16 | 2 | [32,16];[16] | 3 | 与T3比C、N/K相同 |
| T5 | 96 | 96 | 1 | [32,32,32] | 3 | 额外可加性检查 |
| H1 | 48 | 16+16+16 | 3 | [16];[16];[16] | 3 | 未见N、未见K |
| H2 | 80 | 40+24+16 | 3 | [32,8];[24];[16] | 4 | 未见N、未见K和尾块组合 |

上述秩、差额公式均针对同一个完整T_worker观测，不把record内/外拼接成不同响应。T3/T4还改变chunk尺寸分布。因此该对照识别的是**count-affine假说中的g**；若尺寸分布影响残差，不能从差额唯一归因为同步次数。

训练执行顺序预登记为`T3 → T1 → T4 → T2 → T5 → T3(anchor repeat)`，五配置各一次，T3必要重复一次仅检验过程漂移；不删第一T3、不用均值掩盖差额。拟合使用最先五个配置，重复T3为训练后资格检查。最多六训练请求。若训练资格通过，冻结系数与全部预测；新worker session执行`H2 → H1`两请求，最多共八请求，不增加第三block。

设计矩阵列`[1,N_MiB,K,C]`在T1–T5上精确rank=4，T1–T4的4×4 minor determinant=32。对理想可加模型：`g=T4−T3`，`p=T3−T2`，`32b=T2−T1−g`，`a=T1−32b−p−g`；独立第五点预言`T5−2T2+T1=0`。实际拟合使用全部五点，并分别列这些直接差额与残差。

H1/H2的N均未见，K=3也未见；K超过训练最大2，属于**显式操作数外推**，不在训练凸包内。两留出不能建立全域误差界。精确CPU脚本8项通过；其合成系数仅验证代数，不是测得成本。

## 4. 状态协议与观察方式

沿当前本地w4/node27 GPU0路径，实际资源由主agent后续fresh准入统一协调；这里只确定合同。source为相同内容/manifest、同source owner与冻结lineage、连续uint8 HBM副本。绑定后source不可变，source建立与SHA属于bootstrap账目。每请求新tx、新target id与分配代际，原产品prepare→EXECUTING→FILLED→verify→complete→finish完整走通。

所有目标由native `torch.empty`分配，本任务在execute前不zero、不pretouch、不做测试copy。标签仅为“新allocation且本任务未写入”，不能声称物理页、allocator缓存或GPU cache完全冷。分配释放后地址可能复用，必须记录分配代际而非仅看ptr。保留source直到该stage结束；目标逐请求验证与释放，预算包括source96MiB、最大target96MiB、已有64MiB staging以及进程reserve/allocator实占。下一请求不得与上一请求验证、GPU清理或传输重叠。

使用真实ThreadingHTTPServer handler直接调用native execute；每请求实际线程birth、native TID、PID/epoch、handle创建/退出必须可追溯。按真实Thread对象赋birth，不把OS TID复用当同线程。prepare handler可完成set_device，但不能在execute handler中预做view/copy/log暖化。控制模式、CPU/NUMA affinity、torch线程数、logger、profile环境、source初始化历史、每请求前GPU工作/同步历史都冻结。native profile_execute关闭、无CUDA/HIP event、无profiler。

薄观察层必须保持native `_execute`和真实dispatch，不重写copy原语。执行前计算expected chunk向量；最小actual证据为每chunk实际land/copy次数与**sync返回后**完成计数，返回后再物化chunk元数据。可用限定当前执行线程作用域的计数hook，必须证明每次原调用仅执行一次、无额外sync和遗漏；CPU fake backend回放native实现验证。若实现无法提供实际计数，就只能写expected_C，不能冒称actual_C或校准g。计数hook增加的每chunk开销属于冻结观察mode的g；不从实测扣除。主timing不插每chunk详细日志或phase event；worker计数hook开销直接包含主合同，不能再加计数成本。

需要细分时，只用相邻host monotonic_ns/thread_time_ns，详细ptr/stream/字典输出在主窗之后并单列；单次CPU空clock与dummy logger尺度检查也另列。主要拟合使用外层execute方法总wall；native aggregate service为平行诊断。若诊断计时或计数改变产品循环，必须记录源码差异、调用等价证明及mode归属。

训练与holdout均走同样bootstrap步骤；holdout新PID/session/source/target/birth，保持产品与logger版本。重建进程不会令隐藏状态自动等价；保存init/前序请求/verify/finish历史，出现系统性session或order残差则拒绝迁移。此处没有重复F/P或新target-first状态研究。

## 5. 预登记数值门与停止条件建议

以下为后续预登记建议值，执行前冻结，结果后不改门槛。需要区分正确性、成本可辨与预测资格。

**执行账目门：** N/K与actual chunk向量完全匹配；每chunk的copy/land与sync返回计数匹配；所有logical byte逐op全SHA通过；无缺失、重复、跨代source；raw ns分区整数闭合误差应为0，转换native浮点秒时允许1us表示误差；所有phase窗单调且嵌套正确。资源、原资产保持和退出门按既有预算/epoch/Catalog规约执行，不能用这1us容忍窗口重叠或遗漏。任一失败停止，独立新run保留失败与修订。

**训练可辨门：** 对service与外层worker各定义`E=max(5us, 当前CPU空clock额外p95, dummylog变化尺度, abs(T3_repeat−T3_first))`。E只是当前观察尺度，不是置信区间或未来误差保证。两者anchor漂移都不得超过`max(20us, 10%·对应两次anchor均值)`；任一不通过止于训练。主合同差额及以下残差使用E_worker；service同差额单列，用于发现状态/日志/完成交互。直接p/g差額应非负且各自大于2E，32MiB字节增量应为正且大于4E，才给三项分别标`observably_resolved`。未过的项保持unidentified；不能称物理零。五点可加性残差`abs(T5−2T2+T1)`≤4E、训练最大绝对残差≤2E；不通过则完整四项合同不进入holdout。

**外层冻结规则：** 同五训练点冻结`r_controller=median(controller_execute−T_worker)`及`r_filled=median(fullfilled−controller_execute)`，均为完整实测窗之差，保留inventory/prepare与RPC相位原账。预测controller为worker主合同+r_controller，filled再加r_filled；这些仅是粗公共残差假说，不赋予物理机制含义，不额外回归q+eK，也不叠旧控制系数。published/live预测null。

**独立预测门：** 冻结所有H1/H2的worker/controller execute和filled预测后再启动holdout，service保留诊断；用holdout实测控制项回填预测禁止。每一holdout的worker误差同时≤20%及≤`max(250us,2E_worker)`；controller execute误差同时≤20%及≤`max(1ms,2E_controller)`；filled误差同时≤20%及≤`max(2ms,2E_filled)`。各外层E来自相应anchor差额和计时观察尺度，独立报告。

这些门分层判定：worker通过而controller/filled未过只能认证局部条件经验，不能认证完整恢复成本。filled/published均不认证live T_ready。两个holdout都保留worker绝对/相对误差、service/phase诊断残差和少参数对照；四项模型如果不能比简单对照更准确，选简单可用合同，不为参数名称增加复杂度。小模型改进需超过相应观察尺度，且worker与filled有同方向改善，才讨论规划价值。

任一非有限/负拟合项（含a/p/g）、负字节斜率、未解释首op差额随K/C重分配、尾块/操作顺序交互、anchor漂移、会话迁移失败或长点淹没差量，止于本轮上限并报告`local_cost_unidentified`。不删除长点，不截负系数、不新增首状态参数/尺寸扫描救模型。误差阈值没有通过也不追加第三block。旧A/B关闭状态保持。

## 6. 路径扩展的入口

先完成localHBM合同判断。remoteDRAM、SSD、peer/网络只能在后续两合法layout确实需要且改变决策时各取必要路径。local b/p/g不能跨路径套用；remote target记录可能含发送端等待，双方窗口不能相加，旧DAG递推再加wait可能双计。

若扩展，先沿既有pair epoch/warm标签验证单路径相同N/K/C合同，保留path-specific有效系数、实际read/staging/broadcast/land次数和同步完成证据。SSD路径是owner read+staging+传输的服务，不是裸盘带宽。改变64MiB chunk必须视为另一执行配置，独立检验/冻结，不能与32MiB静默混拟；double_buffer、live scatter、共享链路竞争和真实model handoff均需对应缺口与独立资格。

## 7. 交付、数量和未定项

八请求若全部执行，测量payload512MiB、15操作、20chunk/sync；训练384MiB/9操作/13chunk，holdout128MiB/6操作/7chunk。sourcebootstrap96MiB每新session另列；两个session各自建立不能合并隐藏。SHA按逐op验证source/target分别记逻辑检查量，不把重复读校验或preparation当硬件总流量。本轮这些均为设计计数，实际GPU样本数0。

CPU命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/cost_contract_design/check_identification_rank.py --output codex_coding/results/v0.1.23.3/task2/cost_contract_design/identification_r01/rank_checks.json
```

结果为`cpu_design_rank_checks_passed`，8/8检查通过，输出见`rank_checks.json`。首次使用`python`命令exit127（本机无该别名），切换`python3`后exit0；无GPU失败或重试。

未定项：上述数值门需主agent在后续正式预登记时采纳或明确调整；actual count hook与native窗口等价性尚待CPU实现审查；隐藏状态的稳定性、chunk尺寸分布的可加性及跨新session迁移尚无实测。当前报告的资格仅为设计代数通过，`hardware_validated=false / product_admission=false / decision_ready=false`。

依据：AGENTS.md；docx/next_step.txt最新A结论；docx/debug_philosophy.md；docx/plans/2026-10-11_first_operation_state_validation_plan.md；first_op_validation/analysis_result_A_r01/report.md；main_lib/fluidckpt/cluster/{worker,control,planning,service_cost}.py；main_lib/fluidckpt/executor/chunks.py；docx/01_paper_scope_management_guide.md及2026-10-06 baseline路线图。当前读取源码与本设计绑定，后续实施前需冻结精确SHA。
