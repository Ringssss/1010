# SAFE metadata入口CPU资格

开发编号v0.1.23.3/task2，2026-10-12。主agent已要求三节点只读metadata入口；本次子agent只建设并做CPU验证，没有SSH/GPU执行。

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/check_metadata_cpu.py --out codex_coding/results/v0.1.23.3/task2/online_cost/review_metadata_cpu_r01`。结果passed，证据为CPU_FIXTURE，GPU/SSH/远端写入0。

验证实际覆盖：Docker仅GET、所有容器名字保护（zhujianian-fk、zjn-fluid、其他用户均不遗漏）、cgroup anon/file/shmem字段、模型config原字节SHA、完整shard仅stat、payload读取0、超1MiB metadata拒绝、无torch/ctypes/subprocess导入、无VRAM/KFD/device/environ读取。完整源码组合包含既有Docker GET helper和新host入口，通过compile并记录SHA。

默认scope固定node27/28/30，node27GPU4 BDF85/NUMA4与GPU5 BDFb5/NUMA7、helpersGPU4 BDF85/NUMA4；SAFE仅确认PCI字段，具体GPU编号映射仍需本任务同HIP运行时核对。默认精确image ee3b9807...，新候选容器名zjn-fluid-three-node-online_qwen_r01-n27/n28/n30，现存同名拒绝为候选名字门。

当前正式预算由主最新指示替代初审r02中的尚未冻结候选：targetnode27 DRAM192GiB任务/224GiB容器，helpers96/128；target重载前hostavailable≥256GiB，helpers≥128；target每cardHBM48GiB/selected两card合计96，重载前每card同HIPfree≥52GiB；helper选定GPU物理已用上界+任务margin≤2GiB。pagecache、pinned底本、runtime、staging/cache/temp全部计入，IPC物理背书仅计一次，不扣背景以通过物理上界。NativeCatalog targetkeeper各HBM3GiB/DRAM48GiB是分配限制，不替代完整物理资源门。

本CPU资格不证明现场host/GPU容量、新镜像存在、端口空闲、正确权重、模型可用或10%预测准入。主运行collector后需独立核对actual metadata，再准入独占新容器stage和本任务GPU进程。
