# 最终进程关闭账本修订与实际原件分析

实际 node27 单节点诊断已完成完整70,329,718,112B权重单映射捕获/全SHA、参考输出、物理暂停及DRAM保活源持久性检查。两rank的 allocated下降与整卡free增长都通过>=90%权重门。没有helper/cache/pathcal/C0/留出成本样本。

- rank0: engine allocated下降35180746240B；实际整卡free增长35213279232B。
- rank1: engine allocated下降35180746240B；实际整卡free增长35213279232B。

原host返回1且run_summary.passed=false保持：仅 engine supervisor 原cleanup guard记录 `identity changed before KILL; no further signal`，发送过TERM，没有KILL。guard进入前leader159/start_time28561631与登记身份一致；随后subreaper实际waitpid回收leader159/status9及其他直属/收养子进程，并返回无剩余子进程。最终收集核自有容器pids.current=1。物理暂停成功、最终quiet成功、原guard失败是分别保留的事实；不改判旧supervisor.summary或旧host结果。

未来源码修订仅derived_supervisor.py、对应provenance与新CPU检查。原runtime_gate_r03.py精确信号归属guard及其SHA不变；新final_owned_closure在原guard和bounded subreap之后执行，只读取当前boot/PID namespace、登记leader与其pgid，并记录ECHILD。要求登记完整、boot/ns不变、leader缺失、精确组为空、子进程ECHILD。原guard失败只在上述同一before-KILL理由、信号前exact owner一致、且waitpid收到了登记直属leader终止记录时允许新的最终关闭账本通过；未回收的直属child PID不能被复用，之后出现相同PID的live对象仍拒绝。任何未知身份、不同失败理由、剩余进程或缺ECHILD均拒绝。没有重试或增加信号。

summary保留原cleanup及其passed=false，新final_owned_closure独立记录；未来summary.passed以最终严格关闭账本、原failure为空和owned STOP为条件。r03冻结的138项stage资产不修改，现有实验没有应用修订。

CPU命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/check_final_closure_cpu.py --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_final_closure_cpu_r01`。16项通过：实际ledger回放、原guard/归档不可变、多种拒绝条件、真实自有CPU child终止waitpid/ECHILD以及future provenance SHA一致。无GPU、SSH或额外signals；没有完整模型重跑。回放给旧reap增加的ECHILD只是旧代码唯一passed分支的CPU解释，不写回旧原件。
