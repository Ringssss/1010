# Key Conclusions

## 2026-10-12｜成本计费已闭合，操作数有局部价值，控制段决定整体迁移

native单worker GPU4六校准+两新会话留出完成；GPU0资源拒绝0样本独立保留。三项worker经验T(us)=235.052833+3.02440625N(MiB)+95.451833K，在两留出点误差+.45/−6.16%，简单只按N在V1低估27.66%。这是有限域经验预测证据，不能称物理BW或唯一op固定费；独立chunk/首状态费用仍未识别。

完整模型升级未过：三项filled误差改善39.977/152.723us均小于冻结Efilled575.449us，虽然该候选各outer数值门过，最终selectedworker/outer仍null；不得看见留出后放宽规则。V1总低估1.676ms中1.622ms来自请求前段，inventory+1.387ms/prepare+.154ms，worker误差仅43.790us。当前32–96MiB synthetic场景控制比worker更大；published118–313ms主要SHA，不能把filled替代live可用。全SHA/计费/精确回收与模型晋升分开验收，八请求上限已结束，无算法加速/规划收益。

## 2026-10-11｜成本先按归属计账，再识别系数与规划

用户要求成本探索优先；计划18.33/记录48与新cost_accounting_and_identification_plan为最新。资源容量界、执行DAG、完整请求是不同层：有效端到端速率不是物理容量上界，不能把LP代理值直接称严格下界或加到DAG。prepare_s含inventory，op beginlog在record内/endlog在外，worker execute_s不是完整handler窗；SSD为复合腿、sync为剩余完成等待。先统一ns非重叠分段，显式残差不等于根因解释。

新N/K/C五配置+一锚点+两条件留出仅设计，CPU秩4/八检查通过，不代表真实线性、已识别首状态或新预测精度。原每op一chunk有K=C混淆；新C变化还改变块大小分布，chunk系数只能是待验证有效项。保留A unidentified/B skipped及旧bounded域，不强制正带宽。下一步最小计时覆盖层→校准/留出→必要路径/共享竞争→同预算决策和live主指标。新GPU0，无产品/旧模型变更。

## 2026-10-04｜本轮新增证据与边界

完整GLM原生在HCA0/FP8 Triton/AITER_MOE=0下两题重复生成通过。8层诊断的三节点CPU保活、warm restore与适配HIP IPC已通过全量字节和输出校验；完整GLM Fluid恢复正在full_distributed02执行，还不能报告成功或加速比。

新增两个实证机制：CPU owner与GPU传输进程分离后，GPU客户端退出仍可重新接管字节；三节点64MiB迁移25/50/75%时可从新旧混合位置恢复并通过全SHA。大模型A/B和同预算强基线/算法消融仍待验收。HLL只作为需求摘要候选，不能替代精确覆盖与最后副本保护。

原生SGLang CPU备份不能简单认定不存在。补齐隔离TMS库和DTK兼容问题后，Qwen35B能完成首次CPU恢复，但第二次输出错误；目前该路径未通过模型验收。GPU微探针成功不等于模型成功。

最新执行状态以 `history-chat.txt`、`v0.1.23.2-task2_glm_three_node_execution_20261004.md` 和各run的JSON为准。

## 2026-10-04 06:12｜故障边界与最新限制

- 完整GLM原生生成已通过，但full01完整恢复生成没有通过。TopKConfig中的旧meta Tensor引用说明必须修复普通Python配置里的Tensor别名，不能只重绑parameters/buffers。
- GPU通信watchdog可终止整个keeper组；私有DRAM由这些进程持有时会一起丢失。在真实数据mesh上运行未验证路径探针是本次实验隔离错误，禁止重演。
- 独立CPU owner持有memfd页，GPU客户端异常退出后数据仍在；替代ClusterWorker接管32MiB实机测试通过。完整模型集成仍在诊断阶段，不宣称已解决节点/owner故障。
- full01所有自建进程/容器已清理，没有待接续的存活快照。当前只运行8层diagnostic_owner01。

## 2026-10-04 03:50｜完整 GLM 已能原生推理

- native_triton04在gfx936/DCU上原生TP16给出Paris和5，各重复一致。此前“GLM本平台不能启动、只能等vendor”的判断已被实测推翻；靠现有后端配置解决，没有改安装包源码。
- 有效配置：--fp8-gemm-backend triton，SGLANG_ROCM_USE_AITER_MOE=0，AMD_SERIALIZE_KERNEL=0。需要在所有基线和FluidCKPT中一致使用。
- source checkpoint约703.7GiB，运行时每rank权重约45.86GiB，原生startup 1722.17s。本数字是兼容性run，不是公平保活性能比较。
- 3节点22keeper full01当前在做734GiB量级运行时权重capture/park/restore，尚未宣称通过。

## 2026-10-04｜GLM故障定性更新，当前仍在推进

- 不能把历史NSA Python栈当作唯一故障源：默认HCU FP8 GEMM在不调用NSA时即可重现VMFault；对应Triton内核通过CPU参考比较。完整模型生成尚待验证。
- TP16全局kernel同步调试开关3时卡首次RCCL all-reduce，关闭后完整通信初始化通过；保活搬运优化不应混入平台后端修复收益。
- 共享handoff上，IPC归属必须同时包含主机和本地GPU编号。FP8 scale格式等scalar属性属于恢复状态；单有权重字节不充分。
- HLL可用于大规模需求去重摘要，不能替代精确副本覆盖；三节点的精确统计更小。研究价值先检验同容量保留慢来源块能否减少恢复尾部。
- 逻辑块目录拆分不等于物理可释放；分段让位需要后备分配粒度配合。

## 2026-10-03｜三节点和 GLM 的当前判断

- 用户最新允许三个节点；既有final03是四节点结果（node30仅供SSD），不能改名为三节点结果。
- GLM约703.72GiB，候选node27+29 TP16；一台约503.63GiB DRAM不能完整保活，需分布或SSD。三节点上的运行可行性仍需原生推理验证。
- 当前GLM仍无成功生成记录；历史纯SGLang有VMFault证据，本轮只读核查不代表新复现。不要让这条平台旁线抹掉已完成的Qwen生命周期结果。
- 当前2.29–3.75×仅对逐出后磁盘回载；独立CPU保活基线与MILP消融尚未完成。

## 2026-10-03｜生命周期实测结论（覆盖下方“仅设计”状态）

1. 固定TP2、Qwen3.6与9g_8b、海光四节点已跑通A→B→远端A→本地A和重复切换。final03没有失败事件、源码漂移或清理错误。
2. 相对适配基线的逐出后磁盘回载，端到端观察分别快3.75×/2.29×/3.11×；纯resident IPC仍为0.828s。不能扩成所有场景或官方原生daemon的胜出结论。
3. A的本地保活权重HBM/DRAM均可释放；65.5GiB快照由远端DRAM+SSD完整覆盖，两个阶段全量SHA256通过。warm engine的KV/运行时仍需显存。
4. 内存规划必须计入同时保留的A+B与运行时；只按A选择NUMA会导致B分配失败。最终选择GPU0→NUMA1来满足容量，表明本机有空闲DRAM不等于指定NUMA放得下。
5. 少量SSD供给字节可主导恢复尾部；当前HTTP代理成本须分解后再归因。迁移59s中真正execute约5.61s，其余准备和全量验证；这些生命周期成本必须显式计入。
6. 下一步是同预算策略消融、慢路径区域缓存、迁移分段提交与中途召回；当前还没有严格全系统内存封顶或多负载SLO证明。

