# SYS_NICE 单一 capability 因素独立验收

实际HostConfig除CapAdd增加SYS_NICE外与首次private baseline一致；native init命令只按新CID/StartedAt/out更新，源码927f、privateIPC、原LD、OMP/MKL1、GPU2/BDF55/selected及15秒期限保持。21项原件/窄保护核对通过，GPU初始化失败。

Torch导入1.850848863s后set_device没有exit，总15.215212380s含回收，child13/start485377029匹配完整owner，一次TERM后absent、组空。child.log为0B不能据此宣布该child hylog中的mbind诊断消失；准确ownchild hylog待父取回。配置因素未使当前初始化在期限内完成，也不等于已证明该cap没有生效或权限因素完全无关。

新容器和原两容器完整身份持续、13旧进程exact、六目录hash保持、lease0/无事务。实际HIP BDF/free均未到，copy、XML及性能均没有新通过证据。审核未进行远端/GPU调用。后续若采用较长期限诊断，应另存源码副本和run，保持本次及此前15秒失败原件，并将“诊断期限完成”与“原15秒准入通过”分别报告。
