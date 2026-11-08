# 恢复成本账本与可辨识校准计划

日期：2026-10-11；开发编号：v0.1.23.3 / task2；总计划18.33，实验记录48。

**2026-10-12最终状态：C0及六校准＋两独立留出已完成（18.34/记录49）。** GPU0准入失败后在预登记GPU4域执行；原生账本/全SHA/回收通过，三项worker经验候选获局部资格，完整模型复杂度升级未过。见[八请求报告](../experiments/2026-10-12_cost_ledger_calibration_report.md)。未追加第三会话或C3/C4。下文保留设计与实施过程，标记“尚未执行”的句子仅描述当时状态。

状态：设计与本地源码审查阶段。本文中的新硬件矩阵尚未执行，系数尚未识别。当前用户要求先把成本项弄清楚，再开始规划；这优先于上一轮“直接回到两布局比较”的后续建议。历史首操作A已完成，B因状态门未通过而跳过，本计划不重启B。

2026-10-12实施更新：用户已授权按本计划推进，进入C0覆盖层/冻结分析/独审建设，随后C1及有条件C2；总计划18.34、记录49。主agent批准单node27 GPU0、native world_size=1/rank0的最小配置，不启动旧六worker/三SSD服务；仍调用真实init_mesh、保留64MiB staging、96MiB source及所有原生恢复步骤。新配置独立校准控制项，不复用旧六worker数值。最先五点raw OLS候选要求系数非负，负值保留并拒绝该候选，不clamp；这是实测前明确的模型准入假设，不称硬件规律。当前段记录实施起点，硬件结果以记录49后续状态为准。

2026-10-12资源适配预登记：GPU0首次启动于mesh后被2GiB保守上界准入拒绝，bootstrap=0、校准=0，已完整退出；整卡同HIP已用54.01→54.22GiB，PyTorch reserved64MiB不能代替完整运行时占用。只读Docker配置另见两组当前服务可见GPU0/1及2/3，不能据此分摊全部占用。主批准唯一备用候选为此前地图已核node27 GPU4/BDF0000:85:00.0/NUMA4，仍native world1/rank0和原矩阵/门槛。使用全新execution_gpu4、analysis_gpu4和domain ID，保留GPU0冻结源码/失败原件；不减掉未归属背景占用、不提高2GiB上限、不终止其他作业。备用卡须通过同样fresh门才运行校准；若仍拒绝即止，不扩其他卡扫描。该调整在任何校准数据产生之前登记，GPU0失败不计作C1模型门失败。

## 1. 要解决什么问题

FluidCKPT的目标仍是：在分布不均的HBM/DRAM/SSD保活预算和共享通信资源约束下，联合组织暂停模型的权重驻留与恢复，改善保活空间—权重可用时延的权衡。规划器需要知道布局实际花多少时间，而不是只知道节点间有多少标称带宽。

本轮先回答三个具体问题：

1. 一次恢复中，查询、准备、搬运、等待、校验和接管各花了多少时间，是否存在重复计费或漏计？
2. 搬运成本随总字节数、操作数、分块数如何变化？哪些项可预测，哪些只是尚未解释的状态波动？
3. 单路径成本如何沿真实执行依赖组合，何时必须补共享资源争用项？

交付是计时合同、最小可辨识实验与停止规则。先复用现有worker/controller和实验监督设施；不扩展求解器，不移植Janus整套引擎，不新增全路径扫描。

## 2. 已有证据及其边界

| 已有证据 | 支持的判断 | 不支持的判断 |
| --- | --- | --- |
| host-only同handler两copy：4MiB .346575→.089062ms；16MiB .289744→.123432ms | 首操作主机开销能够掩盖字节变化 | 第二次copy时间就是第一次恢复时间；两点可拟出物理带宽 |
| 首轮4→16MiB差−56.831µs，其中非sync主机差−58.521µs、sync差+1.690µs | 该对照中的负差主要在主机执行段 | 所有历史负斜率都已唯一定位到某个驱动/TLS函数 |
| A8持久线程R2首−次CPU仍30.440/187.743µs | 持久线程不足以保证消除首成本 | 可以直接按thread-first或统一request-first收费 |
| A8固定8MiB、每请求两copy | 能观察状态与操作位置差额 | 能独立识别字节、操作、请求三类系数 |
| 旧bounded独立mixed24MiB execute误差−5.77% | 原有限路径、执行器和状态域有初步迁移证据 | 全拓扑共享竞争、当前新矩阵或live权重可用时延已获认证 |

