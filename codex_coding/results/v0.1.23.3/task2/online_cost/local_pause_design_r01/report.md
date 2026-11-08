# 完整 Qwen 目标节点物理暂停验证

开发编号保持 v0.1.23.3 / task2。本入口只验证修正后的唯一 IPC 映射捕获与真实显存释放，不产生在线成本校准数据。原两节点 138 资产快照、controller、产品代码和历史原件不修改。主 agent 统一资源准入、冻结附加源码、启动与回收；本实现 agent 没有执行 GPU、SSH 或 Docker 操作。

## 入口与范围

新增 `online_cost/execution/local_pause.py` 继承现有 `controller.Run`，复用原生 Cluster、capture_regions、资源门、park、release 生命周期及 physical_park_snapshot。只覆盖本地两 keeper 的初始化和完整 bootstrap 的前半段。运行计划须显式绑定 node27 GPU4/5，逻辑 rank0/1、NUMA4/7、完整八卡可见编号、TP2 engine base GPU4、`--mesh node27:4-5` 和 `warm_pairs=[]`。计划仅有 node27 三个角色，任何远端节点、额外角色、非完整权重或非空 warm pair 在 RPC 前拒绝。

完整 processed 权重每 rank 35,164,859,056B，共 70,329,718,112B。每 rank 原生 33 个 DRAM 区域、每区最多 1GiB；尾块 805,120,688B。Catalog 按页计费，每 rank allocation 为 35,164,860,416B，保活检查使用 capture 返回的 allocated_bytes 总和，避免把页尾 padding 误判为泄漏。

## 执行与证据

1. `init`：两个 keeper 的原生 mesh；warm 调用 pairs=[]；写 engine capture lifecycle 命令，保存两卡当前资源门与不可变 plan_ref。
2. `pause`：读取当前 init.json 并核对 session、精确 plan_ref、空 warm；等待实际两 rank IPC export；在 DRAM 分配前检查计划分配余量。
3. 并发调用原生 `capture_regions`，每个成功 rank 立即保存 checkpoint_capture_rankN.json，随后合并捕获 manifest 与全量逐块 SHA。要求 SHA 使用同一唯一 IPC 映射，禁止新的重复导入。
4. 等待真实 engine API ready，执行固定 prompt、temperature0、32 tokens 的参考生成，保存原始 response。
5. 原生 release_live 后保存暂停前全量 inventory；执行 release_memory_occupation(weights)，保存暂停后 inventory 与两个 rank release_begin/release_end。
6. 要求每 rank 引擎 allocated 减少量和实际整卡 free 增长量均达到权重 n 的90%。不扣除背景漂移。失败时物理检查原件仍先写入，不能把接口成功写成释放成功。
7. 要求全部 capture 区域仍为 VALID、lease0、完整 lineage 不变、每个 worker DRAM 计费与 capture allocation 完全一致、HBM/SSD Catalog0、transaction0，再核对当前资源门。
8. 只写 `local_pause.json`，明确 diagnostic_only、online_cost_samples0、online_cost_qualified=false、owner_stop_required。在 helper、cache、路径校准与 C0 之前结束，不写 bootstrap.json，不发布恢复 ready 或成本模型预测。

每阶段采用新目录、exclusive 写入、原始 checkpoint 保留。控制器硬 deadline 30..3600s，默认1800s；超时或失败写明主负责精确回收，不自动退休保活源。keeper、engine 生命周期仍由已有 frozen supervisor/subreaper 管理，正常和异常路径均须主核对最终退出与 quiet。

## CLI

本命令由主统一部署到新 stage 后执行；PLAN/OUT 使用冻结 stage 中实际路径，OUT 在 online_cost 结果根的 execution_* 下。

```bash
python3 local_pause.py --plan PLAN --action init --out OUT/phase_init --deadline-s 180 --execute
python3 local_pause.py --plan PLAN --action pause --binding OUT/phase_init/init.json --out OUT/phase_pause --deadline-s 1800 --execute
```

没有 `--execute` 只输出 not_executed、GPU_or_RPC_calls0。standalone 入口没有 calibrate/validate/cleanup action，主正常停止本轮精确 engine 与 keeper。

## CPU 验证与来源

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/check_local_pause_cpu.py --out codex_coding/results/v0.1.23.3/task2/online_cost/local_pause_cpu_r02
```

`local_pause_cpu_r02/qualification.json` 共16项全部通过：两 keeper 范围、远端 mesh/非空warm/非完整权重/可见编号拒绝、完整66源保持、页对齐计费、停止于park、无 bootstrap/成本资格、原始前后事实、lineage变化拒绝、物理释放失败保留证据、单 rank 成功/另一 rank 失败仍保留成功原件、错误绑定 RPC 前拒绝、语法。仅 CPU replay；本结果不代表实机物理暂停已通过。

runner SHA256：`f572a9fcfd51ce433d8686161a1153d768b8da122b8e60ccd2eea3c8f58ca835`。
CPU checker SHA256：`6d54dbb8763e200fc0904e8a505440d32c61f45a165736369e6a9205f8aa4b50`。

新实际运行的通过/失败与资源回收由主另行记录，不回填本设计文件为硬件资格。原始指示中两次导入32MiB探针已由主取得物理 allocation差异，其不可信 raw counter offset 不参与本验收。
