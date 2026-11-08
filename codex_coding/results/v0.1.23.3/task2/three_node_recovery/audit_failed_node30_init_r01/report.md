# node30 实际初始化失败的独立证据审查

`summary.json` 的 14 项证据边界检查通过，**实际 runtime 仍失败**。本审查只对已授权失败 run 做当前 node30/container 身份、精确 PID13 和八个固定文件 SHA 的有界只读核对；未调用 GPU/HIP/SMI，没有发送信号，也没有启动 copy 或重复初始化。

## 已确认的运行边界

请求为 node30、masked logical GPU0、物理 BDF `0000:09:00.0`、`stage=init`。Torch 2.10.0/HIP版本字符串6.3.26113 导入耗时 2.095828215 秒，最后事件为 `set_device_enter`；没有 `set_device_exit`、实际 HIP BDF 查询、HIP free、payload 或 communicator 证据。故障发生在 Torch set_device 新进程初始化范围，不能据此断言永久坏卡、XML错误、visibility因果或其他GPU不可用。

supervisor 从 Popen 起使用硬15秒等待，总计15.164986386秒含精确回收，未放宽deadline。容器内 child PID13/start477410700、PGID/SID13、argv、boot 与 PID namespace 在 TERM 前匹配登记。只发送一次 TERM，child rc=-15；supervisor已reap、原PID absent、pending group为空。当前严格hostkey只读核对再次确认同 host boot、同完整容器ID/StartedAt 且 PID13 absent。因此此 run 的 exact cleanup 已证；原实验 `passed=false` 保持。

`init_deadline_passed=true` 是此源码对纯init关卡未配置额外阶段deadline时的返回值，不能作为初始化已准入的证据；实际 `timed_out=true`、无 `init_admitted` 且 `passed=false` 已明确失败。

## 保护与容量范围

独立外层审查 `audit_guard_node30_init_r01/summary.json` 通过：host boot、完整container ID/StartedAt/PID、source assets SHA 和新8GiB隔离cgroup连续；core=0、OOM/OOMKill为0，前后可见KFD owner均0，cgroup pids.current最终1。node30没有已登记own keeper，本次不强求不存在的旧keeper HIP指标。

GPU0 sysfs total=68,702,699,520B、used=2,207,744B，前后 total-used均68,700,491,776B，差0。这里认证的是 sysfs 观察一致，不是 HIP physical free 或任务授权预算；当前masked HIP BDF尚未返回，不能把 requested BDF升级为actual已核身份。owner全量/独占仍未知，性能及planner继续未就绪。

node27 own D child PID4037472/start477235183 仍pending；已退出controller不重列为活跃。node29旧PID21883/start475875814、21901/start475875846仍pending，无新控制/退出证据。本次30窄后置不能升级27/29或完整DRAM保留服务保护。

## 冻结证据

`audit_remote_node30_init_r01/summary.json` 保存严格SSH命令、当前boot/container核对、PID13 absent与远端7个存在文件SHA；第8个`child.result.json`不存在，符合TERM终止没有finally结果写出的边界。`child.log`只有远端SHA，本审查未读取其内容，未将日志未知内容用作因果解释。已落地6个主run artifact全部与远端SHA一致；源码冻结SHA为`927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b`。

`audit_failed_node30_init_r01/summary.json` 绑定上述run、native launcher命令/返回码、外层独立审查与只读核对的路径和SHA。原native launcher rc1、实际failed摘要保留；没有覆盖旧结果或history-chat。

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/audit/review_failed_init.py --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/audit_failed_node30_init_r01
```

该输出目录已存在。重做本地审查须新目录；上文不要求或授权重做GPU实验。