详细证据：[首操作解释](../articles/2026-10-11_local_hbm_first_operation_cost.md)、[A阶段结果](../../codex_coding/results/v0.1.23.3/task2/first_op_validation/analysis_result_A_r01/report.md)。旧bounded模型和原始长点保持；新设计不回调旧门槛，也不把设计数字当作实测结果。

## 3. 三层模型，分别负责不同问题

### 3.1 资源层：哪些地方有容量限制

布局与路径决定资源用量 (D_r)。只有当 (C_r^{upper}) 是同一资源、同一流量口径的可信容量上界时，才有物理完成时间下界：

\[
T\geq \max_r D_r/C_r^{upper}.
\]

当前profile中的端到端有效率通常不是这样的容量上界；用它形成的是条件化评分或代理目标，不能无条件称严格下界。两端的同一逻辑payload不能相加当网络字节；HBM复制的读写流量应按资源定义分别计数。若容量按“逻辑复制字节/秒”测得，则不能再把需求乘二。协议流量、重复搬运、SSD代理腿另列，未知时明确未知。

这层用来约束可行性、发现潜在瓶颈，不单独预测完整恢复时间。

### 3.2 执行层：这些工作按什么顺序完成

用实际执行DAG表示prepare、分块搬运、同步、发布和接管依赖。每条路径采用同执行器、同状态域测得的服务成本。现有serial执行器每个chunk同步，单worker按plan顺序执行参与的op；不相交worker可独立前进。

`service_cost.estimate_execution()`的ready[src]/ready[dst]递推描述worker先后依赖；它不自动描述两个不同worker共享HCA、PCIe或DRAM控制器的竞争。只有实际同时运行且共享资源的路径才需要增加争用约束或条件化速率。

资源下界、DAG完成时间和观测到的等待是不同口径。不能把资源下界再加到DAG预测上，也不能把已经含在端点服务窗里的等待再收费一次。

### 3.3 请求层：从开始恢复到权重真正可用

主指标仍为加载开始到全部目标rank权重可用。保留三个互不替代的里程碑：

- **filled**：目标字节搬完；尚不能保证副本已校验发布。
- **published**：本次协议要求的校验和发布完成。
- **weights_ready**：真实模型完成layout/接管、必要同步和rank协调，权重可用。

合成replica实验只能给前两者。真实权重可用指标需要live路径自己的开始/结束事件或同一协调者的完整外层计时。跨机器不直接相减未校准的monotonic时间戳；max各rank局部duration也不自动等于全局makespan。

保活布局建立、首次runtime初始化、后台owner成本、在线恢复成本分别列账。若在线恢复必须准备某对象，该准备计入主窗；已有保活对象的建设成本单列，不通过移出计时窗隐藏。空间账同时记录持久副本、临时staging、迁移重叠峰值。

## 4. 成本归属合同

| 成本项 | 当前代码边界/观察位置 | 最小建模方式 | 不能混淆的项 |
| --- | --- | --- | --- |
| 库存与在线决策 | `control.py:plan_restore`及`transfer`外层 | 每请求实测；区分已复用snapshot和实际查询 | `prepare_s`已包含transfer库存查询，不能再次加inventory |
| 准备 | worker.prepare：身份检查、lease、allocation、目标映射/等待skeleton | 请求/目标规模条件化；首阶段直接分项观察 | bootstrap、fixture干预不当作免费预热 |
| dispatch/响应 | controller.call_all与worker handler外层 | controller完整execute窗，worker子窗用于解释 | 不能把并行HTTP端点耗时相加；差值不能全命名为网络 |
| op前后开销 | begin/end log、查表、phase/record/bookkeeping | 记录真实边界，K变化用于检验op项 | begin log在op记录内，end log在其外但仍在worker内 |
| chunk主机段 | source slice/read、target slice、copy/broadcast提交 | 每chunk顺序wall/必要threadCPU | submit耗时不是GPU完成时间 |
| chunk完成段 | `torch.cuda.synchronize` | 同步返回前的剩余完成等待；保留总chunk窗 | device-wide sync可能等其他工作；不是纯DMA时间 |
| SSD来源 | `RemoteExtent.read`及SSD HTTP服务 | 先整体记作SSD复合服务腿 | HTTP、CPU材料化、page cache和盘IO不可冒称裸盘带宽 |
| 依赖与争用等待 | worker顺序、pair状态、资源同时占用 | DAG边与有证据的争用项 | 同一跨节点op两端elapsed重叠且可能含等待，不能相加 |
| 校验与发布 | verify_run/complete，含全SHA、必要commit | 完整published窗；协议固定 | 验证流量不是恢复payload；不能事后去掉不利SHA时间 |
| live接管 | lifecycle.resume、IPC/export/wait_done、sync/barrier | 独立live资格阶段补齐weights_ready | replica published不代替live ready；IPC映射未必搬动N字节 |

