# node28两份线程快照与mbind直接日志增量

主线程43/start485348862在set_device后约2.000/6.000秒的两份exact样本均为R/wchan0；utime 327→662、stime 57→121，约3.999951秒间隔增加399 ticks。RSS 207671→327597页，增加119,926页；vsize增162,246,656B。节点CLK_TCK和PAGESIZE本次没有采，因此保留原单位；若分别为100Hz/4KiB，约3.99 CPU秒及468.46MiB驻留内存增量。RSS是进程DRAM页观察，不是HBM free、context成功或有用工作进度。证据支持持续消耗CPU/内存，也允许初始化工作、重试或旋转；尚不能判无进展死锁。

辅助线程44为S、wchan=kfd_wait_on_events，这不是主线程同样卡在KFD的证明。两份所有syscall读取均EPERM，所以没有系统调用号/参数/errno。FD样本已见kfd、hsakmt_shared_mem、8个render设备和FD6自有hylog；说明该child已实际打开这些对象，不能从open成功推导全部ioctl或运行成功。两个sample前后owner完整一致；supervisor15秒仍失败，但TERM exact回收、组空、afterguard通过。父后置单独核observer31与child43 /procabsent，原件见postcheck。

父从FD6明确路径`/var/log/hylog/root_python3.10_20261009_143318.930369_43.log`取到本child的库日志：`mbind is blocked by seccomp`，位置为vendor ROCT-Thunk-Interface_6.3_docker/src/fmm.c:1831。它比无所属的目录open警告更有针对性，是本child初始化路径上的库错误自报告；实际mbind syscallerrno与有效seccomp规则尚未直接抓到。

[Docker官方文档](https://docs.docker.com/engine/security/seccomp/)说明默认filter按allowlist处理，并列mbind与CAP_SYS_NICE相关。[Moby当前上游profile](https://github.com/moby/profiles/blob/main/seccomp/default.json)对mbind及相关NUMA策略调用的ALLOW组设置CAP_SYS_NICE条件；[profile构建实现](https://github.com/moby/profiles/blob/main/seccomp/seccomp_linux.go)从OCI bounding capabilities筛选该条件。这是官方机制证据，当前main不等于node28安装的准确Docker版本/profile。增加SYS_NICE在常见该实现下既改变cap，也会改变默认生成filter中相应允许项；单个cap配置对照成功可证明该配置恢复运行，仍不能独自分离内核cap权限与filter规则的贡献。

父已暂停60秒源码/计划，我尚未生成该copy或命令。下一项为A实现SYS_NICE-only自有新容器：原IPC_LOCK保留、只新增SYS_NICE，privateIPC/原LD/thread1/GPU2/probe927f/15秒和其他限制保持；不unconfined/privileged。父/C核新身份、CapEff/CapBnd和Seccomp保持启用、self-owned日志及init结果。若错误不再出现且15秒init通过，支持该配置是可用修复；若错误不再出现但仍超时，只说明报错路径改变且不足恢复，再结合当前CPU/内存证据决定一次60秒初始化诊断。若同错误仍出现，先核cap与有效filter事实，不继续猜其他cap。

没有新远程或GPU操作，旧计划/源码/失败原件不改；本目录为独立离线解读与下一项实验的判别预期，原件SHA和官方链接见summary.json。
