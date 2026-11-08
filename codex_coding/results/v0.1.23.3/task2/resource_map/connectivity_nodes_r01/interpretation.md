# 单轮连通性证据解释

时间为2026-10-08 15:15:32–15:15:51北京时间；来源node27。同一节点的两个地址和各探测串行，最多四节点并发，TCP连接关闭后才SSH，不以并行SSH制造MaxStartups干扰。

- node27两地址SSH只读身份均为m03r2n07、boot c9bb3504-f2c9-41ac-ab5d-4bd3ce5a7f6b；keeper33000/33001的inventory各一次成功。
- node28和node30两地址均ICMP及TCP22/SSH banner成功。数据地址SSH只读身份成功，分别m03r2n08/boot5313e6f8-1712-45dc-9855-4c47358b6c4b、m03r2n10/bootd9d6a6fb-013f-49ac-98f9-e77eca67952e。管理地址SSH已完成key exchange，失败原因是本地known_hosts没有该IP的ED25519项；并非超时/失联，也不是确认key变化。
- node29管理10.211.3.29由eth0直连路由，ICMP失败，TCP22返回Errno113 No route to host，SSHconnect timeout；随后本地neigh显示该地址FAILED，说明本次管理路径邻居未解析。管理route条目存在，不能把errno113直接写成缺路由表项。
- node29数据12.11.3.29由ib0直连，ICMP成功，TCP22已握手且真实源12.11.3.27，3s未收到banner；SSH显示Connection established后5s banner timeout，尚未进入hostkey/auth/remote exec。两个keeperHTTP各4s超时。这是IP/TCP部分可达且用户态服务未及时响应，不能凭TCP握手证明node29主机身份或sshd正常。
- local_network_snapshot.json同时保存了邻居与完整路由；29数据邻居REACHABLE，备用11.211.3.29/13/14/15.11.3.29为STALE。旧缓存只能支撑曾有该地址邻居记录，不证明此刻仍配置/可用；本轮未probe这些备用地址。

当前证据支持29单节点的管理路径和数据服务异常，未发现28/30同样失联。28/30连通不代表GPU资源可用；node27新HIP/容器进程问题与29失联的因果尚未建立。没有GPU/SMI/HIP/模型调用、signals、重启、修改网络或known_hosts。没有证明29旧探针退出或全量保护对象完整。

初始28项取证manifest原样保留。新增静态网络快照和本解释单独进入supplement_manifest.json；验证原manifest全部SHA、28项唯一观察、每项实际路由返回、known_hosts前后SHA、signals/gpu_operations为零均通过。没有重复网络采样。
