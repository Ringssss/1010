# 在线权重可用成本：初审最终版

开发编号：v0.1.23.3 / task2；日期：2026-10-12。新增 GPU/SSH/性能样本均为0。本目录为最终初审，`review_initial_r01` 保留前版审查，不新增硬件样本。

完整边界、Live/replica 校验区别及可复用入口详见 `../review_initial_r01/report.md`。本版在同一8个历史真实 Qwen请求上补充直接读取冻结 session 的模型源 manifest 与32 token参考文本，独立核对完整目标 identity/extent/rank、实际输出文本等于参考，已全部通过。`sources.json` 还冻结本次脚本本身；所有输入读取前后 SHA 一致。

## 本轮认可的成本口径

按每代表性复杂拓扑留出场景完整在线 weights_ready 相对预测误差≤10%验收；细项没有独立微秒/相对误差门，也没有复杂度升级门。旧账本结果保留历史，不重判。

单主机 TP2 引擎可复用 `measure.summarize` 的 `max(weights_ready)-min(load_begin)`，包括 materialize/export、等待控制与实际传输、原子 done handoff、device sync、TP CPU barrier 和解除 offload。必须另存同 coordinator 请求开始→全部 resume ACK。若必要 inventory/plan/prepare 位于首 rank load_begin之前，必须以完整请求窗为主或调整安排；不能删掉这些工作后仍称完整在线。未校准跨主机时钟不相减。

新建 Catalog replica 的全内容 SHA 必须在 VALID 发布之前。只含 live 引擎目标的 plan 可按既有 `transfer(..., verify_before_ready=False, retain=True)` 将实验全量 SHA 放在真实 ready之后；身份/layout/epoch/generation/源lease/全覆盖/字节/同步/发布检查仍在在线路径。ready后的全 SHA与确定性参考输出必须通过，之后finish/release/owner清理，失败样本不得计入预测资格。

## 主 agent 的新预算候选审查

以下为尚未冻结、尚未现场验收的候选，不授权启动GPU，不修改旧微请求预算：真实 Qwen TP2，目标node27 GPU4/5，辅助node28GPU4、node30GPU4，总4worker；每节点任务 DRAM96GiB，容器硬限128GiB；目标48GiB HBM/card、96GiB/node；每辅助节点HBM2GiB。

| 预算 | 必须覆盖 | 审查结论 |
| --- | --- | --- |
| DRAM96GiB/node | 完整保活底本、额外缓存、所有本任务owner、engine/worker host运行时、host staging、scratch、bootstrap必要临时拷贝 | 单底本约65.5GiB，总账可以容纳；若同节点叠加第二完整底本则不成立。源分片或流式建立须记录实际峰值/观测上界 |
| container128GiB | 容器包含的上述所有物理host内存及运行时 | 只是硬边界，不能替代96GiB任务准入；已有8GiB微请求容器不能直接承载新任务 |
| node27 HBM48GiB/card、96GiB/node | 每rank约32.75GiB模型存储，加KV、engine/runtime、RCCL、staging、缓存、bootstrap临时开销 | 有静态容量余量，当前现场可用量与完整峰值待核对；IPC共享同一storage物理背书只算一次，不能把两个view当两份模型存储 |
| helper HBM2GiB/node | 本任务worker+RCCL+staging+保活缓存+临时source GPU存储 | 不足以在helper加载完整Qwen rank。捕获必须在目标GPU上做，或流式建立host分片；捕获/建立开销单列且保留其资源上界 |

每一项还须明确“本任务物理占用”与“整卡物理已用保守上界”的不同字段。准入时保留背景真实占用与可用headroom；不得扣背景以通过原本按绝对整卡已用定义的门。固定node/card合计预算、各容器cgroup、源与目标build/readiness/cleanup期间账目一致，不把reserved取代实际同HIP卡占用。端点采样只能声称观测最大，连续峰值未测不能声称峰值通过。

## SAFE metadata 可复用边界

`bounded_cost/execution/common.py:33 quiet_readback` → `multitier_runtime/operations/control.py:32 readback` → `host_readback.py` 是当前可复用纯 metadata 入口：固定node27/28/30、host、boot与精确容器；Docker GET和proc/sysfs读；不加载torch、不调用GPUAPI、不执行engineRPC。node27GPU4历史BDF为85/NUMA4，GPU5及新辅助GPU角色仍需fresh metadata。

末端刻意跳过node27 VRAM/KFD容量查询，因此不能用metadata宣称GPU4/5现在空闲。node28/30读取sysfs VRAM及数字KFD owner；node27需要主统一协调的本任务同HIP bounded resource gate。旧 `basic_scenarios/inspect_runtime.py` 调用hy-smi，不作为纯 SAFE metadata来源。

## 验证与未定项

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/audit_initial.py --out codex_coding/results/v0.1.23.3/task2/online_cost/review_initial_r02`。本地独立核对8个历史live完整模型请求通过。每请求完整目标70,329,718,112B；完整coverage/identity、执行次序/字节、每op全量SHA、同主机两rank原件T_ready、32token准确参考文本均通过。该命令输出拒绝覆盖。

当前预测资格仍false：新现场角色与容量、真实模型底本部署、新预算冻结、最终主时间窗口、有限代表场景校准与独立layout留出协议尚未齐备。对相同layout和相同workpoint再测一次仅是重复验证，不作为planner/layout泛化资格。源版本/模型/布局/路径/大小域必须在留出前冻结；结果超10%时保留失败，不回填控制残差或按看到的误差改范围。
