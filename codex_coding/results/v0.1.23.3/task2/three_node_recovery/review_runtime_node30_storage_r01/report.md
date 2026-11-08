# node30 存储及静态环境独立窄审

直接读取 `diagnosis30_storage_env_r01/stdout.json` 与 `result.json`：2026-10-09 06:19:41 UTC 同boot、旧隔离容器完整ID/StartedAt/image/tasklabel持续，CPU采集返回0、进程退出、无signal、GPU/device-open或远端文件写入。

根盘、/tmp、DockerRootDir `/var/lib/docker` 和现stage同设备，available0B；inode有余，不能解释为inode用尽。另 `/data/node30/shared` 只读access检查为可写、有1,793,051,893,760B余量，但未挂进现容器，也尚未创建目标私有目录。数据盘余量不认证Docker根盘元数据可写，不能以单换stage宣布创建已通过。现隔离容器SizeRw19,768,387B为Docker逻辑口径，不认证物理根盘归属。

完整env.sh未source，静态SHA033843c698cb207a828a93545181c811860c3495870b94a9503a6f60b4d61b79。rocm_smi分支的OR表达式因两so存在而为true，支持潜在新增 `/opt/dtk/.hyhal/rocm_smi/lib` 路径；该检查没有加载库或验证GPU因果。后续主agent将单路径候选转到同日同容器28对照，避免同时改变容器/stage/env；独立计划和真实失败结果分别见review_runtime_ldplan_r01及review_runtime_node28_ldpath_r01。

本项是当前存储及环境元数据，不是30的新GPU初始化失败。30新guard在bootstrap ENOSPC时没有执行；旧10月8日set_device失败仍保持历史边界。无copy、XML或性能证据。
