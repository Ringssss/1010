# 在线权重恢复成本验证：执行进展、IPC修复与资源阻塞

开发编号：v0.1.23.3 / task2。对应总计划18.35、实验记录50及[执行计划](../plans/2026-10-12_online_ready_cost_validation_plan.md)。本报告为正常开发增量，未覆盖history-chat.txt，未做全历史归档或上传GitHub。

**当前后续结果入口：**用户已批准两节点三GPU单发送者简化，完整C0/Hnear/Hremote已经执行，见[简化在线恢复报告](2026-10-12_simple_two_node_online_report.md)。Hnear实测1.31220s/误差21.70%未过，Hremote4.56026s/误差8.70%通过；功能与回收过、整体10%未认证。下文保留此前四GPU尝试和物理暂停验证过程，不再作为“目前没有任何在线样本”的状态依据。

**续跑最终状态覆盖下方旧快照（2026-10-12 18:52）：**小型IPC对照与完整Qwen物理暂停均已完成，见第7节。once导入释放32MiB，twice导入保留32MiB；修复后的完整模型每rank释放35,180,746,240B引擎分配，整卡free增加35,213,279,232B，全部70,329,718,112B权重保持在DRAM，校验通过。远端node30即时HIP两次准入失败，成本校准/C0/留出仍0；目前没有10%误差资格。原engine监督清理竞态失败保持，subreaper及最终quiet通过，两个新r03容器最终已停止。下文未完成的小探针/物理释放描述是上一轮历史状态。

## 1. 当前结论

**尚不能认定在线成本模型已达到整体10%误差。** 本轮建立并实机启动了真实Qwen TP2验证链路，完成完整权重捕获、校验、参考输出以及远端副本构建，但停在正式校准之前。完整C0、Hnear、Hremote均未执行，不能报告预测准确率或端到端性能收益。

最终独审状态与逐项证据见[qualification.json](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_final_two_node_r02/qualification.json)：`partial_scientific_result_resource_blocked`，预测误差为null，修复CPU通过、实机释放尚未验证，最终自有容器停止另行确认。

两次完整工作流尝试分别暴露线程预算问题和暂停后显存未释放问题。线程配置修正后已通过原失败阶段；新捕获校验中重复导入同一IPC凭证的代码错误已修正并通过CPU验证，实际显存回收效果待硬件验证。最后的32MiB验证在分配payload之前被GPU容量门拒绝，当前所选卡上已有外部GLM作业；本轮自有两个容器均已停止。

| 验证层次 | 已有证据 | 当前状态 |
| --- | --- | --- |
| 两节点四卡身份与初始容量 | node27/node30 GPU4、GPU5；真实HIP BDF85/b5、NUMA4/7一致 | 初始准入通过，不能沿用为下次准入 |
| 原生通信组与两条远端pair | 四keeper mesh/warm及真实完整副本传输 | 已执行 |
| 完整真实权重捕获 | 每rank 35,164,859,056B、1009个storage、33个DRAM区域；两rank captured标记均full_sha_passed | r02完成，详细逐块SHA未被旧控制器阶段末尾写出，原件边界保留 |
| 参考生成与暂停接口 | generate与release_memory_occupation返回成功 | 接口成功；物理暂停未通过 |
| 本地/远端完整恢复成本校准 | 六路径、双流、完整C0 | 0个正式校准样本 |
| Hnear/Hremote留出与10%验收 | 预测必须提前冻结 | 均未执行，误差未知 |
| IPC一次/两次导入实机诊断 | 新脚本及7项CPU契约检查 | 一次导入配置在创建源tensor前拒绝；两次配置未运行 |
| 修正后的单映射捕获与物理暂停检查 | 产品默认行为、失败回收、完整事实、物理暂停CPU检查 | CPU通过，实机待验 |

## 2. 目标、计时与预先登记的验证设计

