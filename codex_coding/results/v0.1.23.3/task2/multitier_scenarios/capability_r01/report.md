# 三节点多GPU、多层保存能力盘点（r01）

编号 v0.1.23.3/task2。用户新增场景按三节点多GPU、HBM/DRAM/SSD和典型复杂拓扑展开；本交付只读现有代码与raw，不部署、不SSH、不启动GPU。现有系统原语足以复用多块内容、三层副本和恢复事务；当前新配置认证仍限28/30选定两节点路径。完整SSD底本始终可以是合法最快后备，各方法共享它，不能隐藏fast fallback制造优势。

## 支持矩阵

“当前”指最新28/30隔离任务的配置实证，不是曾在同型号GPU跑过；“历史”保持旧版本、session与预算标签；“CPU/静态”不冒作新增运行证明。

| 能力 | 当前配置实际证据 | 历史其他配置 | CPU/静态能力与边界 |
|---|---|---|---|
| HBM缓存/DRAM底本+Cluster恢复 | 28/30，4布局各2×16MiB，实际SHA/filled通过 | 旧全模型恢复另有证据 | 多pool/TP4合同CPU通过；新实测不是3节点多GPU或完整模型T_ready |
| 独立CPU SSD HTTP range service | 未在新28/30任务执行 | node28_01 16MiB HTTP读写fsync+SHA通过，v0.1.23.2 | 服务实现不依赖GPU；本轮仅静态检查；buffered IO + FADV_DONTNEED，不是O_DIRECT/GDS，不证明冷盘延迟 |
| 多块SSD完整后备/区域混合恢复 | 未在新配置执行 | local_pressure02 Qwen 70,329,718,112B，DRAM比例1/.5/0含全SSD，正确token通过 | restore_plan覆盖区间/path_for支持SSD；旧v0.1.23.2结果/旧session/旧预算不能迁移为当前认证 |
| 本地SSD文件分块readinto→pinned→HBM | 新任务未测 | 旧keeper存在实现；未单独认证其新配置性能 | 静态代码支持；不走RemoteExtent；remote SSD在旧flowplan必须先入DRAM；不等于任意跨机直接文件读 |
| SSD服务→HTTP→GPU暂存→RCCL→目标 | 新任务未测 | 旧local_pressure02有执行证据，但其合法具体路径需引用实际flow | worker serial源owner.read；目标owner.write；double_buffer传source tensor(None)给pipeline切片，因此SSD源未接通双缓冲；storage_node可异于GPU worker node |
| SSD fsync备份 | 新任务未测 | node28_01 committed_bytes=16MiB | commit fsync文件，然后publish；文件存在不等于掉电恢复协议：无目录fsync/原子manifest/catalog恢复；RemoteExtent构造必create(xb)，无open-existing；close会删extent |
| 多块identity/tp/off/n + meta/index +内容校验 | 新2×16MiB全部SHA通过 | 旧132量级block/每rank多个256MiB extent及全模型访问 | Catalog generation/lease/coverage；wire压缩manifest；SSD store只hash key.bin不存权重manifest；manifest留worker/controller；不能以文件名恢复完整内容布局 |
| CPU-only DRAM memfd owner/NUMA/lease/adopt | 新GPU应用DRAM通过，但本轮没有CPU owner重启认证 | owner_verify10重建后全GLM正确，旧v0.1.23.2；owner_verify07 NUMA失败保留 | owner不导入GPU，UNIX SCM_RIGHTS同host映射；worker HIP注册；persistent指GPU worker退出后CPU进程保活；不是DRAM掉电持久；owner恢复依赖活client /proc FD |
| CPU-only存储角色与Cluster端点 | 未部署第三节点 | 旧SSD独立CPU service存在 | SSD endpoint可放第三存储节点；DRAM owner仅本地；ClusterWorker/Worker+mesh需GPU/CUDA/NCCL；CPU owner不是Cluster.transfer rank。第三CPU SSD节点是未来合法存储mapping，不构成三GPU节点准入 |
| 多层候选放置与共同评分 | 新2节点路径子图可执行；未认证多层优化收益 | 旧其他配置结果不认证当前 | joint_scenarios按tier/location通用预算可容SSD；path_for/restore支持SSD；placement_plan usage仅dram/hbm；不要笼统重建solver：greedy SSD usage键/descriptor store_endpoint传播缺口；joint SSD候选也需执行manifest/store绑定与预算审查 |
| SGLang Weight Cache/IPC复用 | 新配置未运行冻结upstream daemon | vendor native node29全模型gate通过；自有HIP IPC旧S1通过 | 冻结源码daemon postquant TP state_dict + Torch IPC/VMM FD + compatible CacheConfig；upstream源码功能、vendor加载、项目适配分别标识；IPC同host同GPU producer需活；没有本轮SSD/DRAM联合放置证据 |
| Janus sleep/wakeup/fork/xTensor | 原系统未复现 | 共同执行器Janus host/D2D mechanism control已有旧正确恢复 | 冻结上游有host cached weights/light sleep/fork/xTensor；复用源/lease/生命周期及共同执行器机制对照；不能称原Janus性能或任意SSD/多节点规划已支持 |

