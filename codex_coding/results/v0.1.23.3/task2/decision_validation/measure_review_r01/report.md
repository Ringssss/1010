# 原产品计时与最小测量审查

开发编号：v0.1.23.3/task2。只读审查与资源刷新；本轮 GPU 启动、信号、远端写入均为 0。当前没有已证需要新增 GPU 样本的策略差异。

## 可复用接口与时序含义

- `Cluster.transfer` 已分列 `prepare_s`、`execute_wall_s`、`verify_and_publish_s`，并保存单 controller 时钟的 `load_begin_ns/all_targets_filled_ns/all_targets_published_ns` 与逐 worker RPC telemetry。
- `execute_collection` 可复用同一窗口、保留完整验证和 finally finish；固定缓存建设在恢复窗外记录。完整 synthetic SHA 对源和目标各读一遍，发布窗不能当传输成本。
- `ClusterWorker._execute` 按相同 op 顺序执行本 worker 参与的操作，serial 每 chunk 都 GPU synchronize。local 是直接 land；远端 HBM 是 source HBM→staging→broadcast→target land；远端 DRAM 是 registered host→source staging→broadcast→target land。原 16MiB 小于默认 32MiB chunk，只有一个 chunk。
- 两 worker 的远端操作均使用同一 pair，并且按顺序同步。D 的逆向、正向不是同时双向通信；local-only 的不相交 worker 可以独立推进。
- `records.elapsed_s` 与 worker `execute_s` 含 Python、日志、同步和对端尚未进入 collective 的等待。独立单 op 可去除前序本地 op 造成的等待，仍不能去除 RPC 派发偏斜。不得相加 source/target 时长、跨机 monotonic 相减，或把 `n/elapsed` 当恒定带宽。
- `service_cost.estimate_execution` 已实现 `start=max(ready[src],ready[dst])` 的有序依赖模型；既有 service rerank 可复用。无需另建执行器或求解器。`fit_path/fit_model` 要求两尺寸与 cold/warm 样本；`fit_request_model` 还要求等字节多 op 样本。单个 16MiB 样本不足识别 fixed/byte/request 参数，不应强行套用这些拟合入口。

## 决策先于测量

当前两个 16MiB 目标分别在28/30，各位置可放16MiB、总cache32MiB时，强nearest目标本地=A可以同时保住两块。若 localHBM 在两目标均不劣于相关 DRAM/remoteHBM 的有限服务成本，A在每个目标上逐项占优、没有pair依赖，联合规划没有额外空间。这个结论须标明服务顺序的前提；当前真实结果支持A短，但未证明所有替代路径的成本不等式。

若强simple与联合规划的选择签名相同，停止新增GPU测量；声明symbolic CPU反例只能测试已有排序器能否识别依赖，不应为了制造实测差异扩缓存、底本、GPU或并发矩阵。只有冻结目标/候选/预算后出现非重复选择，并且具体成本不等式仍未定，才测该不等式涉及的路径。

必要候选路径为 localHBM28、localHBM30、HBM28→30、HBM30→28，以及完整底本位于28时的 localDRAM28、DRAM28→30。按具体选择删去不相关项；不默认测 DRAM30 或增加底本。每路径最多一次16MiB同产品单op观察，保存 source/target role elapsed、worker execute、controller分段和原计划；把它称同工作点的有限服务观察，不输出恒定GBps或形式容量。

## 独立组合 holdout

若路径观察确实改变了选择，先冻结已有依赖模型、选择签名、预测使用的标量口径及误差验收条件，再运行一个未用于参数观察的实际多op组合。HBM-only最小结构可用逆向后正向，但它只检查同pair顺序相加与派发/等待误差，不测并发。混合DRAM/HBM的holdout必须来自实际待决策方案；不盲跑所有A/B/C/D。

holdout 保持16MiB块、原serial/defaultchunk、同warm pair和worker incarnation；完整SHA在filled后，prepare/execute/verify/publish分开。校准和holdout的目标/cache分别退役，lease=0/transactions空后再下一阶段；观测失败或非增量预算失败保留原run，不反算holdout成本后再称准确预测。一次16MiB标量观察最多支持同工作点组合误差检查，不能生成两尺寸线性模型、未知共享竞争参数或完整模型T_ready。

## 预算与刷新

前次原baseline下runtime增量约2.03GB；node28实测全程增量2,118,258,688B，距2GiB仅29,224,960B。维持两块底本总32MiB、逐阶段退役复用，不增加新的host底本或128/256MiB旧校准样本。前次8MiB margin仍低估实际增长；下一次须由parent重新baseline/postwarm准入，绝不改预算或改用postinit baseline吞掉runtime。

只读刷新入口：`../measure_refresh_r01/summary.json`。2026-10-10 07:48:49/50 UTC 两容器身份连续、pids.current=1、noOOM、原source/旧keeper保护通过，34500/29972 listeners free。这只是现场元数据，不是GPU初始化、runtime预算或完整物理峰值准入。复用冻结guard、refresh_host、listener_host；未访问27/29，未读取vramtbl，未调用HIP/SMI。原XML/defaultmapping不改。

## 证据与验证

必读并核对：计划18.19、正式记录34、layout_validation/review_final_r02/report.md、execution_r01/execution.json，原 cluster/control.py、cluster/worker.py、benchmarks/calibrated_recovery.py、cluster/service_cost.py 及旧 run_cost_calibration.py/run_request_cost.py。独立解析原execution确认A/B/C/D时序角色；旧cost脚本为不同worker/128–256MiB模型，不满足本轮有限预算与primitive场景，未执行。未修改产品或实验源代码，未新增实现测试。
