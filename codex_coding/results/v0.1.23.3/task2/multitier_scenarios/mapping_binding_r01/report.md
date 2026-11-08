# 三GPU节点六角色静态绑定候选

仅静态metadata资格，physical_ready=false。10:41–10:43 UTC有界读取27/28/30均自然rc0；node29未访问。

node27 old4037472/start477235183已absent，boot由c9bb3504-f2c9-41ac-ab5d-4bd3ce5a7f6b变为359fdde8-1501-43e0-a58a-c5b9b08e6f75，跨重启边界旧D身份不再成立；没有恢复退出码，不猜重启原因，新GPU健康仍未测。

|逻辑|实机|目标/helper物理卡|DRAM NUMA|SSD真实mount|available B|
|---|---|---|---|---|---:|
|N0|node28|rank0_target GPU0;helper0 GPU4|0/4|/data/node28→/dev/nvme0n1p1|842387083264|
|N1|node30|rank1_target GPU4;helper1 GPU0|0/4|/data/node30→/dev/nvme0n1p1|1793051893760|
|N2|node27|rank2_target GPU0;rank3_target GPU4|0/4|/home→/dev/nvme0n1p1|48728178688|

每节点GPU0/BDF09/NUMA0与GPU4/BDF85/NUMA4分别近shca0/shca2。共同shca0可产生NUMA10/34近远组合，拆近HCA可改变共同出口关联；静态距离不是带宽或独立通路实测。全部baseline必须拥有相同合法HCA机会。

首轮class03选择误列了板载VGA，raw保留mapping_refresh_r01；专门mapping_pci_followup_r01校正为Hygon PCI class0b4000/vendor1d94/device6320，各node8卡。不要使用首轮gpus字段作为DCU图。

blockdev容量由sysfs size×512，df容量由本地mount statvfs取得，未执行blockdev/df CLI。NVMe实际diskBDF04，PCI父链来自resolved sysfs；SSD NUMA仍unknown。网络/autofs路径未stat，不把共享checkpoint命名为节点本地SSD。

2GiB来自旧小实验暂定gate，不是用户硬上限；本collector无配额变更。主协调的新6GiB DRAM/2GiB HBM每node有限cap须以聚合实测验收。

最小下一步：node27全新隔离容器以原成功60s监督器执行一次GPU0 1MiB initcopy；主审后才启动。成功后补各node第二卡、六worker聚合预算、SSD own目录与同原语串行功能。

未知/阻塞：
- Node27 fresh HIP init/copy after changed boot is untested; GPU ordinals and free HBM need bounded runtime gate
- New six-worker mesh, same-host GPU paths and shca_2 paths are not qualified
- DRAM6GiB/node and HBM2GiB/node caps need aggregate per-phase measurement before six-worker admission
- Local NVMe parent mounts are bound; task-owned writable directories and SSD service/data manifests remain uncreated
- SSD HTTP proxy placement, buffers/pagecache NUMA accounting and actual disk IO/cache state are unverified
- Shared PCI/HCA effective capacity, restore finite schedule and actual processed TP4 model are unbound
