# node28 精确 child 只读 observer 计划窄审

源码 `diagnosis30/observe_node28_init.py` SHA `f9a596f07e8d0aba9d42be10e8ae100d2581be02960b9de9999af0334ba70278` 通过独立阅读；没有修订阻断。仅接受新 `probe_node28_observed_r01`、private容器e693完整ID/StartedAt、固定boot/PID namespace及含GPU2/selected/BDF55的完整child argv。两次快照在set_device_enter后计划2/6秒，对leader在读取前后核PID/start/argv/PGID/SID/namespace；身份变化不接受样本。

每次至多32个TID、16个FD symlink，仅读proc stat/status/wchan/syscall，不打开GPU/KFD设备、不ptrace、不signal；permission或退出错误保留具体error而非0。FD目标只是readlink路径，不读取所指设备。源码20秒是软件循环期限，外层有界执行另负责observer退出取证；不能把loop deadline描述为单个proc读取的硬中断。GPUchild仍由原15秒supervisor负责精确回收。

native observed命令与首次baseline逐项相等，仅新输出目录变化；OMP/MKL1、原LD继承、源码927f、e693容器/GPU/BDF/visibility/deadline保持。这是baseline必要的第二次复现并附只读等待取证，不是新增参数扫描。thread/wchan/syscall只能定位所观察等待/运行状态，不能凭单个名字认定唯一驱动机制根因。父统一启动、取回原件与前后保护，审核agent没有远程或GPU授权。