目标是预测线上从必要查询/计划开始，到全部目标rank权重可用且引擎返回恢复ACK的时间。使用同一控制器monotonic时钟，保留SGLang真实materialize、IPC导出/导入、复制完成、GPU同步和TP barrier。全SHA和参考输出检查在ready之后，保活源构建、初始加载和实验额外检查单列。

模型复用已有worker依赖DAG：六条实际路径分别测512MiB/1GiB各一次，一个真实双remote并发组合确定注册共享阶段的经验因子，再以一个完整C0校准共同外层残差。每rank固定1GiB分块、33个extent，尾块805,120,688B；总66个操作、2096个32MiB chunk。固定目标与顺序，只改变来源：

- C0：首1GiB本地HBM，接着16GiB远端DRAM，其余本地DRAM。
- Hnear：首1GiB本地HBM，其余本地DRAM。
- Hremote：首1GiB本地HBM，其余远端DRAM。

Hnear/Hremote完整预测必须在它们运行前冻结，每个场景独立要求绝对相对误差≤10%，不使用平均值掩盖失准。当前是同一个已初始化引擎/keeper生命周期内的新请求，不外推跨任意新进程。共享并发必须有同目标主机时钟的重叠记录及实际HCA日志，不能只凭图上连边推定。

三节点初始方案在校准前因node28候选卡高占用而收敛为node27/node30四卡。本轮范围不覆盖三节点或SSD。目标节点DRAM任务192GiB/hard224GiB、HBM48GiB/card；辅助节点DRAM96GiB/hard128GiB、两辅助卡HBM合计2GiB，包含runtime与staging。预算在新数据前登记，未因失败放宽。

## 3. 实际执行顺序及失败记录

结果根统一为`codex_coding/results/v0.1.23.3/task2/online_cost/`，下文目录名均相对此根。

### 3.1 部署与容量准入

`execution_setup_two_node_r01`冻结129个资产。第一次setup创建node27后，被Docker挂载数组返回顺序变化误拦；独审56个旧容器，仅24个Mounts数组顺序变化，挂载内容和身份未变。规范化完整挂载记录后仍拒绝Source/Destination/RW变化，精确复用刚建node27，再创建node30，成功结果在`execution_host_setup_two_node_r02`。

admission r01因把约1.33MB资产spec放进argv，在OS创建探针子进程之前报E2BIG。白名单参数缩为283/279B后，r02实际HIP准入通过：四卡各total68,702,699,520B、free68,365,058,048B、used337,641,472B。辅助两卡聚合675,282,944B加256MiB余量通过。独审`review_admission_two_node_r02`104项通过，仅认证该时点准入。

### 3.2 完整运行r01：线程上限失败

`execution_host_run_two_node_r01`及容器内`execution_runtime_online_qwen_two_node_r01`建立通信组并预热。引擎初始化时，OpenBLAS默认64线程池导致cgroup pids.current十次达到512，日志169次pthread_create失败，rank1退出-11；尚无权重capture或成本样本。

主读取精确controller PID/启动时间/cgroup后停止等待中的bootstrap控制器，再由原监督器停止本任务进程。宿主直接TERM因权限被拒、未生效；在准确容器内匹配NSpid342/start_ticks27998454后TERM成功，未使用泛化pkill。旧PID1为sleep，遗留4个僵尸；保留原件后仅重启本轮新建node27，StartedAt变化有单独记录。`execution_host_collection_two_node_r03`补收两节点原件并确认quiet；独审在`review_failed_bootstrap_two_node_r01`，原失败不改判。

修正仅为所有worker、engine、controller在导入前设置OPENBLAS/OMP/MKL/NUMEXPR/RAYON线程数1、TOKENIZERS_PARALLELISM=false。pids512、模型、目标、预算与原129资产快照保持；使用独立r02运行目录。

### 3.3 完整运行r02：源构建完成，但物理暂停和最终资源检查有缺口

