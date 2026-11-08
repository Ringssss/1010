# 首操作状态阶段A执行验收

实际 session `first_op_runtime_A_r01` 完成八请求；211 项执行证据检查通过。九 supervisor rc0，九 child 按 own STOP→TERM 退出且进程组为空。

|cell/request|executor/target|op1 µs|op2 µs|source_slice CPU1/2 µs|worker ms|full ms|
|---|---|---:|---:|---|---:|---:|
|cell1.R1|F/fresh|228.393|90.171|48.160/13.490|1.566401|22.006556|
|cell1.R2|F/fresh|428.156|92.401|122.972/13.720|1.789634|20.730898|
|cell4.R1|P/pretouched|393.025|90.811|101.632/12.710|2.159640|23.198772|
|cell4.R2|P/pretouched|384.495|88.001|99.531/12.730|1.534610|20.487106|
|cell3.R1|F/pretouched|297.954|101.641|72.471/13.350|1.748404|25.748616|
|cell3.R2|F/pretouched|444.866|89.311|127.481/12.271|2.368212|22.596944|
|cell2.R1|P/fresh|202.343|87.991|35.691/11.970|1.373859|22.635705|
|cell2.R2|P/fresh|186.603|86.791|25.860/12.300|1.317948|20.432254|

F 的 R2 executor 为新 birth，P 在 R1/R2 保持同一存活 birth，累计 op 序号从1/2推进为3/4；四 cell 的准备线程各独立于 executor，并跨两请求保持同 birth。A/B/scratch 地址范围不重叠且同时持有 lease，fresh 写 scratch、pretouched 写目标；状态全程遵循原产品生命周期。

每阶段128MiB测量复制、64MiB准备干预复制，bootstrap16MiB另计；八目标各8MiB全SHA，共64MiB测量目标逻辑SHA，另bootstrap16MiB。两次copy共用目标，仅末次全SHA，不称每轮独立SHA。

|node|DRAM观测最大 B|HBM同HIP绝对上界观测最大 B|
|---|---:|---:|
|node28|3482730496|1124073472|
|node30|3988688896|1281359872|
|node27|4286472192|1868562432|

预算合并 postwarm gate、active monitor、八请求 fresh gate 与 supervisor cgroup 样本；所有观测低于每node6GiB DRAM/2GiB HBM。完整连续 peak 未测，具体最大值来源见 summary。

旧保护资产保持：13 个 owner、12 个 container；原XML SHA不变。最终 VALID/used/transaction 全空、三 SSD files0。

worker/op wall与threadCPU分账及controller/full窗逐请求精确闭合。线程create/queue/join、prepare干预、详细诊断均按实际请求留窗；报告只比较本机duration，没有跨机器timestamp相减。

首/后copy差额及因素判别交给事前冻结模型分析；这里不决定B准入，不调整系数，不追加GPU测量。source_slice、logger、copy/sync的首差异不自动指认底层TLS函数或裸带宽。
