# node27 本机初始化与跨节点依赖边界

编号保持 `v0.1.23.3/task2`。本轮仅有界读取 mount、路径 metadata、64B 模型配置、Docker metadata/archive 与 ELF dynamic section；没有 HIP/dlopen、SMI、模型权重、GPU payload、communicator、engine health、网络探测或服务改动。网络结论引用兄弟任务，不重复扫描。

结论：当前明确的远端异常是 node29；未发现另一台 node28/node30 失联。node29 两个 rank 没有加入原四 rank Gloo rendezvous，可以解释原 `2/4 clients joined` 失败；现有证据不能把它与 node27 单机库加载/hipInit 失败归为同一原因。已确认模型 NFS 来自 node28，当前 metadata/64B 小读成功；本机 HIP 诊断没有加载模型。

| 依赖 | 本轮实际路径及底层 | 证据与作用 |
| --- | --- | --- |
| 项目和日志 | `/home/zhujianian/.../fluidckpt_zjn`；host/container 都为 `/dev/nvme0n1p1` ext4 | 不是远端项目 NFS |
| Qwen 模型 | host `/data/node28` NFS4.2，`node28-ib0:/data/node28`，addr `12.11.3.28`；container 为同 server 的 `/data/node28/shared/zhujianian` ro bind | stat 成功；`config.json` 前64B约6.7ms读成功，不能扩展为全部权重读取验收 |
| 容器 DTK/HIP | `/opt/dtk` 属 Docker root overlay，overlay2 存储位于本机 `/dev/nvme1n1p3` ext4 | 容器 mountinfo 没有 `/opt/dtk` 的远端覆盖；实际 HIP symlink/ELF 由只读 Docker archive 补证 |
| 驱动用户库 | container `/opt/hyhal` ro bind；mountinfo root `/usr/local/hyhal`，底层 `/dev/nvme1n1p3` ext4 | host `/opt/hyhal` realpath 同 `/usr/local/hyhal`，不是 node29/NFS 路径 |
| GPU 内核接口 | `/dev/kfd`、`/dev/dri/renderD128` 为本机 devtmpfs | 只 stat，未打开或初始化 GPU |
| 跨节点控制 | `ssh node29` 经主机解析到 `10.211.3.29`，route `eth0/src10.211.3.27` | 旧 run cleanup 的 banner timeout 明确使用此地址 |
| 跨节点数据 | rendezvous `tcp://12.11.3.27:34211`；node29 数据地址 `12.11.3.29` 经 `ib0` | 控制地址与数据地址分开，不能以一条 ICMP 推定 SSH/Gloo可用 |

host collector `rc=0`，约0.41s完成。唯一正式 container collector 在约15:16启动失败 `rc=126`：`OCI runtime exec failed ... error writing config to pipe ... broken pipe`；`container.json` 保存原始错误文本，**它不是 JSON**。这是本次取证执行入口故障，没有运行 collector Python 或 HIP，不能归因为 node29 网络或 HIP 库。没有重试 container exec；后续只用已运行容器 PID561107 的 mountinfo、Docker inspect/cp archive 静态补证。`/proc/561107/root/...` stat 权限不足原样记录，不提权绕过。

静态 HIP 链已明确：`/opt/dtk/hip/lib/libamdhip64.so → libamdhip64.so.4 → libgalaxyhip.so → libgalaxyhip.so.5 → libgalaxyhip.so.5.2.26113.2065-6699a1f0`；`/opt/dtk/lib/libgalaxyhip.so.5 → ../hip/lib/` 同一 ELF。image 的 LD_LIBRARY_PATH 与原诊断读取的 env.sh 均列本机 DTK/hyhal路径。copy出的12,349,016B ELF仅用于 readelf，SONAME 为 `libgalaxyhip.so.5`，RUNPATH 为 `/opt/dtk/lib:/opt/dtk/lib64:/opt/hyhal/lib:`；DT_NEEDED 是 pthread/rt/numa/dl/stdc++/m/gcc_s/c/ld-linux 系统库，未出现远端文件路径。静态检查不能覆盖库 constructor、内部 dlopen、运行时驱动交互或配置的全部行为；未建立 HIP 故障根因。

原跨节点失败证据来自 `xml_startup_owned_r02`：rank0/1属于node27，rank2/3属于node29，四个 launch reply已存在；node27 rank0报 `2/4`，rank1日志明确连接同一 node27 store并超时。缺席的是node29的两个参与者，但远端日志/新身份不可取得，不能进一步断言两进程已经退出、卡在何处，旧exact cleanup仍pending。网络任务 `connectivity_nodes_r01` 确认27/28/30数据SSH通，29管理不可达、数据TCP22握手但无banner、两keeperHTTP超时；`connectivity_cross_alt_r01` 确认28来源观察一致，备用11.211.3.29的SSH端口明确返回 `Exceeded MaxStartups`。它支持node29控制服务异常，尚未确认可用于恢复控制的身份。

原单机 `local_xml_diagnosis_r01` 子进程只执行 ctypes库查找/加载与 HIP metadata，不访问 node29 RPC、模型路径或 communicator：all-visible库加载返回后hipInit10s报警；0/1-visible在库加载范围内SIGSEGV且未到显式hipInit。没有单机远端等待堆栈或同因证据；继续保持两条故障链，GPU运行停止状态不变。

原始文件为 `host.json`、`container.json`（failed raw）、`static_fallback.json`、`container.ld.so.cache`、`libgalaxyhip.static_copy.so`。对更深层 `/var/lib/docker/overlay2` 的 findmnt 返回1且输出为空，保留在 `docker_backing_fs.txt`；改读可查询父目录 `/var/lib/docker` 返回0，ext4结果单列在 `docker_backing_fs_r02.txt`，没有覆盖原失败。`manifest.json`记录命令/退出/哈希与旧证据引用。没有正式文档、XML、网络、镜像配置或既有run改写。
