# 阶段1/2建设判断：复用、最小新增与暂缓

开发编号保持`v0.1.23.3/task2`。最终证据为`calibration_probe_r01/summary.json`与`scenarios_freeze_probe_r08/manifest.json`；本目录独立审查390项全部通过。审查新增GPU样本0，仅引用9配置各一次观察；16次同buffer传输是每配置内部计时迭代，不是16个独立样本。中间冻结/CPU失败/注册/审计不增加性能样本。

## 本轮确定了什么

| 项目 | 结果 | 建设含义 |
| --- | --- | --- |
| network pair | 共同窗口36.4592GB/s；匹配单流独立预期0.168256s、并发0.235604s，比值0.714147；两流耗时分别增至2.3606/1.4003倍 | 当前isolated RCCL双流存在明显共享路径退化，值得保留物理微probe证据 |
| network准入 | 已同步调用区间overlap约1；起始差25.09µs，占最短单流0.02548%；实际接收HCA端口8,643,065,468B，对8,589,934,592B payload，背景估计约4.04e-7 | 结果远离退化/起始偏斜门槛；本轮无需第二次确认或扩扫描 |
| node27/node29 H2D pair | 共同窗口89.9207/89.4334GB/s；独立预期/并发耗时比0.9920/0.9790 | 这两个本地H2D配置没有同等明显退化；不能由此唯一归因network结果为某NIC、PCIe或NUMA控制器饱和 |
| 实际保护与回收 | 四keeper目录、事务、allocated/reserved/staging及旧可访问端点保持；四独有PID/starttime对应进程退出；物理HBM差在128MiB内 | 少量探测已收尾，保留现场可复用；不以空cleanup错误列表代替原始证据 |
| 固定布局取数 | fastest/resource_lp共6对有序操作签名相同：T1一对、T2四对、T3一对 | 无此项方法增量，不安排六对重复完整模型恢复 |

速率为empirical effective service。单流速率和69.1388GB/s仅为描述性参照，既非容量上界，也不是异速独立流的正确并发完成时间参照。调用区间含排队/等待，未测设备kernel占用。GPU0/1单流不等速和5%起始偏斜反例均已纳入CPU门槛。

## 三个场景readiness

| 场景 | 冻结契约/预算/CPU计划 | 快照余量 | 物理微probe | 当前应用S5-P主张 | 应用预测/完整恢复runner |
| --- | --- | --- | --- | --- | --- |
| T1：本地4+4GiB控制 | ready | admissible | 有共享路径证据，但不是本地场景收益 | 未建立 | prediction=false、restore_runner=false |
| T2：外部6+2GiB、互斥未来目标各0.5 | ready | admissible | 有共享路径证据；0.5为合成预登记 | 未建立 | prediction=false、restore_runner=false |
| T3：4+4GiB零散源、远端目标 | 条件冻结；功能CPU计划ready | admissible | `physical_micro_shared_path_evidence=true` | keeper_serial争用未验证，保持conditional | prediction=false、restore_runner=false |

三场景均保留完整DRAM，额外HBM全局上限8GiB，各位置单列。目标完整分配、既有staging及建立成本账本明确。缓存预算是外部实验约束，不把snapshot free伪装成实际只剩4+4/6+2GiB。最终冻结引用校准后inventory_after；其缺少独立session/status字段，只有与成功校准的精确路径、hash、保护及前后目录签名相连才能认证。r07失败及旧r06保留，r08为本轮最终冻结。

## 建设决定

**reuse：**现有完整底本、权重目录identity/TP/epoch/generation、块建立和lease、HIP IPC、serial/double_buffer能力、共同rank_interleaved、fastest/LP/成组简单placement、有限候选multi_joint、全SHA/固定输出与清理验收。无证据要求另建owner、新MILP或通用异步执行器。

**minimal：**下一阶段只补现有实机runner的冻结契约桥接：接受外部candidate extents与各位置预算；每次绑定一个互斥target；把hypothetical candidate转成真实已校验副本，并重绑实际ID/epoch；共用rank_interleaved；计缓存建立、规划、prepare、目标与staging/物理峰值；沿原SHA/输出/回收门槛执行。T1仅需最薄配置桥接，T2需替代硬编码4+4并绑定目标，T3还需keeper_serial的真实路径/共享影响证据。CPU registration是reviewable输入，不是可直接提交GPU的有效权重目录。

在placement比较之前，同一有限candidate、来源、复制机会、预算、未来目标和executor上的强简单比较必须补齐。优先复用已有成组/边际greedy，允许与joint相同的重复块机会；当前fixed layouts不能单独代表这项强对照。必要时仅对这几个有限候选做离线一致性核查，不建设全局求解平台。

当前所有CPU预览仍使用functional provisional profile。isolated网络/H2D测量不能直接替换keeper_serial有效路径模型，尤其未测peer、反向网络与DRAM-staged network。若某条路径决定下一候选选择，只补该生产执行器路径的最小验证；有证据改变选源/布放且实测T_ready兑现时，才加一个必要共享资源约束或评分项。完整e4/e5必须另登记并执行正确性与主指标验收，本轮没有完整模型收益。

**defer：**全面基线移植、relay/组播/collective合成、通用多流重写、新求解器、自动复制、预测、全历史归档及e7/e8。负结果或同解保留；不通过增加有利场景救算法。

## 停止规则

本轮在九配置与三冻结场景完成后结束。后续若强简单策略和候选仍同布局/同来源/同顺序且无独立机制差异，停止完整恢复比较；若存在可解释差异，先一组有界e4/e5完整模型对照，异常/歧义才补第二次。应用共享证据未满足时保留T3条件状态，不为让S5-P通过而扩大执行器。完整结果不改善主指标时采用简单方案并调整主张。

独立验收命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/audit/report.py --calibration codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01/summary.json --freeze codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_freeze_probe_r08/manifest.json --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/audit_final_r02
```