`execution_host_run_two_node_r02`先通过fresh admission r03，再完成真实Qwen加载。两rank各导出1009个storage、35,164,859,056B，capture标记均记录全SHA通过；参考generate和park RPC返回成功。控制器随后完成两个远端完整DRAM副本和两个本地1GiB HBM缓存的原生transfer，最后的资源门抛出`actual selected-card conservative upper exceeds cap`。

旧代码在最终gate之后才写bootstrap.json，因此失败卡号和当时的upper/cap没有落盘，不能补猜是哪张卡或哪个节点。阶段末尾数据丢失本身已作为实现缺陷修复。远端复制日志可见完整操作，流程经过默认verify-before-publish；逐块校验明细未持久化，不能虚构缺失的SHA原件。

更关键的现有证据来自两个rank的release事件：

| 指标 | 每rank数值 |
| --- | ---: |
| release_begin allocated | 36,496,468,480B |
| release_end allocated | 36,496,468,992B |
| allocated减少量 | −512B，即几乎完全没有下降 |
| release_end整卡free | 29,574,037,504B |
| 该时刻整卡used上界 | 39,128,662,016B，约36.44GiB |

该较早的target占用低于48GiB上限，不能据此反推稍后最终gate失败的卡。它明确说明“park RPC成功”不能等同“权重显存已释放”。在这种状态下不能继续申请另一整份约32.75GiB/rank的权重恢复内存并报告成本。

## 4. 修复的代码问题与验证边界

新实验wrapper原先先调用原生capture，再重新打开同一个pickle构造HbmTarget做SHA。两次直接调用`_new_shared_cuda`消费的是同一份`_share_cuda_`导出凭证。安装环境的Torch2.8/ROCm源码与上游实现表明，导出为该计数槽设1，每个received storage析构会减1；重复消费同一凭证存在1→0→−1下溢风险，生产者读取无符号非零计数时会保留allocation。源码证据见`review_IPC_reimport_r01`及[StorageSharing.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/csrc/StorageSharing.cpp)、[CudaIPCTypes.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/csrc/CudaIPCTypes.cpp)。这与整份权重未释放和“超过1000个被消费者引用的块”警告一致，但本轮未读到实际失败计数，不能宣称唯一运行时归因已经完成。

最小修复：

1. `main_lib/fluidckpt/cluster/capture.py`新增可选`verify_full_sha=False`，默认不增加全量检查。启用时在原有唯一HbmTarget存活期间直接校验，完成后再释放映射。
2. 实验worker调用上述可选校验，不再第二次导入相同IPC凭证；captured标记仍在校验通过后发布。
3. 控制器分阶段保存capture、参考输出、helper/cache transfer及完整inventory；资源检查先收齐所有卡的实际值，再记录明确失败节点、BDF、upper、cap、margin。
4. 在构建helper前，以及每次在线恢复后的park之后，验证每rank的引擎allocated减少量与整卡free增加量都达到完整权重字节数的90%。该规则验证物理暂停，不是成本模型误差门，也不减去未归属的背景占用。
5. 新监督器在本任务内启用subreaper，原准确进程组清理之后有界waitpid回收自身收养的子进程；真实fork CPU检查通过，后续新源码冻结才会生效。

原产品worker/control/service_cost未改。没有重写layout算法、移植Janus或新增solver。最终`execution_core_cpu_r04`和独立`review_core_final_cpu_r01`各35项通过，覆盖单次映射、默认无SHA、失败无marker/准确回收、完整gate事实和物理暂停判定。新增capture_s_scope明确可选SHA计入bootstrap capture_s；它不是线上恢复时间。这些结果不等同HIP映射释放或10%准确率实测。

## 5. 32MiB诊断为什么没有运行完

`probe_ipc_counter.py`准备在独立进程中比较一次/两次raw导入：每次生产者仅导出一个32MiB tensor，检查全部字节；消费者删除所有view/storage后，记录只读计数槽及生产者释放后的allocated/free。所有进程有限时、准确归属回收，整卡占用门2GiB。

