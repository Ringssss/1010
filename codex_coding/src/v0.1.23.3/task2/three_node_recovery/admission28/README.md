# node28 再准入与 28/30 最小功能入口

当前统一入口为 `runtime_gate_r03.py`，SHA `d1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058`。`--stage init` 零 payload 和 `--stage copy` 全 1MiB H2D/D2H 的总期限均为 60 秒；`--stage nccl` 为初始化子期限 60 秒、总期限 90 秒，process group 单项期限保留 20 秒。native NCCL 外层 100 秒，OMP/MKL 各 1 线程。原 927f/97a 源码及其失败结果保留，不用旧 `run_gate.py/host_gate.py` 的 35 秒包裹启动这个入口。

结果和准确父执行命令在 `admission28_nccl_deploy_r03/`。两个 `node*.command.sh` 须由父在最新身份/旧保护/实际 HIP 准入和 source readback 通过后并行执行，各用新的 `probe_nccl_2830_node*_r03` 目录。28 使用原 private 容器 e693、GPU2/BDF55 与自有 r02 bind；30 使用原 private 容器 7d314、GPU0/BDF09，在 `/dev/shm/fluidckpt_diag30_r01/project` 按相同源目录层级部署。每端 bundle 只有一个新源码文件，已经验证的 XML/manifest 原 SHA 复用；不覆盖 97a、自有输入 XML 或厂商 XML。30 manifest 的 input_file 是 shm ROOT 下的既有部署副本，原 CPU manifest 不改。

兼容配置恢复 `NCCL_NET_PLUGIN=librccl-net-shca.so`、`HSA_FORCE_FINE_GRAIN_PCIE=1`、`NCCL_NET=IBext_v8`，采用历史成功的 `NCCL_IB_HCA=shca_0`、当前实际接口 `NCCL_SOCKET_IFNAME=ib0`。这是整体兼容配置功能检查，不作单变量效果归因。实际验收必须是本 PID/CUDA device 的 IBext_v8 channel、正文绑定的 global rank/world/comm 与 `shca_0:1` 选择日志；Socket 回退不能通过。HSA 实际环境保存在 child.result。gdr0 仍是自有 XML 的保守策略，不声称带宽或 GPU Direct RDMA 能力。

验证层将 NVIDIA 风格 `cudaDev`、厂商物理 `nvmlDev` 和 global rank 分开：前缀按 PID+cudaDev，正文按 global rank/world/comm，busId 与 fresh HIP BDF 绑定；dump dev 必须是同 communicator 的 nvmlDev。BDF、完整 PCI chain、真实 NUMA/affinity、rank 与 gdr0 仍严格比较。所选 NET 的 shca_0:1 名称/端口也与原输入的完整 PCI chain、真实 NUMA/affinity、gdr0 逐项对照，GPU 与 NIC 物理路径分别记录。

厂商原始 dump 若仅在 GPU/NET start tag 中出现分隔完整的裸空名空值 `=""`，派生解析视图只删除这三个字节。每 tag 至多一个此 token；其他格式损坏、GPU/NET 之外的空属性、匿名非空值和缺分隔 token 都拒绝。原 dump 保存不动，另保存 `topology.parse_view.xml` 与 `topology.parse_view.json`，后者记录每处 tag 名、offset、原件/视图 SHA 和删除数量。原输入、raw dump 和派生视图三者分路径。

`test_validation_r02.py --out <新admission28_validation_r02_cpu_目录>` 用已冻结 r01 两端原始 dump/log 回放，并以错误身份/BDF/设备/rank/路径/GDR/PCI ancestry、HCA/port/protocol 与格式损坏做负例；使用已有 CPU fixture 缩放验证初始化与总期限，不导入 Torch、不访问 GPU。最终 `admission28_validation_r02_cpu_r04` 的 73 项通过。首轮 CPU harness 的 AST 提取错误保留在 cpu_r01/failure.json；后续 cpu_r02/r03 与最终 r04 分目录。原 r01 实机 summary 仍为失败，CPU 回放通过不改写其历史状态。

每个新 NCCL run 应取回 12 个限定文件：原有 registration/source、owner/process/log/events/result/summary，加 nccl.log、topology.dump.xml 以及两个 parse_view 文件；不是同步整个项目。父聚合两 rank full 1MiB SHA、实际 XML/log/dump、原文件 SHA、精确退出与前后保护后，才接受本次跨节点功能。采样事件间隔和 HCA 标称速率不是传输性能。

`test_validation_r03.py` 最终 `admission28_validation_r03_cpu_r02` 的 37 项通过，直接回放两端 r02 的真实 IB dump/log：每端一个 GPU token 和一个 NET token，派生视图仅去六字节；日志、GPU 和 NIC 路径全部通过。NET 名称/端口、PCI/NUMA/affinity/gdr0、非空/重复/粘连属性、其他标签损坏与 Socket 反例均拒绝。预算、精确清理、GPU 绑定和日志函数与 r02 逐 AST 相同，未重跑已有 GPU copy 或额外性能重复。首个 r03 CPU harness 因 ElementTree 叶节点布尔值误用失败，原件保留；修测试选择器后用新目录通过，runtime 源未因此变动。r01/r02 实机 failed summaries 都保留；最终运行状态由父汇总新的 r03 证据。
