# GPU4新物理域启动审查

本次独立检查通过：原生CPU原始账本215项、物理域修订及旧冻结证据保持44项、最终冻结包/源码/角色隔离CLI223项。分别见 `review_gpu4_native_cpu_r01`、`review_gpu4_scope_r01` 和本目录的 `qualification.json`。

指定候选仅node27 GPU4、BDF `0000:85:00.0`、NUMA4，沿历史已登记身份建立新world1/rank0域。原GPU0启动已止于资源门，仍为0校准、0bootstrap、无新系数；不消费其模型或将其当训练。原146项部署资产及原GPU0全部源码/失败原件SHA保持。

GPU4分析源码只改两处配置/拓扑字面量；worker、controller、session、mode helper四个执行机制文件逐字节保持，原生copy loop、计时合同、数值门、OLS、停止与C2冻结审查规则保持。预算计算、runtime纳入、预留与安全余量也逐字节保持，不减去全局占用baseline。CPU资格使用GPU4/NUMA4身份fixture并走真实原生HTTP及完整bytearray复制/SHA；最终部署的每项执行源码与CPU回放冻结版本一致。

最终包的远端worker隔离CLI、真实stdlib mode创建/拒绝覆盖及本地controller角色CLI通过。源码/CPU/包具备启动条件；实际准入仍须主agent检查fresh同HIP运行时总上界，满足6GiB DRAM/2GiB HBM原门。GPU4若也失败即停止，不扩大GPU扫描。本轮审查无GPU、SSH或产品改动。
