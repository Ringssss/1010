# 资源地图最终独立审查

编号保持 `v0.1.23.3/task2`。最终审查对象为 [build_r04/resource_map.json](../build_r04/resource_map.json)。**地图结构、证据绑定和失败关闭接口通过；自有完整 XML 的 runtime acceptance 尚未建立，node29 两个自有探针 PID 的退出与保护状态尚未确认，性能规划仍阻塞。** 独立审查只访问本地文件，新增 GPU/SSH/RPC 操作为 0。

| 交付与验收层级 | 结果 |
| --- | --- |
| 全设备结构与原始证据 | [summary.json](summary.json)，547/547 passed |
| 失败/未知状态边界 | [state/summary.json](state/summary.json)，21/21 passed |
| 最后 recovery 同状态增量 | [recovery_delta/summary.json](recovery_delta/summary.json)，12/12 passed |
| 独立规划准入 CPU 用例 | [audit_cpu_r06/planner_negative.txt](../audit_cpu_r06/planner_negative.txt)，18/18 passed |
| 独立 XML CPU 用例 | [audit_cpu_r06/xml_negative.txt](../audit_cpu_r06/xml_negative.txt)，9/9 passed |
| 新完整 XML 实机接受、保护和回收 | 未通过认证；原始失败保持 failed |
| 当前性能/规划输入 | `runtime_ready=false`、`performance_ready=false`、`ready=false` |

地图包含两机16 GPU、16 NUMA、8 HCA、8端口以及88个唯一 PCI bridge。审查直接逐项对照两机冻结 host/container sysfs、NUMA meminfo、SMI 和历史 discovery 的 ordinal→BDF 证据，覆盖全端点的 BDF、真实父链、NUMA、CPU集合、DRAM总量、报告HBM总量、KFD身份和全部顶层source SHA。GPU ordinal证据带原采样时间，不伪称本次重新初始化 HIP 后得到的fresh映射；新物理证据与旧BDF映射逐项一致。

400Gb/s保存在 `nominal_line_rate_bps`，所有未测有效资源速率 `capacity_Bps` 保持 null。network fabric内部拓扑和总容量保持未知；没有推造GPU间完整实测带宽矩阵。GPU0/shca0的两个共同PCI祖先以唯一resource ID查询一次；node的DRAM汇总不是额外资源。旧G0/G1/G2、H2D和keeper RPC工作点可溯源查询，但 `rate_Bps=null`、`current_profile_eligible=false`，未进入当前规划参数。

free、任务权限、外部owner、显式授权预算分别保存。12:00左右的availability是历史快照，`live_status_claim=false`；预算为null不因有free自动获得授权。最终诊断请求明确列两机GPU0/1及两条RCCL方向，尚缺fresh资源/owner证据、exclusive条件、显式budget和当前匹配的有效速率；node29还因控制不可达与自有清理未证阻塞。`planning_input`抛 `PlanningNotReady`，不会输出貌似可执行的计划。

CPU负例覆盖active外部owner即使有headroom、裸 `allow_shared`、旧owner/boot、未授权budget、陈旧/受干扰/NaN速率、runtime scope扩张、配置fingerprint不符、重复GPU ordinal、跨node父链、虚造fabric容量、bool容量、multi-path aggregate重复当单path容量、控制失联和清理未证。一个有效与一个无效匹配测量并存时，窄输入只输出有效记录。多path联合准入默认关闭，本轮只建设地图和准入接口，没有建设共享容量solver。

XML负例覆盖错误NUMA、CPU mask、真实祖先、PCIe宽度、固定动态dev/rank/GUID、虚构net速率、未接受的gdr政策以及重复CPU。审查发现的net speed/gdr/CPU唯一性漏检已由实现agent修复；规划器的历史记录过滤、fabric/bool容量、aggregate复用等问题也已修复。原失败与修订后验证分别保存：`audit_cpu_r03`保留两项容量漏检失败，`audit_cpu_r06`为最终通过；`audit_static_r02`的两项代码SHA漂移失败保留，最终冻结 `build_r04` 全部SHA通过。

