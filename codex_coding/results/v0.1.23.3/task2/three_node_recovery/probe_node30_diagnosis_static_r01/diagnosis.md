# node30 fresh 初始化静态诊断

Torch 2.10.0/HIP 6.3.26113 导入约2.096秒完成，随后停在 `set_device_enter`；15秒外部deadline后TERM，own PID13/start477410700/PGID13退出与组回收通过。没有实际BDF、HIPfree、payload或communicator，不把本次失败叫XML错误或硬件永久故障。copy/NCCL停止。

fresh实际已提供kfd/mkfd/dri/infiniband、hyhal只读、hostnetwork、无限memlock、core0，不能称简单漏掉这些设备/mount。历史成功isolated launcher同样非privileged、hyhal只读，并用hostIPC+SYS_NICE；fresh是privateIPC+IPC_LOCK、shm1GiB、memory8GiB、pids512。历史launch还先source /opt/dtk/env.sh，而fresh native Python继承image ENV。这些是待解释配置差异，当前没有单变量runtime证据排序其因果。旧vendor容器常privileged不能反推fresh必须privileged；不改共享权限/driver。

`Could not open /var/log/hylog/.`只直接证明一次日志路径open失败，当前未取得其errno/目录存在性/UID权限/实际调用点；不认唯一根因。fresh仅bind项目和hyhal，旧容器不同home/model挂载与此单卡零payload没有已证明关系。same-image既有容器列表只认证镜像/配置，已有长寿命GPU进程不等于同配置fresh初始化已通过。

下一步仅一次CPU-only只读取证：在同fullID/StartedAt/boot容器中运行runtime_metadata.py，8秒内部alarm/外部10秒；读effective whitelist ENV、静态env.sh相关赋值（不source）、UID/groups、CapEff/CapBnd/Seccomp/NoNewPrivs、limits、设备stat major/minor/mode/os.access、hylog目录存在性与mountinfo。host补inspect User/GroupAdd/SecurityOpt/CapAdd/ReadonlyRootfs、cgroup memory.events/pids.current。不导入Torch、不loadHIP、不openGPU设备、不SMI，不建新容器/改权限/加cap或延长GPUdeadline。父统一stage源码SHA、执行与collect，分析agent没有发起远端操作。

原始事实、同镜像配置及source SHA见diagnosis.json。当前只做原因范围收窄，不给新的GPU实验或容器重建建议。
