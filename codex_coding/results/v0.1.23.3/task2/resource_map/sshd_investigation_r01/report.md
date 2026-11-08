# node29 SSH 准入只读调查

保持 v0.1.23.3/task2。2026-10-08 北京时间15:42:08，node27/m03r2n07 发起两次严格串行 SSH；这轮仅新增两条 SSH 连接，没有 TCP/banner 前探、keeper 重复轮或 node28 交叉重试。原 connectivity、resume、pending 和冻结地图均未修改。

| 目标 | 路由/源 | 单次耗时 | 返回值 | 精确失败阶段 |
|---|---|---:|---:|---|
| 11.211.3.29:22 | eth1 / 11.211.3.27 | 0.007532s | 255 | TCP已建立；读到 `Exceeded MaxStartups` 后远端关闭，未收到SSH协议版本 |
| 12.11.3.29:22 | ib0 / 12.11.3.27 | 0.007571s | 255 | 同上 |

准确stderr均含 `kex_exchange_identification: banner line 0: Exceeded MaxStartups`。stdout均为空；没有 `Authenticated to`，主机公钥交换尚未完成，不能声称新加密身份或主机/容器连续性通过。采集器每次 ConnectTimeout=6s、ConnectionAttempts=1、总deadline=15s，第一客户端结束后才启动第二个；两次均快速由远端关闭，没有外层timeout或对其他进程发送signal。

旧 known_hosts 中 node29 已信任的 ED25519 公钥被复制为本次结果目录的只读临时 known_hosts，强制 HostKeyAlias=node29、HostKeyAlgorithms=ssh-ed25519、StrictHostKeyChecking=yes、UpdateHostKeys=no；实际成功握手时必须比对该旧公钥。没有接受未知公钥，也没有改原信任库；两份原 known_hosts 前后 SHA 均相同。hostkey_provenance.json保存旧条目查询与临时副本SHA。此次因更早拒绝，实际服务公钥未获得。

较15:18旧轮，当前数据地址由“TCP通但没有banner”变成了同样明确的准入拒绝；备用管理地址的拒绝再次出现。两地址的新观察都支持当前SSH准入受到限制，不能据此宣布所有地址持续处于同一状态，更不能把HTTP和本机HIP故障归为同一根因。

## 源码机制边界与待取证项

