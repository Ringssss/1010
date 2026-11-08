# 新 outer 协议独立设计审查

结论：通过，可由主 agent 冻结源码并执行 fresh 硬件准入。无剩余 launch blocker。当前仅 CPU/设计资格，尚无新 GPU 模型精度资格。

已独立逐字节对照原归档，确认继承包19件完全相同，只有预 outer checkpoint 与18份原始路径预热/校准；旧 C0 outer、Hnear/Hremote 未进入新拟合。成本数值、原 context/session/SHA 不变，新物理映射与 source epoch/NUMA/worker incarnation 另核。

四个完整请求由同一 controller sequence 执行：C0_prime→C0_cal→两预测同时冻结→Hremote→Hnear。唯一残差为 C0_cal 在线完整时间减旧成本表在当前合法 plan 上的原 DAG。原始实际 prime 时间单列，模型范围明确为同一 prime 后 controller/engine/keeper incarnation 的后续恢复。

审查发现的引擎身份缺口已补：从 raw.rank_timing 实际 load_begin/weights_ready 提取双 rank host/PID，prime/cal 一致并绑定到模型，随后校准与两留出均须相同；两个 rank ready 必须位于 inclusive ACK 时间内。每次原主时钟、完整70329718112B/66extent、SHA/固定输出/实际物理 park、资源/lineage/lease与精确清理保留。

最新核心 execution_outer_core_cpu_r03 共41项、部署 outer_ops_cpu_r01 共31项通过，均为CPU证据。未重复旧28项或追加测试矩阵；未调用GPU、SSH或Docker。完整14项审查判断与当前源码SHA见qualification.json。主冻结时应核该SHA，后续改动需针对改动复审。
