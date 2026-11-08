# node27 完整模型物理暂停诊断 host 入口

新入口 local_pause_host.py 复用原 HostOps 的身份、fresh HIP 准入、stage hash、STOP 与收集。构造时加载原 r03 两节点 snapshot 后明确仅保留 specs.node27，并要求本地主机 m03r2n07，拒绝 SSH fallback；没有 node30 容器操作或 GPU 查询。

prepare 从原 r03 计划保留两个 GPU4/5 keeper 及同一 TP2 引擎，改唯一 runtime online_qwen_local_pause_r01 与 mesh node27:4-5，warm_pairs=[]。绑定 fresh admission 的精确 container ID/StartedAt/boot，保持192GiB DRAM、224GiB hard、48GiB/card HBM 与线程1，不放宽 helper 门。init/pause 的实际 runner 计划验证通过。

执行只追加 local_pause.py 新文件：128KiB 上限、SHA 校验、exclusive xb；原138项资产不覆盖，源引用独立保存在计划 addition_ref。启动2keepers→init→engine→pause，成功或失败都按精确 STOP plan SHA 先 engine 再两个 keeper；记录 supervisor返回，收集全部本地原件并核 quiet。没有 helper构建、cache迁入、校准、C0、留出或在线成本资格。

CPU共14项通过：真实runner.validate_plan、role/namespace/预算/线程、原138map、host node过滤，以及注入pause失败后的3进程停止顺序与本地收集。无Torch导入，无GPU/SSH/Docker操作。真实fresh admission、物理暂停与资源回收由主agent执行验收。
