# 同尺寸资格查表诊断固定规则

本文件在本轮离线计算输出之前固定方法；这是 GPU 实验结束后的分析规则，不是 GPU 运行前预注册预测。既有四 case 是 holdout 记录，不进入参数拟合或规则选择。

- 训练记录仅 operations_actual_r01/qualification/qualification.json 的 23 个单操作资格传输；每路径一次、每次 4MiB。
- 查表键严格包含源 worker/tier/NUMA、目标 worker/tier、4MiB、serial、32MiB chunk、相同 session/epoch、必要 pair 已 warm；不把 HBM 和 DRAM 目的层混用。
- 单个操作服务值唯一采用资格传输目标端 role=target 或 both 的 records.elapsed_s。它含同步和对端/到达等待，是有效端点服务时长，不是纯链路时间。
- 按 service_cost.estimate_execution 的递推：start=max(ready[src],ready[dst])，end=start+lookup_s，更新两端 ready。所有操作严格同尺寸，不拟合 alpha/beta，不外推尺寸，不估冷启动。
- controller residual 唯一采用所有资格传输 execute_wall_s-max(worker.execute_s) 的非负中位数；作为 dispatch 经验项。request 成本不可辨识，数值占位为 0，不表示已测得 request 无成本。worker 循环/日志等未辨识项保留在误差中。
- filled 的经验控制项分别为上述 23 次资格 inventory_rpc_s 与 prepare_rpc_s 的中位数，只加资格控制项，不加实际 case 控制时间。published/SHA 不作预测。
- 报告每个恢复及保存传输的服务 DAG、controller execute、filled 的预测/实测。signed_error=predicted-actual，relative_error=signed_error/actual；实际值是误差分母。正式模型精度不据此认证。
- 不扫描参数、不试多个汇总规则选最优、不读 case 回写参数。SSD buffered/pagecache 状态未标准化；单点与单请求向多操作请求转移、远端等待重复计入递推及不同数据源/目标总大小均是适用性限制。
- 原件只读，新输出目录独立创建。无 GPU、SSH、安装或产品代码修改。
