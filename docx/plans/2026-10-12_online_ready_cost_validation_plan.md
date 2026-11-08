# 真实在线权重可用成本：整体10%验证计划

日期：2026-10-12；开发编号v0.1.23.3 / task2；总计划18.35、实验记录50。状态：资源阻塞，完整C0及留出尚未执行。最新实际结果见[执行报告](../experiments/2026-10-12_online_ready_cost_validation_report.md)。

**18:52最终进展：**小IPC一次/两次对照和node27完整真实模型物理暂停均已验证，实际每rank释放约32.77GiB、整卡free增加约32.80GiB，完整70.33GB保留在DRAM。该前置正确性问题已经解决，不再作为下轮探索任务重跑。node30最新HIP各卡used49,048,190,976B，远端矩阵仍不能准入；保持helper原预算，待资源可用后新freeze直接继续原C0/Hnear/Hremote（仍保留每次物理pause正确性门）。所有本任务容器已停止。局部controller通过与engine退出guard竞态造成hostrc1分别记录；未来最终closure账本CPU16过、未改旧原件。正式成本预测样本仍0。

**18:35后续执行修订（在新完整模型数据前）：**用户再次授权继续。资源刷新后node27可用，32MiB once/twice独立生产者对照已完成：一次导入释放全部32MiB，两次导入保留全部32MiB，支持单映射修复。实际容器Torch2.10.0；旧探针数值读取未处理allocator头部，不作为IPC槽证据，未来探针默认未知。新138资产完整r03容器已创建，但fresh HIP发现node30 GPU4/5各used32,700,891,136B，完整两节点准入失败；原结果保留。当前先执行独立`online_qwen_local_pause_r01`：仅node27两keeper+TP2引擎、相同192/224GiB DRAM和48GiB/card预算，复用原native capture+SHA+参考输出+物理park双90%门，在helper之前停止。该前置验证不产生路径校准/C0/留出或10%资格；node30不启动worker，不改helper预算。通过后才按资源情况恢复原两节点完整矩阵。

**最新执行修订：**r01因CPU线程池触及pids512失败，限制本任务线程后r02完成真实模型捕获/参考输出/helper副本，但最终资源门失败；暂停事件显示权重显存没有释放。发现新实验wrapper重复消费同一IPC导出凭证，已改为原有唯一映射内可选SHA，并新增实际释放量检查与阶段/失败资源事实落盘，CPU验证通过。32MiB诊断在创建tensor前因整卡used约32.44GiB拒绝；另一容器已启动GLM TP8/PP3，当前资源不足。原失败卡号/当时upper未记录，不能用更晚出现的作业回填原因。两个自有新容器已停止，当前没有后台实验。下次先小IPC诊断与物理暂停，再继续原C0/Hnear/Hremote；不变更10%目标或盲目放宽预算。

**校准前资源修订：当前只实施node27/node30两节点四GPU域。** node28 GPU4及旧图已核GPU0/1均被高占用，不能满足原helper上界；不改变预算来强行准入。逻辑worker0/1为node27 GPU4/5，worker2/3改为node30 GPU4/5、CPU NUMA4/7。node30同时保存两个rank：任务DRAM96GiB/hard128GiB、初始host余量128GiB/运行保留32GiB，HBM仍为所选两helper聚合2GiB含runtime与staging。两节点只读元数据与GPU5/BDF b5/NUMA7已补齐；node30两卡sysfs均仅使用2,207,744B，仍须新容器内真实HIP容量准入。下文三节点布局是原方案及资源发现，**本轮通过也仅认证两节点四GPU，不认证三节点或SSD**。校准、C0/Hnear/Hremote语义与10%主门不变，所有变化在任何性能数据之前登记。当前唯一执行命名空间为`online_qwen_two_node_r01`。

## 1. 本轮目标与新准入标准

用户明确：成本模型服务于放置/拉起方案选择，应预测实际在线恢复代价；代表性场景的整体误差约10%即可进入规划器优化，不需要逐项追求微秒级精度。

本轮主准入为每个独立留出场景 `abs(predicted_ready-actual_ready)/actual_ready <= 10%`，使用整数时间比较，不能用跨场景平均误差掩盖某个场景失准。不沿用报告49的50µs service门、20%门或额外复杂度收益门；这些旧规则和旧结果作为历史保持，不回写改判。

FluidCKPT的目标仍是暂停大模型在不均资源预算与共享通信约束下的权重驻留/恢复组织，改善空间—权重可用时延。达标后应把建设重点转向合法布局与实际恢复收益；不以识别所有微观参数作为前提。

## 2. 在线时间窗与正确性

使用既有SGLang eager生命周期：权重卸载到meta后，恢复会重新materialize、导出存储句柄、等待keeper填充、执行device synchronize与TP CPU barrier、发出weights_ready并返回恢复RPC。

