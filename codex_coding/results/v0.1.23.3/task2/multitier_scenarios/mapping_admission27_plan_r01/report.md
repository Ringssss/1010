# node27新隔离单卡资格准备

prepared-only，未创建容器、未GPU。复用fresh_host setup，仅27身份/唯一name/stage/6GiB cap/禁止旧driver guard五处薄适配；runtime完全冻结成功init_gate_diag60.py，copy内60s、外75s，1MiB全SHA、PID精确监督和core0不变。

先主审setup source及plan，执行setup命令并捕获完整stdout；再以动态CID/StartedAt运行copy_entry。runner在docker exec前后核CID/boot和旧停止容器身份，保留精确进程/结果文件。任何deadline/cleanup/pending失败阻断后续，不重启旧容器、不创建NCCL、不改原XML/defaultmapping。

HBM2GiB cap仍需实际runtime增量验收；1MiB payload不代表context只占1MiB。此次单卡资格不升级六worker/网络/SSD/model资格。
