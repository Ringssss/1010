# 真实在线权重恢复：复用审查与最小接入

日期：2026-10-12；v0.1.23.3 / task2。本报告仅基于本地代码和历史结果；没有 SSH、GPU 初始化、在线服务查询、模型权重全文读取或进程清理。源码指纹与模型规模见同目录 `audit.json`。当前硬件可用性须由主 agent 重新准入。

## 结论

已有真实 Qwen TP2 在线恢复链路，无须新建加载器、keeper 或取消副本校验。最小新增内容是独立实验入口：显式绑定新会话、真实模型完整目标、物理 GPU/NUMA、布局及预算，把控制器恢复请求至所有引擎确认返回作为主指标。在线计时之后必须执行全量 SHA 与确定性输出检查。逐场景完整主指标的相对预测误差不超过 10% 即足够；旧微实验细项信号门与复杂度升级门不沿用。

## 可直接复用的入口

| 入口 | 已有职责 | 本轮用途 |
| --- | --- | --- |
| `main_lib/fluidckpt/serving/lifecycle.py:16,48` | 空闲检查、权重转 meta、恢复 materialize/IPC、设备同步、TP CPU barrier、weights_ready | 同一初始化引擎暂停与恢复 |
| `main_lib/fluidckpt/serving/sglang_hook.py:109,229` | 完整 postprocessed 参数/buffer/plain attr 清单、IPC 目标导出、等待 .done | 原加载与目标 handoff 保持 |
| `main_lib/fluidckpt/serving/keeper.py:94,537` | HbmTarget 身份/layout 检查、HIP IPC 导入、按逻辑 extent scatter | 将 packed 副本写进真实模型 storages |
| `main_lib/fluidckpt/cluster/worker.py:133,217,306,323` | epoch/generation lease、目标映射、真实复制/RCCL/staged SSD、SHA、complete | 每次完整 live 恢复、计量、事后校验 |
| `main_lib/fluidckpt/cluster/control.py:105,125` | 全操作 SHA 合并；单控制器 prepare/execute/complete | `transfer(verify_before_ready=False, retain=True)` 仅 live 目标 |
| `codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py:175,189` | 异步 resume、export wait、在线重新库存/规划、请求 ACK 计时、事后全 SHA | 复用流程，以新最小入口参数化 |
| `codex_coding/src/task2/cluster/run_lifecycle.py:204,213` | 生命周期真实目标构造与恢复流程 | 原生控制动作复用 |
| `codex_coding/src/serving/sglang_fluid_launch.py` | TP spawn 内安装加载/测量/生命周期 hooks | 原启动器原样使用 |

`keeper_bridge` 仅真实 DRAM→受限 HBM replica，未绑定模型；不能作为在线 weights_ready。历史 basic 场景主指标为同机 max(weights_ready)−min(load_begin)，已另存 `request_to_ready_ack_s`。本轮后者升级为主指标，rank 局部窗口仅诊断。`serving.measure.summarize` 明确拒绝跨 host monotonic 相减，必须保留。

`ClusterWorker.complete` 对新 replica 要求 `verified_rows` 和 verified 标志，维持 SHA 发布门；只有完整 live 目标可在复制同步成功后产生 .done。故此处使用已有合法 live 协议，并非绕过 replica 发布。

## 计时合同

前置阶段：重新准入硬件/容器/端口与旧资产；建立或绑定可信源，布局驻留与其持久成本另列；目标引擎已初始化且权重已释放。必要命令配置写入可放在计时之前，但禁止把 materialize、IPC import、必要库存/规划、控制 prepare 或 rank 屏障移出窗口。

控制器记录单一 `monotonic_ns` 的开始，立即提交 `resume_memory_occupation`。各 rank materialize→export IPC；控制器等目标导出，执行必要库存/规划和 Cluster.transfer；worker prepare 校验/租约/IPC 映射→同步复制→complete .done；引擎等待返回→CUDA synchronize→TP CPU barrier→清除 weights offload tag→weights_ready→RPC 全 rank 聚合确认。控制器在全部 RPC 确认收到后立即记录结束。跨机器只使用这一控制器时钟，两端时钟不相减。持久化 JSON、汇总日志等实验诊断放在结束后，在线必要 RPC 序列保留。

结束后先 fullSHA，再确定性生成/既定输出比对，保持 tx/源 lease/IPC target 引用至校验完成；失败不会成为可接受样本。生成、校验、park、retire 与 cleanup 分窗。全部目标 n 等于真实 model shard 的完整 extent，拒绝只有几 MiB 的假 live 目标。

## 历史 Qwen 和现实资源要求

