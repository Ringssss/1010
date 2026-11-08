# 28 ↔ 30 选定 IB 路径独立验收

最终功能通过：node28 GPU2/BDF55 ↔ node30 GPU0/BDF09，两端实际使用 `IBext_v8`、`shca_0:1`、gdr0；fresh init、1MiB copy、跨节点全量 SHA、GPU/NIC 物理映射和自有 XML runtime 均通过。r03 含初始化的进程总耗时分别25.784592082/25.600282903s，自然rc0、无signal、child absent且组空。独立19项事实绑定检查通过；小机器清单见 `qualification.json`，45份原件SHA索引见 `evidence_refs.json`。

两端全1,048,576B非恒定pattern的SHA均为 `631b84027d6b9e52b539c4e8373622d23032dfadc64d60af87339c9037e4f769`。实际own communicator的global rank、逻辑CUDA0、NVML物理2/0、HIP BDF及nNodes2相互对应；选GPU和shca_0:1的完整PCI链、NUMA、affinity、gdr0均等自有输入。raw dump保持原SHA，派生视图每端仅删除GPU/NET各一个裸空属性、共6bytes，named字段保持。原厂XML与default mapping前后SHA一致。

28原两容器、13旧owner/worker/keeper身份及六catalog保持，1098副本lease0、事务空；未重读完整权重内容。30最终采用取回结束后的quiet guard，pids1、无OOM。保护范围为上述声明对象，27新D与29旧两pending没有新退出证明。30根盘仍满，本轮小资产和结果在原私有容器自有shm执行。15秒失败、Socket r01及IB r02验证器失败原件均保留；较长期限成功已更新运行可用结论。

下一轮配置入口为 `admission28_nccl_deploy_r03/launches.json`，runtime源码 `admission28/runtime_gate_r03.py` SHA `d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058`；自有XML目录 `probe_xml_2830_gdr0_r01`，输入SHA `b0d2633711e3779784265aaa80c44f4ad0beaaf95728c0e6bef555b99503e9b5`。后续使用新run名和当前资源guard。当前performance_ready=false：尚无该路径带宽或weights-ready样本，GPU负载共享。本轮没有新增产品图、solver或覆盖原资源地图。
