# 节点连通性单轮只读采样

来源：node27 / m03r2n07；UTC 2026-10-08T07:15:32.839015+00:00。

| 节点 | 地址 | ICMP | TCP22/banner | SSH只读身份 | 实际路由 |
|---|---|---|---|---|---|
| node27 | 10.211.3.27 | reply | ssh_banner | remote_exec_ok | `[{"type":"local","dst":"10.211.3.27","dev":"lo","prefsrc":"10.211.3.27","flags":[],"uid":1020,"cache":["local"]}]` |
| node27 | 12.11.3.27 | reply | ssh_banner | remote_exec_ok | `[{"type":"local","dst":"12.11.3.27","dev":"lo","prefsrc":"12.11.3.27","flags":[],"uid":1020,"cache":["local"]}]` |
| node28 | 10.211.3.28 | reply | ssh_banner | hostkey_rejected | `[{"dst":"10.211.3.28","dev":"eth0","prefsrc":"10.211.3.27","flags":[],"uid":1020,"cache":[]}]` |
| node28 | 12.11.3.28 | reply | ssh_banner | remote_exec_ok | `[{"dst":"12.11.3.28","dev":"ib0","prefsrc":"12.11.3.27","flags":[],"uid":1020,"cache":[]}]` |
| node29 | 10.211.3.29 | no_reply | connect_error | connect_timeout | `[{"dst":"10.211.3.29","dev":"eth0","prefsrc":"10.211.3.27","flags":[],"uid":1020,"cache":[]}]` |
| node29 | 12.11.3.29 | reply | banner_timeout | banner_timeout | `[{"dst":"12.11.3.29","dev":"ib0","prefsrc":"12.11.3.27","flags":[],"uid":1020,"cache":[]}]` |
| node30 | 10.211.3.30 | reply | ssh_banner | hostkey_rejected | `[{"dst":"10.211.3.30","dev":"eth0","prefsrc":"10.211.3.27","flags":[],"uid":1020,"cache":[]}]` |
| node30 | 12.11.3.30 | reply | ssh_banner | remote_exec_ok | `[{"dst":"12.11.3.30","dev":"ib0","prefsrc":"12.11.3.27","flags":[],"uid":1020,"cache":[]}]` |

| Keeper | HTTP inventory | 耗时(s) |
|---|---|---|
| node27:33000 | http_ok | 0.012 |
| node27:33001 | http_ok | 0.015 |
| node29:33000 | http_failed | 4.014 |
| node29:33001 | http_failed | 4.010 |

每目标ICMP一包/2s，TCP+banner共3s，SSH连接5s/外层8s，keeper inventory总4s；每项一次。
SSH严格hostkey且禁用UpdateHostKeys；known_hosts前后SHA保持：True。
节点28/30连通不代表GPU资源可使用；ICMP或TCP握手成功不证明远端用户态/容器/keeper可执行。
该采样没有建立HIP初始化故障的因果，也没有证明node29旧探针退出或保护对象完整。
