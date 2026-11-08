# SYS_NICE失败后一次精确CPU安全元数据与日志检查

2026-10-09 06:40:01.788 UTC，前后完整CID bcee43e68fc88a6bcea76cc58a11241646bd5b2d5605aaab7a7cda156a675d99/StartedAt2026-10-09T06:36:37.844554212Z/image/tasklabel、host boot/hostname及PID namespace4026541416通过。本次CPU读者CapEff/CapBnd均0xa88465fb，SYS_NICE位确实存在；Seccomp=2/filter1，NoNewPrivs=0。它证明这个docker exec继承的能力/filter启用状态，不是原GPU child13的完整syscall/filter明细。

`/var/log/hylog`目录读取返回ENOENT，故没有拿到child13日志，也不能判断原mbind错误是否消失。原件`mbind_blocked_text_present=false`仅为“本次捕获文本中没有该字符串”的计算结果，不能当“实际完整日志无此错误”。保留原始失败与目录error，不创建目录、不改权限或再次GPU尝试。

一次stdin只读取证约0.28秒、rc0；远端前/后inspect与CPU docker exec自然退出，signals0、GPU0、远端文件写0。stdout.json、result.json和冻结readonly_host.py保存准确命令、身份、错误与源码SHA。父已知SYS_NICE init仍15秒set_device超时、TERM回收与afterguard通过；运行结论以probe_node28_sysnice_r01原件及独立C审为准。
