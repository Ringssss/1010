# 首操作状态 A：独立验收与停止判断

结论：A执行证据通过，状态结构 `unidentified`，止于8个请求，不启动B。独立raw生命周期/时钟/目标/SHA检查164项通过；独立资源与精确清理59项通过（`review_session_A_r01`）；独立三候选系数与freeze拒绝门4项通过。原模型、产品源码与分析门不变，本审查无SSH/GPU/远程写入。

八请求每请求第一copy的非log主机wall/CPU均高于第二copy，source slice CPU也一致下降。这里能确认实际路径内的首copy主机差额，不能确认某个可迁移的线程或请求生命周期状态项。

| cell | 模式/目标 | R1非log CPU 首−后(µs) | R2非log CPU 首−后(µs) | R2 source slice CPU 首→后(µs) | R2整个op 首→后(µs) |
|---|---|---:|---:|---:|---:|
| 1 | F/fresh | 61.041 | 225.434 | 122.972→13.720 | 428.156→92.401 |
| 2 | P/fresh | 45.292 | 30.440 | 25.860→12.300 | 186.603→86.791 |
| 3 | F/pretouched | 110.252 | 233.962 | 127.481→12.271 | 444.866→89.311 |
| 4 | P/pretouched | 188.724 | 187.743 | 99.531→12.730 | 384.495→88.001 |

P/fresh两个请求实际executor birth均为`e159e94ebc0341448b7503a03c490691`、native TID6794；P/pretouched均为`8a29b8ae4c3e44dfbe02fbba8b71dda7`、TID6729。两者累计op分别1,2→3,4，R1不join且R2开始存活，R2结束join。故持久线程证据成立，而R2首copy仍有30.440/187.743µs非log CPU差，均超过冻结2S=10µs；纯线程首状态预言不成立。

S=5µs来自事前公式；worker CPU空paired clock p95=.590µs，dummylog p95=15.650µs、波动p95=2.989µs，仅单列未减去。R2 F−P非logCPU在fresh/pretouched分别198.243/47.420µs，都超过request模型要求的S=5µs，不能把持久线程R2首差直接归为统一request项。P的fresh−pretouched service首−后差交互为−196.682µs，超出冻结57.176µs界；F交互−19.800µs在界内。目标条件与状态大小仍有明显交互，按协议停止，未修改阈值或增加解释参数。

三候选仅A内拟合：C0 p=205.8095625µs；C1 p=90.889750µs、r=229.839625µs；C2诊断thread p=129.821600µs、h=202.6345667µs。独立用相同16op原始记录重算一致。固定两个copy时C0请求服务`2p`与C1的`2p+r`均为411.619125µs，C1只重新分配请求内部首/后成本；若C2按request首状态，也与C1同构。C2状态规则标记`thread_diagnostic_only_not_admitted`；`model_frozen_r01/frozen_prediction.json`的`frozen=false`、B predictions空，不是可部署模型，没有holdout预测误差或主指标收益。旧bounded正式预测与published预测保持null；固定8MiB未识别带宽。

每stage128MiB测量payload加64MiB干预，bootstrap16MiB另列，合计208MiB payload；测量逻辑最终SHA64MiB、bootstrap逻辑16MiB。每目标两copy后一次最终全SHA，没有把16轮当独立校验。全部两copy、prepare scratch/pretouch、创建/queue/join、set_device、endlog/gap、后诊断保留在对应实际窗；没有用第二copy替代首次恢复时间。

主会话49.706s含启动、mesh、输入建立、矩阵与退出。九STOP/九supervisor返回0，九child按计划TERM/-15退出，全部PID absent/组空；最终Catalog有效项、used、事务和三SSD文件为0。原13owner/12container与拓扑XML保持；三节点冻结135source SHA在会话前后相同。

DRAM最大已见样本node28/30/27为3,482,730,496 / 3,988,688,896 / 4,286,472,192B，均小于6GiB；同HIP绝对HBM采样上界为1,124,073,472 / 1,281,359,872 / 1,868,562,432B，均小于2GiB。HBM最高来自请求准入gate，监控峰更低；共享cgroup采样按node最大值核对，未累加重复进程读数。完整连续peak未测定。

本轮收口为首copy主机成本可见、具体可重置状态未辨识；没有认证具体TLS/库函数、裸DMA、通用request系数、跨runtime或placement收益。保留全部长点与原始记录，不继续B、不现场救模型。
