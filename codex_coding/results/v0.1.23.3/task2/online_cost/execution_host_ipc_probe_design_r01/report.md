# 单 share tuple 双 import 最小诊断

只在当前本任务 node27 容器、物理GPU4/BDF85执行，维持8卡visibility，before-import线程限制。一次独立 parent进程创建一个32MiB uint8=13张量，只调用一次 _share_cuda_，pickle存储返回tuple。唯一 consumer 子进程分别做1次或2次 raw _new_shared_cuda import；同一tuple、每次全32MiB known byte检查后del/gc/sync，避免Torch multiprocessing shared cache 改变路径。两个配置必须使用两个独立parent进程与新输出目录。

记录 parent/consumer allocated、reserved、same-HIP free/total；fresh以及每一步whole-card total-free不得超过原2GiB门，超过即停。parent在consumer退出后释放source/storage、GC、empty_cache、ipc_collect、sync，保存allocated差值。可用时仅read-only读取自己exporttuple的 refcounter shm名/offset指向的8字节，记录signed/unsigned，缺失为inconclusive。不会修改共享counter或扫描其他owner/shm。

parent启用subreaper；consumer40s、parent60s watchdog，parent45swait，超时只对注册准确PID/start/PGID/SID/argv/cgroup的consumer新进程组TERM/KILL并有界waitpid。parent watchdog先执行相同exactcleanup并保存deadline_child_cleanup.json再退出。无完整模型加载、无DRAM迁移/RCCL、不动129项旧源码、预算或其他GPU。

7项针对性CPU mock检查通过 execution_host_ipc_cpu_r01，语法检查通过；子agent未执行GPU/SSH。commands.txt只供主agent分阶段运行，每条GPU命令仍须外层deadline建议75s，先收集一次import结果，再执行二次。result.passed只指诊断完成与payload/进程清理，allocated/counter差量是否支持假设须独立审查；不能据此宣称完整模型唯一根因。
