# 新容器 setup 局部恢复

第一次 setup 已成功创建本任务 node27 容器，随后旧容器 Mounts 数组顺序变化触发主控保护误报。原始 `execution_host_setup_two_node_r01` 不覆盖。修复只把每个完整挂载记录按 JSON 内容排序再比较，仍拒绝 Source、Destination、RW 或其他挂载字段变化；其他旧容器身份和配置比较保留。

主控 `orchestrate.py` 新增 `--resume-setup`。它读取原 setup command、创建结果和前后元数据，再 fresh 读取 node27。CID、StartedAt、boot、精确 image/资源/mounts、原冻结源码 SHA、pids1、主机余量与空闲端口全部一致，才跳过 node27 创建与部署。继续 node30 的原冻结 setup。旧129项 staged snapshot 保持，不 restage core；recovery_provenance.json 记录主控更新 SHA 与远端冻结 orchestrator SHA。

针对性10项 CPU 检查通过 `execution_host_recovery_cpu_r01/qualification.json`，包括直接复核真实前后元数据、挂载排序、挂载修改拒绝、node27 无写操作、精确身份保持、仅 node30 创建和旧129资产保持。CPU检查使用 host/record mock，无 GPU、SSH 或 Docker 执行。`commands.txt` 提供唯一新结果目录 r02 的恢复命令。后续 admission/bind 使用 r02/setup.json；旧来源和失败另存。