主时间窗采用同一个controller monotonic时钟：**恢复请求开始（含本次必要inventory/计划工作）→完整resume响应，且已观察所有目标rank weights_ready**。同主机各rank的load_begin→weights_ready作为分段证据；跨主机monotonic不直接相减。整个实验session初始化、加载源模型、构建保活布局与实验额外验证分别列账。

在线必需检查仍包括模型/内容版本、目标布局、源epoch/generation/lease、完整字节覆盖、复制完成同步、正确接管和rank协调。完整SHA作为实验正确性验收，放在ready后；不从已有published数字中减去SHA来冒充新的在线实测。

仅全live目标可用原生 `Cluster.transfer(verify_before_ready=False, retain=True)`，保留源lease和目标绑定供ready后全SHA比对，再finish/release。新副本的transfer发布仍使用既定校验；capture_regions的完成D2H发布与后续实验全SHA要准确区分。禁止将未填满的live目标发布done，禁止用空目标/假ready校准handoff。历史source owner不借用后退役，所有源与句柄由新会话建立。

## 3. 复用与实际模型

复用产品lifecycle/sglang_hook/HbmTarget/Catalog/capture_regions/Cluster、已有worker DAG、原SGLang launcher及精确归属supervisor。新增代码位于`codex_coding/src/v0.1.23.3/task2/online_cost/`，结果在对应results根；不重造加载器、缓存引擎或求解器。

模型沿用已有实际Qwen TP2：路径`/data/node28/shared/zhujianian/Qwen3.6-35B-A3B`，配置架构Qwen3_5MoeForConditionalGeneration。metadata_local_r01已读取26个safetensors的stat，总文件71,903,776,776B；仅元数据，不读payload。历史processed权重每rank35,164,859,056B、合计70,329,718,112B，须以本轮实际capture再确认。

目标为node27 GPU4/BDF85/PCI NUMA4、GPU5/BDFb5/PCI NUMA7；keeper与engine保持相同完整GPU可见编号，engine使用base-gpu-id4，避免IPC句柄device编号错误。模型TP2目标同主机，远端来源覆盖node28与node30。helper物理GPU按fresh准入绑定；逻辑worker0/1为目标，2/3为两个helper。node29不参与。

## 4. 最小校准与留出

所有完整恢复固定同一模型、两个目标、相同目标extent顺序，每rank采用1GiB分块，共33段，末段805,120,688B；两rank合计66个op。executor为实际serial、每op内部32MiB chunk。源位置可变，目标工作量和排序不变。

三个全模型方案：

| 方案 | 每rank来源安排 | 作用 |
| --- | --- | --- |
| C0_mixed | 第0段1GiB本地HBM；随后连续16段来自各自远端helper；剩余本地DRAM | 唯一完整模型校准，记录在线layout/IPC/scatter/控制合并残差 |
| Hnear | 第0段本地HBM；其余本地DRAM | 未用全请求时间拟合的独立留出 |
| Hremote | 第0段本地HBM；其余各自远端DRAM，两条disjoint worker pair可同时运行 | 独立留出，覆盖三节点和共享接收端通信资源 |

Hremote的“远端”和“共享瓶颈”是同一个物理场景，不能另列成重复的第三种场景。操作按extent交错rank0/rank1；C0的远端段连续，避免用不成立的全程带宽上界漏掉local/remote交替间隙。

先对实际需要的具体路径各测512MiB和1GiB各一次，复用packed replica和原生执行器；这些大小包住完整恢复的1GiB/尾块操作，但不冒称已验证任意大小。只测两条本地HBM、两条本地DRAM及两条远端DRAM路径。另测一次两个远端pair各1GiB的真实并发组合，保留实际重叠和HCA绑定证据。

模型复用实际worker依赖DAG。共享连续双流阶段只使用该注册双路径的实测并发/孤立服务因子，调整相应远端op后重算DAG；不把它称物理容量或再与DAG相加。若无法确认真实共享资源/重叠，则该组合预测保持未知，不能伪造并发资格。

用C0一次完整合法模型恢复校准 `在线ready总时间−预测传输DAG` 的合并残差，包含真实目标分配、IPC映射、scatter及控制工作；同模型/目标/N/K/C下假设可迁移。参数、两份完整留出预测、源身份和适用域必须在任何留出数据产生前冻结。C0只校准一次残差，不能给每个留出布局各拟一遍全模型常数。

同一已初始化引擎与keeper生命周期下，完整校准后暂停，再执行两个新的留出请求，契合在线保活恢复状态。它证明的是注册incarnation内新方案预测，不宣称跨任意新进程迁移。每配置通常一次；最多一次额外复查需有预登记原因，不能看见不利误差后追加样本或回填控制段。

