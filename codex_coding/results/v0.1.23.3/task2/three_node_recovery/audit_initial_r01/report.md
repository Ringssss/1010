# 27/28/30 迁移的独立前置审查

本审查仅阅读本地代码与冻结历史结果，无 SSH、HIP、GPU、SMI 或信号操作。编号保持 `v0.1.23.3/task2`。必读依据为当前 AGENTS 指示、`docx/next_step.txt`、`docx/debug_philosophy.md`、正式计划 18.11–18.14、实验报告 26–29 与 `resource_map/resume_map_r02`。

## 最小可推进路径

先采 27/28/30 的 sysfs/KFD/DRM 物理身份、主机容量、进程 owner 与容器事实；选择满足当前功能资源条件的节点，再由主 agent 安排单卡初始化、单卡 1MiB 全量回读、同机自有 XML 两卡功能与跨节点小功能。各关卡独立新目录，一次失败后保留原始结果并按已知故障边界决定下一步。局部功能可独立推进，不能因 node29 控制缺证而永久停止所有新节点工作。

## 可复用与必须改造

`resource_map/local_xml/common.py` 的 lease 语义、全 catalog 哈希、原 XML 指纹、固定 payload 和 guard 可复用；正常历史 RELEASED 且 leases=0 的条目允许存在，完整 DRAM 底本仍需 VALID 且 leases=0。

`local_xml/run_local.py` 和 `read_local.py` 有 boot/容器连续、spec SHA、PID/starttime、live argv/PGID 与 Z/absent 不发信号的封装。`cluster/process.py` 自己只有 PID/starttime，不足以直接作为跨节点安全 stop；新入口必须保留完整封装，或由创建进程的 supervisor 直接回收/reap 自己的 child。

旧 local_xml 绑定 node27、GPU0/1、旧 container 和旧 keeper session；不能把修改 NODE 常量视作通用迁移。旧 `inventory.node_snapshot` 间接调用 HIP/SMI，不能用于新的无 HIP 首轮采集。

## 证据边界

- 物理 GPU 身份固定为 node+BDF。sysfs/KFD/DRM ordinal 不等于 masked HIP ordinal；单卡可见时 HIP0 必须以实际 BDF 核对。
- sysfs total-minus-used、HIP physical free、任务授权预算分别记录。sysfs 余量可筛选候选，不能替代 payload 前 HIP free 的原 2GiB guard。
- 28/30 的历史 GPU 占用不是永久禁用，新节点授权也不等于独占。扫描权限失败或 owner 未知必须保留 unknown，不能据零候选认定空闲。
- 小功能结果不提供当前有效速率；性能样本、XML 接受、资源独占/预算、规划准入分开。不得搬用 node29 的旧 rate 或旧 availability 作为新节点 live 证据。
- 每个 GPU child 在库加载前 core=0、先落盘 PID/starttime/boot/container 身份、早期 deadline 与外层 timeout；超时清理不能只杀 docker 客户端。跨 boot 或容器重启拒绝继承旧 PID owner。
- 原厂 XML 和默认 mapping 不修改；本任务 XML 与 dump 分文件，只在新进程显式配置。
- 涉及 node27 时保护其保留 keeper、完整 70,329,718,112B DRAM 底本、parked source、旧 legacy 服务与元数据目录；未涉及 node29 的窄功能不冒领 node29 postflight。

## 必须保留的旧待证状态

node29 / `zjn-fluid-basic-1bce3897a2075b` 的旧 rank2 PID21883/start475875814、rank3 PID21901/start475875846 继续 pending。无新身份/退出证据不能改成 cleanup 成功，也不能据此断言它们仍占 GPU。`resume_map_r02` 原件不覆盖，全局 `protected_postflight_complete=false`、`protected_state_corrupted=null` 保持。

本文件是脚本编写前的审查意见，不是平台、XML 或 GPU 运行验收。新源码与结果出现后另建独立审查目录并绑定冻结 SHA。