最终报告：`docx/articles/2026-10-03_lifecycle_results.md`；原始证据：`codex_coding/results/lifecycle/final03/`。

## 2026-10-03｜生命周期目标与基线纠正

1. 官方 Weight Cache Daemon 自己拥有 HBM，engine 退出不是其失效条件；本项目自写 B2 不能直接冠以官方 daemon 成绩。上游也已讨论 VMM/fleet 供给，论文需要更强的差异化。
2. 已有历史跨节点真实恢复与完整字节验证；近期 F 驱动退化不可抹去这些成果，也不可把旧成功视为当前有源码漂移的实现重新通过。
3. 当前用户要同预算端到端 A→B→A；B2 的0.341s权重窗口与125s启动必须分开。保住权重不自动保住框架初始化状态。
4. 最有价值的设计假设是联合满足“释放显存期限”和“恢复服务期限”，以瓶颈、层依赖和TP互补性分配有限缓存。MILP/LP是实现工具，按层重叠、VMM或远端传输本身已有先例。
5. 相同 resident 命中条件下，远端搬运不保证严格快过零拷贝；应保留本地快路径，并证明在内存压力和反复切模时扩展资源-延迟可行区域。page cache计入DRAM，先验证用户态方案，再决定内核扩展。

证据与设计：`docx/plans/2026-10-03_fluidckpt_lifecycle_design.md`。本轮为设计审计，未取得新性能成绩。

## 2026-09-29｜managed04 单目标联合控制层已完成验收

- 全模型自动候选、联合plan RPC、预算可行变更、持久checkpoint、发布目录恢复接口已通过真实Qwen TP2链路；不再是仅脚本离线求解。
- 在每目标卡允许8 GiB时，400候选MILP仍选择4+4 HBM和1+1 DRAM；gap=0仅对该模型与有限候选成立，不证明任意场景均分最优。
- 紧预算变更14动作，临时空间0，保留完整node28底本；提交后中断/续作/重复应用通过。不等于节点崩溃下的全局原子布局替换。
- 当前配对样本：搬运1.379845→1.221305s，加载2.034551→1.722079s；分别降11.49%和15.36%，准备差异独立披露。两次完整70.33 GB和生成均通过。
- GPU空闲不能保证邻近NUMA能容纳底本。managed03中node28 NUMA2只有25.0 GB可用，而单rank需35.16 GB；按容量换到NUMA3后闭环通过。预选不是OS预约，实际分配仍重新检查。
- 本次尚未给出多目标、可变底本、在线自适应或SGLang自身保活的严格对照。

## 2026-09-29｜过程增强中的能力边界复核

- 现有Catalog/transfer提供单worker状态、leases、内容核对及协调保护，不等于整个placement跨节点全有或全无提交；partial complete时已VALID副本不会被abort盲目撤销。
- `Cluster.retire`做全局覆盖检查，底层worker.retire仅管理本地状态。当前实验直接调用低层退休由完整node28底本前提保护；下一阶段应统一入口和变更前置条件。
- 联合求解返回的是候选选集；候选描述不应直接当成实体目录。真实target需经reserve、校验、publish才可作为新来源。
- 连续校准本地HBM 1 GiB约2.895ms，4 GiB线性估算约11.58ms；真实模型worker6对应op约36.06ms。说明该外推仍有缺项，不能仅凭差额断言某单一scatter根因。
- 以上为现有代码/结果的深入复核，不新增性能成绩；详见过程文档附录B～F和自动证据附录H。

## 2026-09-29｜v0.1.23.2 / task2 最新结论（下方保留历史）

1. 当前系统已在Qwen TP2完成主动NUMA保活、三节点八阶段闭环；不能继续把GLM平台问题当作全部项目阻塞。
2. 指标是同主机`max(weights_ready)-min(load_begin)`；不等同进程/HTTP/首token。校验保留但置于live加载计时之外。
3. 控制面压缩曾将2.305257s降到1.955185s，数据搬运基本不变；不是LP收益。恢复/放置默认nearest，实验显式选择LP。
4. 双缓冲功能与失败恢复通过，但单次2.105821→2.090150s不构成明显加速证据；默认serial。
5. 重算正常serial窗口，搬运1.598505s约76%，拷完收尾28ms；约0.25s快卡等慢卡不能重复算纯控制开销。
6. bulk诊断显示源端H2D与通信衔接值得优化；RCCL kernel驻留包括等供货，不能证明网卡饱和。该run因分析脚本指纹变化保留failed，仅diagnostic_usable。
7. 联合问题为预算副本放置＋资源约束流分配＋最小最后完成时间。单副本贪心可能因minimax平台不前进；本例成对贪心和MILP找到同值。
8. placement01在固定底本、单目标、36候选内，MILP gap=0；执行等价的目标侧均衡布局。传输1.361813→1.255519s（约7.8%）；完整加载2.133549→1.812675s（约15%，混有准备变化）。每种一次，不声称稳定或全局普适。
9. 每目标GPU 4 GiB上限是预设。没有实测“允许单卡8 GiB”后的集中vs均分，不能用被约束排除的方案证明普遍最优。
10. 最新联合控制层七步计划尚未实施。下阶段补自动候选、真实预算、迁移峰值容量、需求场景、计划执行与反馈；不重搭环境、不跑五轮。

证据主入口：`code_building/process_docs/v0.1-init-project/v0.1.23.2-task2_system_profiling_joint_placement.md`与`codex_coding/results/task2/cluster/placement01/placement_analysis.json`。历史早期资源数字不可直接替代当前路径/并发组合的实测。

## Living Insight Ledger

- 方向选择前优先查看 `docx/context_index/04_insight_ledger.md`。
- 该文件是 FLUIDCKPT 机制洞察的 canonical 活文档（I1–I13），维护每条洞察的机制、数据来源、
  状态和优化含义。
- 新实验如果改变了 insight 的状态，应优先更新该 ledger，再在本文件追加最高层结论。

## 2026-09-27 (v0.1.23.1 仓库规范化迁移)

- **四层归属确立**：
  - `main_lib/` = 会被 `import` 的产品代码（核心库）。FluidCKPT 本体 21 个 `.py`、2,768 行、
    6 个子包全部在此。
  - `codex_coding/src` = 跑一次看结果的脚本；`codex_coding/results` = 产物。
  - `docx/` = 理解层（为什么做）；`code_building/` = 过程层（做了什么、结论是什么）。
  - 判据一句话：**会被 import 的进 `main_lib/`，跑一次看结果的进 `codex_coding/src/`。**
- **迁移零回归**：`compileall` 退出码 0；6 个子包 import 全部成功；`pytest` 5 passed；
  旧路径零残留；核心库行数与迁移前一致（2,768）。
- **`ROOT` 硬编码是搬迁脆弱点**：`glm_e2e.py` / `glm_keepalive.py` 原先写死绝对路径，
  已改为由 `__file__` 上溯推导。**后续新增驱动脚本一律自定位，不写绝对路径。**
- **`fluidckpt` 的定位是库不是脚本**：`keeper.py`(454) `sglang_hook.py`(230) 等是产品部件，
  与"跑一次看结果"的驱动脚本有本质区别。此判断决定了它们在规范仓库里进 `main_lib/`。

## 2026-09-27 (v0.1.23.1 磁盘事故：core dump 写满 /home)

- **事实**：32 个 core dump 共 **203 GB** 占据 `/home/zhujianian/fluidckpt`，
  使 `/home` 剩余仅 1.3 G。单文件 17.6–22 GB，即**单卡显存全量**。