主agent已交叉检查：`worker._execute`的`execute_s`从set_device之后起算，在return表达式处结束；profiling退出、HTTP序列化和可能的finally drain并不都在其内。外层execute计时需要包住整个调用，内部原字段保留兼容并准确命名。

### 4.1 闭合关系与最小记录字段

在同一controller时钟和同一协议下检查：

\[
T_{published}=T_{inventory}+T_{prepare\_rpc}+T_{execute\_wall}+T_{verify\_publish}.
\]

其中`prepare_s = inventory_rpc_s + prepare_rpc_s`；若在线plan或controller锁等待发生在transfer起表之前，另由外层请求窗计入，不由上述transfer内恒等式覆盖。默认finish发生在publication取样之后、transfer函数返回之前，不包含在coordinator_wall中。prepare_rpc_s还包含tx/body构造与active登记，不是纯网络时间。filled是controller收齐响应的观察点，随后字节核对通过才可认定成功。

新覆盖层采用同一组相邻monotonic_ns边界闭合；旧float monotonic与ns字段来自相邻但独立的clock调用，不能要求两套采样整数精确相等。原字段只作明确容差的兼容检查，不通过扣除观察者开销制造闭合。

每条span保存：run/session/worker/epoch、clock_domain、tx/op/chunk ID、parent span、start/end ns、actor出生代际、源/目标身份与分配/写入历史、path/target mode、N/K/C、实际chunk向量、executor/chunk配置、有效NUMA/设备/通信状态。紧凑数字先收集，大字典组装和落盘留在外层账；其开销仍记录。

同步主机段用互不重叠的相邻wall边界检查加总。CPU时间是辅助归因，不和wall相加；GPU事件如以后需要，只进独立诊断模式，不能混入主校准。GPU可能在CPU提交返回前已开始执行，所以“submit wall + 完整device event时间”不能直接相加；当前优先使用submit到完成的总服务窗。

`residual = outer - sum(children)`只说明剩余时间是多少，不能证明它是什么原因。闭合验收还须检查子窗不重叠、嵌套/时钟正确、覆盖缺口，以及独立边界。跨进程只比较可对齐的局部duration和因果消息，不伪造全局纳秒时间线。

## 5. 最小本地辨识实验

先固定node27 GPU0的local HBM replica路径、native HTTP实际执行方式、serial和32MiB chunk。每个请求使用新目标、相同source对象的有效区间与相同allocation策略，不再用同目标连续覆盖的第二copy替代真实多op恢复。op覆盖不重叠目标区间，完整源身份与SHA保持。

候选聚合模型是：

\[
T_{worker}=a+bN+cK+dC+\varepsilon(z),\quad C=\sum_i\lceil n_i/32\text{MiB}\rceil.
\]

N为逻辑MiB，K为op数，C为实际chunk/sync次数。a只是固定协议下的请求截距，不是已证明的首调用机制；c/d是条件化有效费用。b是完成服务的尺寸系数，不直接命名物理带宽。未解释状态z保留在残差与元数据中，不能每个请求新增自由参数后声称模型准确。

| 配置 | op大小（MiB） | N | K | C | 用途 |
| --- | --- | ---: | ---: | ---: | --- |
| L1 | 32 | 32 | 1 | 1 | 尺寸基点 |
| L2 | 64 | 64 | 1 | 2 | 同K增加N和C |
| L3 | 32+32 | 64 | 2 | 2 | 对L2固定N/C，改变K |
| L4 | 48+16 | 64 | 2 | 3 | 对L3固定N/K，改变chunk数及其大小分布 |
| L5 | 96 | 96 | 1 | 3 | 额外尺寸一致性约束 |
| L3′ | 32+32 | 64 | 2 | 2 | 唯一必要重复，检查顺序/状态漂移 |
| V1 | 16+16+16 | 48 | 3 | 3 | 留出：新N、新K、等块 |
| V2 | 40+24+16 | 80 | 3 | 4 | 留出：新N、新K、不等块 |

