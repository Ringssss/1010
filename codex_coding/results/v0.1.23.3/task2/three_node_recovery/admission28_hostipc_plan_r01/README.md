# node28 host IPC 单变量配置

从父已成功的admission28_setup_r01/run.argv派生，仅两个argv token变化：container name isolated_r02→isolated_r03；IPC private→host。GPU2、image、8GiB memory、pids512、IPC_LOCK、设备、hyhal、host network和所有其他参数逐token相同。保留--shm-size 1g参数；在hostIPC语义下使用宿主共享IPC namespace，而不是原私有1GiB tmpfs。没有SYS_NICE、privileged、seccomp、source env.sh或LD路径变化。fresh init仍须OMP/MKL各1，由父另外启动。

复用原r02自有project bind和冻结927f源，probe采用新唯一run目录并串行。setup.command.sh仅native docker run，没有GPU启动。父保存stdout/stderr/status后，docker inspect新name取得完整ID/StartedAt；使用本目录node28.spec.json和原fresh_container --mode guard即可，必须提供新identity。不可把此spec交原fresh_container --mode setup，因为原setup硬编码privateIPC。原private容器/旧keeper保持，hostIPC不改变宿主全局环境或原XML。

setup.argv.json是逐token diff；如果Docker拒绝hostIPC与shm-size组合，保留失败原件，由父决定新run显式省去无效shm参数，不能静默fallback。子agent未执行创建、GPU或新的远端调用。