- **成因**：`kernel.core_pattern=core`、`core_uses_pid=1`；GLM-5.3 每次崩溃是 8 个 rank
  同时被 `KERNEL VMFault` 打死，各 dump 一份 → 8 × ~22 GB。
- **处置依据（三重验证，缺一不可）**：
  1. 崩溃栈已完整落盘：`(v0.1.22.k/` 日志含全部 stack、`FOR DEVICE 0x...` 地址、
     `[BLASPLT]` 提示；
  2. 32 个 core 与 32 个日志 PID **双向零差集**——无孤儿 core（若存在，才是唯一该保留的理由）；
  3. 三批 core 时间戳一一对应三次已知崩溃，无未知来源。
- **必须记住**：core dump 对本案**无新增信息**，因为读它需要 gdb + 对应 DTK 版本，
  且文件权限 `root:root 0600` 我们**无法读取**（`file core.*` → no read permission）。
  日志是文本、可比对、可归档，证据价值远高于二进制内存快照。
- **防复发**：`next_step.txt` 新增原则 17 —— **跑 GPU 实验前必须 `ulimit -c 0`**。
- **附带收获**：`keep2` 神秘死亡定性为 **ENOSPC**。它跑在 19:25–19:35，此时 `/home` 已被
  17:47 两批 core 写满；`keep2_resume.log` 为 **0 字节**（创建成功、写入失败），
  与「worker 起不来被回收」（`resource_tracker: 8 leaked semaphore objects`）形态一致。
  **此前怀疑的「keeper 重启干扰」在机制上不成立**——`ctl.stop()` 只 pkill
  `fluidckpt.serving.keeper`，不碰 SGLang。

## 2026-09-27 (v0.1.22 及以前：平台缺陷定性，迁移前完成)

- **GLM-5.3 在本硬件上崩溃与 FluidCKPT 无关**（决定性结论）：
  - 用**完全无 hook 的纯 `sglang.launch_server`**（`FLUIDCKPT_MODE=""`，进程 cmdline 与日志双重确认）
    复现了**完全相同的**崩溃：20:03:43 启动 → 20:34:28 `KERNEL VMFault`（17 次），栈完全一致。
  - 对比 `vanilla1`（带 hook）亦有相同崩溃。
- **根因链完整**：
  - `is_hcu()` 为 True（`gfx936` 在支持列表 `["gfx936","gfx938","gfx928"]` 内）
  - `is_hcu_native_fp8_supported()` 要求字面量 `gfx938` → **恒为 False**
  - → `use_fp8_index_k_cache` 恒 False → `index_k_buffer_dtype = bfloat16`
  - → `_use_hcu_bf16_index_cache()` True → `_get_q_k_bf16`
  - → `lightop_attention.fuse_layernorm_rotary_embedding`（`nsa_indexer.py:438`）崩溃
- **翻任何 flag 都绕不过去**：NSA 的**两条** HCU 分支都调用同一个 `_get_q_k_bf16`
  （`nsa_indexer.py:1573-1576`），因此 `--kv-cache-dtype` 无法路由到别的路径。
- **vendor builtin 不可内省**：`inspect.signature` 报
  `no signature found for builtin <built-in method fuse_layernorm_rotary_embedding of pybind11_builtins...>`，
  无 env 开关。
- **平台本身健康**：同一硬件上 Qwen3.6 用 triton attention backend **成功 fired up**
  （2026-09-22 日志）；而 GLM-5.2 / GLM-5.3 **从未成功启动过**。
- **下一步判断**：不等 vendor。用 Qwen3.6 打通保活全链路拿端到端数字，
  GLM-5.3 可用后直接换模型——保活链路的价值不依赖具体模型。


## 2026-10-04｜IPC命中与物理让位需分开验收

完整GLM roundtrip通过并不代表GLM/Qwen闭环通过；后者在IPC baseline后Qwen OOM。目录RELEASED仍可有跨进程引用保留HBM；必须用实际allocator/device计数和后继模型入场验收。独立小探针已定位重复导出策略问题，完整模型修复尚未复验。科研主张应是同预算恢复能力与可执行迁移的联合收益，远端传输/MILP/内核修改本身不足以构成贡献。


## 2026-10-04｜动机实验实测与执行模型耦合

同一Qwen warm engine和后备容量，本地DRAM优先+SSD vs集群DRAM：50.38%本地覆盖32.617→3.007s，0%67.349→3.214s。三个位置是探索性配对点。执行器串行而profile漏约束时，100%DRAM仍选498.9MB SSD；ABBA实机2.572→1.781s（30.76%），验证成本模型改变取数和实际延迟。联合放置增益仍由独立消融回答。

三节点GLM/Qwen迁移中途召回/最终全量SHA通过。owner OOM后的memfd重建保住现存worker映射，完整GLM再恢复并全SHA通过。NUMA规划被prepare默认值覆盖的问题已修，并在22 live worker安装，27项CPU回归和容器内2项owner回归通过。


## 2026-10-04晚间｜实际恢复关键路径

按本地Meld Figure4构建TP rank时间线：fig4_lanes11完整GLM首token12.119s，全784.2GB SHA/输出一致。9个localDRAM ranks约3.93–4.07s到齐，最慢10.95s；数据到齐后等待53.45% transfer rank-time。六个远端rank共享node28供给，约294GB；缩短关键路径需要改变实际瓶颈资源负载，不能把等待面积直接当作可消除时延。

### 2026-10-06｜v0.1.23.3/task2 第一阶段证据边界

e1/e2 已从原始 rank 事件重算同主机权重可用时延：0%本地覆盖66.893→2.779s、50.38%32.197→2.383s，支持远端DRAM的容量/局部性动机；与首token数值分开。100%拷贝时间接近，总差主要为准备/等待，不归因于布局收益。两容量点与100%来自不同版本历史运行，保留父运行失败记录。

e3 同预算四布局全量SHA通过，分散平均fill12.976ms vs集中16.305ms；校验发布完整耗时约0.50–0.89s，单次小快照fill不能替代完整模型主指标。复制收益未定，下一步应复用接口验证真实模型布局收益，再决定自动化/并行建设。详见正式e1–e8实验报告第17节。

### 2026-10-06｜Qwen资源受限恢复已测至e6，复杂求解器增量尚未成立

完整Qwen IPC双循环与物理回收通过，T_ready约154–161ms。e3同8GiB缓存集中/分散的等权均值2.538151→1.931097s（单次23.917%），主要由远端execute3.319→2.138s变化贡献；HBM总量相同不代表来源负载和实际命中相同。

e4强贪心与MILP同布局/同有序路径，e5无新决策、0新GPU样本，不能用同phase引用证明不同planner总耗时相同。e6固定完整DRAM底本，greedy额外HBM0→16GiB使远端T_ready2.770961→2.348826s（约15.23%）、两目标均值约9.894%改善；16GiB joint相对greedy仅约0.48%单次均值差，未证明稳定算法增量。优先保留简单方案，资源曲线收益与算法贡献分开。

GLM两次fresh完整权重SHA通过，但首次生成均未匹配历史reference，r03第二次生成匹配；根因未定，GLM适配baseline修复版完整gate尚未执行。此结论不否定此前warm生命周期轨迹，也不允许把Qwen完成外推到GLM。自建模型/监控已清理，原owner/keeper和底本保留。证据见e1–e8正式报告18节。

### 2026-10-06｜文献优先：多节点布放与恢复模型边界

