# 有界控制与精确身份只读复核

继续 `v0.1.23.3/task2`。本轮只检查控制通道、容器生命周期、旧探针与保留进程身份以及keeper compact inventory；GPU操作、signals和清理调用均为0。[summary.json](summary.json)和[manifest.json](manifest.json)保存原始结果及SHA，旧 `xml_startup_owned_r02/summary.json` 未改。

node27 的host boot、完整container ID和StartedAt与原始物理证据一致；三个受保护进程PID118、136、309的starttime、argv、PGID与记录匹配。旧探针13525/475875063和13543/475875081仍为Z，本脚本未对Z或absent进程发信号。node27两个 `/inventory?compact=1` 返回成功，记录12/10条replica；这是当前端点/进程观察，不能替代全量权重内容认证或全部旧14 keeper的后置保护验收。

node29管理与数据地址SSH均未取得JSON身份，两个keeper HTTP请求超时；本次数据地址ICMP通，管理地址ICMP失败。不能据ICMP推断控制恢复，也不能据管理地址一次失败推断整机重启。PID21883/475875814和21901/475875846仍为精确待证目标；原始boot、container ID、StartedAt和PID namespace连续性尚无新远端证据。

当前源码额外绑定原始四rank固定pid/starttime/spec SHA/state SHA、原始raw摘要SHA和session SHA；hostworker核本身源码SHA，只有host boot、完整container ID及StartedAt全匹配、容器running，才读旧namespace PID。PID复用或owner argv/PGID不匹配明确blocked；Z/absent仅返回退出证据，`cleanup_eligible`不是清理执行或已清理结论。host SSH+JSON可达和原容器控制可达分别报告。旧PID namespace inode未记录，连续性依据是三个生命周期身份字段。

时间界限保留SSH ConnectTimeout=5s/单命令12s、Docker inspect=3s/exec=4s、HTTP=4s、ICMP=4s。本次没有health、服务重启、SMI、HIP初始化、Torch导入或新GPU实验。SSH显式StrictHostKeyChecking=yes与UpdateHostKeys=no，避免写用户known_hosts。首轮r01继承SSH默认配置时曾自动加入node27管理IP hostkey，该副作用已报告；没有逆向改用户文件。

CPU验证10项通过，覆盖boot/container/start缺失或变化、PID复用、Z/absent、argv/PGID不匹配、固定旧state SHA漂移和原始生命周期漂移，结果为[CPU日志](../resume_control_cpu_r01/test_control_recheck.txt)。r01保留原始源码及结果；r02使用完善后的新源码，未覆盖r01。

后续先恢复node29控制并核精确身份与全部保留对象，再补跨节点XML输入接受。当前 `exact_cleanup_confirmed=false`、`protected_postflight_complete=false`、`protected_state_corrupted=null`；无新性能样本、有效速率或规划准入。

```bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/resume/test_control_recheck.py
python3 codex_coding/src/v0.1.23.3/task2/resource_map/resume/control_recheck.py --out codex_coding/results/v0.1.23.3/task2/resource_map/resume_control_r02
```

上述远端命令已经执行；未来复核必须另用新结果目录，不为schema修订重复扫描现场。
