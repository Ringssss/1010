# Insight Ledger

## 2026-10-23 补强：计时窗口和分解层次必须落实到代码

重新核simple_controller.online确认parked_evidence/gate/command先于begin，inventory/plan/真实resume/transfer/完整ACK在内。已有10%资格对应实际登记窗；未来论文完整请求比较须统一判断前置必要项。七段同主机相邻分区由8个边界形成并精确加和；每rank并行内部窗不能重复相加。校准O=Tcal−Dcal与实际剩余T−W不同，T_hat−T=(D−W)+[O−(T−W)]闭合。详细证据见新过程专题第5–6节，保持旧数据和资格不回填。

## 2026-10-23归档保留：状态一致校准可服务整体主指标

10/12同controller四完整恢复的唯一后续C0校准，将共同outer从.808030s更新为.423580s，原12路径点保持，两个留出−.187%/−6.036%通过整体10%。这支持当前后续域的绝对时延预测。共同常数不改变布局差/排序；下一阶段必须用相同预算下的真实布局选择与恢复时间证明规划收益。首次单列3.024893s、首次预测未知；Hnear仍多约80ms接管但整体达标，不继续无界微调。

资源状态是带时间的证据：10/12精确容器停止不代表10/23集群空闲；历史GLM真实多节点与本主线synthetic/两节点Qwen分别保留。归档保留失败和旧原件，避免恢复后把新代码、新资格套入旧测量。

## 2026-10-12：局部误差改善不自动变成完整请求预测价值

八请求成本账本显示只按N会漏多op请求开销；事前三项N/K经验候选在两个新会话留出点worker误差+.45/−6.16%通过，但filled改善不足冻结观察尺度，未晋升完整模型。V1误差主要库存/准备前段，原件已定位窗口；不是需要继续给DMA拟更低误差的证据。四项C1不准入不能因C2准而重选，service50.020833us略超50us仍按协议拒绝。见成本报告2026-10-12和记录49，阶段完成于八请求，不加样本救模型。

## 2026-10-11：成本的可计账性先于参数可预测性

prepare/execute/op/chunk是嵌套计时，worker/endpoint可并行，不能直接求和；闭合残差可观察但机制仍可能未知。N/K/C设计秩4仅必要条件，未解释状态和chunk大小分布仍可能导致拟合失败。有效path吞吐量与物理容量上界分开、资源界与DAG分开，规划才不会把重复等待或伪带宽当收益。证据：计划18.33/记录48，cost_contract_design三报告；本轮仅设计/CPU，不改变A unidentified或旧bounded资格。

## 2026-09-29续作：managed04

| ID | Insight | Evidence | Status | 含义与限制 |
| --- | --- | --- | --- | --- |
| I23 | GPU空闲与邻近NUMA可容纳底本是两个条件 | `cluster/managed03/summary.json`、`cluster/managed04/summary.json`中的numa_preflight | Supported | NUMA2仅25 GB、需35.16 GB；按容量改位后成功。预选不等于系统预约 |
| I24 | 单目标自动联合控制层可在零临时副本预算下完成布局切换 | `cluster/managed04/{placement_plan,placement_journal,summary}.json` | Supported | 固定完整底本，14动作，提交后中断/续作通过；非全局原子/节点崩溃证明 |
| I25 | 放开每卡8 GiB后，本例模型仍选择均分并有实测搬运改善 | `cluster/managed04/managed_analysis.json` | Supported | 400候选gap=0；旧/新传输1.37985/1.22130s。每种一次，未另跑集中布局实测 |

I22的“未实施”仅指此前归档时；managed04已实施并验证单目标、完整底本保护版本。

## 2026-09-29新增洞察：task2联合规划

下方I1～I13为历史、特定环境证据。尤其历史聚合205.8 GB/s不能直接当当前两路恢复的资源容量；历史GLM平台结论不能外推为当前Qwen系统阻塞。