`execution_ipc_probe_once_r01/producer_init.json`实际记录：allocated=0、reserved=0，整卡used34,833,694,720B/free33,869,004,800B。探针在创建源tensor之前即被拒绝，**没有payload、share或consumer；双导入配置未执行**。

`execution_resource_block_r01`保存只读CPU进程证据：另一个`/glm-5.2-int`容器中GLM vLLM TP8/PP3、三节点服务于17:38:21启动。它晚于r02约17:27的资源失败，**不能用这个新作业解释更早的失败**。当前selected GPU已不满足全模型52GiB空闲门，不继续换卡扫描或扩大预算。

## 6. 回收和下一步

`execution_host_collection_two_node_r04`保存r02两节点全部原件，明确记录当时quiet未过：node27 pids7、node30 pids3，均为sleep及退出后的僵尸。随后仅处理本轮新建的两个准确CID，原件留在stage/results中。最终`execution_host_owned_stop_r01`显示两容器Running=false、PID=0、OOMKilled=false。ExitCode137来自对不处理TERM的sleep PID1执行有时限的docker stop，不是内存OOM。没有运行中的本任务GPU实验，未触动并行GLM任务或共享XML。

原r02监督器结果也保留其失败：engine的准确进程组清理通过，四个worker均记录`identity changed before KILL; no further signal`，当时child_returncode=null。**原自动回收未全部通过，最终停止状态由后续只读进程检查和准确容器stop另行证明。** 新subreaper的CPU资格不等于已经修复所有退出竞态；下一次实机需核对原guard返回、子进程reap和最终quiet三项，不能把旧失败覆盖成成功。

继续顺序已经收敛：

1. 所选资源重新可用时，重新准入；先跑32MiB一次/两次导入诊断，保留完整计数与显存证据。
2. 冻结包含单映射capture、阶段记录、物理暂停检查、线程限制及subreaper的新源码；不得复用旧r01/r02运行目录或旧容器身份假设。
3. 完整Qwen捕获后先确认真实释放；再构建helper，记录资源门具体事实。若预算不足，依据实际占用区分runtime与权重空间，再决定有限调整。
4. 然后完成六路径/双流校准、C0、提前冻结的Hnear/Hremote。各场景整体误差≤10%后转向既有规划器和同预算布局收益，不再为微项精度扩大任务。

主要可复用入口：`online_cost/execution/prepare.py`、`orchestrate.py`、`controller.py`、`worker.py`、`probe_ipc_counter.py`；模型接口为`online_cost/model/{predictor,freeze,evaluate}.py`。完整本轮执行命令见总实验记录50.4及各`execution_host_*_design*/commands.txt`；已经执行的结果目录均不得原名重跑。

## 7. 用户再次授权后的实际IPC对照与局部完整模型验证

### 7.1 资源刷新和32MiB受控复现

主执行`review_resume_metadata_r01`两节点只读元数据，host内存/端口/镜像均过。node30所选GPU5/GPU4先后sysfs读used2,207,744/2,269,184B，仅为候选观察。主仅启动旧准确自有node27私有IPC容器用于探针；`resume_target_admission_r01`实际HIP确认GPU4/5每卡free68,365,058,048B、used337,641,472B。

使用原冻结探针SHA b2b7429e...分别启动独立producer/consumer，一次导出、导入次数为唯一变化；两配置均32MiB已知字节13，全量检查通过，consumer rc0/准确回收通过。实测原件为`execution_ipc_probe_once_r02`与`execution_ipc_probe_twice_r02`。

| 结果 | 一次导入 | 两次导入同一凭证 |
| --- | ---: | ---: |
| 单次producer export | 1 | 1 |
| consumer raw import次数 | 1 | 2 |
| payload字节 | 33,554,432B | 33,554,432B |
| drop/GC/empty_cache/ipc_collect/sync后allocated | 0B | 33,554,432B |
| 同阶段整卡free增加 | 33,554,432B | 0B |
| producer额外退出警告 | 无对应警告 | shared CUDA tensors未全部释放 |

