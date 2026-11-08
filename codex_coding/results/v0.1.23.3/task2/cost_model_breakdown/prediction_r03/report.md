# 六 worker 成本预测证据与同尺寸诊断

本轮实机没有运行前冻结成本预测。runner/adapter 使用 frozen_witness，estimated_transfer_s=None，明确无 rate model/runtime prediction。历史两路径 service 模型的 topology_id、worker 编号、NUMA、路径和拟合尺寸与本轮不符，不能移植后报告正式 out-of-sample 误差。

以下是 GPU 实验完成后固定规则的离线 single-size lookup diagnostic。23 条先执行的单操作资格记录作为唯一参数来源，四个 case 的实测时间不进入拟合。它检测同尺寸单路记录向操作组合转移是否吻合，不是原 alpha+beta 成本模型正式校准。

资格经验项：dispatch=3.092064ms，inventory=62.050982ms，prepare RPC=4.530871ms；request_s=0 为不可辨识占位，不代表请求无开销。

每操作采用 qualification 目标端 target/both elapsed_s，按原 service_cost 的两端 ready 递推；不是全部操作串行相加。Worker elapsed 包含日志、同步、对端等待；disjoint pair 可同时推进。表中误差统一 (预测−实测)/实测，不使用预测作为分母。

| case/阶段 | DAG服务预测/worker实测(ms) | execute预测/实测(ms) | execute误差 | filled预测/实测(ms) | filled误差 |
|---|---:|---:|---:|---:|---:|
| SSD_only/restore | 57.384 / 59.633 | 60.476 / 63.390 | -4.60% | 127.058 / 132.188 | -3.88% |
| C0/save | 57.384 / 59.214 | 60.476 / 61.970 | -2.41% | 127.058 / 128.490 | -1.11% |
| C0/restore | 1.002 / 2.061 | 4.094 / 5.290 | -22.60% | 70.676 / 72.915 | -3.07% |
| C1/save | 56.291 / 60.347 | 59.383 / 63.871 | -7.03% | 125.965 / 136.571 | -7.77% |
| C1/restore | 1.683 / 2.745 | 4.775 / 4.685 | +1.94% | 71.357 / 71.450 | -0.13% |
| C3/save | 31.498 / 29.007 | 34.590 / 31.043 | +11.42% | 101.172 / 103.253 | -2.02% |
| C3/restore | 13.810 / 11.411 | 16.902 / 14.138 | +19.55% | 83.484 / 81.052 | +3.00% |

filled 预测只加资格阶段控制项中位数；没有加实际 case 的库存/prepare 时间。约62ms库存项能压低总窗相对误差，不能遮蔽 execute 或服务段误差。published 还含全SHA/发布，未校准所以不给预测。

严格匹配已通过：23键包含目的层（非HBM追加 :dram），没有目的层碰撞；四恢复以及三个保存的路径/层/尺寸/executor/chunk均覆盖；session/epoch一致，必要六pair已在资格前 warm。资格与case为同host不同controller进程，完整clock_domain不同；仅每个transfer内部取同钟差。run_session同步等待qualify完成后才调用cases，两controller记录rc0，证明资格先于case；不跨clock_domain用绝对ns相减证明顺序。

恢复段中 C0/C3 execute 误差约−23%/+20%，查表还把 C0/C1 的单次 execute 顺序预测反了。C0/C1实际差仅0.605ms且每配置一次，不能认证统计排序，但足以阻止把本诊断宣称为已经验证的排序器。服务DAG与worker envelope误差仍应单看，filled的较小误差不替代它们。

适用限制：每路径仅4MiB一个点，不能同时辨识固定项与带宽；本诊断直接查表，没有拟合alpha/beta/gamma，也没有创建正式schema1/2 service_model。若把lookup编码为零固定/零冷项和等效rate，零项仅为同4MiB重现数值的约定，不是已经识别的物理参数，不允许尺寸外推。单操作与7/8操作请求不同，目标4MiB与整rank8MiB不同，loop/request开销未分离。跨worker的目标elapsed包含未对齐启动/对端等待，再按DAG相加可能重复计等待；SSD源文件、pagecache/磁盘状态及组间变化没有控制。n=1不能统计检验排序，不能外推模型规模、冷盘/冷pair、共享HCA容量或live模型T_ready。

最小下一步：先用一个无数据控制请求/库存诊断确认约62ms观测成本；成本测量只补目标决策必需路径，固定同worker/incarnation/层/chunk/warm状态，各取两个warm尺寸以分离固定项与字节项（沿用fit_path/fit_request_model）。若请求成本需要单列，增加等总字节、不同操作数记录；冻结参数后仅执行一个独立尺寸或组合holdout。冷pair仅在研究冷启动时单列一次。不要重建模型或扫描104机会。

完整每op lookup、DAG时间线、误差精确值、资格经验项、原件SHA在summary.json；规则在源码rules.md。此次仅离线CPU分析，无GPU/SSH/产品修改。
