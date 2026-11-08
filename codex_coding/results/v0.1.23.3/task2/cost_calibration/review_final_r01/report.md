# 库存定位与关键路径校准：独立实际证据审查

日期：2026-10-10；编号：v0.1.23.3/task2。仅本地CPU读取实际结果与冻结源码，无SSH/GPU操作。

实机执行与证据链通过，完整成本模型未识别。最终session为`costcal_runtime_r02`，九个warm校准配置、仅本地HBM路径三个配置按事前规则反序各重测一次，以及cached→legacy同内容混合恢复各一次均完成。原fitter因本地HBM负斜率拒绝拟合，冻结model与prediction明确为null；没有有效的独立预测误差可报告。没有舍弃该路径、补造正速率或依据holdout调参。

## 库存查询定位

两次legacy与两次cached均在mesh/pair warm完成且无payload时执行。完整controller中位数由60.576ms降至6.190ms；最大worker窗口中位数由57.899ms降至2.723ms，最大library_resolution＋CDLL分段由55.292ms降至0.002ms。原字段保持，只有库路径与CDLL句柄按PID缓存；Catalog、epoch、事务、HIP BDF、PCI/NUMA、HCA、proc/cgroup、RSS和预算持续fresh。缓存初始化的legacy请求sequence保留，worker分段＋残差守恒。

这些观察支持重复查库是约62ms库存查询的主要负担。每mode n=2，额外诊断计时与模式文件读取仍包含在完整请求窗内；各worker时间戳只在自身时钟域解释，controller与worker时长差仅作残差。

## 成本拟合的实际结果

单path三配置为4MiB单op、16MiB单op、总4MiB四op。下表是实际目标worker时间及原schema2公式的独立算术，非完整模型profile。

| 路径 | 4MiB单op ms | 16MiB单op ms | 4MiB四op ms | raw op ms | raw request ms | 正斜率有效GB/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| w2 DRAM→w4 HBM | 0.838 | 2.074 | 1.710 | 0.291 | 0.135 | 10.183 |
| w4 HBM→w4 HBM，规定两次均值 | 0.381 | 0.277 | 0.676 | 0.098 | 0.318 | null |
| w4 SSD→w5 HBM | 7.571 | 21.272 | 11.843 | 1.424 | 1.580 | 0.918 |

本地HBM首次small/large为0.378/0.281ms，规定重测为0.384/0.274ms，均值斜率为−8.26454×10⁻¹²秒/字节。原fit_request_model实际重算仍拒绝，schema1也拒绝同一路径。两条正斜率路径可作本轮warm有效服务诊断，其中SSD包含HTTP读、主机buffer、GPU暂存和peer传输；不把它称为纯盘带宽。每path raw request相差明显，也提醒共享request中位数假设需要单独验证。冷pair gamma未测，derived cold兼容输入标observed=false并保留warm来源hash。

## 混合恢复与主窗

holdout按local HBM w4→w4、remote DRAM w2→w4、SSD w4→w5的顺序各12MiB，保留source/target ready依赖。两mode来源id、identity、epoch/generation、字节范围与顺序完全相同，目标对象分别按生命周期建立。每组36MiB全字节SHA通过。

| 固定执行顺序 | execute ms | 请求→filled ms | 请求→published ms |
| --- | ---: | ---: | ---: |
| cached | 23.142 | 42.207 | 114.392 |
| legacy对照 | 23.286 | 156.026 | 228.260 |

主计时起点在fresh inventory、预算gate和必要planning之前；内层Cluster.transfer窗单独保留。两组数据执行时长接近，固定顺序端到端观察差异约114ms，符合每个请求包含两次库存查询的流程。legacy仅配对观察，不入fit；顺序、时间漂移、缓存状态与n=1限制仍在。没有有效预测，排序准确性、布局收益、solver价值和真实live模型T_ready均未认证。

## 资源、清理与来源

同HIP绝对采样上界node28/node30/node27为1,124,073,472 / 1,281,359,872 / 1,826,619,392B，均低于聚合2GiB；DRAM supervisor样本最大值加256MiB仍低于聚合6GiB。runtime与staging包含在样本内，shared cgroup取一次，未混sysfs/HIP精确计费，也未认证完整物理峰值或共享容量。

实际仅pair24/45 communicator，PID/GPU/BDF与RCCL日志一致，跨node为IBext_v8/shca_0/gdr0。最终own Catalog使用、事务与SSD文件全零；九supervisor rc0，九child为owned STOP→TERM/−15，无KILL，三nodefresh PID absent/group empty/pids1。旧13owner、旧6/2/4容器、boot与host XML保持；stage全资产SHA在baseline/after保持，原产品service_cost.py与初审snapshot一致。

首次`costcal_runtime_r01`因运行预算monitor复用了“必须无活动事务”的准入谓词而自然失败，七partial训练、源码与日志原件保留，不参与r02拟合。修订只将monitor与idle gate分离，身份与预算核查、原事务字段保留。r01残留16MiB自有SSD文件按journal id、文件名hash、bootstrap全SHA、CID/boot/quiet精确清除，其他文件保持。

证据入口：`qualification.json`为综合独审，`review_measure_r01/qualification.json`保留195项中的model_identified失败，其余194项通过；`review_operations_r01/qualification.json`42项实际日志/预算/清理检查通过，`review_actual_protection_r02/qualification.json`复用原保护审查通过。所有来源均有独立snapshot与SHA。review_retry_launch_r02误将允许修改限定为两个runtime文件，r03只修正审查条件以承认prior-setup支持和定向CPU检查，原失败记录保持。
