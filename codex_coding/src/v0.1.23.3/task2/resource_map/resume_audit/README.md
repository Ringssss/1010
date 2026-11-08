# 资源地图续作独立审查

本目录只读取本地结果，不发起SSH/RPC，不启动GPU。冻结地图`build_r04`和历史失败不修改；新事实单独作为状态delta。

## 恢复准入必须条件

- node29控制恢复需要SSH取得可解析的host/container身份与容器内状态。ICMP通不证明控制恢复；HTTP恢复与SSH恢复分别记录。
- 原host boot基准为`a5bc9009-f911-406d-aa56-30a8d08f01f5`；原node29 owned容器名为`zjn-fluid-basic-1bce3897a2075b`，旧preflight记录StartedAt为`2026-10-06T12:39:02.990558427Z`、host init PID为3263521。同名不是同容器身份，须保存新container ID/StartedAt、boot、PID namespace与采样时间，说明与旧证据的连续关系。
- 若host boot或容器生命周期改变，旧PID/starttime不能用于清理当前PID。原rank退出可由旧生命周期终止证据证明，必须标明证明方式；不得伪称当前namespace中的同号PID就是原rank。
- exact rank2 PID21883/start475875814和rank3 PID21901/start475875846：仅身份连续、spec/state SHA、PID/starttime、argv/PGID全匹配的live原owner允许精确stop；absent或Z只取证，不再次signal。PID复用或身份不明则拒绝signal。
- cleanup恢复与保护恢复独立。只有两个原exact owner退出已证，才可关闭pending ledger；只有新鲜完整keeper/完整底本/parked引擎及旧14访问证据，才可声明保护恢复。进程live检查不是底本内容和端点服务验收。
- 原`xml_startup_owned_r02/summary.json`、旧recovery r01/r02、原XML/manifest/默认mapping的SHA保持。新失败和重试保留独立目录。

## 新自有XML功能验收必须条件

- fresh preflight、物理余量、owner归属和原保护准入满足后才由主agent决定启动；当前共置负载仍阻止性能校准。
- 固定四rank、每node GPU0/1、shca_0:1、1MiB/flow单次、zero performance samples、core=0、独立dump、deadline和精确owner清理；保持128MiB物理回收门槛。
- 新run保存脚本/输入/源SHA。分别核四份pre-Torch环境、实际库fingerprint、所选BDF/NUMA/PCI祖先、各node net GUID/dev、实选端口、实际transport、全buffer SHA及四个exact PID退出。
- 四份actual dump和owned NCCL日志必须与原rank PID及本node XML对应；未用叶仍保留于XML不能升级为已运行设备。实际范围最大为node27/node29各GPU0/1与shca_0:1，其他GPU/HCA runtime仍未认证。
- raw failed与postexit补证并存。补证只更新派生结果；输入被接受、cleanup、保护、性能就绪分别判定。旧builtin/empty-seed速率不得成为新XML速率，性能与规划就绪保持false，直到取得匹配配置和合法负载下的有效校准。

## 当前审查发现

旧`xml/recover_owned_run.py`只核spec/state SHA、PID/starttime及live时argv/PGID，未校验host boot/container lifecycle/PID namespace。对Z仍调用`cluster/process.py stop`，后者会signal group；续作应使用只读Z/absent取证分支。旧`audit/review_recovery_delta.py`写死“两SSH失败、cleanup未证”，不能直接用于恢复成功的审查，需要薄适配当前JSON而非覆盖历史结论。

旧`audit/review_startup.py`可复用实际graph/transport检查，但独立冻结源SHA、raw输入、artifact manifest、cleanup与保护的新证据须在续作层另核。其旧GUID来源是`gdr_readonly_r01`；如host/container生命周期或HCA身份变化，不得只依据旧GUID快照声称当前身份。

## node27单机替代功能验证

node29控制阻塞时，可以按新预登记由父安排node27单机GPU0/1两rank、每rank1MiB功能，前后只检查node27两个keeper/完整DRAM和parked引擎。本次执行不调用node29。

- 两rank内容SHA、两份actual图、Loading topology日志、输入/source SHA、原128MiB门槛、两exact owner退出及完整本机保护分别审查。
- 仅建立node27自有输入接受和GPU0/1本机实际transport。P2P/SHM或其他模式按实际日志陈述，不能由tensor接口命名推断直传。
- actual dump含HCA叶只证明图中保留该叶；没有NET传输则HCA不得写入`transport_validated_resource_ids`，不能声称NIC/GDR性能通过。
- `observed_gpu_bdfs`最大为09/36，`observed_hca_ports`应按schema语义保守保持空；图中HCA另放`graph_observed_hca_ports`。node29接受、跨节点传输、GPU2–7/HCA1–3及任何速率保持未证。
- 单机通过不解除node29控制/清理/保护阻塞，不将跨节点runtime/performance readiness升级。