五个训练配置的[1,N,K,C]可达秩4；常见“每op一个chunk”数据中K=C，不能区分c和d。L1/L2/L5自身N与C共变，不能独立拟带宽。L4增加C同时改变chunk大小分布，因而d只能作为“固定chunk策略下的聚合增量”假说，不能唯一解释为一次同步API固定费。分段记录和V2用来检验该聚合假说是否站得住。

固定校准顺序为L3→L1→L4→L2→L5→L3′；最先五请求拟合，L3′只检验漂移，不用平均锚点掩盖变化。通过后新session按V2→V1运行留出。最多六个校准请求（含一个重复锚点）加两个留出；每配置通常一次，不默认五轮。V1/V2的K=3超出训练K范围，属于明确的小范围外推验证，通过之前不扩模型域。训练和留出分别做完整96MiB source bootstrap/清理，计入生命周期账，不进拟合样本。八请求计划payload共512MiB、15op、20chunk/sync，不含bootstrap及校验流量。

统一主拟合边界是worker的完整execute方法外窗，覆盖内部set_device、return/finally和实际启用的profile退出；handler解析、锁等待、响应编码在更外层单列。原native execute_s与sum(record.elapsed_s)保留作内部诊断。用同一矩阵可另列service候选，但两套系数不能互换或相加。实际计数及copy/sync完成证据由最小覆盖层提供；预期C不能冒称actual_C。

拟合优先比较更简单的嵌套结构与上述四项结构。保留原始无约束系数与残差；负系数或近零尺寸信号不能被静默截断成“可解释正带宽”。若增加C后仍不能迁移，保留整体路径经验成本或unknown，不继续堆状态参数。

## 6. 分阶段执行与关口

| 阶段 | 做什么 | 达到什么效果 | 停止条件 |
| --- | --- | --- | --- |
| C0 账本 | 子agent建设最小计时覆盖层，CPU检查实际源码边界、非重叠span、配置与字节/块计数 | 明确哪些钱花在哪里，外层完整窗不漏项 | 路径/边界不清先修计时，不跑拟合 |
| C1 本地 | 六请求校准，保留首/后op及chunk位置，不新增状态干预矩阵 | 区分N/K/C是否具有可迁移解释力 | 锚点漂移淹没预期差量、信号弱或明显结构残差则止于校准 |
| C2 留出 | C1冻结模型、适用域与两份预测，新session运行V1/V2各一次 | 验证未见规模和组合，不用留出实测控制成本回填预测 | 任一关键域不通过，拒绝该域模型，不补第三session救模型 |
| C3 必要路径 | 按下一布局需要补DRAM/跨节点/SSD单路和一个必要混合组合 | 把相同账本延伸到实际候选路径 | 不为地图完整度测全组合；配置不同不混用系数 |
| C4 共享资源 | 仅当候选执行会并发共享资源，测两路单独与同时、含启动重叠证据 | 判断资源竞争是否需要进入DAG/速率约束 | 实际串行或无可辨竞争时不引入争用参数 |
| C5 回到规划 | 同内容、目标、预算、正确性要求下比较合法布局，再接live主指标 | 证明可信成本能改善布局决策与权重可用时延 | 模型差量小于已见不确定性则保留同分/直接测候选，不冒称细排序 |

本轮交付到设计和离线矩阵验证。C0的执行覆盖层及新GPU矩阵是后续实施，不把本文件当作已完成C1–C4。C3/C4由本地结果和具体候选需要触发，不预先承诺所有路径/竞争组合都做。

验收分别给出：功能/数据正确性、计时账本、结构可辨识性、独立预测、决策能力。五项不能用一个PASS替代。每次独立预测同时报service、worker、controller execute、完整filled的绝对与相对误差；published只有拥有独立校准才给预测，live尚未测则null。不能靠大公共控制段掩盖服务层误差。

### 6.1 固定数值门与预测冻结规则

以下是本设计采用的探索门槛，不是硬件常数、统计置信界或论文最终精度要求。C0实现时冻结成机器可读协议；不得看见C1/C2结果后放宽。

