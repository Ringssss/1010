# node28 host IPC 独立验收

独立21项原件和窄保护检查通过，初始化仍失败。实际成功的baseline Docker argv与hostIPC计划逐项对照，仅新容器名及 `--ipc private`→`host`变化；实际HostConfig也只IpcMode不同。使用宿主IPC必然改变共享namespace/shm语义，本次结果不能单独归因某个shm子机制。init命令只按新CID/StartedAt/输出目录更新，未追加LD候选、未source env.sh；原927f、OMP/MKL1、GPU2/BDF55、selected visibility及15秒期限保持。

Torch导入1.868491724s后最后事件为set_device_enter；总15.215304624s含回收，child13/start485324143匹配完整owner，经一次TERM后absent、组空。日志32B仍为hylog open警告，没有实际HIP BDF/free，未copy/XML/性能。hostIPC配置单独不足以使当前初始化通过期限，尚未建立永久硬件故障或唯一根因。

before/after新容器完整身份持续且IPC=host，原两自有容器持续，13旧owner/worker/keeper exact匹配、六目录hash保持、lease0/无事务。范围是进程和目录保护，没有重读全权重或升级27/29未知状态。

验证命令：`PYTHONDONTWRITEBYTECODE=1 python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/review_runtime/review28.py --hostipc --run codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_node28_hostipc_r01 --after codex_coding/results/v0.1.23.3/task2/three_node_recovery/admission28_hostipc_guard_after_r01 --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/review_runtime_node28_hostipc_r01`。审核没有远程/GPU调用，源码及输入SHA保存在本目录。