新增长期参考 `docx/articles/2026-10-06_weight_placement_and_topology_literature.md`。ServerlessLLM已有缓存布放/加载选址，BlitzScale已有长期参数池和拓扑感知权重分发；不能将独立owner或远端权重池概括为独有贡献。SCCL/TACCL/TE-CCL/ForestColl提供既定初始数据下的容量/路由/时序/树方法；Helix流单位为token/s。长期预算和未来恢复目标的项目映射需要保持内容身份、物理可执行性与主指标边界。

某条传输计划的max资源负载是该计划的模型内下界，不直接是整个placement的最优下界。若优化源/路径，需要对允许方案取最小或证明必需跨越量；严格物理下界要求容量是有效上界，实测平均值只支持模型估计。吞吐最优、有限消息完成时间及worker互斥不是同一概念。上述均为已有理论的条件化应用，当前并未证明新的最优性或算法增量。

### 2026-10-06｜Baseline与暂停权重研究定位

暂定主张为有限且分布不均的保活预算下，联合组织权重块驻留与恢复，改善空间—权重可用时延权衡。SGLang IPC、Janus host warm/active D2D为核心对照；拓扑主张需对照BlitzScale参数池/分发，ServerlessLLM缓存与选址必须纳入相关工作，强简单分层策略保留。该定位不是首次性或性能优越性的既成结论。

Janus ec1beca2已具备NUMA亲和性、跨服务器D2D、缓存引用计数与页池回收；公开D2D选择一个逻辑源实例、TP对应复制，尚未展示本文预算条件下的选择性块驻留与多源联合规划。池内回收不等于归还给外部进程。原版、HIP适配与机制复现的名字和结论范围分开；本项目尚未实测Janus原版。具体来源、公平协议和建议顺序见 `docx/articles/2026-10-06_baselines_delta_and_research_roadmap.md`。

### 2026-10-06｜基础场景通过，简单情形仍无算法增量

S2本地host两方法T_ready1.503806/1.330876s，execute0.837673/0.836650s；S3跨节点active复制2.559229/2.443636s，execute2.035478/2.040021s。各场景内有序操作相同，4次恢复完整70,329,718,112B SHA、32token输出和清理通过，单轮主指标差异不算联合规划收益。S3实际来源全active HBM，源前后生成不代替持续干扰评估；S2/S3不同目标不能作H2D/D2D横向排名。

e3离线记录进一步定位同worker跨目标串行依赖，集中B rank0 copy→publish等待1.882269s、分散0.010276s；软件依赖存在但物理出口争用未证明，A的HBM命中4→8GiB也影响原均值。下一步应构建有控制变量的S4/S5，而非直接扩求解器。

旧node27容器OOM退出导致旧Qwen底本不可达，本轮已隔离重建Qwen平台。新4keeper/完整DRAM/2parked引擎保留，原14keeper、2196VALID副本目录元数据不变；不是旧内容全量重验。最新会话和数据见basic_scenarios/qwen_bootstrap_r01与analysis_r01。

### 2026-10-06｜完整场景表：执行顺序是已验证的独立变量

S4固定8GiB共享缓存时host/fastest/LP=1.388548/1.168985/1.267901s；后两者同源同有序操作，execute均约0.75s，没有LP增量。host实际取数HBM=0，共同fixture8GiB不是其独立最低保活空间。

S5-S同布局/来源/字节、仅source0顺序[0,2,3]→[0,3,2]，提前rank1缓存操作解除后续接收的长依赖。首轮原序4.461190→交错2.645741s，反向轮原序3.672967→交错2.867578s，分别40.69%/21.93%；两轮均有真实worker顺序与全内容/输出/回收证据。该结论限当前串行worker的执行控制，不是物理网络、自动placement或MILP收益，不能给稳定平均排名。当前LP预览仍是原序，未独立测LP时延。

完整表最终入口为`complete_table_r01/review_r02`：13新观察全部通过，16历史和15别名分开；S5-P物理共享全未测，四个原版系统仍未完成统一baseline复现。ServerlessLLM仅CPU估价/候选子函数适配，不能填端到端成绩。下一步先对应已有调度方法、允许各对照共用执行改进，再评估驻留/选源增量。
## 2026-10-07｜三条真实baseline路径通过；速度差必须结合驻留条件

Qwen TP2完整payload70,329,718,112B下，海光vendor原生checkpoint observed cache T_ready122.266963s，Blitz源选择/单目标TP分发HIP适配2.804379s，真实ServerlessLLM Store HIP131.121135s。三条均全SHA、固定输出和回收通过，各一次成功观察。Blitz使用活跃HBM、Store使用processed NFS快照+66GiB pinned pool、native使用原始文件和mixed cache，不能把时延比作为Fluid算法增量或原版系统排名。当前单源单目标Blitz与既有D2D同路径，尚无新增决策收益。

Store全流程可运行不代表整套ServerlessLLM调度/选址/前端已复现；同理Blitz多目标pipeline、原CUDA/RDMA后端与Janus/xTensor原版仍未验收。文件建立、pool准备、source owner建立和后置校验分别记账，完整fixture DRAM不是每条baseline必需空间，未测全系统峰值不填推测值。

校验路径也必须满足运行时契约：原生HIP已工作不等于PyTorch GPU状态已初始化；直接_new_shared_cuda前缺初始化在24KiB中复现139，显式set_device后通过，随后完整2018storage校验及权重回收通过。另一个接入失败由复用engine内部120s期限短于143s Store加载造成，已加实际PID/environment期限准入。两类失败均有原日志、控制证据和独立复核，不用正确生成替代全量SHA或物理释放。

当前有效现场为recovery_r03/session_overlay_owned.json，旧node29目标引用不可继续使用。数据与论证入口为正式报告第21节，开发编号保持v0.1.23.3/task2。
## 2026-10-07｜隔离跨节点双流共享压力已实测；应用收益仍待桥接

固定node27→node29两条GPU对应路径，各流4GiB，单流98.480422/168.255970ms，并发共同235.603975ms，独立预期/实际0.714147，起始差25.09µs、调用区间overlap1；实际shca_0/port1计数与payload匹配。两机本地H2D相同比较0.991993/0.978973，无同等级退化。结果支持隔离RCCL共享路径压力，不能唯一断言NIC饱和或自动转成keeper_serial/整模型S5-P收益。GPU0/1分别NUMA0/3而实际NIC为NUMA0；NUMA0和3同CPU package0，不是跨插槽。

不等速流不能用“共同窗口聚合速率/单流速率之和”判断共享，否则独立流也会假阳性；本轮测量前以max(single durations)/pair makespan和5%startskew门槛修正，实测GB/s不是物理capacity上界。

三场景最终r08契约、CPU计划和快照余量通过，生产runner/预测profile未完成，T3应用共享归因conditional。T2包含复制布局，比较时须同复制机会且物理字节全部计费；6对固定布局fastest/LP同操作，不证明优化器增量。下一步最小缺口是生产路径小桥接、外部布局/预算/共用schedule薄适配、强简单placement同机会对照；不扩新求解器。详细依据见正式报告22节与audit_final_r02。
## 2026-10-07｜双流共享路径与GDR模式的因果拆分

G0同NIC A-on/B-off GDR pair237.870ms（独立172.743ms）；G1 split两流GDR pair98.194ms（独立98.471ms）；G2 split且B继续非GDR pair205.220ms（独立204.996ms）。G2的B单流反而比G0慢，但A并发从18.26恢复43.75GB/s，证明解除额外互扰不要求B变快；固定split仅切B GDR，B自身耗时变化约2.083倍。定位到HCA/PCIe/NUMA路径组合与transport状态，尚未唯一定位NIC/PCIe/CPU主因。

