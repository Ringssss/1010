# 在线 ready 恢复前独立审查

开发编号 v0.1.23.3 / task2。本轮仅本地读取源码和已有原件；未调用 GPU、SSH、Docker、node29、KFD/VRAM owner 表，未修改产品代码、实验脚本、XML、历史原件或 history-chat.txt。

结论：**现有核心修复具备 CPU 资格；32MiB 探针可在 fresh same-HIP 容量通过后有条件执行。完整 Qwen 必须使用新的冻结源码和新容器，仍须先通过真实物理暂停，预测资格保持 false。** 当前资源由主 agent 单独重新检查，不沿用先前空闲状态。

## 1. 核心路径

- `main_lib/fluidckpt/cluster/capture.py`在唯一 `HbmTarget` 内完成 D2H 和可选全 SHA；默认 `verify_full_sha=False` 保留。实验 wrapper 不再用同一凭证重新构造映射，marker 在单映射验证及 GC/sync 之后发布。
- controller 在完整捕获、参考输出、物理暂停、helper/cache 与最终门之前逐阶段保留事实；资源失败记录全卡实际 upper/cap/margin/BDF，不能推测原 r02 未落盘的失败卡。
- 正式 `online()` 的同 controller 起止包含本次 inventory/布局/状态和真实 resume ACK；全 SHA/参考输出在 ready 之后。`summarize(metrics,2)`继续检验两 rank 的真实 weights_ready，不能把 ACK 单独当全模型 ready。
- helper 构建之前及每次恢复之后，两个 rank 均要求引擎 allocated 减少量和整卡 free 增长量各至少完整权重的90%，不扣背景占用。原 r02 的 −512B allocated drop 必须继续拒绝。
- 先前独审35项 CPU 原件保留；其中21个 execution 源文件 SHA 与本轮源码逐项一致，未重跑测试。产品 capture 文件单列本轮 SHA，必须纳入新完整源码冻结；prepare 已枚举全部 main_lib/fluidckpt Python 文件，不能复用旧129资产。

## 2. 1/2 次导入探针的边界

两配置使用独立生产者进程，每次一个32MiB uint8=13源、一次 `_share_cuda_`、唯一 consumer、同一 tuple 1/2 次 raw import；每次全量 known-byte 比较和 del/gc/sync，读取仅限自有 handle 的单计数槽。当前 `result.passed`只说明 payload 与 consumer 清理，不说明生产者 allocator 归零、计数机制成立或共享内存无残留。

原脚本无退出后私有 SHM 缺失检查，且 watchdog `os._exit`跳过 Torch teardown；source/share 后的 pre-child sample 失败也在主要 try/finally 外。主 agent 已给出有限处理：**仅在旧自有 node27 私有 IPC 容器执行探针；运行前旧 GPU 角色已退出，完成或失败后验证准确生产者/consumer 退出，再停止该容器销毁 IPC namespace；完整 Qwen 使用新 r03 容器。** 已有 setup inspect 确认准确 CID `8b484137e72e13d49b546c9425aea09e643196cdeea93a515dbf3248cb4cfb0d`为 `IpcMode=private`、`ShmSize=1073741824`且无 `/dev/shm` bind。下次 fresh inspect 应再次保留这三项。

该边界封住故意双消费导致私有计数文件滞留的范围，无需扫描/修改/删除其他 SHM。最终应保留：每配置结果或失败、producer 精确 PID/start/退出证明、consumer owner/cleanup、有限 deadline 原件、容器最后 Running=false/PID=0/OOMKilled 状态。一次配置完成并审查后才进入两次；两次结果不能要求 allocated_retained=0 才视为诊断有效，否则会把拟检验现象误判成执行失败。

## 3. 资源与证据口径

- 探针 GPU4/BDF85/全8卡 visibility，fresh 以及每阶段 same-HIP `total-free <= 2GiB`；Torch allocated/reserved 仅作自身证据，不能替代整卡门。
- 完整运行固定 node27/node30 GPU4/5、BDF85/b5、NUMA4/7；target初始free至少52GiB/card，DRAM初始余量256GiB；helper两卡整卡上界聚合加256MiB margin不超过2GiB，DRAM初始余量128GiB。运行任务预算/余量沿用原计划，不因失败放宽。
- 避开 node27 KFD/VRAM owner 表，node29 完全不参与。原厂及共享 XML、默认 mapping 与旧运行不动；如需新 dump，输入与 dump 分开。
- 原 once_r01 在 tensor/share/consumer 前拒绝：整卡 upper=34,833,694,720B、free=33,869,004,800B、Torch allocated/reserved=0。两次配置与完整模型新修复尚无硬件证据。晚启动 GLM 不能解释更早 r02 资源失败。
- 探针是 IPC 机制诊断，真实 park 是生命周期资格，C0/Hnear/Hremote 才是在线成本证据。正式样本仍0，不借旧 synthetic 或小块 service 误差；两个留出各自整体误差≤10%，预测须事前冻结。

主要原件：`review_core_final_cpu_r01/qualification.json`、`execution_host_setup_two_node_r02/resume_existing_node27/stdout.json`、`execution_ipc_probe_once_r01/{producer_init,failure}.json`、`execution_host_owned_stop_r01/node27_after.json`；本次完整引用与当前 SHA 见 `qualification.json`。
