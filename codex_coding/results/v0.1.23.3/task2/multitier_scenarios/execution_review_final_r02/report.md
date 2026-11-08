# 三节点物理绑定与API衔接独立验收

本轮完成三节点六角色的物理候选绑定、原产品串行API衔接及node27新单卡实机资格。119项独立检查全部通过：最小设计8项、映射与setup46项、API/失败控制41项、node27运行原件24项。引用SHA与scope核验另列60条，均通过；它们是取证条目，不是新增性能实验。[机器资格](qualification.json)保存最终范围和不可变快照。

## 实机突破与最终角色

node27已跨boot变化，旧PID4037472/start477235183缺席。主agent在全新隔离容器执行GPU0/BDF09单次1MiB init/copy，完整SHA为`631b84027d6b9e52b539c4e8373622d23032dfadc64d60af87339c9037e4f769`，独立按原`i % 251`字节模式重算一致。进程自然rc0、无timeout/信号、精确child缺席/进程组空；容器回到pids1、memory.current=4,849,664B，OOM计数不变。三个旧停止容器CID、StartedAt、停止状态和历史OOM标签均保持原样。

19.990555s是包含import与HIP初始化的进程观察窗。HIP时点total−free为337,641,472B，加1MiB载荷338,690,048B小于2GiB；这是当前selected GPU含外部用量的保守时点上界，完整物理峰值仍待测量。

| 逻辑 | 机器 | target/helper GPU | NUMA D0/D1 | 本地SSD父目录 |
|---|---|---|---|---|
| N0 | node28 | target GPU0/BDF09；helper GPU1/BDF36 | 0/3 | /data/node28 |
| N1 | node30 | target GPU4/BDF85；helper GPU5/BDFb5 | 4/7 | /data/node30 |
| N2 | node27 | rank2 GPU0/BDF09；rank3 GPU4/BDF85 | 0/4 | /home |

最终映射为`mapping_binding_r03`。GPU class、vendor/device和完整PCI链与raw逐一对齐；DRAM顺序与worker角色对齐。本地块设备mount、nonrotational NVMe和容量独立复算成立，任务SSD目录、服务和文件尚未建立。28/30 focused owner数据证明所选卡在采样时低占用；可见KFD记录有权限与范围限制，资源独占资格未取得。

## 新预算和最小恢复

2GiB/node源于旧18.18双worker小实验的暂定增量gate。当前用户授权三node六GPU建设，主agent已接受新场景候选cap：每node DRAM6GiB、HBM2GiB，聚合两个runtime、HBM/DRAM保活、恢复target、staging、HTTP/pagecache、校验和注册。6GiB不能分别发给六个worker。旧超预算失败、旧合同及原始run保持。预算出处文档按当时内容保存快照，后续正常文档更新不会使本验收失效。

最小恢复序列是SSD_only、C0、C1、C3各一次，目标每次四rank共32MiB。SSD_only覆盖三块SSD供给及跨node/同node异worker恢复腿；C0检验target本地HBM；C1检验target本地DRAM；C3让两个helper真实跨node供b1/b2，含远端DRAM和4MiB SSD缺口。选定32条恢复操作即可，不扫104机会。保存、prepare、filled、verify/publish、退役分段；相同底本、manifest、允许资源和目标范围。

每node两个64MiB staging为128MiB，六worker合计384MiB；target为8/8/16MiB；SSD完整底本为16/8/8MiB。C0已知HBM项144/144/160MiB，C1为140/140/152MiB，C3为144/140/148MiB。历史postwarm runtime已含staging时只追加未分配应用项，避免重复计账。运行前以fresh物理/cgroup/NUMA余量收紧cap，postwarm应用账本留256MiB动态余量；物理峰值观测单列。

## 原生API与防伪边界

native CPU资格79项覆盖真实产品`Cluster.transfer/verify/finish`、`ClusterWorker.prepare/_execute/land/verify_run/complete/finish`、Catalog、wire以及三本地HTTP服务/RemoteExtent协议，六recipe各32MiB全SHA。GPU设备、GPU分配/暂存和NCCL以CPU mock替代，因此资格是接口与字节正确性。首次r01的预期open-existing负路径异常未被测试捕获，原失败保留；r02修正后通过。

native r02的adapter执行源码通过逆转两处最后新增断言重构，字节SHA严格匹配当时记录`47aaa562…`；最终`5038e02d…`差异只有worker budget一致和cleanup后的worker身份复核。重构件明确标记为执行后恢复，不伪称当时已保存快照。最终源码的控制路径由journal7项及独立41项验收覆盖。

独立负路径拒绝未绑定、过期/错SHA、重复GPU、缺选定路径、底本缺失/epoch或generation变化、账本不完整、worker budget漂移与底本混入清理。保存成功、恢复首target发布后注入失败时，12个拟建ID先写journal，9个已建ID精确退役，3个未建ID跳过；八个底本VALID、generation/epoch匹配、lease归零、事务空。`RemoteExtent.close()`会删除extent，清理禁止退役任何SSD底本。

binding JSON与引用SHA用于编译及防输入漂移。物理执行必须有主协调器可信`trusted_admission`，独立审真实path内容/身份、SSD活owner、fresh guard和每node聚合预算；仅把CPU fixture改成`purpose=physical`仍因缺可信gate被拒。该物理gate的实现与实测输入尚未交付，当前不会产生六worker launch-ready资格。

## 明确的下一输入

后续按本轮候选补各node第二卡与新六worker会话、必要pair/NUMA/SSD路径。node27本次copy容器只有IPC_LOCK，SYS_NICE与seccomp下的NUMA mbind/触页/注册仍待资格；复用已成功minimal_measure任务权限配置时应新建任务容器。三个本地SSD需要自有可写目录、服务、16/8/8MiB完整底本manifest和当前Catalog owner。取得postwarm聚合预算及可信gate后，执行上述四次恢复即可闭合三层功能。C2a保留逻辑独立参考，共享工作点和真实模型T_ready按各自证据进入下一阶段。

## 验证命令与产物

本子agent只执行CPU/本地只读验收，无SSH/GPU/远端写入。四个入口均位于`codex_coding/src/v0.1.23.3/task2/multitier_scenarios/execution_review/`：

- `build_minimum_plan.py --out .../execution_review_minimum_r02` → 8项通过，最小合同与快照。
- `audit_preparation.py --mapping .../mapping_binding_r03 --admission .../mapping_admission27_plan_r02 --out .../execution_review_setup_r02` → 46项通过。
- `audit_adapter.py --native .../adapter_cpu_r02 --journal .../adapter_journal_cpu_r02 --native-adapter-snapshot .../adapter_delivery_r02/reconstructed_native_r02/adapter.py --out .../execution_review_adapter_r01` → 41项通过。
- `audit_runtime.py --setup .../mapping_admission27_setup_r01/stdout.json --run .../mapping_admission27_run_r01 --plan .../mapping_admission27_plan_r02 --out .../execution_review_runtime_r01` → 24项通过。
- `finalize_review.py --out .../execution_review_final_r02` → 汇总119项与60条取证/scope核验通过。

每个脚本以系统`python3`调用；`.../`结果前缀为`codex_coding/results/v0.1.23.3/task2/multitier_scenarios/`。所有输出使用新目录，旧r01/中间结果保留。final r01把119检查和60取证合列179；r02只调整计数呈现，检查结果及结论一致。
