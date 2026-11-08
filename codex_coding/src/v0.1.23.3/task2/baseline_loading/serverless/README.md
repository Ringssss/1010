# 实际上游 Store 加载入口

本平台 `ClusterWorker` 没有开放旧 keeper 的 `/save` API，且 `adopt` 已将底本移入 catalog。`prepare_store_data.py` 的第一尝试因此失败且保留零 payload 记录。实际格式入口是 `prepare_from_process_memory.py`：独立无 GPU 的 1 GiB CPU exporter 容器共享 source PID namespace，借助只读 `/proc/PID/mem` 读取唯一完整匿名 HostAllocation mmap；不 attach、暂停或写 source 进程。每 rank 必须全量 SHA 等于 S2 冻结源 SHA，catalog/epoch 前后不变，才接受输出。

每 rank 使用上游识别的 `tensor.data_0`，按原 packed offset 记录 uint8 storage index，尾部补零到 4096 字节；payload SHA 不含 padding。全量数据只保留一份 NFS 输出，64 MiB CPU stream buffer，不落本地整模型副本。唯一授权 NFS run 为 `serverless_data_r01`，首个零数据失败不覆盖；有效文件写在 `generation_r02`。

`offset_gate.py` 必须先在主 agent 排定的 GPU 上执行。它让独立 owner 持有小 storage，验证同一 allocator handle 的多个非零 storage offset，再通过真实上游 `CheckpointStore` 的磁盘、pinned host cache、dispatcher、HIP IPC 写入路径完成加载。全量字节/SHA 与 guard 验收不依赖 CPU mock。

`load_engine_storages.py` 初始化完整 upstream host pool，发布 `pool_ready.json`；收到本轮引擎 restore 导出的新 pickle 后，验证源/目标 identity、tensor layout、storage nbytes 与 packed coverage，再启动两 rank 的真实上游管线。每 rank 完成后发布既有 `rankN.done`，引擎用原协议产生 `weights_ready`。其 `store_window_s` 仅表示 Store 的子窗口；主指标必须从引擎 metrics 取 `max(weights_ready)-min(load_begin)`。

完整 Qwen 逻辑 payload 为每 rank 35,164,859,056 字节，总计 70,329,718,112 字节。默认 66 GiB pinned pool 能容纳两 rank 的 chunk rounding；没有额外整模型 HBM staging，目标 storages 由同一既有引擎分配。pool pin/setup 时间、NFS 格式建立/源 SHA 时间和 post-ready 全量目标 SHA 时间分别记录。新写 NFS 文件的 cache 状态明确记录，不执行 dropcache。

所有 GPU 运行均须由主 agent 启动，不运行脚本即没有 GPU 实验结论。

完整协调入口为 `run_serverless.py`。小 gate：`python3 codex_coding/src/v0.1.23.3/task2/baseline_loading/serverless/run_serverless.py --mode gate --node node27 --run serverless_offset_gate_r01 --timeout 180`。

完整恢复：同一入口 `--mode full --node node29 --run <新serverless_*run> --prepared-data <prepared_data.json>`。脚本自动创建独立 GPU adapter 容器，主进程、worker、owner 和容器均设置 core=0；外层 deadline 限制 C++ 等待。`pool_ready` 后才调用既有 target_b restore，Store 写入最终 storage 并发布真实完成，生成正确后才触发全量 SHA；随后 release target、关闭 IPC consumer、移除仅本 run 的容器，恢复原命令并核验 keeper 元数据和物理 HBM 回收。若 resume 尚未确认而失败，不伪造 done；保留待恢复状态及本 run 容器供主 agent 收尾。

`test_fifo_stream.py` 的 3 个 CPU gate 使用真实 FIFO，分别验证写端延迟/EAGAIN、缺字节拒绝以及读端磁盘失败后先关闭 reader、producer 收到 EPIPE 并快速退出。

冻结源 full payload SHA：rank0 `50886ec38c6a85911072eba148e0717dfd34144f99e1863bc016757eb5ea67cd`，rank1 `fd0f438bcb07669eb3f39f0b4742ea4c15ae94a37c330e31dad04dfa2a5d9311`，来自 `basic_scenarios/s2_r01/summary.json` 的两项完整 rank 传输验证。
