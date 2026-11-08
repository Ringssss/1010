# 三节点六 worker 实际集成独立验收

开发编号 `v0.1.23.3/task2`。`operations_actual_r01` 实际运行 session `multitier_runtime_r02` 一次，综合资格见本目录 `qualification.json`。六 worker 的实际 HIP BDF、NUMA、boot、PID-start 与当前 session/epoch 对齐；三节点真实 SSD 底本及四个冻结 recipe 的完整32MiB原 serial 恢复通过。子 agent 仅本地 CPU 审查，GPU 启动由主 agent 执行。

实际角色顺序为28 GPU0/NUMA0/BDF09、28 GPU1/NUMA3/BDF36、30 GPU4/NUMA4/BDF85、30 GPU5/NUMA7/BDFb5、27 GPU0/NUMA0/BDF09、27 GPU4/NUMA4/BDF85。全六位置都有实际触页、256页NUMA采样和host注册成功证据。六个必要 pair `01/02/03/12/24/45` 的原始 RCCL commId 在两端对应相同 communicator，subgroup rank 为0/1，PID/GPU/busId 与当前 owner 匹配；日志选择 `IBext_v8`、`shca_0:1/IB`、gdr0；w0–w4有实际跨node IBext channel，w5仅参加本机45的P2P/IPC，不将w5当跨node网络实证。原 mesh 创建15个 pair 对象，实际资格覆盖六个所需 communicator 与23条原生路径，不宣称另外九个 pair 已测。全部必要路径各用4MiB逐字节 SHA 资格，不扫104个候选。

完整底本为明确 synthetic 的 TP4式八块，每块4MiB，当前活 Catalog owner 发布并校验；真实目录通过 container `/task_ssd` mount 绑定到28/30的 `/data/node*/shared` 和27的 `/home` 项目结果目录，三者最长匹配 mount 均为本机 `/dev/nvme0n1p1` 的ext4。SSD按16/8/8MiB分布，组间八份底本 VALID、零租约、epoch/generation不变。C3的两个 helper 实际供给 b1/b2，b7 实际由SSD补齐。

| Recipe | 暂停HBM/DRAM实际副本(MiB) | 建立 filled/published(ms) | 恢复 filled/published(ms) | 恢复执行窗口(ms) |
|---|---:|---:|---:|---:|
| SSD_only | 0/0 | 无 | 132.187553 / 194.581360 | 63.390130 |
| C0 | 32/0 | 128.489813 / 191.183504 | 72.914718 / 99.886000 | 5.289751 |
| C1 | 16/16 | 136.570661 / 197.155873 | 71.449818 / 96.808268 | 4.684593 |
| C3 | 16/12 | 103.253224 / 162.932195 | 81.051807 / 111.562266 | 14.137719 |

四组每组一次，全部四 target 各8MiB、合计32MiB全SHA通过。filled与published按原 coordinator 同钟分别保留。计时起点在 `Cluster.transfer` 内，外部 trusted gate、前置binding/inventory检查不在窗口；当前target是replica，未执行live模型handoff。表中是冻结手工 witness，暂停预算不同，不是同预算planner胜出；C0/C1不到2ms的差距不认证性能排序。恢复 `inventory_rpc` 为61.84–63.29ms，需单独定位其诊断开销，不能把整段filled时间当纯数据搬运或模型 `T_ready`。建立阶段从SSD生成副本是功能准备，不代表模型暂停时的原地保活成本，不能用于通用save+restore break-even。

三node应用预算为DRAM6GiB/HBM2GiB聚合，容器硬cap8GiB；两worker共享同一cgroup只计一次。runtime已含每worker64MiB staging，不再追加384MiB。250ms采样的容器DRAM最大值28/30/27分别为5,287,882,752 / 5,424,803,840 / 4,325,363,712B，均保留256MiB应用余量，所有任务容器最终pids.current=1且无本轮OOM。

HBM计数有明确口径边界：parent用HIP used减去28/30 prelaunch sysfs used，只能作为跨计数源增量估算。独立改用相同HIP来源的两张selectedGPU绝对占用上界，在postwarm、qualification后及cases结束三点均为28=1,889,533,952B、30=1,516,240,896B、27=1,811,939,328B，均低于2GiB。28绝对上界余量246MiB，比256MiB少10MiB；原增量gate通过其256MiB门槛，不应认证绝对上界也保留同样余量。13组应用阶段监控还可由同HIP postwarm基准加后续非负HIP占用变化重构保守采样上界：28=1,910,505,472B、30=1,539,309,568B、27=1,853,882,368B，仍低于2GiB，余量分别226/580/280MiB。28的采样上界余量也未达256MiB。完整物理峰值仍未测。

每组精确退役本run HBM/DRAM拟建对象并回验完整SSD底本；最终显式结束底本生命周期，所有当前Catalog对象 RELEASED、租约/事务空。九个新 child 均由匹配plan SHA的STOP结束，supervisor rc0，child收到TERM返回-15，无KILL，九个进程组精确回验为空。28的13个旧PID身份和28/30/27的6/2/4个旧容器身份前后相同；宿主默认mapping SHA不变。宿主原厂XML不存在，不能将此冒称旧容器副本SHA；执行源只读原输入、自有XML与独立dump路径不重叠。

SSD服务为buffered IO，fsync和fadvise不证明冷介质读取；每组save/restore及全SHA验证合计HTTP读取64MiB，不是恢复逻辑SSD来源字节或盘带宽。仅从最终一次样本认证所需原生路径和功能恢复，不认证冷SSD IO、共享容量、全部15 pair、真实模型、重启持久性或规划器收益。

独立实测核心证据见 `review_actual_r01`，原始RCCL/NUMA及预算口径见 `review_actual_logs_budget_r03`，旧owner保护见 `review_actual_protection_r01`，最终启动源码/计划冻结见 `review_launch_r01`。引用SHA和独立脚本快照归入本目录；审查条目数不是实验样本数。此前E2BIG、SSD父目录PermissionError、CPU fixture失败和logs-budget r01仅漏读qualification NUMA见证的审查失败原件保留；operations后处理最初过严要求所有INIT_COMPLETE行及本机w5的NET channel，原source/结果已保留并仅修派生解释。没有为修正审查再跑GPU。
