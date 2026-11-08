# node30 初始化后只读元数据解读

当前未发现可直接解释15秒set_device超时的简单“缺设备节点、缺核心库路径、hylog目录不存在或当前DAC不可写”证据；根因保持unknown，copy/NCCL停止。此次只读采集没有source env.sh、导入Torch/HIP、openGPU设备或新GPU运行。

有效PATH已含DTK/LLVM/HIP/hipify/hyhal，LD_LIBRARY_PATH已含DTK dcc/gcvm、hip、llvm、lib/lib64、hyhal lib/lib64、dushmem、opencl及RCCL/plugin，ROCM_PATH=/opt/dtk。它与旧node27已成功容器顶层白名单的核心路径一致，因此不能把“native没source env.sh”直接改写为“缺DTK环境”。旧长寿命worker进程环境另有 `/opt/dtk/.hyhal/rocm_smi/lib`、`/usr/local/lib`、`/usr/local/lib64`及重复前缀；这是真实进程环境差异，其必要性和对本次HIP初始化的影响没有证明。静态env.sh中rocm_smi兼容路径在条件分支内，采集未取得全部分支文件存在性，未执行脚本，不能宣称source后实际delta已测得。

metadata采样UID/GID/groups为root/0；kfd/mkfd以及renderD128–135为字符设备、mode0666，当前os.access读写均true；四类设备配置没有简单遗漏。os.access认证DAC/当前metadata范围，不认证真正device-open、ioctl、device-cgroup或seccomp允许。

`/var/log/hylog`在后置采样实际存在，root:root、0777、当前read/write/execute均true；`/var/log`也可写。故此次证据不支持当前目录缺失或简单DAC拒绝，但这是初始化失败后的采样，未取得当时open的errno/flags/调用点，不能排除时序变化或其他open失败机制，也不能用日志警告当唯一根因。`/opt/hyhal`是本机ext4只读bind，历史成功isolated同样只读，不据此建议改为可写。

当前Seccomp=2/filter1、NoNewPrivs=0，CapEff/Bnd=0xa80465fb；有IPC_LOCK，缺SYS_NICE。历史成功isolated launcher提供SYS_NICE+hostIPC，本fresh为IPC_LOCK+privateIPC/shm1GiB。capability、seccomp、IPC仍是未做单变量验证的配置候选，没有拒绝syscall或失败errno证据，不宣称必须privileged/unconfined/hostIPC。

core=0、memlock及nofile=1048576已确认。后置guard同fullID/StartedAt/boot连续，OOMKilled=false，memory.events的max/oom/oom_kill均0、pids.current=1/max512；不支持本次触发容器OOM或留下探针进程。该后置状态不排除其他瞬时资源或驱动运行条件。所选GPU实际HIP BDF/free仍未获取，sysfs候选余量不冒作HIP free。

原始metadata源文件来自父此次实际部署冻结版本，不能把后续本地collector白名单修订当成本次执行源码。证据路径与SHA见diagnosis.json。本轮收口于只读差异解释，不追加GPU、source、容器配置修改或泛化实现。
