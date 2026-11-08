# 在线权重可用成本：独立初审

开发编号：v0.1.23.3 / task2；日期：2026-10-12。本次只读审查本地源码与归档，GPU 启动、SSH、新性能样本均为 0。`initial_audit.json` 与 `sources.json` 分别保存核对结果、行号锚点和输入 SHA。

本轮准入口径采用用户最新要求：每个代表性复杂拓扑场景的完整在线 weights_ready 预测相对误差不超过 10%。细项只帮助解释与构造预测，不另设每项微秒/相对误差门，也不保留复杂度升级门。旧成本账本结果和旧拒绝理由作为历史记录保留，不能反向变更。

## 1. 最小可信时间窗口

若 TP2 两个引擎 rank 均位于 node27 GPU4/5，可直接复用 `main_lib/fluidckpt/serving/measure.py:58` 的同主机汇总：`T_ready = max(rank weights_ready) - min(rank load_begin)`。必须恰好收齐预定 rank、每 rank 唯一开始/结束且无 load_failed；混合主机事件时拒绝减法。每台主机的局部耗时可以分别报告，不能将未校准的不同主机 monotonic/wall 时间戳拼成全局时间轴。

`serving/lifecycle.py:44` 的恢复窗口包含模型 layout/materialize、storage export、IPC 映射等待、实际搬运、原子 done handoff、引擎 device synchronize、TP CPU barrier 和解除 weights offload 标记，终点为 `weights_ready`。引擎 ready 后的 SHA、生成和实验清理不进入该窗口。

为覆盖真正请求之前的在线准备，另保留同一 coordinator 的 `request_to_ready_ack_s`：开始必须位于请求必要的 inventory/plan/prepare 之前，终点必须收齐全部引擎 resume ACK。若这些必要操作被前置到最早 rank load_begin 之前，单独的引擎 T_ready 不再覆盖完整请求，此时应将 coordinator 请求窗口作为完整在线主指标，或调整请求安排后继续采用引擎主指标。两种窗口都应保留，事前冻结选择，不能根据留出误差挑选。

已有 `basic_scenarios/run_scenarios.py:189` 的真实模型入口在 start_resume 后做库存查询、场景选择和在线规划，然后执行 live transfer；该部分位于引擎等候 handoff 的在线区间内。其请求起点还覆盖 save/start_resume 的当前实现开销。实验构造的大型 dump 或专用 observer 如需移出，必须在测量前明确调整，不能事后从总时间扣估算值。

## 2. Live 与保活副本发布不能混淆

| 对象 | 在线前必须保留的检查 | 全量 SHA 的位置 |
| --- | --- | --- |
| live 引擎目标 | manifest/model/TP/layout/extent identity；worker epoch；源 generation 和 VALID lease；完整 plan 覆盖；IPC storage 名称/大小/layout 对照；scatter 字节检查；实际目标记录字节；设备同步；done 原子发布；引擎同步与 rank barrier | 可以按已有协议在 weights_ready 后进行完整逐 op 源/目标 SHA |
| 新建 Catalog replica | 上述适用检查以及 complete content verification、SSD commit（适用时）、Catalog BUILDING→VALID 发布 | 必须在该副本 VALID 发布之前；不能简单关闭 `verify_before_ready` |

具体依据：`cluster/worker.py:132` prepare，`cluster/catalog.py:43` acquire，`cluster/planning.py:71` validate_plan，`serving/keeper.py:73` HbmTarget，`cluster/worker.py:218` execute，`cluster/control.py:131` transfer，`cluster/worker.py:323` complete，`serving/lifecycle.py:77` sync/barrier。

`worker.complete` 只在 `run['built']` 非空时强制逐副本 SHA 再发布；直接 live 目标无新建 Catalog entries，可复用现有 `transfer(plan, verify_before_ready=False, retain=True)`。因此旧小请求 published 的 118–313ms SHA 窗口，不应当作真实 live 引擎的必要在线开销。也不能把旧 filled 8–10ms 自动改名真实模型 weights_ready。

## 3. 正确性与验收最小证据

