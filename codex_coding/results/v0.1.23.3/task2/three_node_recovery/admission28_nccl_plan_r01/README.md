# 28/30 单卡跨节点 NCCL 1MiB 准备

rank0=node28/GPU2/BDF55，rank1=node30/GPU0/BDF09，world2/scope cross。当前计划与候选XML都只在CPU本地生成，没有远端或GPU操作。28的init60/copy60已通过；30初始化/copy准入由父和B执行，尚未取得结果时不启动通信。

1. 父执行namespace28.command.sh，取回原container_namespace输出的container.json。30的同任务命令由B存diagnosis30_shm_assets_r01/namespace_command.sh。namespace.py需要同probefolder原init_gate.py927f；B已将这两源及frozen host JSON按相同层级复制到30私有shm。CPU采样不依赖双方GPU通过。原inventory_r03是静态host物理基底，不把当时free作为当前准入。
2. 父执行node28.network.command.sh和node30.network.command.sh：仅exact本机12.11.3.IP地址、peer route、单29870监听检查。socket interface从实际地址与route交叉确认，不凭历史node27的ib0推断两远端。库存HCA真实name是shca_0，port1 ACTIVE/400Gb/s，所以process-level NCCL_IB_HCA可精确设=shca_0:1；未实测速率，gdr0仅policy。
3. 收到两namespace原件后，原probe/build_xml.py用host28/30 inventory_r03和对应真实container.json，输出新的probe_xml_2830_gdr0_r01；产品验证失败则停。这里的node28/30 XML是产品physical_xml离线候选，各完整8GPU/4HCA，candidate.manifest明确namespace_validation_passed=false/runtimefalse，不提前认证实机。
4. 父只上传新runtime_gate.py和已验证的XML/manifest到各自owned项目目录。28ROOT保持/home/zhujianian/zhujianian/fluidckpt_zjn，30ROOT为/dev/shm/fluidckpt_diag30_r01/project，30 deployment manifest只重写input_file到该ROOT/results/.../probe_xml_2830_gdr0_r01/node30.physical.gdr0.xml；保留原CPUmanifest原件与XML bytes SHA，记录deploymentcopy SHA。两rank runtime_gate必须保持97a740672a2c1e526ecdc80ae4592c7438ff7edddc3c81a60ab120c28feaef11和同parents6层级。
5. 父分配未占用29870、fresh guard、同步两个native docker exec。process线程OMP/MKL1、原LD继承，NCCL_SOCKET_IFNAME从步骤2事实精确选择；NCCL_IB_HCA精确shca_0:1。stage nccl/visibility selected、master tcp://12.11.3.28:29870。新副本GPU初始化子deadline60、总90，建议native外层100；保留原process_group单项timeout20。若以后等待发生在communicator阶段，按实际日志单列20s期限，不混为GPU-init60失败。

NCCL source唯一函数改动在main预算声明/调用；cleanup、collective、dump/log/fullSHA和原XML保护函数逐AST相同。source_diff_validation.json记录该结论。原927f及旧failed不改，init/copy的15s模式仍保留在这个NCCL副本（单机60copy由B已有d533副本执行）。

验收每rank必须满足自然或exact退出、HIP真实BDF/free、communicator后再次free、full1MiB SHA、独立dump中完整BDF/PCIchain/NUMA/affinity/rank/dev/gdr0、精确childPID/rank日志Loading owned input、无default mapping回退、actualNET channel与nNodes>1。聚合两rank和各前后保护才能接受跨节点功能，不用事件间隔或HCA标称速率声称带宽/端到端收益。

原run_gate/host_gate只支持init/copy短外层35，artifact ALLOW不含NCCL日志/dump，因此不要拿它启动/归档这次90s NCCL。父沿用已有native启动和限定文件取回，将nccl.log/topology.dump.xml加到明确本run文件列表；不取整个project。输入XML与每rankdump分目录，绝不能覆盖原厂/defaultmapping/自有输入。