运行时builtin XML的GPU1 NUMA/初始化CPU与shca1PCI父链和sysfs不一致；实际模式要读逐GPU/通道日志，不用HCA级能力探测或GDRCopy开关代替。未改生产XML，未独立证明其差异引起全部性能损失。

共享资源的有效服务依赖实际路径/并发模式，36.46GB/s不能作为全状态固定cap，因为同口A单流已有43.61。split每节点增加一个实际使用的NIC出口；后续基线应具备相同合法资源机会，不能将配置/额外网络资源改善冒充同预算placement贡献。生产keeper/整模型收益继续待桥接。证据为正式报告23节、diagnosis_audit_final_r02（263项独立通过）。
## 2026-10-07｜真实keeper桥接与外部布局恢复已可执行，算法增量未建立

1GiB合法DRAM小块生产路径单A/B/pair receiver RPC45.165/59.922/69.974ms；原worker耗时单43.646/58.527、pair68.169/66.078ms，支持应用链并发敏感性观察，但RPC含派发返回且无worker绝对monotonic，不升级物理共享归因。原staging/scatter/chunk不同于isolatedprobe，旧36.46GB/s不可作应用cap。

原r08冻结布局/caps/互斥target已薄适配到真实恢复；T2 fixed split_rank_prefix→target_b/fastest/interleave完整通过2.827939181s。物理缓存8GiB＝unique6＋duplicate2，实际命中6GiB；完整内容/生成/回收通过，不把未使用copy成本省掉。此为功能观察，非CPU策略对照。T1/T2/T3同候选、复制机会、预算、共用LP评价的强simple/joint均同解，模型估计仍provisional，没有新solver/重复策略GPU的必要证据。正式报告24节与keeper_bridge/audit_final_r01为依据。

### 2026-10-08｜拓扑输入不是物理事实：v0.1.23.3/task2

- 本部署取消NCCL_TOPO_FILE会实际进入vendor mapping，并非纯sysfs发现。GPU1实际NUMA3，builtin与默认mapping置NUMA1；empty system version2显式输入经四rank实验证明可使所选设备物理对齐。
- builtin同时提供gdr1；unset/empty实际gdr0并走non-GDR。拓扑真实性、GDR能力选择、实际性能必须分别验收；不能删除XML便宣布修复/提速，不能把vendor逻辑NUMA用于DRAM真实分配。
- 当前另有活跃vLLM占卡，unset正式组全卡free门槛失败但own PID退出和keeper保护通过。性能比较未完成，不灌入成本profile、不声明端到端收益；400Gb/s物理标称和mapping200Gb/s均不是测得容量。证据见正式报告25节和topology_alignment/provenance_r01。

### 2026-10-08｜资源地图与规划输入的证据边界

- 物理图可完整查询，不等于已有实测共享容量或可最优规划。资源总量、SMI Available、total-used、外部预算分别记录；共享PCI/HCA按唯一身份引用，aggregate微probe速率不能逐path复制。
- 自有XML静态8GPU/4HCA正确与runtime接受分开；本次新XML的控制组初始化未完成，不能认定XML格式错误。原厂XML保留。
- 当前node29 ICMP通但SSH/keeper HTTP超时，两个owned probe退出未证；地图live_status_claim=false，control/cleanup gate阻止用旧快照调度。后置保护未证不等于底本损坏。详见正式报告26节、resource_map/build_r04及xml_recovery_r02。

### 2026-10-08｜新进程初始化与已有keeper可响应是不同条件

- 本轮node27 keeper/sysfs查询快速，不代表新HIP运行时可用：metadata一次all-visible在hipInit10s报警，一次0/1在库加载阶段SIGSEGV，两种失败均早于XML认证，visibility唯一因果未证。停止GPU而不绕过准入。
- 正常RELEASED目录条目不等于底本损坏；完整base须VALID/lease0，历史状态前后守恒。70.33GB底本目录窄保护通过，不等于新读全权重SHA或完整postflight。
- sysfs total-used、SMI Available和HIP free不同，不能混为一个可分配预算；owner权限错误/零候选不能判断空闲。最新resume_map_r02仅追加失败/状态，物理与测量不变，最优规划尚未就绪。

### 2026-10-08｜node29服务异常跨来源复现，MaxStartups只证明SSH准入拒绝

- 27/28/30数据SSH成功；29主管理邻居不可达、数据IP/TCP可达但SSH/keeper无响应，由27与28双源观测一致。ping通不等于能运行远端实验。
- 11.211.3.29返回准确Exceeded MaxStartups，支持SSH未认证连接准入限制已触发一次；未获29登录身份、实际阈值/计数/来源，不作为全部HTTP/HIP故障唯一根因，也不据此改共享sshd配置。
- 模型NFS在28小读正常，项目/DTK/hyhal本机存储；当前无证据将纯本机HIP初始化失败归因于29失联。两个故障链继续分开，旧exactPID未证不清理。正式报告28节。

### 2026-10-08｜MaxStartups明确拒绝仍不足以唯一定位根因

- 11.211.3.29及12.11.3.29两单次SSH均认证前被拒，本机own SSH/mux及目标TCP22为0；没有可复用控制通道。local0不排除server残留/其他来源。
- OpenSSH8.9p1机制参考中全局或每来源准入均能返回同文本，且先于该新连接fork，不能仅凭它宣称全局上限满或本次fork失败。实际node29版本/配置/压力未知。
- 未取得server现场前保持两个故障链和旧pending不变。只读console入口已备，失败或权限不足count=null，TCP总连接不能当未认证连接数。报告29。

### 2026-10-08｜候选节点资源充足不等于fresh运行时可用

- 28/30物理快照各8GPU/8NUMA/4HCA已核；28 Docker实际root所在盘available0，30 fresh PyTorch导入成功但set_device未在总15s内完成，未达到copy/XML。三个候选节点的运行准入尚未齐备。
- 27新采集误读KFD vramtbl进入D，后续代码已排除；own child4037472/start477235183仍pending，不能把controller退出写成全回收。30init ownchild则精确TERM后absent，区别记录。
- 30日志hylog目录open失败与privateIPC/权限/环境差异属于待查条件，尚无唯一根因。sysfs total-used与HIPfree、标称400Gb/s与实测速率继续分层；本轮没有性能收益结论。正式报告30。

### 2026-10-09｜node28 keeper内存和磁盘分账

- 当前两自有容器持续运行，root86%/余59.97GiB；SizeRw合计163.64MiB，GLM共享memfd273.88GiB。keeper大RSS不等于根盘大文件，FD或RSS直接相加会重复计共享页。
- 自有/data文件837.30GiB在不同文件系统，盘54%/余784.54GiB；共享镜像逻辑32.21GiB也不可按容器重复计。宿主json日志大小无权限取得，不能完全排除历史日志原因。
- 旧28storage_blocked应按新df更新为可再次准入；本轮没有清理、停止keeper或认证GPU。报告31。

### 2026-10-09｜物理地图到实际IB路径闭环已完成，速率仍待校准

