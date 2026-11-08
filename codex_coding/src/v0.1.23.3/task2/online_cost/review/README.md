# 在线权重可用独立审查

开发编号保持 v0.1.23.3 / task2。当前脚本只读本地源码和历史真实模型结果，检查 live 目标完整覆盖、执行顺序/字节、逐操作全 SHA、确定输出与同主机两 rank 的完整 ready 边界。它不执行 SSH 或 GPU，不授予当前域预测资格。

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/audit_initial.py --out codex_coding/results/v0.1.23.3/task2/online_cost/review_initial_r03
```

输出必须为新的 `online_cost/review_*` 目录，已存在目录拒绝覆盖。初审最终说明位于 `results/v0.1.23.3/task2/online_cost/review_initial_r02/report.md`。后续根据主 agent 冻结的有限真实模型矩阵审查协议/启动/数据，采用每代表场景完整在线 weights_ready 相对误差≤10%，不沿用历史细项门或复杂度升级门。

## 只读现场 metadata

以下入口由主 agent 统一运行，子 agent 不自行执行 SSH/GPU。复用既有 `operations.command/old.record` 的限时传输，Python源码经stdin执行，远端不写文件、不exec已有容器。读取固定node27/28/30的Docker GET/proc/安全PCI与NUMA，不访问node29，不调用GPU资源API，不读取VRAM/KFD owner文件。所有已有容器均列为受保护对象，包括 `zhujianian-fk` 和其他用户命名。记录精确镜像、PID、cgroup memory.stat anon/file/shmem、端口及原XML SHA；只在node27读取指定模型config/index并stat shard，模型payload读取为0。

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/collect_metadata.py --out codex_coding/results/v0.1.23.3/task2/online_cost/review_metadata_r01 --run-id online_qwen_r01 --model-path /data/node28/shared/zhujianian/Qwen3.6-35B-A3B
```

可增加 `--build-only` 仅冻结源码/spec、不启动远端采集；`--spec` 接受node27/28/30为key的精确自定义spec。默认新名字和端口与execution agent确认一致。`collection_passed`仅说明metadata采集成功；host准入、镜像、候选名字、端口分别显示，GPU可用量保持null，fresh GPU准入false。

CPU资格：`check_metadata_cpu.py --out .../review_metadata_cpu_*` 实际mock检查所有保护名字、无GPU/owner路径、cgroup分项、config原始SHA、weight仅stat与超大metadata拒绝；结果 `review_metadata_cpu_r01/qualification.json`，GPU/SSH均0。
