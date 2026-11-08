# 后续监督器最终退出条件与容器收口独审

静态审查通过；5项源码出处/原件一致性核对通过，已有16项针对性CPU检查通过。没有新增测试或GPU/SSH/Docker调用。新derived SHA为`fae687b7fdfc672b03c957af0fba128af4c5229e840d0d28a6987ef8e74e16bf`，provenance与wrapper检查一致。详细引用见`qualification.json`。

新增`final_owned_closure`在原准确signal cleanup及有界subreaper之后执行，只读取事实，不发送信号。判定要求当前boot/PID namespace与登记时一致、登记owner字段完整且PID=PGID=SID、原leader不存在、该准确进程组为空、subreaper无待回收且waitpid实际到ECHILD。原guard成功可沿正常出口确认；原guard失败只接纳明确的`identity changed before KILL; no further signal`，且原pre-signal before必须与登记owner完全匹配，并取得该准确直接子进程的终止wait状态。其他guard失败、PID复用、wrong start、缺终止状态、非空group或缺ECHILD均拒绝。主summary仍保留原cleanup、child_returncode和独立closure事实，阶段失败或非coordinated STOP不获通过。

旧实际engine归档及其guard失败保持原件SHA `e6dd7f07...`、host run passed=false，不回填新closure为旧实机结果。CPU中的实际账本回放和真实direct-child wait/ECHILD只认证新判定逻辑；**新退出条件尚未在下一次GPU监督运行验证**，下次需要新冻结资产/新run，并分别检查原guard、reap、final closure及最终quiet。

`resume_owned_stop_r01/node27_after.json`和`node30_stop.stdout`最后一行真实状态均Running=false、PID=0、OOMKilled=false，两自有新容器已停止；ExitCode137保留，不称OOM。停止前两个容器的进程原件只有sleep PID1，没有本任务GPU角色。此收口与[完整局部暂停实测](../resume_review_r05/report.md)共同构成本轮结束证据。

当前可认定单映射capture/完整70.33GB物理暂停功能通过；node30资源仍不满足helper聚合2GiB门。pathcal/C0/Hnear/Hremote样本0、在线整体10%误差和放置收益未知，不追加GPU实验或放宽预算。