全SHA与固定贪心参考输出在ready后执行，各请求再回到parked；正确性/资源/回收失败与预测误差分开报告。若两个候选差异处于10%误差量级内，排序弃权或后续少量直接比较，不为此继续细化微项。

## 5. 明确资源预算与当前资源发现

本轮是真实70GB模型，旧2GiB HBM/6GiB DRAM属于小块实验协议，不能承载该模型。本轮预算在新数据前明确，并只用于新容器，不修改任何旧容器限制。

| 角色 | 任务DRAM上限 | 容器硬限制 | HBM任务范围 | 初始主机余量 |
| --- | --- | --- | --- | --- |
| node27 target+engine | 192GiB | 224GiB | 两张目标卡各48GiB、合计96GiB；含engine/KV/runtime、1GiB缓存、校准临时目标/staging | MemAvailable至少256GiB |
| node28 helper TP0 | 64GiB | 80GiB | 所选卡2GiB含runtime/staging，不加载整个模型GPU权重 | MemAvailable至少80GiB |
| node30 helper TP1 | 64GiB | 80GiB | 同上 | 同上 |

target较大DRAM预算覆盖约72GB checkpoint pagecache加70GB pinned floor及运行时/暂存，不能忽略文件缓存计费。每helper只存一个32.75GiB分片，Catalog DRAM48GiB，不能把两rank都塞入同一个helper。target keeper的HBM Catalog预算3GiB用于1GiB保活块与至多1GiB校准目标等；其物理占用仍必须满足每卡48GiB总门，IPC双视图同一物理storage只计一次。

运行期间主机余量按已持有与未来新增分开检查：target额外留64GiB、helper留16GiB再加下一步新增；不重复扣除已在当前占用中的全部预算。所有selected-GPU容量使用真实同HIP准入和实际占用口径，不用PyTorch reserved代替全部runtime、不通过减掉未归属背景占用救门。source创建/迁移峰值和验证scratch均计账，连续峰值未经证据不宣称已知。

已完成的CPU元数据结果：node27约480GiB可用，node30约396GiB，node28约87.7GiB。最初helper96GiB+32GiB余量门在node28失败，原记录保留；在任何GPU数据前将helper限定为一个rank并缩小为64/80GiB预算、16GiB余量，重新元数据检查通过。

node28 NUMA4仅约5.8GiB的`MemFree+FilePages−Shmem`估计，不能容纳整个rank；NUMA3约41.28GiB，为唯一足够的单NUMA候选。明确把helper28 DRAM放NUMA3，GPU自身PCI NUMA另记，不把二者混同。该估计包含可回收文件页，不是保证可分配；实际触页/注册/驻留检查及每步余量失败即停止。目标CPU NUMA4/7、helper30 CPU NUMA4的估计足够。

node28 GPU4的只读sysfs占用64,727,023,616B，已超过helper保守上界；不能启动该候选。正在检查旧地图已核的GPU0/BDF09与GPU1/BDF36作为备用，最终GPU/端口/mesh在数据前固定。node30 GPU4只读used约2MiB，仅支持候选选择，仍须runtime准入。node27不读取历史有风险的GPU owner表。

所有原厂/共享XML与默认mapping保持；项目input和每新进程dump分开。所有旧容器、owner和并行服务只读保护，禁止清理他人进程、drop_caches或修改全局环境。新进程core dump关闭、有限超时、STOP绑定plan SHA、精确PID/进程组回收。

## 6. 当前实现分工和阶段关口

- 主agent：问题定义、预算/最终物理绑定、计时和10%口径、资源启动与验收、正常增量记录。
- online_ready_path：容器内原生worker覆盖层、fresh capture/bootstrap、calibration/online控制器与对应CPU检查。
- online_host_ops：复用已有容器setup/stage/supervisor、fresh admission、会话编排与精确收集；不重写加载器。
- online_cost_model：已有DAG上的路径/共享阶段经验预测、预登记冻结与10%判定，实际数据未产生前预测null。
- online_cost_review：独立检查真实计时、source/layout/预算、GPU/NUMA/HCA、freeze无泄漏、正确性与资源回收。

先完成CPU接口回放、角色隔离CLI、source/version与新资源计划审查；主再建立新容器、启动有限runtime准入、创建真实源模型与保活副本。只有通过后才跑路径校准、C0与两个留出。故障修复使用新run并保留失败，不将准备工具通过写成在线模型资格。

达成当前范围的10%整体资格后，转入已有规划组件的候选评分/合法布局选择；未达标时只定位会改变决策的主要误差窗口，不恢复旧微秒级门或无限重复。当前主范围为HBM/DRAM与三节点共享通信；SSD留待确有该层决策需求时补，不能凭此次结果声称覆盖所有存储层。

本轮仅正常开发增量，不覆盖history-chat.txt，不全历史归档或上传GitHub。