- node28/30选定GPU2/0及shca_0:1在自有gdr0XML下真实IBext_v8跨节点1MiB全SHA/设备物理路径/退出通过，原XML保持。物理输入全8GPU/4HCA每node，实机资格只覆盖选定端点，不把图完整当全部链路测试完成。
- 首次GPU进程18.6–22s能正常初始化，旧15s总期限会截断可完成的调用。超时首先是观察边界，需分阶段CPU/内存证据再判死锁或改配置。本轮没有权重可用时延性能提升结论。
- 必须复用既有海光librccl-net-shca.so显式插件配置；缺省会退Socket。日志前缀CUDAdev/globalrank/NVML物理dev为不同空间，应以PID/comm/BDF强绑定。vendor dump匿名空属性只对已观察GPU/NET做有偏移/SHA的严格派生解析，保留raw。
- 下一步真实路径带宽和共享竞争/NUMA页驻留校准，性能规划仍false；node30root满仍未修，只把小探针输出放私有shm。27/29旧pending不升级。

## 2026-10-09｜最小测量：运行时预算与论文指标必须分开

1. 新组合28NUMA0/GPU0→IBext_v8/shca_0:1→30GPU4/NUMA4已1MiB全SHA与实际XML/GPU/NIC通过。64MiB本地1.471437ms/45.6077GB/s，远端因果链5.494220ms/12.2144GB/s，各一次、共享环境有限观察；微测primitive与产品分块不同，不直接作为产品成本profile。
2. remote64 source观测DRAM增量2,149,457,920B，超过2GiB预算1,974,272B；正确性通过不等于预算通过。runtime/comm固定开销必须与权重块及buffer分开核计。memory.peak缺失，采样只给峰值下界，不能宣称完整物理峰值已核实。
3. 固定两块分别恢复到两个已知目标时，target_local_distributed与强nearest-target同解。人工集中/分散实验可以验功能及placement现象，不能算联合规划器delta。
4. synthetic replica先全SHA后发布是既有Worker协议。本轮分开buffer填完和verified collection发布；没有真实模型binding就不报告模型T_ready。finite工作点契约不等于已取得独占带宽/共享容量或已启用最优规划。
5. 产品r02两块16MiB同预算执行通过：buffer填完5.456453→3.956054ms，已校验发布69.624693→43.226580ms。发布窗差26.398113ms中24.897764ms落在校验/发布，不能全归因网络或泛化到模型启动。实际source/target采样增量2,104,721,408/2,070,065,152B在2GiB内，但比preapp投影高5,816,320/344,064B，说明buffer估算不是硬峰值界；当前fullphysicalpeak仍未验。

## 2026-10-09｜地图与放置的有限时间预言得到验证

四布局同2×16MiB HBM cache、同目标，A目标本地/B集中28/C集中30/D交叉实际路径均符合。filled4.203/5.813/5.669/6.908ms，A最短D最长与冻结预期一致；serial不是全局串行，A两local独立，D两pair通信依次执行。B/C0.144ms小差不能推出方向硬件优劣。

published49.881/69.844/67.461/46.628ms排序不同，D发布较快不意味着搬运较快；必须分离prepare、execute、验证发布。工作点elapsed包含等待，不存在本轮已经验证的恒定链路速率/精确绝对公式；完整模型/solver收益均未证。

## 2026-10-10｜有界决策关口的负结果

相同候选/预算/底本/来源机会下，两块等大、同目标worker、localcache只容纳一块的3个人造成本区间，现成strong_simple与joint重评服务代价相同；不同rank缓存或op先后签名不等于性能增量。不能为了joint收益削弱baseline或不断搜参数。

执行依赖评分service_cost.estimate_execution已经存在；managed只重评current/base/bandwidth-MILP提案，非serviceglobalopt。资源bound与worker有序DAG可错序的CPU反例是评分范围问题，非本集群真实误差；先复用已有组件/匹配原语独立校准，而非再造solver。本轮零新GPU/真实预测误差仍unknown。

## 2026-10-10｜理论联合必要性有三个来源

等大固定独立收益且单一基数预算下top-k最优；需要检验的耦合来自稀缺位置可替代性不同、多目标max互补、共享cut负载变化。并非GPU数量多就需复杂优化。

TP4理论实例Ti=8−3xi，任意proper subset整体8、all4整体5，原strictpositive singleton/paired三个control实际CPU停空，原joint得5。简单瓶颈均衡亦得5，后续必须补强此baseline，不能把理论例子冒作实机MILP优势。全缓存HBM例子的4/3最优需明确后备/relay动作，合法fallback一旦引入就可能改变下界。理论精确、硬件参数仍待验证。

## 2026-10-10｜TP4合同：共享出口改变缓存价值，均衡也需计空间

当前8本地候选的固定CPU模型中，C1不均预算[2,1,0,1]使rank2主目标下界2不可改，base-only0cache已同主评分；瓶颈均衡填16MiB只改善非关键rank。C2仅增加source0/1共享出口后，base-only评分4，原strongsimple与joint都用8MiB达到2；缓存价值随资源incidence改变，但暂无joint相对强简单的增量。均衡/填满的同T多占空间应判Pareto较差，不追求填满配额。

CPU容量分数与实际finite时序不同：共享流在local前序后才就绪可能让可达时间高于cut下界，单独无争用DAG也不足。4rank逻辑合同完成不等于真实模型/多worker预算已准入；runtime共享效应未知，原2GiB/node不自动扩。

## 2026-10-10｜稀缺位置竞争可解释，强交换已足解当前实例

在两pool各两槽、四块各可放两处的冻结symbolic模型中，pool0被三个内容偏好而只有两个槽；r0有温和替代，r2/r3更依赖pool0。备选非支配要求v>DRAM1、固定队列局部占位更差要求v<12/7；先推后唯一取v3/2，全部8候选具有实际共同route使用证据。原.5实例两候选neverused，负门槛保留。

同16MiB缓存下，逐块最快/单步均衡11/12，交换/joint5/6，差来自r0让出pool0给r2/r3，实际LP与预登记fullcache witness一致。原paired addition不含回溯swap；新的至多二删二加局部参考已与joint同解，因此当前只证位置联合考虑价值，未证MILP独有收益。数据仍理论单位、无实机速度/模型T_ready资格。

## 2026-10-10｜多层恢复研究的容量必要条件与实机边界

三node六GPU的有限场景已具结构资格。唯一需求W、HBM/DRAM总容量H/D时，限定完整SSD后备的来源集合中，至少max(0,W−H−D)逻辑字节从SSD提供；C3为32−16−12=4MiB。pagecache可命中，不代表盘介质读4MiB。C2a/b两helper确实跨node供给，后者仅改变共享关联；不能凭拓扑复杂或覆盖可行声称joint优于强交换。

已有SSD serial/CPUowner/Cluster与tier规划预算应复用。SSD底本重开/持久目录、SSD doublebuffer、旧ledger/descriptor多层薄适配是具体边界。原serial每worker64MiB HBM暂存是条件已知项，六worker384MiB不是actual allocation。原2GiB/node、三node物理资格与实际SSD映射尚未解决；历史单worker线性投影不是物理下界。后续先绑定真实资源和同原语cost，再测权重可用时间与空间曲线。最终合同r02，review_final_r01独立247项通过；无新实机性能结论。

## 2026-10-10｜当前资源资格不能永久继承旧限制

node27已跨boot、旧D进程不存在；新GPU0实机1MiB全SHA通过，旧失败不意味着当前节点永久不可用。但该单卡资格不等于六卡mesh/NUMA/SSD就绪。静态候选必须核owner：28GPU4实际旧keeper占用后改GPU1，30helper改GPU5；最终候选28 0/1、30 4/5、27 0/4，三节点不同NUMA组合可形成真实亲和性差异。NVMe父路径是28/30 data目录、27 home，不能照抄共享路径。

