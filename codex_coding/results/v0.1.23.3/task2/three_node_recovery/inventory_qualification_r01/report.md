# 三节点资格清单

| 节点 | 当前资格 | 缺口 |
|---|---|---|
| node27 | 新资源采集未完成；runtime受阻 | own collector4037472/start477235183退出未证；停止新尝试 |
| node28 | storage_blocked | 实际Dockerroot=/var/lib/docker，同根盘available0B；停止新容器方案 |
| node30 | 新隔离容器与前置身份已核；init状态=pending_parent | copy、自有XML与跨节点功能均未由本清单认证 |

node30容器fullID与StartedAt绑定原setup及guard；存在外部KFD上下文，只考虑有界功能资格，performance_ready=false。node28的/dev/shm备用方案仅离线准备，不能据此绕过Dockerroot满。三节点状态分开，不把30单节点资格写成三节点通信或性能就绪。node29从未访问，旧两个exact pending保持。

此清单从冻结文件离线生成，不执行GPU、SSH、Docker或模型读取。runtime状态待父的真实结果时保持pending，不把构建CPU检查当运行通过。
