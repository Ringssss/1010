# 本轮有限微测观察

开发编号 `v0.1.23.3/task2`。两项各一个完成样本，初始化、communicator 建立、host 分配/注册、pattern 填充与 SHA 准备、warmup 及结果全 SHA 验证均不混入搬运时间。实测源码均为 `feda34ab...`；原始结果保持。

| 有限配置 | 实际源 → 目标 | Payload | 单时钟完成时间 | 条件有效速率 | 正确性/功能 | 资源预算状态 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| 本地 NUMA H2D | node30 NUMA4 → GPU4/BDF85 | 64MiB | 1,471,437ns | 45.607GB/s | NUMA4 256/256页、全 SHA 通过 | 采样增量在2GiB内；完整峰值未定 |
| 远端因果链 | node28 NUMA0 → GPU0/BDF09 staging → IBext_v8/shca_0:1 → node30 GPU4/BDF85 | 64MiB | receiver 5,494,220ns | 12.214GB/s | NUMA0 256/256页、两端全 SHA、XML/GPU/NIC、退出通过 | source已观测DRAM超2GiB，预算失败 |

全部 payload 的 SHA256 为 `98dc891b284e4d84ac25b0c0a24fdbe39a7f0dbd643ad5e8aa06e02fc6258254`，每端完整验证67,108,864B。远端计时从 receiver request token 之前开始，source 收到 request 后才 H2D，receiver 目标 GPU sync 后结束；计入请求开销、source H2D 与 RCCL 完成。不跨主机相减时间戳。

本地 Torch 导入1.866s、GPU set_device14.261s、host 分配触页注册42.143ms、pattern/SHA准备371.754ms、warmup3.365ms、计时后全SHA68.931ms。远端源 Torch1.817s、set_device14.572s、communicator5.911s、host45.639ms、pattern410.464ms；目标 Torch1.832s、set_device14.475s、communicator5.781s。这些独立成本在 JSON 原件逐 rank 保留。

远端 node28 相对本次前置 guard 的 cgroup 采样增量为2,149,457,920B，比2GiB超1,974,272B（约1.88MiB），明确 `failed_observed_source_DRAM_budget`；node30 增量2,097,999,872B。两端 GPU 采样增量分别371,326,976B、380,530,688B。系统不存在 `memory.peak`，20ms `memory.current`/sysfs 采样不能证明完整物理峰值；source 已观测越线本身已足以否定本轮2GiB预算通过。共享页 RSS不作为独占新增内存求和。

这些观察不等同产品32MiB chunk/64MiB staging/receiver land，不是完整模型权重可用时延，也不建立独占硬件容量。`planner_profile_eligible=false`、`formal_performance_ready=false`；不拟合 alpha/B 两参数，不生成正式成本 profile。精确输入、配置、源码、前后 guard 和审查 SHA 均绑定在 `observations.json`。

256MiB r02（源码54840291...、静态应用sourceDRAM上界457MiB/HBM257MiB）保持 prepared-only，没有部署或GPU运行。512MiB未执行。本轮停止新增微测；真实产品先按2×16MiB最小块核mesh资源gate，由主agent统一决定后续。原64MiB预算失败和完整峰值未定均保留。