2GiB是旧双worker小实验暂定配额，用户批准三node六GPU后可新建有界预算；新DRAM6GiB/HBM2GiB每node聚合含runtime，旧失败不能重写。预算账本完整与完整峰值实测分开，首次功能不能被不可能先验要求永久阻塞。CPU原API+HTTP协议验证不当GPU性能，可信物理准入仍需当前live证据。下一步核六worker/selectedpaths/NUMA/三SSD底本，正式目标仍是空间—权重可用时间，不以图大或79checks代替收益。

## 2026-10-10｜复杂三节点多层布局已在原执行器兑现

23条必要路径、六GPU/六NUMA注册与三个本地NVMe服务实际完成四配置32MiB全SHA恢复，两个helper与C3必要4MiB SSD来源均真正参与；不再停留于CPU合同或单节点微测。三node不同NUMA亲和性在同gdr0/shca0/原serial路径下具功能资格，其他pair/HCA共享容量仍未知。

原native事务filled约132ms(SSD)、73/71ms(两纯内存配置)、81ms(混合)，每配置1次且预算不同，不认证统计排序/联合收益。约62ms inventory在小载荷占主导，诊断与外部gate/模型handoff边界需要先明确，不能拿execute或replica published冒称完整模型T_ready。save从SSD建fixture不是实际暂停保活成本。空间仍分暂停配额与runtime/目标/临时量；DRAM样本和同HIP绝对upper均在本轮cap内，跨sysfs/HIP taskdelta不是精确计费，完整peak未知。下一步同预算强基线与必要共享竞争工作点，为主指标服务，复用已通平台。

## 2026-10-10｜总窗误差小不等于布局选择可靠

原六GPU run是frozen_witness、prediction null，没有当前已校准成本模型。容量与ready依赖可解释观测，旧八worker两路径service参数不能直接迁移。固定4MiB资格lookup事后诊断的执行误差−22.60%至+19.55%，加约62ms控制项后filled误差约±4%；共同项掩盖数据服务误差，C0/C1预测与n1观察细排序相反。应同时审成本分项、排序与真实主指标价值，不用低总窗百分比替代planner决策验收。

C3长尾来自w4先接b5再从SSD转b7，b7 source/target两个计时窗口重叠且含等待；SSD每腿未打点，不归因纯盘带宽。下一步复用现DAG和fit_model，先控制段/主指标口径，再关键路径2size/必要equal-byte batching及一个独立holdout；shared容量另测必要并发。无新GPU证据，本轮不声称完整模型或solver收益。

## 2026-10-10｜查询开销主因已实测，控制收益和模型资格须分别判断

报告43的新实机定位约62ms查询主要来自实验诊断重复查库/CDLL。仅缓存PID内库路径/句柄，动态字段仍每次读取，查询median60.576→6.190ms；同36MiB完整filled156.026→42.207ms、校验发布228.260→114.392ms。execute约23ms未变，两次查询解释约98.51%总差量。它提高当前实验执行效率，但不是拓扑placement/搬运算法贡献，且新覆盖层未全局写入产品。固定顺序每模式一次，不宣称统计加速；filled未校验发布，published未live模型handoff。

local HBM4/16MiB的规定两次均值.381/.277ms，仍不符合正字节斜率仿射式。完整fitter拒绝，冻结model/prediction=null，absolute_prediction_ready=false：独立验证流程存在不代表预测验证已完成。remoteDRAM约10.187GB/s、SSD约.918GB/s仅warm有效服务估计，非物理裸带宽。下一步为小local快路径选择有适用范围的固定/分段成本并独立验证，避免无限重复制造正斜率；共享容量和布局收益仍需各自证据。

## 2026-10-11｜有限域模型够做粗候选筛选，尚未证明布局决策收益

local4–16MiB小快路径不必强拟正带宽：旧四单op service平均.280796ms，保留byte slope未知和原负值。新8MiB实测.268654ms，+4.52%且在旧经验范围。远端DRAM/SSD原拟合与现ready DAG组合后，mixed新24MiB的maxworker/execute/fullfilled误差−3.68/−5.77/−7.23%；先冻结后实测且无新训练，支持有限域新会话迁移的初步可用性，非全域/统计精度保证。published字节/对象成本未建模，预测null、实际88.079182ms保留；mixed纯service不能由重叠含等待的双端窗口相加获得。

mixedexecute绝对低估.997928ms，与粗remote−local .913433ms同量级，现证据不能保证亚ms细排序；单次误差不是未来界。SSD−DRAM粗差10.893513ms值得优先检查，但当前源身份/目标不同，不是同任务替代布局。三条path都经w4，排列同分，不产生复杂规划独立价值。下一步先同内容/target/HBM-DRAM预算的两个合法layout合同和CPU评分，未知path仅按决策需要测；同分保留，优先已有简单/交换策略，不重造solver或追极低预测误差。最终独审96checks过但decision_ready=false，区分模型验证与论文收益证据。

## 2026-10-11｜独立请求总时差不能直接解释为HBM字节斜率

负斜率旧四点保留；新native没有稳定复现、1.037ms长点不删。真实host-only同handler同ptr4/16各双轮：first .346575/.289744ms负size差，second .089062/.123432ms正差。source_slice CPU首/次下降77.210/61.481us（发生在copy前），log与copy提交也下降，首轮非sync主机差−58.521us盖过sync+1.690us；后轮sync差+35.400us主导正size差。这提供首操作host成本能掩盖字节完成项的证据，不是具体TLS/驱动函数或旧每点的唯一解释。copy/sync第二轮另有firsttouch/cache变化，不能直接推裸设备带宽。

模型应按真实worker/handler状态区分首操作、后续每op、字节/目标状态完成成本，组合仍沿readyDAG。增加首项须从旧含日志/首次切片/提交的service拆出，原request_s=.050530ms只是execute-record外残差，不是首调用项；否则双计。原local常数.280796ms只有限单op经验，非物理平台期/多op每块恒定费用。所有诊断工作保留完整请求账目，两轮均计时，不把第二轮暖时间冒称首次恢复提升；旧模型未改。详见新article及报告45，下一步先契约，停止无界补测。

## 2026-10-11｜先验证状态重置规律，复用Janus能力而不重复底座

报告45支持同handler首ophost额外成本，但thread/request/target/log状态尚共变。新A8+B8设计固定8MiB，用实际F/P线程×prepare独立预写目标/scratch控制，只有状态预言和独立execute/full价值保持才准入新state模型；无效或与共享request同构则选简单。当前是待实施计划，不能将16请求当已测，不能固定size拟带宽或把旧bounded扩域。

Janus不是缺少保活：完整host cache、LIGHT/DEEP、活跃实例D2D、NUMA亲和/隔离和NPU rolling已有。源码具体hooks仅NPU，generic缺项fatal/remoteNPUonly，xTensor池份额归还不等于外部devicefree；所以原版并非加拓扑图即海光可用。我们已有Janus式来源策略及HIP/Catalog/owner/多层执行器，优先保留，必要时做逻辑extent/内容版本/ownerlease/目标commit薄adapter。论文增量应在有限不均预算与共享通信下的块驻留/恢复决策，而非再造缓存、IPC或多模型服务调度。Janus实际4线程池轮转不证明每线程已暖，跨runtime参数各自认证。

## 2026-10-11｜论文主体与复用方向

