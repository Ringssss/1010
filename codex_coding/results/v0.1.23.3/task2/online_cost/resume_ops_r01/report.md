# 本轮有限资源复查与 IPC 探针交接

开发编号保持 v0.1.23.3 / task2。本目录只建设协议、来源及命令；子 agent 未执行 GPU、SSH、Docker 命令。

`commands.json` 使用现有 `hostops_guard.py` 的 same-HIP 查询及原 52GiB/card、helper 两卡聚合 2GiB + 256MiB 余量门。查询仅选定 GPU4/5，保持完整八卡编号，核 BDF85/b5 与 PCI NUMA4/7，不读 node27 KFD/VRAM 表或 owner，不访问 node28/node29。node27 如任一卡资源不足，停止本轮准入，不启动完整模型；node30 查询仅在目标通过后需要 helper 时执行。

32MiB 探针按原 single-share tuple/单 consumer 的 once、twice 顺序，新输出目录 r02，不覆盖旧 r01。once 必须完整 known-byte 验证和精确 consumer cleanup 通过，再进行 twice。twice 只用于辨别同一 IPC 引用计数凭证重复消费；counter 未知则结论未知，不能用 raw_import_count 或进程退出本身证明下溢。

静态审查：consumer intrinsic watchdog40s、producer60s；父等待45s，精确 consumer PID/start_ticks/pgid/sid/argv/cgroup 匹配后 TERM/KILL，producer 是 subreaper。consumer 不启动框架 mesh。线程环境在 Torch 导入前置 1；GPU 可见编号完整且清 ROCR/CUDA 覆盖。源码原来的 pre-child source/share 阶段不在 finally 内，但 watchdog 和进程退出限制运行；该阶段异常后仍必须收集 failure，并停止精确 own27 容器。Docker/SSH 客户端超时不等于容器内任务退出，应等待 intrinsic deadline 后再精确 stop。

producer 分配/删除和 consumer 原件、私有 refcounter 文件缺失是不同清理层。完成两配置后停止准确 own27 CID，使私有 IPC 命名空间关闭；保存 stopped State（Running=false/Pid=0/OOMKilled=false）及新 StartedAt，作为退出和 IPC namespace disposal 的证据。不扫描共享 SHM、不删除外部文件。若 node30 仅为 fresh query 重启，也由主停止准确 own30。

本次没有改动探针实现。未运行新的 GPU 配置；物理释放与计数器假说仍待实际原件。
