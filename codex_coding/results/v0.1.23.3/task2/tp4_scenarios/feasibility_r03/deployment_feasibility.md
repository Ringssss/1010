# TP4 部署可行性与预算账本

开发编号 v0.1.23.3/task2。ready=false；本轮只读元数据与本地CPU核算，没有启动GPU或申请扩大配额。

原CPU平台期fixture是4个target+4个source、8个不同worker。8不是TP4的数学必要数量，但它保留了原例的四个互不相交worker pair。把target兼作其他target的source会加入source-ready依赖；合并source会串行其出口，不能沿用Ti=8−3xi的独立服务式。CPU owner也不是当前Cluster.transfer的独立GPU传输端。

预算保持DRAM/HBM各2GiB/node；隔离容器8GiB cgroup上限不是任务授权。两端单worker warm后DRAM观测为2,029,518,848/2,038,231,040B，HBM为371,302,400/371,310,592B。HBM观测已含64MiB staging，下面另列staging但不重复加入。独立worker观测线性相加只是保守预算投影：共享映射库/页缓存可降低增量，8worker mesh的28个pair group又可能增加开销；真实多worker峰值和最小物理内存仍unknown。

字节账本按主agent接口约定：每rank两个4MiB块、d=8MiB，共4rank/32MiB完整DRAM底本，8个local candidate各4MiB。C1/C2总cache上限16MiB，下表以每target保存一个4MiB块的对称分配示例计账；C0总cache上限32MiB，JSON另列每target两块全保活账本。原2×16MiB manifest并不自动变成真实TP4。各节点预留2×d校验DRAM临时区和8MiB余量，仅用于投影；前轮8MiB margin已未覆盖全部增长，不能认证峰值。原理论8GiB/4GiB参数的底本/target/cache分别32/32/16GiB，单target也不满足当前2GiB/node任务HBM额度。

| 候选 | worker分布 | 节点 | runtime DRAM投影B | 底本B | cache/target/staging HBM B | 含应用DRAM投影B | 2GiB DRAM投影 |
|---|---|---|---:|---:|---|---:|---|
| two_nodes_eight_distinct_workers | 8总worker | node28(4) | 8,118,075,392 | 0 | 16,777,216/33,554,432/268,435,456 | 8,143,241,216 | 超投影、未准入 |
| two_nodes_eight_distinct_workers | 8总worker | node30(4) | 8,152,924,160 | 33,554,432 | 0/0/268,435,456 | 8,211,644,416 | 超投影、未准入 |
| three_nodes_eight_distinct_workers | 8总worker | node27(3) | 6,114,693,120 | 25,165,824 | 0/0/201,326,592 | 6,165,024,768 | 超投影、未准入 |
| three_nodes_eight_distinct_workers | 8总worker | node28(3) | 6,088,556,544 | 8,388,608 | 8,388,608/16,777,216/201,326,592 | 6,122,110,976 | 超投影、未准入 |
| three_nodes_eight_distinct_workers | 8总worker | node30(2) | 4,076,462,080 | 0 | 8,388,608/16,777,216/134,217,728 | 4,101,627,904 | 超投影、未准入 |
| two_nodes_four_target_source_reused_workers | 4总worker | node28(2) | 4,059,037,696 | 16,777,216 | 8,388,608/16,777,216/134,217,728 | 4,100,980,736 | 超投影、未准入 |
| two_nodes_four_target_source_reused_workers | 4总worker | node30(2) | 4,076,462,080 | 16,777,216 | 8,388,608/16,777,216/134,217,728 | 4,118,405,120 | 超投影、未准入 |

两节点8worker是4+4；三节点8worker可3+3+2，但27仍有旧D采集进程退出未证，未访问其驱动，也未访问29。仅按单worker保守投影、若每node放1个新worker，需要8个节点；这是条件式账本选项，不是已证物理最小配置，也没有获得8节点授权。当前两/三节点方案均未证明预算准入。

28/30静态各8GPU、8NUMA、4HCA，GPU NUMA编号0/3/2/1/4/7/6/5；HCA位于NUMA0/1/4/5。rolemap列出GPU0/3/4/7与这些位置的亲和建议，但当前产品资格只覆盖28GPU0↔30GPU4，经双方shca_0:1、IBext_v8、gdr0。HCA亲和性不是独立容量实测；单node多source/target使用shca_0时共享同资源ID，不能为每GPU复制一份网卡带宽。

旧keeper不能合法直接复用。28保留cluster workers的session=full_distributed02、ranks16–21，原mesh是node27:0-7,node29:0-7,node28:2-7，共22rank；当前控制器要求连续rank和单session，现有pair communicator不能跨新session拼接。旧39702–39707为serving.keeper，未证明支持cluster prepare/execute。历史启动development=v0.1.23.2，仅是历史来源标签；旧进程当前加载的源代码SHA未知，磁盘新源码SHA不能证明热进程版本。保留GLM DRAM目录合计294,078,259,200B是受保护底本，不能替换为本轮32MiB fixture或当空余容量。

本轮08:59:04UTC各一次metadata-only刷新通过：同boot/CID/StartedAt，隔离容器pids1/noOOM，28两个旧容器和13个已知进程identity保持，新隔离project资产SHA匹配。复用已部署guard但在内存中将keeper endpoints置空，禁pycache；没有keeper RPC、HIP/SMI、信号或远端写。目录完整性仍引用07:48旧证据，本轮未升级full status，也未刷新端口/模型内容/新运行时健康。

已绑定最终contract_cpu_r03/contract.json及其SHA。JSON的contract_case_budget_ledgers按C0[2,2,2,2]、C1_equal[1,1,1,1]、C1_uneven/C2[2,1,0,1]逐target容量核算节点cache上界。C1/C2仅更改source4/5的共享出口语义；目前全走shca_0时还会耦合其他source与target，不能冒认符合此受控差异。

下一步按已冻结synthetic case的内容与DAG证明当前额度可准入或形成独立可审资源方案。新process与existing protected keeper分别计账；无需重造worker。缩块只能降低应用字节，不能证明固定runtime可叠加。共享HCA容量、路径成本与并发执行都留unknown，launch_command=null。

机器可读原件：deployment_feasibility.json；只读元数据原件在feasibility_r01/metadata_node28/30（JSON精确引用）保存stdout、stderr、command与时间戳。所有证据SHA见JSON evidence。
