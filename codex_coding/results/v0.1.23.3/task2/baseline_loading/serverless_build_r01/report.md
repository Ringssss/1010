# ServerlessLLM Store 实际数据面建设记录

开发编号保持 v0.1.23.3/task2。官方 SHA：`9f50241baa5386e06a9321c51f19a9ef5f964c2b`。冻结 `lib_cite/ServerlessLLM` 无改动；适配放独立 `ServerlessLLM-hip` worktree，patch 在 `lib_cite/patches/serverlessllm/dtk_gfx936.patch`。

## 已完成

- 两个上游 HIP 扩展实际构建和 CPU 导入通过；二进制 SHA 见 `extension_sha256.txt`，导入路径和 DTK/Torch 版本见 `extension_import.json`。构建容器 4 CPU/16 GiB，无 GPU device 映射，不安装/修改共享 Python 包。
- 适配仅补 gfx936/CMake 发现/已有 glog/未使用 BLAS 头；磁盘、pinned host、chunk dispatcher、H2D 等真实上游 C++ 管线保持原样。
- adapter 直接向既有 parked 引擎的最终 storages 写入。目标由引擎分配，不增加整模型 HBM staging；1009 storage/rank 的 packed source index 和 IPC allocation offset 独立处理。
- FIFO 读端延迟/EAGAIN、短数据与读端故障/EPIPE 三项 CPU gate 通过，见 `fifo_cpu_gate.log`。
- 已准备完整 Qwen TP2 NFS 文件，每 rank logical 35,164,859,056 字节，sector padding 1,360 字节。唯一 data run：`/data/node28/shared/zhujianian/fluidckpt_zjn_baseline_data/v0.1.23.3/task2/serverless_data_r01/generation_r02/`。
- 输入数据从仅本次 source worker PID118/136 的唯一完整匿名 HostAllocation VMA 只读读取。CPU exporter 无 GPU device，仅 1 GiB/2 CPU，通过 `/proc/PID/mem` 读取，不 attach、停止或写 source 进程；源 epoch/catalog 前后不变。
- rank0 完整 SHA `50886ec38c6a85911072eba148e0717dfd34144f99e1863bc016757eb5ea67cd`，rank1 `fd0f438bcb07669eb3f39f0b4742ea4c15ae94a37c330e31dad04dfa2a5d9311`，均等于此前 S2 冻结 full-rank SHA。格式成本分别 128.567509s、128.006032s。metadata/布局/文件长度复核见 `../serverless_format_r02/cpu_validation.json`。
- CPU exporter 已移除。此次导出使用 PID+epoch/catalog 前后验证；当时没有单独采集 stat.starttime，不将后续补充的严格 starttime 检查记成已执行证据。

## 已保留的失败与修正

1. `serverless_format_r01`：ClusterWorker HTTP 不提供旧 Keeper `/save`，且 adopt 已将 shard 移入 catalog。请求 HTTP500，未导出 payload；零文件和日志保留，随后在同一唯一 NFS run 的新 generation 进行 CPU 只读导出。
2. `serverless_offset_gate_r01`：新容器缺宿主 `/opt/hyhal` runtime，Torch 无可用 HIP GPU。修为同既有容器的只读 mount 与 LD 路径，并固定 Image ID；失败容器已清理。
3. `serverless_offset_gate_r02`：实际上游 C++ 已完成 24 KiB disk→host，raw HIP IPC 打开报 invalid device pointer。实际 PyTorch handle 是 66B、头 `02 63`，上游自己的 IPC 接口预期裸64B。外层180s超时后只清理本 run 容器，exit137，原始结果保留。

