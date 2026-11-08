# 三节点资源调查：28/30完成，27只读采集待回收

本次只读调查未调用HIP/SMI、未分配GPU、未访问node29、未改原XML/默认环境。node28/node30的SSH严格hostkey；known_hosts SHA保持。

node27读取KFD vramtbl_45786时own collector进入D态；源脚本误纳入页表统计，已在后续源码排除，未重试。外层subprocess.run超时自动发出一次SIGKILL但未回收；这修正原未完成manifest中的signals=0，不能称完成或零signal。原始失败r01、r02和r03源码副本均保留。

| 节点 | sysfs显存余量GiB | 已见owner | 容器/建议 |
|---|---|---|---|
| node28 | GPU0=11.007, GPU1=11.007, GPU2=62.208, GPU3=62.458, GPU4=62.458, GPU5=62.459, GPU6=62.459, GPU7=62.459 | 外部SGLang在GPU0/1各52.431GiB；两组自有旧keeper在GPU2–7 | 自有full_distributed02与keep-v02均同vendor image/Python/DTK可用 |
| node30 | GPU0=63.980, GPU1=63.980, GPU2=63.980, GPU3=63.980, GPU4=63.980, GPU5=63.980, GPU6=63.980, GPU7=63.980 | 10个外部vLLM KFD owner当前vram_*为0；仍有活动上下文 | 无自有container；准确vendor image已本地存在，可新隔离container |
| node27 | 未取得新快照 | own采集PID4037472 D态 | 停止新尝试，controller4037469已absent |

两机均8GPU/8NUMA/4HCA，4个HCA单端口报告400Gb/s且ACTIVE。ASPEED card0是显示卡，从GPU清单排除；GPU logical仅按KFD render minor登记，HIP索引未验。

28与30均有image `sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588`，tag为SGLang0.5.12/DTK2604。28两自有容器/usr/bin/python3为3.10.12、/opt/dtk与/dev/kfd可读写；这仅metadata，不认证库初始化。30只image元数据，没有进入其他用户容器。

显存数字是sysfs total-used，不是HIP free，不构成最终2GiB准入或本任务预算。全部候选只支持下一步有界功能调查；共享进程/未知scope不允许性能就绪。旧14legacy仅本轮可见node28部分PID/容器取证，未RPC全目录验收；node29旧两pending与缺证保持。

node27 pending独立记录于own_pending.json及own_pending_fd.json；不能依此对其他owner或旧keeper发送signal。

父授权后只向精确controller4037469/start477235176发一次SIGTERM，boot/UID/argv/PGID全匹配；controller已absent。未再次signal D child4037472，其start不变且D/PPID1，退出仍未证。总计一次自动timeout SIGKILL尝试与一次授权controller SIGTERM，不能称signals0。
