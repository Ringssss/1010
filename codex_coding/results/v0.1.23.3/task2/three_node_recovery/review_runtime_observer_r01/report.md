# 精确 observer 原件独立验收

13项原件一致性检查通过。两快照与对应JSON原件相等，target owner完全等GPUchild43/start485348862，采集前后PID/start/argv/PGID/SID/PID namespace匹配；实际set_device_enter后2.000272173/6.000222661s。原observer源码SHA等计划审过的f9a596f0版本；无signal、ptrace、GPU或device-open调用。

主线程43两次均R、wchan0，utime327→662 ticks、stime57→121 ticks，RSS207671→327597页；辅助线程44为S/kfd_wait_on_events且CPU ticks0。syscall四次均EPERM(errno1)，实际syscall号和参数未知。上述数据只显示采样期间CPU和内存继续推进，不直接证明spin、deadlock或正常初始化。

该child FD6的hylog路径等父准确fetch命令路径，原件写出 `mbind is blocked by seccomp`。这是ROCT库自己的诊断，不是本次直接测到的mbind errno/filter规则，支持针对权限因素继续核对。PID后置stderr确认observer31和GPUchild43均absent；observer约7.904s自然返回，原GPUchild仍在15s deadline处精确TERM退出。baseline必要第二次复现结果依旧failed，原失败保持。

同run前后保护已在review_runtime_node28_observed_r01独立21项通过：旧两容器、13进程、六catalog保持。完整权重内容未重读，copy/XML/性能尚未通过。