1. **账本门**：N/K/实际chunk向量和copy/sync调用数完全匹配，全SHA及生命周期通过。同一组相邻ns分区加总整数精确闭合，另行记录的父窗检查真实嵌套与空隙；旧float转换检查允许1µs表示差，不能用这个容差吞掉set_device/日志等漏项。
2. **漂移门**：分别对service和完整worker定义当前观察尺度E=max(5µs、CPU空clock额外p95、同结构dummy log变化尺度、L3′与L3绝对差)。锚点漂移须≤max(20µs、两次锚点均值的10%)。E仅是本轮观察尺度。漂移门失败止于C1，保留所有点。
3. **结构门**：在同一边界上直接检查c差量=L3−L2、d差量=L4−L3、32b=L2−L1−d。要声称四项分别识别，c/d各须为正且>2E，32b须为正且>4E；第五点|L5−2L2+L1|≤4E，五训练点最大残差≤2E。未分辨的项标unknown，不当作物理零。若较简单的a+bN或a+bN+cK结构满足自身残差门，可只冻结该简单聚合进入留出；四项未全部识别不等于所有粗预测都被否定。
4. **外层规则**：从相同五个校准请求冻结controller execute减完整worker窗的中位残差，以及完整filled减controller execute的中位残差，各只收一次。它们仅是粗迁移假说，包含控制/prepare等费用，不解释为纯网络或常数分配成本。完整filled的开始统一在该请求必要inventory/计划之前。若配置、查询次数或准备协议变了，外层预测置null；不得拿V实测残差回填。
5. **独立门**：每个留出点的worker预测绝对误差≤250µs且相对误差≤20%；若另冻service候选，service≤50µs且≤20%。controller execute≤1ms且≤20%，完整filled≤2ms且≤20%，分别判定。外层不通过不抹掉有效的局部资格，但不得宣称完整恢复模型已过。published/live仍null。这里用固定绝对上限，不随较大漂移自动放宽。
6. **复杂度与规划门**：更细模型只有在独立worker/外层表现有超过观察尺度的改善才保留；等效时选简单结构。后续真正比较布局前另登记拟辨的时延差Δ和误差预算δ，要求δ明显小于|Δ|，并测同任务合法布局的真实排序。上述1/2ms门不授权亚ms排序；本轮少量误差也不构成未来误差界。

冻结包须包含源码/仪表版本、时钟合同、原始训练引用、候选与最终选择规则、全部系数、状态/配置拒绝域、两份留出预测和判据。任一留出失败保留失败及阶段资格，最多八请求结束，不额外增加session或参数救模型。

## 7. 资源与协作

主agent负责计时合同、矩阵取舍、现场资源准入和最终证据验收。子agent分别承担计时/执行覆盖层、冻结拟合及CPU检查、独立计费与预测审查；写入目录互不重叠。沿用gpt-6.1-sol/high、fork_turns=none。

本地C1/C2只需一张指定GPU；source最大96MiB、target最大96MiB，分配策略全配置一致。既有staging、runtime、prepare与安全余量一起做任务聚合准入，不能只拿192MiB判断设备够用。继续每node6GiB DRAM/2GiB HBM任务上限与fresh准入；现场不足则记录并停止该启动，不挤占旧owner。后续跨节点只用27/28/30必要角色，node29不纳入。

保留原厂/共享XML与mapping、不修改全局环境。每次运行冻结新run ID、源码、模型与协议，失败和重试分别留存，`ulimit -c 0`；只清理本任务精确持有的对象/进程。当前轮无GPU/SSH、产品模型修改或全历史归档，history-chat.txt保持。

## 8. 本轮证据入口

- [成本归属审查](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/ownership_r01/report.md)。
- [辨识矩阵与离线验证](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/identification_r01/report.md)。
- [理论与重复计费审查](../../codex_coding/results/v0.1.23.3/task2/cost_contract_design/theory_r01/report.md)。
- 总计划18.33、实验记录48及当前过程文档的同名增量，记录最终取舍和实际检查命令。

分报告保留审查时的备选建议；实际统一协议以本文第5节及6.1为准。采用完整worker方法窗作主拟合、固定绝对误差上限，未分辨的细项可退回通过资格的简单聚合；不叠加theory报告中的另一套五请求模板。主agent独立重跑rank检查到identification_review_r01，8/8通过；主文档检查PASS，git diff --check通过。

本阶段完成后，应能回答“某条恢复路径为什么花这些时间、哪些项能随布局变化预测、哪些条件下须拒绝预测”。然后才把成本交给已有规划器。