独审`resume_review_r02`24项通过。小块对照已直接证明当前部署中重复消费同一导出凭证会保留整份分配，支持唯一映射修复；这还不等于完整Qwen释放或在线成本资格。两配置最大整卡upper725,614,592B，小于原2GiB门。结束后只读ps仅sleep/观察进程，主停止准确旧容器，Running=false/PID0，私有IPC命名空间关闭；没有泛化删除SHM文件。

### 7.2 两项证据解释修正

探针实际返回`torch_version=2.10.0`，纠正旧审查把另一环境的2.8源码版本当部署版本。旧引用和原件保持，当前实际解释采用2.10源码。

原探针`pread(offset*8)`没有处理RefcountedMapAllocator数据区前的64字节头部；实际offset0读到的恒定1不能用作IPC槽值。**此次只证实0/32MiB分配保留差异，没有直接观察计数下溢。** 子agent已把未来探针改为未验证布局时默认unknown、不读取数值；7项CPU检查过，无追加GPU运行。原b2b探针结果不覆写，修订见`resume_ops_r01/ipc_counter_correction.md`；已冻结r03的旧探针副本未用到完整模型流程，不原地更新冻结资产。

### 7.3 完整两节点r03拒绝与局部验证安排

`execution_setup_two_node_r03`冻结138资产，包括产品单映射capture、完整失败事实、物理park及subreaper修复；新容器建立成功。`execution_host_admission_two_node_r04`实际HIP显示node27两卡通过，但node30 GPU4/5各used32,700,891,136B、free36,001,808,384B，聚合65,401,782,272B超过原helper2GiB门。此前低sysfs与此刻HIP值不同，不能当成同一时间的空闲承诺，未放宽门或启动远端worker。

在任何新完整模型数据之前登记独立`online_qwen_local_pause_r01`：仅node27 GPU4/5、两个native keeper和一个TP2引擎，同192/224GiB DRAM和48GiB/card；本地mesh及warm_pairs=[]，捕获/单映射SHA/参考输出/真实park/完整DRAM留存验证之后停止，不建helper/cache/C0。新`local_pause.py`只追加到新r03 stage，保留138资产；原controller/product复用，runner16项CPU、host14项CPU、独审6项通过。Catalog按页分配每rank35,164,860,416B，逻辑权重35,164,859,056B，不能把尾块填充误判为泄漏。

主即时目标准入`execution_host_local_pause_admission_r01`通过，冻结plan_r01后运行`execution_host_local_pause_run_r01`。该局部功能验证无成本校准样本，不承诺整体10%资格，实际结果与回收随后追加。

### 7.4 完整Qwen物理暂停通过

最终实际独审[resume_review_r05](../../codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r05/report.md)31项通过。未来退出判定修订由[resume_review_r06](../../codex_coding/results/v0.1.23.3/task2/online_cost/resume_review_r06/report.md)独立核对，仍不回写原host失败。

`execution_host_local_pause_run_r01/collection_node27.tar`保存完整实测；内部`phase_pause/local_pause.json`为功能通过，两个rank的33个区域各自完整SHA记录分别保存在checkpoint_capture_rank0/1.json，合计66区域、70,329,718,112B。参考输出以“Paris, a city renowned…”开头，完整原件保留。暂停后Catalog全部源仍VALID、lease0、transaction空，实际DRAM分配每rank35,164,860,416B。

两rank分别得到相同资源变化：

| 指标（每rank/每卡） | 暂停前 | 暂停后或变化 |
| --- | ---: | ---: |
| 引擎allocated | 36,496,468,480B，约33.99GiB | 1,315,722,240B，约1.23GiB |
| 引擎allocated释放 | — | 35,180,746,240B，约32.77GiB |
| 整卡空闲显存增加 | — | 35,213,279,232B，约32.80GiB |
| 暂停后整卡free | — | 65,101,889,536B，约60.63GiB |

