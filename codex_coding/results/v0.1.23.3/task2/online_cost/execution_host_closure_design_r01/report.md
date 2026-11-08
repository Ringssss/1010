# 失败收集与本任务子进程回收修复

本轮没有 GPU 或远端执行，也没有修改资源预算。collect 现在独立处理每个 node 的 archive 和 metadata，任一节点 quiet/身份失败仍继续后续节点。原件分别保存，collection_qualification.json 汇总每个节点 raw reference、pids与失败；整体 gate 继续失败，不把僵尸进程当 quiet。5项针对性 CPU mock检查确认首节点pids7失败仍收集第二节点且最终失败。

新 supervisor 在 Popen 前调用并验证 Linux PR_SET_CHILD_SUBREAPER，使本任务进程的孤儿后代归本 supervisor，而非 sleep PID1。原 frozen helper 的 exact child/session/process-group 校验和信号逻辑保持；其 cleanup 完成后只对该 supervisor 的直接/收留子进程执行有界 waitpid(-1,WNOHANG)，不发额外信号。summary 同时保留原 cleanup 与 adopted_child_reap，成功需两者都通过。5项真实 CPU fork检查确认 grandchild 的原 parent 退出后归测试supervisor、被waitpid回收、/proc不再存在，无残留。

这些 supervisor 改动只在下一次主 agent 新 prepare 的快照生效；旧129项 staged snapshot与失败原件不变。主 agent正在审查实际HBM gate事实，由 ready_path保存必要numeric records后再冻结新 run r03；这里不放宽容量、不重跑GPU、不创建新容器、不构建复杂reaper或overlay机制。
