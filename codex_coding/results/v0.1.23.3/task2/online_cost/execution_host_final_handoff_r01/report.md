# Host 执行与诊断最终交接

本子agent完成有限新容器setup、准确绑定、owned same-HIP准入、五进程监督、分阶段controller驱动、旧容器/原XML保护、全部节点原件收集，以及独立32MiB单share tuple raw IPC诊断脚本。源码、CPU检查与主agent实际原件SHA均在manifest.json。

实际进度：setup已成功，原Qwen run r01的engine TP初始化因线程资源失败；线程限制已修复，预算/pids512保留。run r02完整源/cache构建后最终bootstrap资源门失败，没有path校准、C0或留出。quiet失败会标记失败但收集全部节点。僵尸均无GPU活动不等于quiet通过，主agent只对本任务准确容器重启/后续停止。旧129项源码及所有失败原件保持；新subreaper已通过真实fork测试，需未来新freeze才应用。

最小 IPC once probe 的producer_init实际allocated/reserved均0，GPU4 whole-card used34,833,694,720B、free33,869,004,800B，超过原2GiB诊断门，也不满足完整模型目标52GiB free。脚本在创建32MiB payload、share tuple及consumer前停止。仅producer_init.json/failure.json已保存；imports2、完整模型r03重试均未执行。raw双导入的counter假说尚无本轮GPU复现数据，不能宣称已证明完整模型唯一原因。

当前资源阻塞另有主agent纯CPU证据：foreign CID9b0cfde.../glm-5.2-int中GLM tp8/pp3进程于2026-10-12 17:38:21启动，该时间晚于原r02约17:27资源门失败。因此新foreign作业只能支持当前资源占用的解释，不能倒推原r02失败原因。没有读取node27 KFD/VRAM owner，没有干预其他用户容器，没有放宽预算。

所有执行与GPU查询由主agent完成；本子agent只有代码/CPU检查，没有GPU、SSH、Docker执行。现阶段不再启动GPU，未来资源恢复后fresh核身份/容量，先tiny IPC诊断再决定完整bootstrap。readonly_CPU_commands.txt只提供有限docker ps/inspect/top读取，不含GPU/driver扫描、exec旧容器、信号或停止命令。