自有XML从全机真实sysfs构造，每机8 GPU/4 HCA的CPU结构验证通过；dev/rank/GUID由本rank运行时填充。没有把旧source单flow dump复制成全机输入，也未复制node27 GUID给node29。启动薄适配逐node验证manifest/input/source SHA、四rank各自输入与独立dump路径，保持原128MiB门槛，固定1MiB功能协议和零性能样本。生产XML、默认mapping及全局环境未改。

两次新启动都未形成RCCL实际图/transport认证。[xml_startup_owned_r01](../xml_startup_owned_r01/summary.json)在node27只读准入超时，`processes=[]`、零通信进程；独立 [audit_startup_failed_r01](../audit_startup_failed_r01/summary.json)认证该边界。[xml_startup_owned_r02](../xml_startup_owned_r02/summary.json)四rank已注册且pre-Torch环境分别指向正确node输入，但源端Gloo rendezvous约91秒只出现2/4 clients，远端rank尚未形成probe结果。不能将这个失败解释成XML已被RCCL接受或拒绝。

第二次startup raw cleanup为failed，node29 SSH banner和postflight超时。独立 [audit_startup_failed_r02](../audit_startup_failed_r02/summary.json)保留13项中的4项未证失败：cleanup、128MiB物理回收、keeper保护及四个exact PID退出。[xml_recovery_r01](../xml_recovery_r01/summary.json)与最后 [xml_recovery_r02](../xml_recovery_r02/summary.json)仅尝试exact-owner清理，不启动工作负载；两条SSH及keeper HTTP均失败，ICMP可达。12项独立delta检查通过只说明新旧证据一致和raw未改，不意味着清理成功。

当前两个精确待证目标为node29 rank2 PID `21883` / starttime `475875814`，rank3 PID `21901` / starttime `475875846`。清理只允许使用原spec/状态SHA、PID/starttime/argv/PGID/namespace校验后的owner范围；不得根据陈旧PID直接signal。`protected_postflight_complete=false`、`protected_state_corrupted=null`分别表达保护未完成与损坏未获证明。未把ICMP可达替代SSH/RPC恢复，也未把两进程“可能自行超时”写成已退出。后续未继续SSH或GPU实验。

[独立物理SVG地图](../audit_visual_r02/physical_map.svg)直接从原始证据生成，显示每NUMA CPU/DRAM和每GPU BDF/ordinal/DRM/HBM与局部HCA，图中无推造fabric连接容量。其 [说明](../audit_visual_r02/README.md)标明ordinal与HBM来源。

复核命令（输出目录须使用新编号，不覆盖已冻结结果）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/audit/review.py --map codex_coding/results/v0.1.23.3/task2/resource_map/build_r04/resource_map.json --provenance codex_coding/results/v0.1.23.3/task2/resource_map/provenance_r01 --runtime codex_coding/results/v0.1.23.3/task2/resource_map/runtime_r01/snapshot.json --out codex_coding/results/v0.1.23.3/task2/resource_map/audit_final_r02
python3 codex_coding/src/v0.1.23.3/task2/resource_map/audit/review_state.py --map codex_coding/results/v0.1.23.3/task2/resource_map/build_r04/resource_map.json --out codex_coding/results/v0.1.23.3/task2/resource_map/audit_final_r02/state
python3 codex_coding/src/v0.1.23.3/task2/resource_map/audit/test_planner_negative.py
python3 codex_coding/src/v0.1.23.3/task2/resource_map/audit/test_xml_negative.py
```

仍未定：node29控制恢复及两exact PID退出、全保护后置证据、自有XML的所选路径实机接受、其他GPU/HCA的运行时覆盖、稳定同条件有效带宽、fabric共享容量、GPU对间有效速率，以及与placement相关的完整模型weights-ready收益。地图可读查询已成立；以上未知不会被现有接口自动升级成规划就绪。