allocated释放略多于逻辑权重字节，可能包含对齐或附属分配；不将超过100%的部分当额外权重。两个90%门均通过，证明这次完整模型暂停确实释放了权重显存，同时保持完整DRAM保活副本。此前未释放→此次释放与单映射修复方向一致；两次完整运行的mesh/现场不同，不能用整体时长差作性能收益或将所有差异唯一归因。

`diagnostic_duration_s=356.879426979`是整个加载/捕获/校验/参考生成/暂停流程的诊断时间，**不是模型在线恢复时延**。本轮没有resume/C0/Hnear/Hremote，不能从该值计算恢复加速。

### 7.5 管理退出竞态与最终资源阻塞

局部controller功能成功、host整体rc1：唯一cleanup_failures为engine旧guard返回`identity changed before KILL; no further signal`。两个worker原监督结果均通过。engine原始guard仅发送TERM，记录false保持；随后subreaper准确waitpid已收养的本任务进程，包含已登记leader159及相关子进程，pending_children为空。最终collection实际pids.current=1、身份/138资产保持、quiet通过。原自动guard失败与最终清理成功是不同证据，不修改原run_summary为passed。

完整模型物理暂停完成后，再做一次有理由的两节点fresh admission：`execution_host_admission_two_node_r05`中node27通过，node30 GPU4/5各used49,048,190,976B/free19,654,508,544B（约45.68/18.30GiB），仍超helper整卡2GiB聚合协议。未启动任何远端worker，未放宽预算，没有第三次资源轮询或追加性能重复。

主将本次新r03两个容器准确停止，原件保存在`resume_owned_stop_r01`：node27 CID30b7c7e9...、node30 CID2c2b5b9b...均Running=false/PID0/OOMKilled=false；stop前两者仅sleep，退出137为限时stop的PID1处理。旧probe容器也已停止。当前没有本任务后台GPU进程。接续时旧admission和StartedAt均不得作为新准入凭证。

【续跑关键命令】具体脚本与结果均版本目录保存，GPU探针在旧准确私有容器中使用相同已核SHA文件，未使用inline实验：

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/collect_metadata.py --out codex_coding/results/v0.1.23.3/task2/online_cost/review_resume_metadata_r01 --run-id online_qwen_two_node_r03 --scope two_node
docker exec 8b484137e72e13d49b546c9425aea09e643196cdeea93a515dbf3248cb4cfb0d python3 /tmp/online_ipc_counter_probe_r01/probe.py --imports 1 --out /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/online_cost/execution_ipc_probe_once_r02
docker exec 8b484137e72e13d49b546c9425aea09e643196cdeea93a515dbf3248cb4cfb0d python3 /tmp/online_ipc_counter_probe_r01/probe.py --imports 2 --out /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/online_cost/execution_ipc_probe_twice_r02
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/prepare.py --run-id online_qwen_two_node_r03 --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_two_node_r03 --metadata-root codex_coding/results/v0.1.23.3/task2/online_cost/review_resume_metadata_r01 --model-metadata codex_coding/results/v0.1.23.3/task2/online_cost/execution_metadata_local_r01.json
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/local_pause_host.py admission --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_two_node_r03 --bindings codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_setup_two_node_r03/setup.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_local_pause_admission_r01 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/local_pause_host.py prepare --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_two_node_r03 --bindings codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_setup_two_node_r03/setup.json --admission codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_local_pause_admission_r01/admission.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_local_pause_plan_r01
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/local_pause_host.py run --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_two_node_r03 --plan codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_local_pause_plan_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_local_pause_run_r01 --execute
```

下一步不再重复小IPC或完整物理park探索；在远端资源满足且经过新鲜准入后，直接以已有单映射修复继续原native完整源构建、六路径/双流/C0、提前冻结Hnear/Hremote。保留实际物理park作为每次运行正确性门即可。预测误差达到10%后再转规划器优化。
