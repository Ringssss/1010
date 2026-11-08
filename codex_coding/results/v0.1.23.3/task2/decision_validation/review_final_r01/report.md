# 最小决策验证独立验收

结论：CPU范围检查通过；真实joint收益仍未证，当前不启动策略GPU比较，不增加solver或候选矩阵。受限缓存的三个符号区间，原strong_simple和joint在同资源约束、相同路由机会及同有序服务指标下均同成本。只因等大块身份或操作先后不同产生的签名差异不能记为收益。

验收入口为qualification.json：52项独立条件全部通过。检查读已冻结的case_cpu_r02原件，不重复求解器，不新增CPU配置。review/audit_artifacts.py从实际selected IDs、每op来源、byte extent和符号系数独立重算worker ready、timeline与最后完成时间，检查全部覆盖、九种合法子集、位置/全局预算、源码SHA、contract绑定、同成本标识，以及测量合同与只读刷新绑定。

| 符号case | strong_simple | joint | 固定fastest路由的有限参考 | 裁决 |
| --- | ---: | ---: | ---: | --- |
| 分散目标，local1/remote3 | 1ms | 1ms | 1ms | 同决策同成本 |
| 分散目标，local2/remote2 | 2ms | 4ms | 2ms | LP等价目标tie允许选串行D，依赖评分可识别 |
| 分散目标，local3/remote2 | 4ms | 4ms | 3ms | 双方共享的LP模型误排；simple也不是服务最优 |
| 两目标28，localHBM0.5/DRAM1/remote3 | 1.5ms | 1.5ms | 1.5ms | rank等价置换，无增量 |
| 两目标28，localHBM0.5/DRAM2/remote2 | 2.5ms | 2.5ms | 2.5ms | rank等价置换，无增量 |
| 两目标28，localHBM0.5/DRAM3/remote1 | 1.5ms | 1.5ms | 1.5ms | rank等价置换，无增量 |

这些值全部是人为声明的符号输入。第三行中的remote快于local只用于模型反例，不假定实机属于该区间。LP tie的具体布局还受求解器选择影响；本验收绑定r02输出，不把D当作所有solver运行必选。有限参考只枚举合法缓存子集，固定fastest整块来源；未搜索任意分割、顺序或固定成本，不能称全局服务最优。

原strong_simple的直接调用和同candidate copies已核查。自写边际枚举器明确diagnostic-only，未冒称已有基线。原service_cost的DAG已存在；具体工程缺口为managed服务重排只含bandwidth-MILP/base-only/current proposal，原resource_lp路由没有按固定op成本重选。本轮仅识别该边界，没有真实证据要求实现扩展。

预算与测量合同在原32MiB DRAM底本、总32MiB缓存上限、各位置16MiB上限、32MiB目标、每worker64MiB staging及runtime另计的范围内。两目标同worker仅是synthetic块恢复，未获TP模型资格。measure_refresh_r01的身份、quiet/noOOM及空端口只是只读现场事实；fresh runtime准入、完整peak、同primitive成本、真实决策差异均未完成。measurement_contract仍case_gate_ready=false、GPU命令null；calibration与holdout不互相拟合的限制通过检查。

失败记录已额外只读复核：case_cpu_r01/failure.json保留系统Python缺numpy的partial contract，明确追记元数据而非原stderr；case_cpu_r02/execution_metadata.json记录已有sglang Python运行rc0、6个配置，无安装。原失败未被覆盖。独立审核命令为`python3 codex_coding/src/v0.1.23.3/task2/decision_validation/review/audit_artifacts.py --output codex_coding/results/v0.1.23.3/task2/decision_validation/review_final_r01/qualification.json`，结果52项通过。scope_r01/dependency_audit.json另有4项独立模型边界通过。

本轮GPU启动、远端写入、signals、新产品solver均为0。接受此负gate作为本轮完整结论；若后续明确需要同primitive绝对成本认知，应按冻结测量合同仅观测必要路径和一个独立组合，仍不把该工作称为已获joint收益。当前physical_shared_contention_gain、real_joint_benefit_proven、prediction_ready均false。
