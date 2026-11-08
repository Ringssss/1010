# node28 SYS_NICE 单变量配置

独占child hylog明确记录mbind is blocked by seccomp。本配置检验原privateIPC baseline增加SYS_NICE能否改变该等待边界，尚未认定根因或宣称初始化可通过。

准确setup由admission28_setup_r01实际run.argv派生，只替换新container name为isolated_r04-n28并插入--cap-add SYS_NICE；删除新增pair、还原name后与原argv逐token相同。保留privateIPC、1GiB私有shm、8GiB内存、pids512、IPC_LOCK、原cached image、设备、hyhal和r02project bind。未改seccomp profile、privileged、hostIPC、LD或source env.sh。

父执行setup.command.sh并保存原输出，然后inspect新容器取得完整ID/StartedAt，原fresh_container --mode guard可使用node28.spec.json核身份/旧保护/实际HostConfig；切勿使用其--mode setup，因为原setup没有SYS_NICE。父确认CapAdd为IPC_LOCK+SYS_NICE及privateIPC后，仅一次原927f zero payload init、OMP/MKL1、15s总deadline。GPU及copy由父协调，子agent未创建或启动。
