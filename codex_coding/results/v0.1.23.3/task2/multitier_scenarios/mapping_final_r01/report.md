# 物理绑定与第三GPU节点最小资格交付

最终绑定为mapping_binding_r03：N0=28 GPU0/1与NUMA0/3，N1=30 GPU4/5与NUMA4/7，N2=27 GPU0/4与NUMA0/4。三node都是真GPU宿主，各SSD父目录均local ext4 NVMe；task own目录/服务/完整SSD文件未创建。六worker/SSD恢复/真实模型尚未准入。

27旧4037472/start477235183 absent且boot已变，旧D进程前提解除，不恢复退出码/猜重启原因。主创建新6GiB私有IPC容器并执行一次GPU0/BDF09 initcopy：全1MiB SHA631b84027d6b9e52b539c4e8373622d23032dfadc64d60af87339c9037e4f769，通过，内运行19.990555071s，自然rc0/child13组空/no signal/noOOM/原停止容器状态保持。仅selectedGPU0单卡功能资格。

该卡HIP total-free+1MiB=338690048B<2GiB，是初始化后时点含外部占用的保守selected卡上界，非task新增HBM/全生命周期峰值/同node第二卡。DRAM容器6GiB硬上限通过，memory.peak与全阶段峰值仍unknown。

28/30 focused owner/free10:56UTC：28 GPU0/1各used584761344B/free68117938176B；30 GPU4/5各used3751936B/free68698947584B。同boot历史KFD-id对应的可见positive-vram owners为0；不证明exclusive、GPU健康或新worker可运行。GPUvisibility environ受权限限制，未知明确保留。28旧keepers在2–7，GPU4候选因此撤换；不借旧keeper或动其他服务。

共同shca0静态NUMA距离N0=10/15、N1=34/28、N2=10/34；近HCA是N0共同0、N1共同2、N2拆0/2，含共享出口与跨NUMA机会，速率/独立性仍unknown。全部baseline必须拥有相同合法HCA机会。

2GiB来源为总计划18.18行660旧小实验‘暂定新增物理实验配额’，不是永久用户硬上限；本轮主协调6GiB DRAM/2GiB HBM每node，且不同于暂停快层配额。旧失败和原件保持。来源完整原句保存budget_origin.snapshot.md。

所有metadata走冻结独立Python源stdin，严格SSH、35/25s自退出deadline、无远端子进程/写/信号；node29零访问、27零driver补采。主执行setup/copy另有清晰授权和原件，collector自身GPU运行0。首次class03误选板载VGAraw保留，专门PCI补采纠正，各node真实8枚DCU。

产物与复现命令：

- mapping_refresh_r01：collect_metadata.py --out .../mapping_refresh_r01（3node静态/容器/mount）
- mapping_pci_followup_r01：collect_metadata.py --host-source pci_metadata.py --out .../mapping_pci_followup_r01
- mapping_owner_refresh_r01：collect_metadata.py --host-source owner_metadata.py --nodes node28 node30 --out .../mapping_owner_refresh_r01
- mapping_binding_r01/r02/r03：build_binding.py，最终r03，前两原件保留
- mapping_admission27_plan_r01/r02：prepare_admission27.py；最终r02 source refs冻结
- mapping_admission27_setup_r01与mapping_admission27_run_r01：主执行setup、run_admission27_copy.py --plan ...plan_r02 --setup ...setup_r01/stdout.json --out ...run_r01 --execute；source SHA及完整argv在原件
- mapping_final_r01：summarize_delivery.py；23项本agent离线审计通过，独立review由主与reviewer另交。

下一步只补余下五卡scope及六worker聚合预算/同node路径，然后task own SSD目录和同原语串行恢复；27GPU0成功观察不重复增加样本。共享XML/defaultmapping/旧容器不改。
