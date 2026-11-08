# node29 定向交叉与备用管理链路补证

2026-10-08 15:18:43–15:19:01北京时间。依据第一轮node28数据SSH成功身份，父授权从node28仅一轮交叉探测node29两已知地址、两个keeper；依据node27旧STALE邻居记录，仅补一轮11.211.3.29。其他备用IP未探测。

| 来源 | 目标 | ICMP | TCP22/banner | SSH或keeper | 实际出接口/源 |
|---|---|---|---|---|---|
| node28 | 10.211.3.29 | 2s无回复 | Errno113 No route to host | 未重复SSH | eth0 / 10.211.3.28 |
| node28 | 12.11.3.29 | 回复0.080ms | TCP握手、3s无banner | 未重复SSH | ib0 / 12.11.3.28 |
| node28 | 12.11.3.29:33000 | — | — | inventory 4s超时/0byte | ib0 / 12.11.3.28 |
| node28 | 12.11.3.29:33001 | — | — | inventory 4s超时/0byte | ib0 / 12.11.3.28 |
| node27 | 11.211.3.29 | 回复0.156ms | TCP已握手，返回`Exceeded MaxStartups\r\n` | SSH握手后5s banner timeout | eth1 / 11.211.3.27 |

node28执行前验证hostname=m03r2n08、boot_id=5313e6f8-1712-45dc-9855-4c47358b6c4b与第一轮相同。node28源只读Python经一次SSH stdin执行，没有远端落地文件。peer_ssh.json保存整个SSH命令、返回值、stdout/stderr与sourceSHA，node28_cross.json保存六项细分证据与本地路由/邻居。

结论：node29管理主链路异常及数据服务无响应从27/28两个来源一致复现，不能归为node27单源网络故障。备用11.211.3.29仍IP可达，并存在一次明确SSH MaxStartups拒绝；这是端口响应文本，尚未完成加密hostkey/远端身份验证。随后SSH仍无banner。不能据此证明机器重启、node29容器/旧PID状态，或把MaxStartups作为所有HTTP/HIP问题唯一原因。

本探测中node29同host连接完全串行，TCP socket先close，再SSH；没有自己堆并发SSH。每项一次，无重试，不修改sshd/hostkey/网络，不调用GPU/SMI/HIP/模型或signal。所有原始结果与first-round保持，known_hosts前后SHA相同。验证源码语法、初始manifest各SHA、六项交叉+三项备用观察、每项实际路由、零GPU/signals和源身份均通过。

第一轮节点全表与解释：../connectivity_nodes_r01/report.md 和 interpretation.md。28/30数据SSH成功不构成GPU准入；当前故障因果仍需29主机现场/服务日志，不能以网络观测替代。