[OpenSSH手册](https://man.openbsd.org/sshd_config#MaxStartups)规定MaxStartups限制并发未认证连接，可在全满之前概率拒绝；PerSourceMaxStartups还能额外限制每来源的未认证连接。这些是机制定义，node29实际配置和阈值仍未取得。

[官方OpenSSH Portable 8.9p1源码](https://github.com/openssh/openssh-portable/blob/V_8_9_P1/sshd.c#L769)中drop_connection使用全局策略与每来源策略的合取准入条件；任一拒绝都能返回同一条消息。该检查发生在给新连接fork子进程之前。源码参考版本已明确，尚未读取node29部署版本；本地client的8.9p1版本不是远端server版本证据。因此不能由这个文本反推全局硬阈值已满，也不能把它当作该次fork失败消息。

| 区分问题 | 服务器现场需要的证据 | 本轮取得 |
|---|---|---|
| 全局MaxStartups与每来源限制 | 运行listener实际配置来源、对应全局/来源计数、drop日志来源与时间 | 无 |
| 正常短期未认证高峰与长期积压 | preauth子进程年龄、认证/超时日志、LoginGraceTime和连接来源分布；已建立socket不全等于未认证 | 无 |
| 资源耗尽导致旧连接停滞 | sshd fork/exec/pipe错误、进程/线程及cgroup pids限制与当前值、内存/commit/PSI、OOM/内核日志，与积压同时取样 | 无 |
| 主机调度/IO异常导致SSH和keeper共同迟滞 | loadavg/PSI、进程状态与等待位置、keeper只读inventory、相关时间日志 | 无 |
| 是否换机/重启/容器重启，旧PID是否仍属原run | 完成旧hostkey验证后核hostname、boot_id、container ID/StartedAt与exact PID/starttime | 无 |

fork失败和资源耗尽可以与已存在的准入拒绝并存；这轮没有服务器资源数据，不能选择其中一个作为根因。日志中没有相关错误也不等于已排除：权限、日志保留和取样窗口均须记录。两条网口来源不同，但来自同一node27；未知PerSourceNetBlockSize下仍不能由两次拒绝排除每来源策略。node28旧对照已经区分了主路径故障的单源假说，本轮没有新问题要求重复大矩阵。

server_readonly.py已准备：只有hostname=m03r2n09、boot_id=a5bc9009-f911-406d-aa56-30a8d08f01f5、容器7e9daa477513074bae2dda469618899705ccbf21da39e99b76ec4be25e48a402和StartedAt=2026-10-06T12:39:02.990558427Z均匹配才继续限定只读资源、配置白名单、sshd进程、22端口连接、日志各最多80行和两个keeper inventory。它没有执行，配置文件白名单也不等价于listener effective配置。没有sudo、sshd -T、私钥/口令读取、GPU/HIP/SMI、重启、网络/sshd/XML变更或旧PID signal；旧21883/21901保持pending。

## 验证与复现记录

源码语法检查通过；verify.py核每个初始manifest SHA、恰好两目标各一次、客户端完全串行、strict/keyalias/连接及总限时、零远端输出/认证、原信任库保持和两个精确拒绝。原始argv、开始结束时间、路由、返回值、stdout/stderr和remote源码SHA保存在ssh_11_211_3_29.json与ssh_12_11_3_29.json；manifest冻结本轮采集的源码副本和结果，report与validation是随后派生说明，不改原观察。

本轮已执行命令（已有输出不可重用，不自动重试）：

```bash
python3 -m py_compile codex_coding/src/v0.1.23.3/task2/resource_map/sshd_investigation/collect.py codex_coding/src/v0.1.23.3/task2/resource_map/sshd_investigation/server_readonly.py
python3 codex_coding/src/v0.1.23.3/task2/resource_map/sshd_investigation/collect.py --out codex_coding/results/v0.1.23.3/task2/resource_map/sshd_investigation_r01
python3 codex_coding/src/v0.1.23.3/task2/resource_map/sshd_investigation/verify.py --out codex_coding/results/v0.1.23.3/task2/resource_map/sshd_investigation_r01
```

停止点是已有会话或控制台能提供server现场；继续无身份的盲重试不能补负载、阈值或旧PID证据。

## 已有会话/控制台独立入口（已准备，未在node29运行）

独立console_readonly.py无需SSH：在node29已有会话或控制台运行下面一条命令，结果目录必须尚不存在。源码只在node27通过语法检查及三个合成CPU检查，console_cpu_validation.json保存检查时间、argv和源码SHA；没有在node27运行服务器路径或把它当node29现场。

```bash
python3 /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/resource_map/sshd_investigation/console_readonly.py --out /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/resource_map/sshd_investigation_console_r01
```

入口严格要求hostname=m03r2n09；boot/container变化仍保留真实identity_guard，不读取陈旧PID、不作owner/退出断言，通用主机/sshd调查照常执行。仅原容器身份通过才读取keeper本地inventory。所有文件新建、逐项时间/argv/返回码/限量stdout/stderr持久、最后manifest记录SHA；命令共享25sdeadline、单条最多2s（curl外层2.2s）、stdout最多65536字符，logs两类各最多80行，sshd成员细查最多64个。读取sshd相关status/limits/cgroup，以及所在cgroup的pids/memory当前值/上限/事件；socket数量/状态/来源端点和地址另汇总，未认证计数明确null，不能用已认证SSH socket数替代。

不sudo、不导出环境、不读取私钥或口令；日志/进程/配置/Docker权限不足原样保存，不能解读为0连接/0积压/没OOM。已装openssh-server包版本独立采集，包版本与live listener binary不完全等价。配置保存固定目录白名单与Include/Match上下文，以及可读listener的安全-f/-o/-p/-g选项；live effective配置仍明确未核实。[官方8.9p1代码](https://github.com/openssh/openssh-portable/blob/V_8_9_P1/sshd.c#L1714)显示sshd -T在输出之前加载host私钥，故本任务未执行。不会用静态文件或手册默认值伪装为live effective；非标准Include及-f目标只报告，不任意读取。全部数据仍需要真正node29入口执行后才成立。

**主审后的计数修订：**console入口的ps/ss失败、超时或跳过不再把空stdout汇总为0；visible_processes/socket_count和完整状态/来源计数为null，collection_status与returncode保留。只有returncode=0且stdout未截断才给完整输出计数；其他情况仅有非空证据时单列partial_count/partial_states/partial_peer，不升级完整性。collection complete仍不意味着非root能看见所有进程。新增失败/超时/跳过负例与截断部分计数检查，最终5项合成CPU通过，console_cpu_validation_r02.json保存新源码SHA=9cec3de025dd9abf6cf80b136ee5ab27b123795e3200dbcec843caa399fbcc32。旧CPU记录与SSH原采集/worker/manifest均保留，没有新的server运行。
