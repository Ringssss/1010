# 六 worker 最小真实执行独立验收

开发编号 `v0.1.23.3/task2`。本目录仅本地 CPU 审查，不执行 SSH、GPU、远端写入或清理信号。GPU 启动、预算和 run ID 由主 agent 协调。

本轮执行前审查 setup/launch 的具体写入与退出边界，执行后读取原始结果，不把 prepared 合同、测试条目数或 node27 单卡成功当成六 worker 恢复证据。旧失败保留；节点29、原厂 XML、默认 mapping 和旧 keeper 均不进入修改范围。

最小验收覆盖以下事实：

- 六个活 worker 的实际 HIP BDF、sysfs NUMA、boot/CID/PID-start、当前 epoch/session 和 staging；逻辑顺序固定为28G0/28G1/30G4/30G5/27G0/27G4。
- 各 node 的 postwarm 聚合 DRAM 增量6GiB、HBM 增量2GiB准入，runtime 含 staging 时不重复计；后续有界临时开销与256MiB余量。完整生命周期峰值可继续标记未知。
- 原 serial primitive 所需路径的小额全 SHA 资格及实际端点/来源关联，仅覆盖四组所需路径。
- 明确 synthetic 的 TP4式八块，每块4MiB；当前 owner 持有、提交并校验完整 SSD32MiB底本，真实位置分布16/8/8MiB。
- `SSD_only`、`C0`、`C1`、`C3`各一次，四 target 各8MiB全SHA；保存和恢复分别记录 filled 与 published，不能互相代替。
- 每组只退役当前 run 拟建 HBM/DRAM对象；完整底本在组间保持 VALID、零租约、epoch/generation不变。最终只清理当前 owner 所持对象与进程，并核旧 owner/原XML不变。

执行前 clearance 仅允许经过审查的具体 setup/launch，不预先认证实际路径、恢复时延或模型 `T_ready`。执行后报告区分实际通过、失败原件和未测能力。