校准和留出都运行实际模型、实际完整目标存储，不以 32–96MiB replica 请求代替真实 ready。每请求按原协议保留源租约和目标 IPC 映射，ready 后逐操作全量 SHA 覆盖完整模型每个逻辑字节，生成固定 prompt 的确定性 32 token 并与正确参考输出严格比较，随后 finish、引擎 release、退役本任务源 owner。所有 source/target birth/epoch/generation、TP rank、内容 identity、storage layout、实际路径与字节保留。失败/重试独立新目录，不能把错误 ready 或不完整内容样本纳入预测验收。

构建保活状态、模型捕获/底本建立、首次通信初始化、实验 bootstrap、SHA、生成和清理单列总账；其中请求实际需要的在线初始化仍留在请求主窗。持续保活 DRAM/HBM 和源 owner 实际空间必须完整计账。已有 `basic_scenarios/run_scenarios.py:235–253` 已实现 ready→生成/完整 SHA→finish→release 顺序，可优先复用。

本次独立核对 S2 两请求、S3 两请求与 e3 四请求，共 8 个历史真实 Qwen TP2 case：全部 live target、完整 target extent、worker 操作次序、实际目标字节、逐 op 完整 SHA、确定输出和原始同 host 两 rank T_ready 均通过。每请求完整逻辑模型为 70,329,718,112B（约65.499654GiB）。这里只验证历史证据完整性，不产生当前拓扑性能样本或当前预测资格。

## 4. 资源入口与当前未定项

可复用的纯 metadata 链路为 `bounded_cost/execution/common.py:33 quiet_readback` → `multitier_runtime/operations/control.py:32 readback` → `operations/host_readback.py`。末端只有 stdlib、Docker GET、proc/sysfs、文件 SHA、端口检查，声明并实际不调用 GPU API、不打开 device、不加载 torch、不执行引擎 RPC；固定 node27/28/30 和 hostname，并有25秒进程硬期限。它核对 boot、精确容器 Id/StartedAt、cgroup、旧 owner PID birth、原 XML、资产和指定角色 BDF/NUMA。

node28/30 的末端会读 VRAM sysfs 和数值 KFD owner 字段；node27 刻意跳过 VRAM/KFD 容量路径。因此 node27 GPU4/5 的只读 metadata 能确认绑定和 NUMA，但不能证明当前可用 HBM。需要主 agent 统一协调后，以本任务所有权进程中的同 HIP `mem_get_info` 做有界资源准入；不能由旧 GPU4 空闲结论推断 GPU5 或未来空闲状态。

`cost_ledger/execution_gpu4/prepare.py` 现有 metadata/preflight 可复用检查语义，但 launch 固定 node27GPU4/world1，并且 6GiB DRAM、2GiB HBM、容器硬限8GiB，只适用于旧合成请求。真实 Qwen TP2 完整模型约65.5GiB；必须冻结新容器/owner资源预算及实际 GPU4/5 角色，不能借旧 budget 标签运行大模型。GPU4 的历史 BDF 为0000:85:00.0/NUMA4；GPU5、新节点容量与当前现场均仍待 fresh metadata。

`basic_scenarios/inspect_runtime.py` 虽标题为 read-only，却调用 `hy-smi --showmeminfo vram`；不能作为“不调用 GPU 资源 API”的纯 SAFE metadata 路径直接使用。全仓当前 SAFE 命中主要是 SSH 配置白名单，没有发现名为 SAFE 的通用 GPU 资源启动器。旧 basic_scenarios bootstrap 固定 node27/29 GPU0/1，不能原样启动当前 node27GPU4/5/node28/30。

新增 topology 输入与 dump 必须位于本任务版本目录并分文件，仅本任务进程显式设置 NCCL_TOPO_FILE，保留原厂及共享 XML 和默认 mapping。

## 5. 下一步独审范围

主 agent 冻结模型/TP、源与目标角色、预算、代表场景、计时边界和有限校准/留出矩阵后，独立审查预登记、预测冻结 provenance、启动计划和最终实际数据。每配置通常一次，必要时两次；按每场景 whole ready≤10%验收，数据缺失或域外保持 null。计划和启动通过不等于实测预测通过，不引入 solver、全路径扫描或新的通用 benchmark 框架。

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/audit_initial.py --out codex_coding/results/v0.1.23.3/task2/online_cost/review_initial_r01`。结果：8历史 case 通过，GPU/SSH/新性能样本=0。复核时选择新 review 输出目录，现目录拒绝覆盖。