FluidCKPT始终为系统主体。按其实际组件缺口审Janus具体成熟代码，必要实现直接摘取/适配进入Fluid；不改造Janus多拓扑、不替换现底座、不以Janus整体海光运行作为先决条件。现有能力满足需求时保留，迁入须有正确性/主指标/维护收益证据。

当前已经有功能底座和历史真实QwenTP2闭环，最新复杂拓扑验证为三节点六GPU合成TP4块恢复。资源/对象/规划/执行/模型/评测组件存在，不等于当前复杂场景同预算联合决策收益和真实模型主指标已证明。重点是可信成本与预算拓扑约束→合法不同布局→真实权重可用收益，而非再建缓存/通信或外部系统全面移植。

## 2026-10-11｜首操作存在不等于可按统一thread/request首项建模

A8真实F/P线程×目标fresh/pretouch完成，所有首copy非logCPU/wall差额为正，但P R2仍有30.440/187.743us差，F−P198.243/47.420us不支持request等价，P目标/顺序交互超预登限额。S5us、stateunidentified，按协议不开B、不调门槛。不能否认首项现象，也不能假设持久线程足以清除它或预写导致更慢。

同A16op C0/C1固定两copy总record预测都411.619125us，内部成本分解更细不等请求总预测更好；三候选仅训练诊断，frozenfalse/预测0，没有独立accuracy/模型升级。旧bounded有限域资格保持，新实验域外null。新状态项是否值得建仍服务于可辨布局差量和真实权重可用，不让微实验分支无界扩展；未解释波动尺度内不认证细排序。

## 2026-10-12｜在线预测10%目标仍待实测，物理释放必须独立验证

用户明确接受代表性线上weights_ready整体误差约10%，达标后转向规划器；旧微项精度门不沿用。新两节点真实Qwen TP2链路已推进到完整源构建，但尚无pathcal/C0/留出，不能引用旧synthetic误差充当当前资格。

park RPC成功不证明权重显存释放：r02两rankallocated均36,496,468,480→36,496,468,992B，几乎未降。实验wrapper重复导入同一_share_cuda_计数凭证是代码级所有权错误；源码支持双减/下溢风险，修为唯一映射内可选SHA。其实际释放效果及唯一运行时归因待小IPC实验；该探针在创建源tensor前因外部资源占用拒绝。必须区分静态错误、CPU修复、真实物理释放和预测精度四层证据。

不得用更晚出现的GLM作业解释更早没有保存具体卡/数值的资源失败。失败必须在抛错前保存完整实际事实；阶段末尾才写总结果会丢失关键证据。两个自有容器已停止，无后台实验。完整依据见docx/experiments/2026-10-12_online_ready_cost_validation_report.md。

## 2026-10-12 18:52｜重复IPC导入滞留已实证，完整权重物理释放已验证

32MiB同一share凭证一次导入后allocated归0，重复两次导入后保留全部32MiB；独立producer控制对照实际完成，每配置一次。这证实当前部署重复消费的分配滞留后果。实际Torch2.10，旧probe读counter漏allocator头部，数字1不能证明IPC槽或下溢；未来默认未知。

单映射修复后完整Qwen TP2每rankallocated从36,496,468,480降到1,315,722,240B、整卡free增35,213,279,232B；70,329,718,112B全SHA、66个DRAM区域保活通过。由此物理暂停已验证，不再是只有CPU资格。局部总诊断356.879s不是恢复时延；线上cal/C0/留出仍0，10%准确率未知。远端资源不准入时先推进独立正确性前提有实际价值，不需以抬预算强行跑完整实验。实际原件和独审见在线ready报告第7节。

## 2026-10-12｜完整在线预测已验证：主要偏差是外层迁移，非搬运误差

简化两节点三GPU单sender的真实Qwen70.33GB完整恢复已跑通，SHA/参考输出/physpark/源NUMA/冻结与回收过。C0online3.28749s；Hnear1.31220s、预测1.59697s（21.70%失败），Hremote4.56026s、预测4.95703s（8.70%通过）；整体10%未认证，排序正确。手工布局差3.24806s不是联合solver收益。原DAG误差仅9.99/7.47ms，C0共用外层残差.80803s迁移贡献总偏差96.49/98.12%，下一步应校准必要控制/接管窗口，不继续纠链路带宽小误差。

r01两点正斜率假设被实测反例拒绝，r02预先登记实际path×1GiB/尾块服务表与每path一次warmup，没有逐layout常数/插值或反算裸带宽。只认证当前注册尺寸/同incarnation简化域。资源门必须区分whole-cardfree与task allocation；本次helper有foreign占用仍运行成功，最低free6.981GB、reserved最大69.206MB，完整driver归属未知而非伪造2GiB独占证明。全部自有任务已回收，后续复杂拓扑另行准入。完整依据见simple_two_node_online_report与review_simple_actual_r02。

## 2026-10-12｜后续恢复域整体10%通过，停止精度补测

相同常驻controller、首次完整恢复单列之后，只用一次C0_cal校准共同outer=.423580s，原12path-size成本全部保持，两个事前冻结留出remote预测4.572579/实测4.581127s（−.187%）、near1.212516/1.290400s（−6.036%），73项独审通过。首次3.024893s完整报告但未认证其预测；资格限两节点单sender、固定模型/extent的post_one_fullrestore_samecontroller。旧.808030s外层迁移不适合后续请求。新的证据支持明确运行状态后统一校准，而非唯一证明全部first-call机制。

Hnear反序后仍有IPC接管增量，整体误差已在用户10%门内，无需继续微项精调。共同outer校准不会改变候选布局预测差，所以准确率改善不是solver或加载算法加速；下阶段用同预算、同ready口径检验强简单策略与现有规划器的实际选择收益，必要在线规划成本计入。复杂三节点、多NIC/SSD、新controller和首次恢复另验，当前不能外推。完整依据见outer_online_calibration_report及outer_analysis_actual_r02。

## 2026-10-23｜恢复记忆应围绕已验证域与下一步决策价值

最新性能证据仍为10/12单控制器四恢复，remote0.19%/near6.04%，有限后续域达到用户10%目标，正确性和退出完成。10/23仅归档本地证据，不更新硬件资源事实。共同outer校准改善绝对预测但不改变布局排序；论文下一步需同预算、同候选机会、同执行器下验证规划器对真实weights_ready的增量，必要在线规划/准备成本计入。

已有三节点synthetic和旧GLM真实多节点分别保留、优先复用；旧GLM owner/服务时间线不当当前现场。归档后的恢复不要重复A/B首状态探索、IPC小实验或已达标四恢复，不用旧“未过”快照替代当前结果。资源恢复再按候选决策必要性扩复杂域。用户授权此次覆盖history，旧交接已另存，当前编号仍.3/task2。

## 2026-10-23补强｜恢复成本必须同时说明公式与实际计时边界

本次只读源码明确当前主窗从inventory之前起计；恢复前park证据/资源门/command文件发布发生在begin之前。未来完整请求和planner比较需统一分类必要前置项，现有10%通过限实际登记窗。该澄清不改变旧预测/数据，也不把排除项视为零成本。

七段同主机分区严格相加为T；rank内部并行窗口不能再叠加。共同拟合O=Tcal−Dcal，实际外层T−W，误差恒等式T_hat−T=(D−W)+[O−(T−W)]区分参数偏差与实测剩余。Hnear在新第四次完整恢复仍有较长映射，旧它是第二次完整恢复/第一个留出；顺序单因果未识别。详见新增在线校准过程专题第5–7节。
