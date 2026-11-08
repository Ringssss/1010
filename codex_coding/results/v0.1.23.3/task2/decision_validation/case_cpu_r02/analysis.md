# 决策门槛结论

当前没有真实joint收益证据。详见[报告](report.md)的两个冻结case、各三个符号区间表；原strong_simple与joint在三个目标集中28/cache不足区间都给出相同有序服务目标，只出现等价block身份/执行顺序的签名差别。停止策略GPU对比和候选扩张。

原分散目标敏感性保留有价值的模型反例：同符号local/network2ms时，强simple选A依赖成本2ms，LP可能在等价目标中选D依赖成本4ms；local3/network2ms时，二者共同选D4ms，而A仅3ms。这证明原LP边界，不证明实际硬件时延或joint收益。现有service_cost已实现src/dst worker依赖，无需新solver。

`finite_oracle`字段只是合法cache布局×固定fastest整区间路由的有限参考，非全局服务最优。强simple和joint保留原resource LP objective及来源机会，然后同既有DAG scorer复评；自写边际枚举器显式diagnostic-only。原32MiB完整DRAM底本、四个候选、重复缓存机会、cache总32MiB/位置16MiB预算相同。

下一步只在确实影响冻结决策时观察同primitive的28 localDRAM、28 localHBM及30 HBM→28；不取微测GBps替代，不扩底本，不因符号反例启动GPU。实际成本、fresh预算、完整物理峰值、模型rank映射与真实决策差异均unknown。

原函数与实际脚本SHA见[contract.json](contract.json)，每个方法selected IDs、ops和timeline见[inspection.json](inspection.json)。原r01为系统Python缺numpy，partial contract与追记失败metadata保留；r02使用已有sglang环境一次完成6个CPU配置，没有扩大矩阵。
