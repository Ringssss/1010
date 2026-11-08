# 精确owned rank清理的连接补证

本次仅授权xml_startup_owned_r02的node29 rank2 PID21883/start475875814、rank3 PID21901/start475875846。node27两rank此前由父agent确认Z状态；本补证不重新操作其进程，不触碰其他容器或keeper。

node29别名（解析10.211.3.29）和明确数据IP12.11.3.29各尝试一次SSH，均在banner exchange返回255，约5.01s。远端docker/python/原process.py stop均未执行，没有远端signal。两IP ICMP成功，但两keeper HTTP inventory端点33000/33001均4s超时，证明网络层ICMP可响应并不等于SSH/服务路径可用，不能据此判远端进程已退出。

原startup summary SHA仍一致，cleanupfailed保持原状。pending_cleanup_ledger.json记录精确spec身份、PID/starttime和后续只允许的stop路径；rank2/3当前live/exit仍未知，清理未完成。没有重跑GPU、没有认证XML runtime接受或性能，不把Gloo/SSH失败归因于XML。

r01/source.py冻结实际本地尝试的完整源码与summary SHA。由于SSH未建立，原stdin remote worker没有运行；代码审查另发现stdin的__file__不能用于repo父层路径解析，未来入口已改为共享挂载绝对脚本文件执行，并增加worker自身SHA检查。这项修订不改变r01结果、没有新增连通尝试，当前recover_owned_run.py可用于父agent后续全新recovery run。

下一步由父agent统一决定可连接时的有界重试，仅使用两份已登记spec调用原cluster/process.py stop并核对退出；若仍不可达，继续保留待清理账本，不声称完整clean。
