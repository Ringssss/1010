# node28 单一 LD 路径候选独立验收

计划对照八项通过，实际运行证据和窄保护核对通过，但 GPU 初始化仍失败。计划审查为 `review_runtime_ldplan_r01/summary.json`，实际审查为本目录 `summary.json`；各目录保存源码和输入 SHA。审核未调用远端或 GPU。

变体去掉新增的 `-e LD_LIBRARY_PATH=...` 并还原唯一输出目录后，remote argv 与首次28 baseline逐项完全一致。实际原继承 LD 值经只读取证确认；只在原opencl目录后插入 `/opt/dtk/.hyhal/rocm_smi/lib`，其他路径顺序、OMP/MKL=1、源码927f、GPU2/BDF55、selected visibility、完整容器身份、IPC/cap/memory配置和15秒期限保持。

变体Torch导入1.805004766s，最后事件仍是set_device_enter，没有exit/BDF/HIPfree；总15.265080403s含回收。child28/start485301163与注册argv、PGID/SID匹配，一次精确TERM后absent、pending组为空。变体日志为空，baseline日志32B为hylog open警告：新增路径消除了该警告，但没有使当前初始化在期限内完成。因此此候选不足以解决当前失败，尚未排除其他环境因素或建立唯一根因。

变体之后新容器与旧两容器完整身份持续，13个旧owner/worker/keeper exact匹配，六份目录哈希与06:17新基准一致、lease0、事务空。该保护是目录/进程层级，没有重新读取全权重内容。两个运行均只有初始化，copy、自有XML和性能尚无通过证据；不延长deadline或自动启动后续通信。
