# 显式空拓扑 seed

文件：`codex_coding/src/v0.1.23.3/task2/topology_alignment/probe/empty_seed.xml`

完整内容为`<system version="2"></system>`，末尾LF；共30字节。

SHA-256：`3c320d361ec07cbbf69851fc0dc69873dc3f4bfb1b5056f2829c61ca0b6b94a5`

此文件仅用于隔离探针，现有runner支持`--topology-mode xml --xml /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/topology_alignment/probe/empty_seed.xml`。不修改生产XML、默认mapping或容器ENV。

静态forensics指出显式非空NCCL_TOPO_FILE选择读取该输入，绕开默认mapping后进入共用GPU/Net fill；实际图与路径仍须以startup每rank环境、四个dump、GPU级GDR/channel日志验证。seed不是预构造物理图，也不提前证明自动发现正确。

依据：`topology_alignment/provenance_r01`的部署库反汇编与mapping取证；启动与GPU资源由主agent协调。本次仅创建XML和说明，未改probe Python、未启动GPU。

主agent已完成本次unset与emptyseed startup，均退出后认证passed且取得四rank图。unset实际使用vendor mapping（net speed=200000、gdr=0），emptyseed实际fill得到GPU1 NUMA=3/CPU affinity=48-63、net speed=400000、gdr=0；不能将unset命名为自动发现。第三个对照身份是显式原部署XML。上述是startup机制/图事实，正式性能须按三组各自matched A/B/pair记录；图属性gdr=0不替代实际GPU级GDR/channel日志。

退出后证据：`topology_alignment/probe_startup_unset_r02/postexit/summary.json`、`topology_alignment/probe_startup_empty_r01/postexit/summary.json`。

原summary的binding=[]，继承runner空列表检查可能使status仍为passed；它不认证实际传输。最终认证入口始终是本run的`postexit/summary.json`，含四图、实际binding、退出后artifact SHA与完整checks。原摘要不覆盖。
