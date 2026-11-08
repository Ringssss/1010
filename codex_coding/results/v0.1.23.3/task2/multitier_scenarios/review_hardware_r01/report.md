# 本地硬件可行性独立审查

三节点×两GPU是设计角色，不是已准入资源。node28/node30静态结构有历史证据；最新本地08:59元数据只确认容器/boot/保护身份，未证明新六worker、各GPU、SSD路径和共享容量当前可运行。node27旧D采集退出未证，node29旧pending保留，本轮不访问。

独立从warm前后原始cgroup/VRAM记录复算：node28新增DRAM 2,029,518,848B，node30 2,038,231,040B；单worker运行时约2GB。两worker线性投影分别4,059,037,696B和4,076,462,080B，应用字节未加已超过原2GiB/node额度。共享库页可降低净增量，mesh与校验可增量，因此这是规划投影而非物理下界；缩块不能消除固定运行时，也不能把8GiB容器ceiling当授权。node27无新观测，用node30代理须明确标为估计。

已通产品路径为node28 GPU0到node30 GPU4，经双方shca_0:1、IBext_v8、gdr0。按NUMA亲和配置其他HCA不等于路径独立或容量实测；六GPU新mesh、同node多源/多目标共享入口及并发资格均未知。两端历史warm HBM观测已包括64MiB staging，完整账本不得重复加这项；独立DRAM校验区、HTTP bytearray、page cache、pinned staging、allocator reserved和迁移暂存需按实际生命期计峰值。

原始GLM checkpoint在node28本地/data/node28/shared，其他节点通过NFS访问；不能把这个路径命名为各node的本地SSD。新契约的每node本地SSD是假设，节点路径、挂载、设备、可用容量及实际I/O仍待绑定。processed SSD snapshot须另建完整布局/内容身份，不以raw checkpoint字节替代，也不把逻辑SSD来源量当physical read_bytes。

第三节点可以只承接CPU SSD service，HTTP供给两个GPU节点上的接收/转发worker，无须为了SSD owner启动fresh GPU。但这是未来独立映射选项：修改六GPU角色、路径incidence、source-ready DAG、runtime预算与功能资格后才可执行。CPU DRAM owner本身不是独立Cluster.transfer端点；当前本地FD owner仍依赖同host GPU worker注册/发送。node27 pending和存储资格不会因采用CPU角色自动通过。

所有复算及SHA见qualification.json。未SSH、未GPU、未修改配额/进程/原XML/保护数据。