| ID | Insight | Evidence | Status | 含义与限制 |
| --- | --- | --- | --- | --- |
| I14 | 主指标应为全部目标权重可用窗口 | `main_lib/fluidckpt/serving/measure.py`、task2各summary | Methodological insight | 同主机monotonic，不混入进程/HTTP/生成/全量校验 |
| I15 | 控制JSON与首次准备会显著影响加载 | `cluster/control01/control_analysis.json` | Supported | 2.305→1.955s主要控制收益，不能归因LP |
| I16 | 快卡等慢卡不能重复算控制瓶颈 | `cluster/profile01/profiling_analysis.json` | Methodological insight | 正常serial搬运1.5985s，拷完收尾28ms；旧约0.25s包含同伴等待 |
| I17 | 双缓冲正确不保证当前端到端加速 | `cluster/pipeline01/pipeline_analysis.json` | Supported | 2.1058/2.0901s各一次，未证明显著改善；event重叠非DMA占用证明 |
| I18 | 定向路径源端H2D＋通信衔接值得优化 | `cluster/bulk_profile01/profiling_analysis.json` | Supported | 8 GiB每rank约0.183s H2D＋0.13s通信kernel；原run指纹failed，仅诊断 |
| I19 | minimax目标使逐副本贪心出现零边际平台 | `cluster/placement01/placement_solutions.json` | Supported | 本例单副本贪心选0，成对/MILP同值；不宣称所有场景均如此 |
| I20 | 恢复感知placement在当前同预算对比有搬运收益 | `cluster/placement01/placement_analysis.json` | Supported | 1.3618→1.2555s；完整15%混有准备差异，每种一次 |
| I21 | 模型内最优受候选与先验容量约束 | `cluster/placement01/placement_case.json` | Methodological insight | 每卡4 GiB是预设，不能据此证明允许单卡8 GiB的集中方案更差 |
| I22 | 保活终态容量与迁移过程容量是不同约束 | `docx/plans/2026-09-29_task2_joint_planning_control_plan.md` | Methodological insight | 下一阶段必须规划峰值临时空间与完整覆盖；尚未实施自动控制器 |

## 历史洞察与方法规则（保留溯源）

本文件是 FLUIDCKPT 机制层面洞察的 canonical 活账本。每条洞察记录机制、数据来源、状态与优化含义。
新实验若改变某条洞察的状态，**优先更新本表**，再在 `code_building/key_conclusion.md` 追加最高层结论。

## Method Notes

- 每条洞察必须有**可查证的数据来源**（具体结果文件或日志）。
- `Status` 使用下方 legend 的等级，不得自创。
- 被推翻的洞察**不删除**，改为 `Rejected` 并注明推翻依据——历史误判本身就是信息。
- 方向选择前先读本表，避免重走已排除的路。

## Insight Table

