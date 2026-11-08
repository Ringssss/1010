# node30初始化调查：当前写入阻断与静态环境差异

2026-10-09T06:19:41.626399+00:00一次stdin只读采样，同host boot、完整容器ID/StartedAt/image/task label及原PID namespace连续。根盘、/tmp与DockerRootDir=/var/lib/docker的available均0B，root保留块在内的free为5,951,717,376B，仍有27,659,612个inode。父本轮guard此前已实际mktemp ENOSPC；当前停止GPU，不以os.access可写代替实际有可分配空间。

自有isolated容器SizeRw=19,768,387B（约18.85MiB），SizeRootFs=34,601,239,103B。它们是逻辑大小，不含宿主日志/bindmount，也不是物理块归属；本次没有扫描其他服务或归因满盘来源。另一本地/data/node30可用1,793,051,893,760B（约1.631TiB）；shared父目录os.access W=true、zhujianian子目录不存在，未做mkdir/写入验证。当前容器没有该盘挂载，项目bind仍指向根盘/tmp，不能只改宿主bootstrap到另一盘就声称容器结果可写。

完整/opt/dtk/env.sh SHA=033843c698cb207a828a93545181c811860c3495870b94a9503a6f60b4d61b79与旧metadata一致，未source。脚本rocm_smi条件为“不存在cmake目录，或两so同时存在”；本次cmake目录和两so均存在，所以静态条件预测true。HYHAL fallback与hy-smi fallback静态条件false。source预计在现有LD_LIBRARY_PATH中增加/opt/dtk/.hyhal/rocm_smi/lib，同时也改PATH、DTKROOT、AMDGPU_TARGETS、MIOPEN_FIND_MODE和编译路径；静态预测不是已执行环境delta。

存储条件恢复后先跑同日原配置control，不能把隔一天的成功直接归因变体。保持同容器/GPU0/BDF/selected visibility、总15sdeadline、core0、零payload、OMP_NUM_THREADS=1/MKL_NUM_THREADS=1。现run_gate默认host_gate只传PYTHONDONTWRITEBYTECODE，而原r01 native命令还显式两线程变量；对照须保留线程env，以免加入另一变化。

如control仍停set_device，再只改一个LD_LIBRARY_PATH变量：保留原entry顺序，在/opt/dtk/opencl/lib后插入/opt/dtk/.hyhal/rocm_smi/lib；不source全脚本、不改IPC/cap/memory/seccomp。预期：若这一库搜索差异充分解释失败，variant应在相同15s内出现set_device_exit、BDF0000:09:00.0和实际HIPfree、通过init并exact回收；若仍同阶段超时，该增量不足以恢复，不继续copy/XML。若control先成功，本轮仅证明原配置当前可用，无需启动变体。control失败+variant成功仍需必要一次原配置回退确认以区分时间/驱动状态变化，且每配置通常一次、必要control第二次。

保留旧失败、冻结probe/source和生产XML；所有GPU仍由父独立准入/执行。deferred_commands.json为未执行argv，不能直接用当前根盘状态运行。collector的本地自然退出/精确timeout回收/缺路径显式记录CPU3项通过；远端6项CPU命令全部自然退出、signal0，无device open/library load/Torch/SMI，无远端文件写入。未知：原15s超时唯一原因、历史hylog open errno、根盘满归属、容器ioctl权限、另一盘实际写入及未来GPU运行。