历史模型路径：`/data/node28/shared/zhujianian/Qwen3.6-35B-A3B`；identity `530a225f6e1b5ad6d67ec1c76aac709261f8b6bcb0c08d545c10037be934e7d9`。可复用 `basic_scenarios/qwen_bootstrap_r01/session.json` 的 processed TP2 metadata、每 rank 的完整 index/layout/derived 文件、输出参考与启动参数。每 rank 35,164,859,056B，1009 storage segments；总 70,329,718,112B，约 65.5GiB。源内容身份分别以 audit.json 为准；不同历史捕获的 identity 不能混用。

历史启动为 eager、triton attention/MoE、TP2、静态显存比例 .70、context4096、8192 token 上限、seed42、mamba extra_buffer/cache32。主 agent 必须定义完整权重、持久驻留、暂态目标、staging、引擎 KV/runtime、SHA 主机 scratch、host/cgroup 与每卡 headroom 的新预算；当前每 node 6GiB DRAM/2GiB HBM 微预算无法运行该模型。

至少两张目标 GPU；历史 RestoreEngine 对 Qwen 使用每 rank 权重 +10GiB runtime guard（约 43GiB/card），这是历史准入方法，非已验证当前最小峰值。两份完整本地 HBM copy 约65.5GiB/card，超过64GiB容量，还未计 runtime，不能部署。局部 localHBM 可只保留例如1GiB/rank，完整其余部分由 DRAM/remote/SSD 补齐；完整 HBM 命中若采用 IPC alias 则单 storage 计费，指标不同，必须作为 mode=ipc 单列。

历史 slot node27 GPU0/1 与 node29 GPU0/1、旧 PID/.pkl 只当历史证据。用户现指示 node27 GPU0–3 被其他团队占用，node29 排除；不得直接运行旧 CLI。GPU4 仅是过去候选状态，GPU5、node28/30及 host 内容均未刷新。最小候选可为 node27 GPU4/5 的同机 TP2 加 node28/30 远端源 helper，前提是主 agent 完成 fresh gate 与拓扑/容量准入；没有申请或启动这些 GPU。

HIP IPC handle 的 device ordinal 直接用于 `_new_shared_cuda`，HbmTarget 与 ipc_attach 均未 remap。engine 与 keeper 必须使用一致可见设备编号；若 keeper 全8卡可见，engine 使用相同可见列表和安装版 SGLang `--base-gpu-id 4` 选择4/5。安装版 `/home/zhujianian/miniconda3/envs/sglang/lib/python3.10/site-packages/sglang/srt/server_args.py:189,1186` 静态确认有此参数；运行前仍须核实际逻辑卡→BDF。不能将旧 engine `HIP_VISIBLE_DEVICES=0,1` 静默改成4,5而保持 keeper 编号。

## 四路径最小实际接入

固定真实 TP2 完整权重目标、同持久驻留总预算，按主 agent 与 model agent 的事前布局形成少量完整恢复场景；不扫描所有路径。

- localDRAM：各目标 worker 上相应 NUMA 的可信 processed shard，原 h2d/land scatter。
- localHBM：合法容量的同 worker HBM 分块 + 完整 DRAM fallback；`Cluster.transfer` 写真实 live target。若全 HBM alias，则另用原 lifecycle mode=ipc、export_ipc/ipc_attach 入口，源 owner 必须活到消费者释放。
- remote：node28/30 source helper 的 DRAM/HBM extent，通过原有 pair broadcast；相同 source/target shard 内容与TP rank，沿共同操作序列，共享资源按实际 DAG 计费。
- stagedSSD：由本任务先创建/填充/全SHA/commit 的 `RemoteExtent` 与 `ssd_store`，在线 `owner.read`→HTTP/CPU bytes→staging/H2D 或 RCCL→live scatter。不直接 new RemoteExtent 重开旧底本（构造会 create，close 会 delete），不重建存储框架。setup 成本另列，线上读和同步在主窗。

最小入口可接受冻结 spec 的 `model_metadata / engine_slot / endpoints / targets / source_ids / profile / budgets / case_id / prediction_file`，调用原 hooks/Cluster，输出 `resume_request_begin_ns / all_weights_ready_ack_ns / online_weights_ready_s / expected_bytes / actual_bytes / operations / post_ready_full_sha / generation / resource_ledger / cleanup`。此处尚未实现新入口，等待主 agent 明确接口、模型与物理资源配置；产品文件写入授权不需要，因为现有 live 协议已具备。

硬件运行前剩余依赖：fresh 可用两目标卡与至少一远端 helper、目标节点可读完整 model/config、processed source 内容与layout身份、host/cgroup真实容量、当前拓扑 XML、明确运行/持久/暂态预算、精确 owner/cleanup 边界、事前冻结预测和每场景独立验证选择。不会由2GiB微协议自动提高预算。
