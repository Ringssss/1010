# 成本账本执行交付

范围：v0.1.23.3/task2；执行子agent。写入只在cost_ledger/execution源目录及新的execution_*结果目录。产品源码、旧结果、XML、owner/container配置均未修改。子agent未启动GPU或SSH。

## 已交付

- protocol.py冻结C1六点与C2两点、world1/node27GPU0状态合同、固定数值门。
- worker.py直接继承native执行循环，仅thread-local primitive hooks和完整原execute独立外窗；真实HTTP线程出生代际、source/target分配与写史、实际copy/sync/land计数、原float记录与raw ns分开保留。
- controller.py、runner.py复用native Cluster.transfer与完整SHA发布/finish协议；请求起表早于fresh inventory、预算检查、plan构建，分区与父窗独立记录，未解释空隙不作机制命名。
- prepare.py、session.py复用精确container/source/XML readback、supervisor、STOP与artifact collection；仅node27GPU0单worker，没有SSD或remote warm，runtime与staging计入预算。
- gate.py在C2 build/stage/session/runner之前重算冻结包、核引用和newsession，要求独立C1_raw_resource_model资格，CPU模型不能授权GPU。
- mode_file.py标准库隔离CLI独占创建cached配置；README.md给出schema、边界、资源和旧监督目录要求。

## CPU资格

execution_cpu_r04/qualification.json：55项通过。实际native HTTP与init_mesh/synth/adopt/prepare/execute/_execute/land/verify_run/complete/finish/retire，以及真实Catalog租约状态机均执行；torch/注册NUMA owner/runtime inventory为明确CPU模拟。96MiB源bootstrap、六校准请求、全SHA及精确释放通过。primitive_trace.json由模拟primitive独立记录实际字节copy、source/target offsets、nonblocking及sync。完整原方法包含set_device，worker与请求ns分区整数闭合。没有GPU事件、pair组或broadcast。隔离模式CLI首次成功、重复noclobber拒绝。

独立review_native_cpu_r02/qualification.json：215项raw检查通过。schema提供分析agent的原生CPU bundle ingestion检查；CPU时延不能作为硬件系数。

execution_C2_protocol_cpu_r02/qualification.json：10项synthetic协议检查通过。覆盖冻结精确重算、CPU→GPU拒绝、C1failedgate、冻结篡改、旧session、C1 rank0 prior→C2构建、master29981准入/GPU0角色及最终CPU plan再次GPU拒绝。仅这次build构建采用显式cpu_only gate替身；没有stage或启动，模型时延为明确synthetic fixture。新增cpu_gate_check.py在测量源冻结后创建，不属于主agent已冻结/部署的11个资产。

失败原件保留：native CPU r01的模拟Owner缺numa，r02的真实inventory无endpoint暴露并修复为显式cluster.endpoints，r03的模拟trace timedelta JSON写入错误。r04是唯一完整最终CPU成功。补充C2 protocol r01的synthetic ns浮点类型错误保留failure.json，r02修正整数类型。

## 主agent现场启动结果（只读检查）

主agent冻结analysis_predeclared_r03并build/stage execution_prepared_C1_r01后运行execution_actual_C1_r01。GPU worker启动与native world1初始化完成，但mesh阶段的冻结预算门失败，bootstrap与六个C1测量均没有开始：journal.GPU_measurements=0，owned=[]。same-HIP total=68,702,699,520B，free=10,487,857,152B，selected-device绝对used=58,214,842,368B；任务torch allocated/reserved仅64MiB staging。当前门以包含其他GPU占用的绝对upper保守计账，超过2GiB taskcap。没有降低门槛、改冻结源、清理其他owner或重启实验。

execution_actual_C1_r01/summary.json保留失败；唯一supervisor rc0，精确STOP与artifact收集通过。下一步由主agent统一决定资源会计与现场准入，不能把CPU通过写成C1硬件完成或补C2。
