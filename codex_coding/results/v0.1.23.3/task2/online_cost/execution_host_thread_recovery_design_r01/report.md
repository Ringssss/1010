# 初始化线程资源失败与局部恢复

原运行 online_qwen_two_node_r01 native 四 worker mesh 通过，但 engine TP rank1 初始化 exit−11，原 child.log 多次出现 OpenBLAS pthread_create EAGAIN，supervisor memory/pids 样本触及 pids512；未完成真实模型 bootstrap 或成本校准。内存与 pids 限额保持。

主 agent 用准确 HOST PID2411410、start_ticks27998454、容器CID及 controller bootstrap argv 核对后尝试 host SIGTERM，收到 Operation not permitted，无作用。随后仅在准确新容器核对 NSpid342/start_ticks 后 SIGTERM，原 orchestrator 进入 finally STOP。engine 子树退出后 sleep PID1 收留四个 Z，pids27=5 而无存活实验进程；quiet gate 未放宽。主 agent 留存原件并仅重启本任务 node27，原CID保持，StartedAt从08:59:45.834122443Z变为09:14:48.364461376Z。node30不动、旧容器不动、129项 staged source不动。restart before/after 及原失败记录保持。原 runtime collection-only plan 明确更新StartedAt且无GPU启动审批，后续采集只认证当前安静状态，不宣称跨重启原容器连续运行。

新 run online_qwen_two_node_r02 使用相同新容器及原129源码，仅新plan env和主控controller dockerexec设置 OPENBLAS/OMP/MKL/NUMEXPR/RAYON线程各1、TOKENIZERS_PARALLELISM=false，均在第三方库 import 前生效。新runtime/handoff/capture/session/tag/out与旧run分开，旧失败不覆盖。新plan的 main_approved/fresh_admission_passed 为false，原预测不迁移；由主 agent 在清理、安静、fresh admission通过后绑定并运行。

针对性9项 CPU检查通过 execution_host_thread_cpu_r01：五进程环境、默认builder、新runtime/session、相同CID/预算/129SHA、pids512、controller pre-import -e参数、fresh approval不推定。没有 GPU、SSH 或远端执行。主 agent 已完成旧原件采集并开始 admission r03；commands.txt列明下一次fresh admission/bind/run唯一新路径。
