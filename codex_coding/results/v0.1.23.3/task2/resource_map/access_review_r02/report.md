# node29 本机既有控制通道与 SSH 连接只读复核

编号保持 v0.1.23.3/task2。依据 AGENTS、next_step、debug_philosophy、计划18.13和报告28，只读调查 node27 当前用户已有连接，不新建 node29 SSH。首次快照 access_review_r01 为北京时间15:41:51；源码收紧 Unix socket metadata 查询到本地根目录后，第二次快照为15:43:13，前次原始结果保留。第二轮为本报告引用证据。

## 可见事实

| 检查 | 结果 |
| --- | --- |
| node27 当前 UID | 1020 / zhujianian，hostname m03r2n07 |
| 本用户 ssh / sshd / ssh-agent 进程 | 两次均0 |
| node29 三已知 IP 的 TCP 22 | 两次 dport22 均0；15:45:32补充 sport22或dport22双向过滤仍0，覆盖当前 network namespace 的 /proc/net/tcp 和 tcp6 |
| 已配置或已有本用户 SSH Unix socket 候选 | 两次均0，无可复用已认证 master |
| 四目标有效配置 | node29、10.211.3.29、11.211.3.29、12.11.3.29 均 ControlMaster=false、ControlPersist=no，无 ControlPath、ProxyJump、ProxyCommand |
| hostkey 公开指纹 | node29 与12.11.3.29均已有 ed25519 SHA256:YLNQrzxr95ioGBhMsXGjuaE26LBgWHR1DEcc3amO0M8；两管理IP无已有记录 |
| known_hosts | 两次采集前后SHA相同；全部 ssh -G 显式 StrictHostKeyChecking=yes、UpdateHostKeys=no |
| 已存在 D 状态 | 两次均1个，为其他UID1002的 lsof PID3978422；本用户0个；没有读取它的 argv、fd 或文件 |

第二次可读进程数由 snapshot.json 的 process_state_visibility 保存；进程退出竞态与fd可见性错误未删。进程内容仅本UID读取，SSH远端命令/参数payload未记录。TCP表可以含其他用户，但只输出三个目标dport22；其他端口的 ESTABLISHED 不进入SSH连接计数。

## 旁路与 NFS 的证据边界

有限检索 docx、code_building/process_docs/v0.1-init-project 及 lib_cite 的 md/txt，排除 history 与上游源码，命中内容只有“应通过已有会话/控制台调查”的计划性文字，没有明确可调用的控制台、BMC或备用SSH配置路径。有效SSH配置没有跳板或复用配置。本轮没有搜索凭据、读取私钥/secret env/history、尝试控制台API、注入人类终端或调用mux channel。

只读取 /proc/self/mountstats，确认还存在 node29-ib0:/data/node29 → /data/node29 的 NFS4.2 hard、nconnect=4挂载。它与项目本机ext4、模型node28 NFS是不同挂载。两个完整原始段保存在各轮 snapshot.json；age为4678924→4679007，原始字段 lease_expired 为11217→11300；四条 xprt 的 RPC send/receive 及 per-op统计未变化。本机 uname -r 为5.15.0-25-generic；参考上游 Linux v5.15 的 fs/nfs/super.c show_lease，将 lease_expired 定义为租约到期后的秒数，未到期为0；它不是从挂载age开始累计的到期事件次数。本机发行版完整kernel源码未比对，引用为对应上游版本的字段解释。[Linux v5.15字段来源](https://raw.githubusercontent.com/torvalds/linux/v5.15/fs/nfs/super.c)

这些被动字段提供客户端NFS租约状态线索；空闲client可能没有续租需求，RPC计数未变化不能区分没有需求与无法取得进展，不能仅凭它们断定NFS失联、确定服务器故障机制或解释本机HIP。没有访问/stat该NFS挂载目录、使用lsof遍历文件系统或新建NFS请求；D态lsof没有wchan/stack/文件绑定，不能确定由该NFS引起，也不能据它解释本机HIP问题。

## MaxStartups 能证明到哪里

旧 connectivity_cross_alt_r01 中11.211.3.29:22确实返回一次 Exceeded MaxStartups。OpenSSH官方说明把 MaxStartups 定义为并发未认证连接的准入上限，并允许概率性提前拒绝；它与 MaxSessions 的已认证连接内channel数不同。[官方说明](https://man.openbsd.org/sshd_config#MaxStartups)

因此该响应支持一次SSH未认证准入拒绝，提示后续需要远端实际阈值、未认证进程/连接计数、来源、日志和主机负载。尚无加密远端身份，不能确定其实际OpenSSH版本、策略、当前计数、来源或唯一机制。当前两次本机TCP22为0，仅说明采样时没有可见node27残留SSH连接；不能证明此前探针绝无影响，不能排除其他namespace/来源或服务器端尚未回收的未认证状态。HTTP keeper无响应和HIP初始化故障的因果未因此建立。

## 交付与验证

源码：codex_coding/src/v0.1.23.3/task2/resource_map/access_review/collect_local.py；两轮各有 snapshot.json、manifest.json 和执行源码副本，首轮不覆盖。命令为 python3 collect_local.py --output 对应新结果目录；只有ssh -G和ssh-keygen -F本地解析，没有SSH登录或网络探针。15:45:32双向纯/proc补证保存在 tcp22_bidirectional.json，最终collector也支持该双向过滤；本轮执行源码副本保持原SHA。已验证源码语法、执行副本SHA、snapshot SHA、known_hosts前后不变和动作计数全0。没有GPU、kill/signal、XML/系统配置修改，旧两node29 exact pending状态不升级。

当前缺口是没有已登录node29会话/可验证mux，也没有明确可调用管理控制台路径；本任务没有取得node29实际sshd/系统/keeper现场证据。
