# 27/28/30 迁移的独立审查结果

编号保持 `v0.1.23.3/task2`。本审查只读本地文件，并创建新 synthetic CPU 子进程验证 watchdog；无 SSH、Docker、HIP、GPU 或 SMI 调用，无旧 PID 信号。

## 已验收的建设

`audit_inventory_r02/summary.json` 认证 node28/node30 的冻结原始采集、实际 hostname/boot 与已认证历史 SSH 对应关系、collector SHA、独立物理 BDF 与 sysfs 总量减已用算式。两机各 8 KFD GPU、8 NUMA、4 HCA；9 个 DRM 设备中的 ASPEED 显示卡不能算作第 9 GPU。node28 可见 16 个 KFD 进程，node30 可见 10 个；均未认证 owner 完整、独占、HIP free 或任务预算。新数据可用于功能候选，不能作为性能规划准入。

`audit_watchdog_r01/summary.json` 对真实 CPU child 独立验证了两个故障路径：忽略 TERM 后在精确身份检查下 KILL 并 reap；故意使 owner 文件持久化失败后仍在 finally 中精确回收。core=0、无 Torch 导入、两个 child 均退出，所审源码副本与 SHA 已冻结。后续 agent 的 `probe_cpu_r03` 另冻结了缩小非 own `/proc` 读取范围后的源码；该修改只减少取证范围。

`init_gate.py` 已拆分默认 `init` 零 payload 与显式 `copy` 1MiB 全量 SHA 关卡，节点标签与实际容器 hostname 分开，实际 HIP BDF 必须匹配 node+BDF，payload 前 HIP free 保持原 guard。容器 ID/StartedAt 由父在 host 外层核实，内层只证明 boot/PID namespace；内层结果不认证 keeper catalog 或整机后置保护。

`fresh_container.py` / `fresh_host.py` 的静态建设适合下一步创建自有隔离功能容器：只用本地准确镜像，不覆盖已有 stage/容器，8GiB cgroup、private IPC、1GiB shm、core0、hyhal只读。setup 返回码为0仍须检查实际 docker run 和 inspect；guard 返回码为0仍须检查身份字段全 true、源码 asset SHA 与外层保留服务证据。创建容器与 GPU 运行由父统一协调。

## 当前明确未通过的范围

node27 本轮没有新完整 inventory。collector PID4037472/start477235183 在 KFD `vramtbl_45786` 读取中成为 D 态；controller PID4037469/start477235176 经精确身份核对后已退出，child 退出未证。`partial_completion.json` 记录一次自动 timeout SIGKILL 尝试与一次授权 controller TERM；旧未完成 manifest 的 signals=0 不代表整轮最终事实。此处不得盲重读 owner 页表或宣称三机全采完成。

node29 旧 rank2 PID21883/start475875814、rank3 PID21901/start475875846 仍 pending；迁移没有取得新的 node29 boot/container 或退出证据。旧 resume_map_r02 原件、历史速率和 availability 不覆盖。

原两节点资源地图结构正确与新三节点平台可运行是独立验收。当前本审查尚未取得新 GPU/init/copy/自有 XML NCCL 实机证据；不升级 XML runtime、性能就绪或 planner。新增 NCCL 薄入口须先完成 actual PID/rank 加载自有 XML、无默认 mapping fallback、真实 channel 日志与最终 init deadline 核查，再由父安排小功能；本文件不授权 GPU 实验。

涉及 node28 时，需要保护其可访问的旧 legacy keeper catalog、epoch/session/leases；涉及 node27 时需要额外保护完整 70,329,718,112B DRAM 底本和 parked source。正常历史 RELEASED+lease0 条目允许；底本仍须 VALID+lease0。node29 缺证单列，不阻健康节点窄功能，也不冒领全局保护完成。

## 可复现的独立检查

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/audit/review_inventory.py --inventory codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_r03 --scope node28 node30 --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/audit_inventory_r02
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/audit/check_watchdog.py --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/audit_watchdog_r01
```

上述目录已存在；未来重做必须使用新目录。失败、旧源码与旧账本均保留，无全历史归档/history-chat 覆盖。
