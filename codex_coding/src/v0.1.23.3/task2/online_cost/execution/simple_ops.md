# 两节点单远端 sender 部署

此适配保留原复杂四 GPU 工作流，只通过 `prepare.py --simple` 冻结新三 GPU 运行。运行编号 `online_simple_two_node_r01`；node27 GPU4/5 分别为 target0/1，node30 GPU4 为唯一 helper2。完整 TP0/1 DRAM 源各自固定在 NUMA4/7；GPU PCI NUMA 与 DRAM 源 NUMA 分开记录。

主 agent 先运行 `review/collect_metadata.py --scope two_node --helper-gpus 4`，以准确单 helper GPU4 的新元数据进入 prepare。需要读取 NUMA7 的普通 OS 内存信息；不读取 KFD owner 或 driver VRAM 表。

helper2 的 2GiB 是事前计划新增 runtime/staging 预算：初始 HIP 整卡 free≥4GiB，已有 worker 内每 0.5s 采样 free≥2GiB、Torch reserved≤2GiB。显式已知 live tensor storage 去重台账另设≤2GiB。`helper_memory_guard.jsonl` 最多保存14500个样本；inventory保存最近样本及采样最低 free/最高 reserved。缺少采样或违反门时 helper 自有进程退出，由原准确 supervisor 收回本任务进程。

整卡已有占用包含其他任务，记录而不计入 helper 自有2GiB预算，也不将整卡前后差值当成自有显存。仅查询本进程 `/proc/self/fdinfo` 的可用 DRM client VRAM 计数；此信息仍不能证明完整 RCCL/driver 归属。完整 task HBM 实测值保持 unknown，所有采样均 `peak_verified=false`。

目标 node27 保持192GiB任务 DRAM/224GiB容器 hard、初始 host available≥256GiB、运行 available≥64GiB、初始每卡 free≥52GiB、运行整卡占用含128MiB余量≤48GiB/card。node30保持96GiB任务 DRAM/128GiB hard、初始 available≥128GiB、运行≥32GiB。源捕获/副本前按每个 NUMA 的计划实际分配与4GiB余量检查，不能将两个 rank 70.3GB 合计记在 NUMA4。

执行入口为 `simple_host.py setup/admission/bind/run/collect`，继续复用原隔离容器创建、精确身份保护、线程上限、subreaper以及最终 owned closure。bind 只生成计划；实际操作必须由主 agent 传 `--execute`。每个输出目录只写一次。模型捕获前的 native warm 及资源门由 `simple_controller.py init` 完成，后续源构建和在线校准使用原生 transfer。

CPU核验：`check_simple_ops_cpu.py --out .../execution_simple_ops_cpu_r03`，覆盖非空原生 Catalog 字典的 inventory、整卡外来占用共存、free/reserved/已知 allocation 拒绝门、NUMA分摊、可用与缺失 fdinfo 计数、真实 prepare 冻结接口。CPU资格不等于 GPU 运行或成本准确率通过。