| ID | Insight | Mechanism | Data Source | Status | Optimization Implication |
| --- | --- | --- | --- | --- | --- |
| I1 | per-tensor 逐张量集合通信被固定开销主导 | 每次广播有 ~99.3 µs 固定成本，与字节数无关；256 次广播 = 25.43 ms 仅传 1.32 GB/s | `results/bw/bucket_27_29_r8.json`、`codex_coding/src/bench/bucket_bw.py` | Strongly supported | 必须 bucketing：同样字节打成 bucket 后 0.91 ms / 36.73 GB/s，**27.8×** 提升。这是对 SGLang 逐张量远程加载的核心优势 |
| I2 | 跨节点 fabric 只支持 collectives | 跨节点 send/recv 报 `ncclSocketInit: connecting to address with family 0`；collectives 正常 | `results/diag/`、`articles/2026-09-27_multinode_transport_findings.md` | Strongly supported | 传输层设计只能用 broadcast/allgather/reduce-scatter，不能依赖 point-to-point |
| I3 | 跨节点聚合带宽远高于单流 | 单流 25.7 GB/s；8 rank/节点 N 流聚合 205.8 GB/s（64 MiB 时 230） | `results/bw/multi_rank_27_29_r8.json` | Strongly supported | 规划器应按聚合带宽估算，且应尽量多流并行；单流的 25.7 会严重低估能力 |
| I4 | 单节点 tmpfs 装不下一个节点的 TP shard | `/dev/shm` 252 GiB < 8 rank × 44 GB = 352 GB | `df -h /dev/shm`（三节点）、`results/model/glm53_tensor_sizes.json` | Strongly supported | per-node tmpfs 持久化不可行；保活必须落在 keeper 的 pinned DRAM |
| I5 | gfx936 上 SGLang 的 fp8 index cache 恒不可用 | `is_hcu_native_fp8_supported()` 要求字面量 `gfx938`，gfx936 恒 False → `index_k_buffer_dtype` 恒 bf16 | `sglang/srt/utils/common.py:180`、`memory_pool.py:2179-2191` | Strongly supported | 这是 **GLM-5.3 无法启动的根因**；且 NSA 两条 HCU 分支共用 `_get_q_k_bf16`，翻 flag 无解 |
| I6 | 权重字节高度集中在大张量上 | 118,629 张量，其中 58,776 个（8–16 MiB）占 **97.9%** 字节；中位数仅 12 KiB | `results/model/glm53_tensor_sizes.json`、`src/transport/glm_histogram.py` | Strongly supported | 小张量（~60,000 个）字节占比极小但个数极多——正是 I1 的受害面。bucketing 策略应优先合并它们 |
| I7 | HIP IPC 可零拷贝跨进程交接权重 | `t.untyped_storage()._share_cuda_()` 返回 8-tuple，consumer 用 `_new_shared_cuda(*h)` + `set_()` 重建 | `src/transport/ipc_probe.py` | Strongly supported | keeper 与推理进程间不需要经过磁盘或 host 拷贝；consumer 峰值 HBM 仅 0.01 GiB |
| I8 | spawn 启动模式让 import 期 hook 覆盖所有 worker | SGLang 用 `mp.set_start_method("spawn", force=True)`，launcher 的 `__main__` 在每个子进程重新 import | `src/serving/sglang_fluid_launch.py`、`main_lib/fluidckpt/serving/sglang_hook.py` | Strongly supported | 可以在**不修改 SGLang 源码**的前提下给全部 16 个 worker 装 hook |
| I9 | 冷启动时间绝大部分与权重无关 | 122 s 端到端中权重读取仅 14.24 s（12%）；import 链 29.17 s（`sgl_kernel` 单模块 self time 14.47 s）、`Load weight begin` 前 41.2 s | `articles/2026-09-27_glm53_startup_vmfault_forensics.md` | Supported（Qwen3.6 TP=2 单一测量） | **修正过**：早期「保活只省 12%」的框架已被用户纠正。保活的正确定位是与「进程常驻」叠加，省掉整条 `disk → decode → shard → quantize` 路径 |
| I10 | NFS 是数量级更慢的通道 | NFS 0.27 GB/s vs 本地 NVMe 2.5 GB/s vs H2D 46 GB/s vs 跨节点聚合 205.8 GB/s | `articles/2026-09-27_node27_resource_profile.md` | Strongly supported | 多节点权重分发绝不能走 NFS（GLM-5.3 量级会是 47 分钟级）；必须走 IB + keeper |
| I11 | node27 的 H2D/D2H 是对称且远快于本地盘 | H2D 46 GB/s、D2H 47 GB/s，而本地 NVMe 仅 2.5 GB/s（**约 18×**） | `results/profile/`、`articles/2026-09-27_node27_resource_profile.md` | Strongly supported | 在 DRAM ↔ HBM 之间流动远比落盘便宜；SSD 层应只作为「无其它副本时」的兜底 |
| I12 | 单节点资源模型在特定区间内精确，越界即失效 | 冻结的 V9 模型（`model_fit_node27_frozen.json`）在 held-out 上 median 4.4%、max 8.9%；但 V10 的「共享 PCIe switch 容量」假设被否决（拟合给它零容量，且 h2d + NVMe 合计 52 GB/s） | `results/model/model_fit_node27_frozen.json`、`articles/2026-09-27_glm53_model_freeze.md` | Supported（区间受限） | 规划器必须停留在单节点 root port、无 NUMA fan-out 的区间内；**同一 root port 跨两个 NUMA 节点会塌陷**（h2d 掉到 29.5） |
| I13 | core dump 可单次耗尽整个 `/home` | `core_pattern=core`、`core_uses_pid=1`，内核对每个崩溃 GPU 进程 dump 整个显存；8 卡 = 8 × 18–22 GB ≈ 180 GB | `results/logs/core_inventory/core_list.txt`、`df -h /home` | Strongly supported | 跑 GPU 实验前必须 `ulimit -c 0`；2026-09-27 实际发生过（203 GB，一份 core 写到一半被截断） |

## Status Legend

- **Strongly supported** —— 多项独立测量一致，机制清楚，可直接作为设计依据。
- **Supported** —— 有实测支撑，但样本或条件受限（如单一模型、单一节点）。
- **Promising, needs broader validation** —— 机制成立，缺更大范围验证。
- **Revised downward** —— 曾被高估，实测后下调（须保留原判断与下调依据）。
- **Rejected as primary path** —— 已排除，不再作为主路线投入。
- **Methodological insight** —— 关于「怎么测」的洞察，而非系统本身。

## Source Index

| 来源 | 覆盖洞察 |
| --- | --- |
| `codex_coding/results/bw/` | I1, I3 |
| `codex_coding/results/mesh/` | I1, I2, I3 |
| `codex_coding/results/model/` | I4, I6, I12 |
| `codex_coding/results/profile/` | I10, I11 |
| `codex_coding/results/diag/` | I2 |
| `codex_coding/results/logs/` | I13 |
| `codex_coding/src/transport/ipc_probe.py` | I7 |
| `codex_coding/src/serving/sglang_fluid_launch.py` | I8 |
| `docx/articles/` | I5, I6, I9, I10, I11, I12 |
