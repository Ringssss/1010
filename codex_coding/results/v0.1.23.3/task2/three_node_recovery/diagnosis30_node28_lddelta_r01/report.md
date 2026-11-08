# node28 LD单变量实测增量

父已执行原计划probe_node28_ldpath_r01；原diagnosis30_node28_ldplan_r01保持当时未执行的计划快照，本目录单独记录执行结果。新增目录/opt/dtk/.hyhal/rocm_smi/lib不够让本配置在原总15秒期限内通过初始化。未排除其他ENV原因，不认为永久坏卡或已获唯一根因。

Torch导入1.805004766秒；只有set_device_enter，无exit/BDF/HIPfree。supervisor含回收总15.265080403秒，timed_out=true、rc=-15，child.result未生成。child28/start485301163/PGID28/SID28由精确一次TERM回收，absent、组空；同boot/fullID/StartedAt后置guard在06:25:54UTC通过。源码927f543e...、OMP/MKL1、GPU2/BDF55、selected和总deadline维持原配置，准确argv见原plan launch.json。实际载入的rocm_smi库路径没有采样，不能把库搜索目录设置当成库已换版本的证明。

独立C审查另记录baseline child.log有32B hylog警告，本LD组child.log为0B。可确认本次警告没有复现，而set_device阻断继续；日志变化不说明那条警告是超时的必要/充分原因，open errno与调用位置仍未知。

父下一步按独立新容器hostIPC-only变体推进，恢复原继承LD；这次LD设置仅docker exec进程级，没有改全局环境。新容器必须保持其他限制/挂载/设备/cap以及同probe，并明确容器身份变化是IPC对照所必需的新身份。若也失败，再优先从新own child取得直接线程/系统调用等待证据，不继续无证据叠加cap。

建议的下一项只读观察设计（未实现、未执行）：由新GPU run的独立CPU观察者，在owner注册和set_device_enter均已出现后，对这个run确切PID/start/PGID/SID/argv、boot和PID namespace前后验证，再限量采/proc/PID/task/TID的stat/status/wchan/syscall及本人FD symlink，最多32线程/16FD，两次小快照且总run15秒不延长。不打开FD目标，不读KFD/sysfs vramtbl，不ptrace attach/加cap；permission denied显式记录为空缺。运行前设计好观察者自身deadline与精确CPU回收；原GPU supervisor仍唯一负责child信号。

观测预期：长期R且utime快速累积、syscall为running支持用户态旋转；S且futex/wchan等待支持线程同步等待；ioctl及目标fd或D+wchan支持内核/驱动调用等待。这些只定位等待层，不能据一个symbol认唯一根因。Python堆栈如只停在torch C入口仍不足以区分上述三类。观察者若权限拒绝，不因此直接升级SYS_PTRACE/privileged；父据证据另决定。

本增量仅本地离线读取原件，没有新远程或GPU操作。完整原件SHA、准确owner与失败边界见summary.json；独立运行审查见review_runtime_node28_ldpath_r01。
