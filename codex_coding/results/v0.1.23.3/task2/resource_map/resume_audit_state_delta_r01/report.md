# 续作独立审查与状态delta

本次审查全部只读取本地文件，没有SSH/RPC/GPU操作。最终可供新派生图使用的状态为`state_delta.json`；旧`build_r04/resource_map.json`、旧XML和历史测量不修改。`audit_status=passed`表示审查证据一致；`operational_outcome=blocked`表示运行条件尚未恢复。

| 最终审查 | 检查数 | 认证范围 |
| --- | ---: | --- |
| `resume_audit_control_r04` | 119/119 | node27旧host/container身份连续、旧own两rank匹配start的Z、protected三进程匹配且live；node29两SSH与HTTP失败、数据ICMP通而管理ICMP失败，boot与exact退出未知 |
| `resume_audit_local_r01` | 29/29 | 本机功能raw失败，前置读取阻塞，零通信rank注册/启动；不认证XML接受、完整后置保护或性能 |
| `resume_audit_local_catalog_r02` | 78/78 | 两本机keeper的完整70,329,718,112B底本meta/index/hashidentity/epochs及整个catalog前后保持；历史RELEASED条目保留、全条目leases=0；无权重内容重读 |
| `resume_audit_diagnosis_r02` | 33/33 | 新诊断子进程精确注册与退出/core0、两种不同失败边界、sysfs/KFD与keeper free分开、owner扫描缺证；后置compact目录身份投影保持 |

`all_visible`的HIP库加载返回后进入hipInit，10秒报警中断；`gpu01_visible`在库加载入口之后SIGSEGV，未记录库加载出口或hipInit入口。这些是不同阶段的失败，不能据此建立可见卡数、XML或其他owner的唯一因果关系。两个诊断子进程与host/docker helpers已退出，不追加workload。

node27 keeper目录层已证明保留，但新GPU进程的基础运行时仍未稳定。node27本机XML和node29 XML均没有实际接受证据，所观测资源集合保持空。node29原rank2 PID21883/start475875814与rank3 PID21901/start475875846继续pending，未根据旧PID发signal。全量protected postflight未完成，`protected_state_corrupted=null`。

物理结构可以查询；运行/性能准入保持false。旧测量不改fingerprint，不给自有XML绑定旧速率，不将sysfs total-minus-used代替HIP Available/free，不把2566项owner扫描权限错误下的零候选当成空闲。

最初catalog审查把历史RELEASED条目误当必须VALID，四项失败保留在`resume_audit_local_catalog_r01`；修订只要求完整base VALID和所有条目无lease，历史state纳入严格前后比较。最初诊断审查把compact目录误当完整meta/index响应，两项失败保留在`resume_audit_diagnosis_r01`；后续改为严格compact投影且明确不能证明缺失的meta/index字段。

运行结束后的授权catalog准入修订产生新源码冻结版本。原startup源码以实现agent的`snapshot_r01`副本为历史证据；审查源以各审查目录`review_source.py`冻结。不得将新live源码SHA解释为原run执行源码，原失败及其code记录不覆盖。