PyTorch v2.10.0 官方 allocator 的 `shareIpcHandle` 写入 version2、`'c'` 类型再写 raw64：[固定版本代码](https://github.com/pytorch/pytorch/blob/v2.10.0/c10/cuda/CUDACachingAllocator.cpp)。adapter 现严格解码观测到的66B/02c，并支持legacy raw64；拒绝 expandable/未知版本/错误长度。CPU格式、offset、handle去重与拒绝合同通过，见 `ipc_frame_contracts_r03.log`；没有改 C++ 或数据门槛。完整 handle 只留在必要本地 pickle，不写入报告/summary。

## GPU 交付与范围

所有 GPU 运行由主 agent 排程。`run_serverless.py` 提供 gate/full 一条完整协调入口：新 adapter 容器、core=0、资源准入、pool setup、parked target_b restore/export、实际 C++ 加载、正确完成发布、引擎 weights_ready、生成、post-ready 全量 SHA、关闭 importer、release、独立容器清理、原 command 还原、源元数据和物理 HBM 回收。

默认完整 host pool 为66 GiB，adapter cgroup96 GiB；单独核算 pin/setup 和格式建立时间。cache状态是新写NFS文件、源流已全量SHA，不执行dropcache，不承诺warm。真实主指标从引擎 `max(weights_ready)-min(load_begin)` 取得；Store 子窗口单独记录。未确认 resume 的失败不伪造 ready/done，保留明确 pending 和本 run 状态供主 agent 恢复。

本对照是官方 ServerlessLLM Store 数据面 HIP 适配，未运行其完整在线调度、选址和迁移服务。上文保留帧修正时的建设状态；最终实际 GPU 验收与后续修复证据见下节，不用构建成功或 CPU 合同替代。

## 最终验收补记（2026-10-07）

最终 `serverless_qwen_full_r03` 已通过：`T_ready=131.121134861s`，Store 子窗口 `130.833663464s`，首正确 token 从请求起 `131.526087794s`；完整两 rank frozen SHA、生成、worker exit0、物理回收、keeper 元数据和代码无漂移均通过。各 rank 1009 个存储使用209个原生 allocation，801个存储带非零 offset。原始证据为该 run 的 `summary.json`、`worker/result.json`、`metrics/` 和完整后验 trace `loader.log`。

此前完整 r01 因目标既有 handoff timeout120s而失败，上游搬运约143s完成晚于引擎退出。只重建退出的 node29 目标进程，其他 env与参数固定，timeout改900，原keeper/container不动；功能恢复全SHA/生成后park，新owned overlay完整登记替换目标ownership。recovery_r01自身资源快照错误地要求无活动事务，保留失败并修为先finish本次事务再snapshot，CPU流程测试与后续 recovery_r02/r03通过。

完整 r02 已得到正确 weights-ready/生成，但后验 `_new_shared_cuda` 在首次导入时139。独立24KiB current probe复现首条import前Torch尚未初始化、native current device1，显式 `set_device(0)` 对照只改单一调用即全字节/SHA/guard通过、exit0。实际 vendor Torch标准IPC rebuild同样在低层调用前执行lazy init。最终修正仅在每 rank 的后验hash开始显式选择/初始化device，不改上游搬运、计时、逐storage import/release生命周期，也不增加验证进程。

最终资源单列：66 GiB完整pinned pool，setup40.270199s；格式建立128.567509s+128.006032s；ready后的两rank完整SHA分别49.901177s/50.213918s。无整模型HBM staging，释放后引擎各保留1,315,794,944B allocator空间（KV/runtime），目标物理free相对preflight仅约6 MiB差，符合既定256 MiB容差。cache运行态标为未控制/未测，格式建立时的新写状态单列；没有全量预读/dropcache。

在主 agent独立确认最终通过后，仅本 CPU build容器经ID/Image/无GPUdevices核对后移除，after列表为空；records为`build_cleanup_identity_before.txt`、`build_cleanup_remove.txt`、`build_cleanup_after.txt`。完整source/binary/config/数据/验收来源汇总为`final_provenance.json`；原官方checkout不变，两个二进制与patch SHA保持固定，shared包无修改。
