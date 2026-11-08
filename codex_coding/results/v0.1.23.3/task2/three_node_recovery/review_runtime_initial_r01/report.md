# 三节点运行独立预审

本地十项证据检查通过。检查入口为 `review_runtime/review_initial.py`，命令为 `python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/review_runtime/review_initial.py --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/review_runtime_initial_r01`；源码及输入 SHA 已存 `source.py`、`summary.json`。未连接远端、启动 GPU、调用 Docker 或发送信号。

28 在 2026-10-09 06:17 UTC 的新原件确认根盘和 DockerRootDir `/var/lib/docker` 有 64,389,271,552B 可用，旧满盘状态不能直接沿用。两旧自有容器完整身份持续，13 个 owner/worker/keeper 的 starttime 和 cgroup 匹配；六份目录共 1098 条副本均 lease0、事务为空。GPU2/BDF `0000:55:00.0` 的 sysfs 是候选余量，fresh child HIP free 仍为 null，GPU运行健康尚未认证。新容器及新 probe 应由父 agent 执行，并复核当轮后置目录与身份。

旧30失败验收引用的十一份原件 SHA 全部匹配。其 fresh `set_device` 超时仍为失败；精确 TERM 清理通过不升级初始化结果，也没有 copy、XML或性能证据。此次读取不增加对27新D、29旧两 pending 或全权重内容的结论。

配置窄审发现：旧30 r01命令显式设置 `OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`，已有 host wrapper 最初只显式设置 `PYTHONDONTWRITEBYTECODE`。后续单变量对照需保持这两项线程环境为1；已反馈负责 agent。已有 wrapper 核完整容器身份和源码 SHA，但时效仍由父 agent 当轮新 guard 确认。最低判分为 code/metadata、runtime init、copy、XML runtime、performance 分层，前层通过只允许推进下一层。
