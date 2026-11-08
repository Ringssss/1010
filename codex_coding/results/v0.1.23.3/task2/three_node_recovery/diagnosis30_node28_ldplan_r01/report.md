# node28同日原配置后的LD单变量计划

适用节点为node28；文件位于diagnosis30范围是本轮分工边界，并非node30运行结果。父本轮原配置`probe_node28_init_r01`在Torch导入约2.798秒后停在set_device，总15秒超时，TERM child13精确回收，afterguard通过。原件不变。

本目录一次CPU只读snapshot在host/container前后完整ID、StartedAt、image、tasklabel以及host boot/hostname通过后，读取完整env.sh、分支路径exists及ENV白名单。命令全部rc0、自然退出，无signal、远端文件写入、source脚本或GPU调用。env.sh SHA为033843c698cb207a828a93545181c811860c3495870b94a9503a6f60b4d61b79；本次实际存在`/opt/dtk/.hyhal/rocm_smi/lib`，原LD_LIBRARY_PATH不含它，rocm_smi的OR条件静态为true。这证明可测试的环境差异，不证明它是set_device原因。

`command.sh`从`admission28_launch_init_r01/launch.json`的argv生成，只在docker exec插入一个`-e LD_LIBRARY_PATH=...`，保留原entry顺序，在`/opt/dtk/opencl/lib`后加入`/opt/dtk/.hyhal/rocm_smi/lib`。另使用必需的新结果名`probe_node28_ldpath_r01`；OMP/MKL线程各1、PYTHONDONTWRITEBYTECODE、同容器完整身份/GPU2/BDF0000:55:00.0/selected visibility、源码927f543e...、15秒内部deadline、35秒外层期限、零payload及精确回收均同原。未source整脚本、未改IPC/cap/seccomp/内存。GPU命令截至本计划生成尚未执行，由父审查后启动、收集原件和后置保护。

如果该搜索路径增量足以恢复，应在同15秒内出现set_device_exit、正确BDF、实际HIP free与init_admitted，并完成exact回收。如果仍同阶段超时，只能判这一个增量不足以恢复，不能认永久坏卡，也不继续copy/XML。若成功，必要时再一次原配置回退确认，区分LD因果与时间/驱动状态变化；不默认五轮。无性能样本。

真实metadata见stdout.json及metadata_result.json，冻结stdin source为readonly_host.py；可执行但未启动的准确argv、静态input和baseline SHA、预期、父准入条件见launch.json。
