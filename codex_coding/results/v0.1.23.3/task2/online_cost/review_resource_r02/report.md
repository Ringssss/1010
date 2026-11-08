# 新域资源与在线协议独审

开发编号v0.1.23.3/task2，2026-10-12。当前资源主证据为主agent执行的 `review_metadata_r02`；独审子agent没有执行SSH或GPU。`audit_metadata.py`独立检查47项通过，包括完整角色集合（目标GPU4/5）、collector冻结SHA、限时命令、精确host/node/image、新候选名字与端口、安全PCI/NUMA、无GPUAPI/device/old-container exec/远端写入/node29及host门重新计算。`review_resource_r01`前版保留。

## 新预算与现场

目标node27任务DRAM192GiB、容器硬限224GiB；重载前hostavailable≥256GiB。两个helper各只保留一个完整TP分片，任务DRAM64GiB、容器硬限80GiB；初始hostavailable≥80GiB，持续hostavailable≥16GiB+尚未分配的新增空间。NativeCatalog各keeper DRAM48GiB；targetkeeper HBM3GiB（缓存1GiB、校准临时1GiB及余量），整卡任务物理门为target48GiB/card、selected两card96GiB，helper2GiB/node含运行时和margin。目标重载前各card同HIPfree≥52GiB。原96/128helper过度预留与旧微请求预算不改，相关失败原件保留。

fresh hostavailable：node27约480.259GiB、node28约87.820GiB、node30约482.757GiB，均通过新scope的host门。node28此前未通过128GiB门；这次是任何GPU测量前限定单rank、收紧预算的明确新scope，未重新解释旧失败。初始可用量不等于独占配额，分配后仍需实测cgroup/headroom；已经计费的底本不能再次加为未来待分配空间。

node28本次NUMA4仅约5.796GiB `MemFree+FilePages−Shmem`，无法容纳约32.75GiB分片。唯一粗估≥40GiB候选是NUMA3，约41.281GiB（free1.797GiB+FilePages39.491GiB−Shmem.006GiB）。需要显式将helper28的host source allocation绑定NUMA3，GPU4仍为BDF85/PCI NUMA4；不能修改硬件NUMA事实。应单独记录host allocation NUMA3与pci_numa_node4，验证实际owner page placement，并重新校准 `2:dram:3->0`。

node27 selected NUMA4/7粗估分别59.964/58.134GiB，node30 NUMA4约60.214GiB。该公式是页回收估计，不是独占可分配证明；calloc/pinning可能失败，不能假设FilePages均马上可回收。源owner初始化必须有界并保留失败，不自动多NUMA分散或迁移其他用户页。

GPU容量尚未验收。只有safe PCI metadata证明BDF和PCI NUMA存在，ordinal4/5的实际HIP对应关系还须owned runtime核对。`collect_helper_gpu_metadata.py`新增极小候选查询入口，仅node28/30 GPU4/BDF85的两个sysfs计数（每文件≤128B），8秒主机deadline、15秒transport；不走KFD/全driver table、不访问node27/29、不调用GPUAPI。结果只作为候选现场元数据，不声称已初始化运行时或通过最终2GiB预算。node27继续使用本任务同HIP guard。

## 在线协议与实现审查

独审实际运行当前 `online_cost/model/cpu_check.py`，结果位于 `review_model_cpu_r01`：25项通过，GPU0、实测模型预测资格false。检查完整outer residual只加一次、原worker DAG、GiB域/实际warm pair/当前incarnation、整数inclusive10%边界、每场景失败不能平均消除、冻结后突变/旧训练请求/不同clock/错误输出拒绝和near-tie排序abstain。模型只覆盖相同完整target extents、顺序、N/K/C的不同来源分配，不推广到新的block partition/op顺序或任意topology。

本轮whole-online主窗采用同一coordinator从必要inventory/planning/resume之前到所有引擎resume ACK；引擎同host两rank ready原件另列。任何live ready仍包含identity/layout、epoch/generation、源lease、完整覆盖/目标字节、同步、done发布和rank barrier。全SHA与固定32token参考输出在ready之后验证；C0与holdout的 `exact_reference_output_passed` 现为模型验收必需条件，不以weightsusable替代输出正确性。

共享项当前为事前校准的两条disjoint-worker remote腿 concurrent/max-isolated service比值，作用于匹配offset/n的连续paired remote phase后重跑原DAG。它是有限实测假说，不是物理capacity或严格下界；完整C0/Hremote须保存实际paired overlap。未知物理共享不填标称capacity，source软件共享已经由worker顺序依赖计账。

已向实现agent发出并接受两项修订：supervisor使用显式per-node operational DRAM cap，保留原plan SHA和exact-owner cleanup，避免target192预算被旧96全局参数截断；capture新增SHA必须在rank.captured marker之前完成，SHA失败精确退役本次新建regions，不给未核准内容发ready或保留不可信源。原capture Catalog发布定义是同步D2H完成，不伪称已做全SHA。

新容器setup/stage/GPU启动仍待完整冻结源码与独审。helper不需要读取模型payload；完整checkpoint只在target只读挂载和加载，pagecache+完整pinned底本+engine/runtime全部计账。原容器和zhujianian-fk等全部existing owners受保护，不能以名称子串遗漏保护，不修改旧容器。

验证命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/audit_metadata.py --metadata codex_coding/results/v0.1.23.3/task2/online_cost/review_metadata_r02 --out codex_coding/results/v0.1.23.3/task2/online_cost/review_resource_r02`。结果47项通过，GPU capacity admission仍false。输出目录独占创建；重审使用新目录。
