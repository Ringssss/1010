# 最小三节点多层功能准入

新场景采用独立预算合同：每节点新增DRAM上限6GiB、HBM上限2GiB，聚合两个GPU worker运行时、SSD服务、pagecache、buffer与校验开销。容器总限制继续8GiB。主agent已接受此候选cap，并统一分配node27/28/30与六张GPU。最终准入再以fresh物理余量、NUMA余量和cgroup余量收紧；postwarm应用账本预留256MiB动态余量。该余量是有限试验准入安排，完整物理峰值由观测另列。

## 预算出处

原2GiB来自总计划18.18的暂定小实验额度，[计划](../../../../../../docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)第660行写明“暂定新增物理实验配额每node HBM≤2GiB、DRAM≤2GiB”。构建脚本`minimal_calibration/measure/build_setup_plan.py`把该暂定额度写入两台机器spec。后续TP4/三节点计划继承此值；本轮所读材料中未发现用户要求永久2GiB硬上限的原话。旧remote64的超预算失败及旧合同继续保存；新场景有独立run ID和预算记录。

## 最小执行顺序

1. 刷新三node身份、boot、被保护owner和实际GPU BDF/NUMA/HCA；绑定三处本地块设备mount、可写自有目录与fresh剩余空间。
2. 每个新GPU取得有界init/copy资格，六worker新session完成必要mesh/pair warm。warm后以启动前baseline核每node聚合DRAM/HBM，staging已在runtime读数中时只加尚未分配应用项。
3. 按16/8/8MiB创建三SSD完整底本；fsync、每块全SHA和TP4小规模manifest发布后，整个恢复组保持该底本存活。
4. SSD_only、C0、C1、C3各恢复一次；每次四target共32MiB，建立/保存、prepare、filled、verify/publish与退役分段记录。临时cache/target按精确run ID退役后再进入下一配置。
5. 核最终底本VALID、generation/epoch未变、lease归零、事务空；停止本次新workers，保留旧owner、默认XML及底本目录。完整SSD owner的关闭/重开目前需额外接口，不能以RemoteExtent.close清理底本。

SSD_only保证三SSD均实际供给：S0→rank1与S1→rank2具有跨节点GPU恢复腿；S2→rank3具有同节点异worker恢复腿。C0检验四target本地HBM；C1检验四target本地NUMA DRAM；C3让w1供b2、w3供b1，两helper跨node实际参与，同时覆盖远端DRAM与SSD缺口。该四次组覆盖必要功能，不枚举104机会。

## 六worker账本

每node两个64MiB staging，共128MiB；三个节点合计384MiB。四target分配8/8/16MiB，底本SSD为16/8/8MiB。恢复HBM保留副本按实际物理位置收费，target分配单列，首次资格保持零alias credit。

| 配置 | n0已知HBM项 | n1已知HBM项 | n2已知HBM项 | 全局DRAM保活 | SSD恢复逻辑字节 |
|---|---:|---:|---:|---:|---:|
| SSD_only | 136MiB | 136MiB | 144MiB | 0 | 32MiB |
| C0 | 144MiB | 144MiB | 160MiB | 0 | 0 |
| C1 | 140MiB | 140MiB | 152MiB | 16MiB | 0 |
| C3 | 144MiB | 140MiB | 148MiB | 12MiB | 4MiB |

表格是staging+target+保活副本的已知分配账本。GPU运行时、HTTP临时拷贝、pagecache、校验、注册与allocator reserved的真实峰值另列。六worker预算由每node合计判定，不能给六个worker分别发放6GiB。SSD逻辑来源量与块设备实际read_bytes分别保存。

## 下一关的具体输入

node27最新metadata显示boot已改变、旧PID4037472/start477235183缺席；原退出码仍缺证。本轮主已安排全新隔离单卡initcopy准备，保持三旧停止容器。新GPU资格仍需实际init/copy与精确PID/starttime清理证据。六worker执行还需要其余五选定GPU、必要路径、三本地SSD服务、processed小规模manifest及postwarm聚合预算证据。

C2a的独立资源关联作为逻辑参考保留；新现场有共享硬件时不能把物理运行贴成独立通道。C2b共享工作点在原执行器证明两disjoint pair可并发、实际ResourceID关联和单流基准齐备后各一次测量。模型T_ready阶段使用真实processed模型与实际引擎接入，当前合成小块功能只记录bytes-ready。

## 本地验证

`python3 codex_coding/src/v0.1.23.3/task2/multitier_scenarios/execution_review/build_minimum_plan.py --out codex_coding/results/v0.1.23.3/task2/multitier_scenarios/execution_review_minimum_r01`

8项结构检查通过；`minimum_plan.json`冻结4配置、32条选定恢复操作及逐node账本，`source_snapshot.py`保存生成源码。此子任务执行SSH/GPU/远端写入次数均为0。新binding与adapter交叉验收另存独立目录。
