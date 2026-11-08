# 成本账本辨识与冻结

本目录只分析完整 native execute 方法外窗；`service_ns` 为原内部 op elapsed 加总的诊断候选，两个边界分别拟合，不相加。模型是条件化有效费用，不是物理带宽。协议来自成本辨识计划第5节与6.1节，单 worker node27 GPU0 / world1 配置独立于旧六 worker 会话。

`analyze.py protocol --output-dir 新目录` 冻结机器可读门槛和本目录 Python 源码 SHA；可重复提供 `--instrument-source 文件` 纳入实际仪表源码。最终执行源码稳定后由主 agent 冻结全部执行引用，冻结后任何源码变化均拒绝。

`analyze.py calibrate --bundle stage_measurements.json --predeclared predeclared_protocol.json --output-dir 新目录` 要求六个校准请求顺序 L3/L1/L4/L2/L5/L3_repeat；前五个拟合，重复锚点只判断漂移。输出 `frozen_prediction.json`，含所有原始费用、直接差量、无约束有符号 OLS、候选残差、分层账、前五个中位控制残差及两份预测。C1失败时正式 `predictions=[]`、`selected_worker_before_holdout=null`，其系数和候选数字仅属于 `diagnostic_C1`，明确跳过留出。

`analyze.py validate --bundle stage_measurements.json --frozen frozen_prediction.json --output-dir 新目录` 要求新会话 V2/V1。先从校准原件独立重建冻结包，再比对预测；不使用任何留出实测控制残差重新拟合。每点每层绝对与相对误差须同时通过，绝对门固定 service 50µs / worker 250µs / controller 1ms / filled 2ms。published/live 预测始终 null。外层失败保留局部 worker 资格，任何结果均不授予布局决策或GPU启动资格。

输入合同为 `cost-ledger-v1` / `native_serial_thread_scoped_noevents_v1`，配置和状态必须与预声明完整一致。每条样本须有 session/request/target 身份、实际 N/K/chunk/copy/sync/字节计数、四层正整数 ns、全SHA/闭合/生命周期状态及带 SHA 的原件引用。分析程序核引用完整性，独立审查负责核对原件中的真实边界、非重叠、闭合与状态；布尔通过标记本身不是硬件准入。

纳入模型的所有原始系数非负，是明确预声明的建模选择；不截断负数。四项模型另须通过 c/d/32b 信号和第五点一致性门；简单模型只判断自身残差与非负有效性。弱尺寸信号仍标 unresolved，允许简单经验聚合，不称已识别的物理零或带宽。各层观察尺度由固定5µs、空clock额外p95、dummy log变化及锚点漂移的最大值定义，只用于资格与细模型收益比较，不能放宽留出绝对门。

留出前选择最简单的合格候选。留出后的更细候选，即使各层分别通过，也必须对留出前已选简单候选在两个点每一相关层都有超过该层 E 的绝对误差改善，才证明新增复杂度有收益；简单候选留出失败也不取消这个比较。达不到则保留 `complexity_increment_unproven`，可以保留单独候选资格但不自动晋升完整模型。

CPU资格：`python3 test_analysis.py --output-dir 新目录`。全部数据为人工合成，包含负系数保留、漂移失败、简单模型通过、结构失败、旧状态/配置/模式拒绝、不完整数据/计数/原件变化拒绝、冻结预测篡改、绝对与相对联合误差门和外层失败保留局部资格。