## 复用顺序与最小接口缺口

1. 复用 Catalog 的区域覆盖、generation、epoch、lease、source/target SHA和最后coverage退役检查，保持 complete 的SSD fsync后发布流程。复用 Cluster.transfer 串行执行；SSD源不能默认交给现有双缓冲copier。
2. HBM cache、DRAM保活owner、SSD完整底本是独立账本。HBM依赖活producer；DRAM memfd依赖活CPU owner或存活client重构，SSD文件fsync只证明文件提交机制，不构成通用掉电恢复服务。
3. 复用 restore_plan/path_for、multi_joint 通用tier/location/shared预算以及 service_cost 的worker依赖队列。placement_plan 的usage仅枚举dram/hbm，直接给SSD locations会缺usage键；构建候选没有复制store_endpoint/storage_node。应先冻结SSD完整底本，优化HBM/DRAM副本；如果本轮必须优化SSD位置，再补具体descriptor/usage绑定，不另写一套规划器。
4. 第三节点可以未来承载独立CPU HTTP SSD服务；RemoteExtent不要求store与worker同node。但现产品的DRAM CPU owner是UNIX FD本地映射，不能当跨节点Cluster rank；ClusterWorker仍需GPU/CUDA/NCCL初始化。三节点逻辑角色必须另绑定三GPU节点的可用性，不能用CPU服务位置替代第三GPU准入。
5. SGLang冻结upstream Weight Cache已有postquant TP权重daemon/兼容协议/IPC，Janus已有sleep/wakeup/fork/xTensor及host缓存。优先复用项目既有HIP IPC映射、同内容元数据、lease和恢复执行器；将原上游能力、vendor native loader、共同执行器机制对照分开记录。无需先重建这些组件。

## 现成本模型遗漏/尚未校准

path_for对SSD只加ssd:<storage_node>和store_network:<storage_node>两个容量与min速率，未明确服务节点NIC出/入、HTTP worker NIC、kernel page-cache/CPU copy、跨socket内存链、SSD读/写及fsync竞争、控制RPC/队列等资源。新拓扑还需GPU↔NIC/NUMA方向与共享PCIe/root/HCA cut，不能用node均一聚合速率替代物理路径。rates声明不是实际路径证明。

service_cost已有operation固定、first_pair冷启动、warm字节成本、dispatch及同worker执行前序，必须复用。其当前模型只支持serial，且声明没有同时共享链争用校准；SSD源read、HTTP往返和页缓存状态可被同原语服务样本的有效成本吸收，但目前未见新28/30 SSD路径样本。SSD保存侧D2H/HTTP写/fsync、校验重新读全量字节、manifest哈希/IPC映射/NUMA注册、source建立和退役、控制器prepare/verify/publish、进程runtime固定开销以及migration双份峰值需要独立记账。资源下界、worker DAG与物理可执行finite schedule分别报告，不能只max两个下界就认定共享竞争达成。

## 预算与验证边界

本轮继承2GiB/node，不自动提高；SSD完整底本节省DRAM驻留，仍要计SSD服务的Python HTTP内存、page cache、每chunk bytearray/bytes、核验读回和GPU worker runtime/staging。每新增GPU worker约2GB历史观测只能保守投影，不是硬峰值下界。现三节点多GPU没有physical-ready授权或证据，原27/29pending不因历史运行解锁。

本地验证为引用文件存在及SHA冻结、Python源码AST解析；不导入torch、不执行任何网络服务。最初使用python命令失败（环境没有python），随后用python3完成所有静态读取与交付；此失败未引发安装。无GPU/SSH/信号/远端写，未修改main_lib/lib_cite/共享XML。report.json含逐能力证据索引与全部引用SHA。
