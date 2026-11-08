# node28/node30初始化阻断收敛与最小copy功能恢复

原15秒失败记录全部保留。原private配置未增加LD或SYS_NICE，也未改IPC，在60秒诊断期限内两节点均完成新GPU初始化；两端随后各一次1MiB往返全SHA通过。这证明两配置具备可完成初始化的运行条件，原15秒总预算会截断此次可完成的工作；不把所有历史超时都永久归因于单一固定时长。当前是共享负载下的小功能证据，无性能/weights-ready样本。

node28总18.637711728秒，Torch导入1.823252434秒、set_device 14.423684600秒，实际BDF0000:55:00.0，HIP free 67,427,631,104B。node30总21.994490046秒，Torch导入2.672581613秒、set_device 16.626798040秒，实际BDF0000:09:00.0，HIP free 12,668,895,232B。两端自然退出、signals空、精确清理通过；copy实际全量1,048,576B SHA/长度通过，准确run原件及SHA见summary.json。父另聚合前后资产/owner/guard与独立C审，不用本摘要代替它们。

node30本轮guard先因/tmp ENOSPC阻断；准确CPU df/statvfs确认根盘与Dockerroot available0、inode有余。本任务没有删除、prune、停容器或改全局Docker配置。将同60秒copy源码d533按相同codex层级放入已有容器自有/dev/shm/fluidckpt_diag30_r01/project，ROOT与result自动指向shm，保留完整CID/StartedAt、privateIPC/原cap/LD/thread1；这是必要存储路径变动，不能和昨日15秒run称完全单变量。新shm source/结果及CPU namespace资产均有exact前后身份与SHA，未改原源927f或原厂XML。

LD-only与hostIPC-only、SYS_NICE-only在15秒门槛下未恢复，故不能把它们的失败当整个配置永久不支持GPU。两份observer exact样本显示主线程CPU及RSS持续增长，促成一次较长时间边界诊断。owned child43库日志有mbind/seccomp自报告，但原cap后来能完成init，所以该错误不是目前已证明的必然阻断条件；SYS_NICE后hylog目录ENOENT，是否消失仍缺证。没有ptrace/privileged/unconfined扩权限。

30NCCL准备只CPU写入runtime97a、自有gdr0物理XML与仅input_file重定位的deployment manifest，XML bytes/SHA不动；原probe927f、namespace0c307与frozen inventory8da563一并在shm同层保存。最新stage后shm余1,073,324,032B。NCCL实际验收由父/A/C对应新run确认，本报告不提前认运行成功或GDR/带宽。

本agent全部实际远程操作为已授权CPU只读或本任务shm新文件stage；GPU均由父统一启动。新脚本/结果限diagnosis30范围，不触27/29，不全历史归档、不覆盖history-chat或旧失败。
