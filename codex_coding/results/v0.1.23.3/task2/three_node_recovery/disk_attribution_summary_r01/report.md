# node28 自有容器、keeper 与磁盘归属只读检查

2026-10-09 06:02–06:06 UTC（北京时间14:02–14:06），严格已知SSH `node28`、显式 `Hostname=12.11.3.28`，actual hostname `m03r2n08`、boot `5313e6f8-1712-45dc-9855-4c47358b6c4b`。boot与既有inventory_r03一致；两个完整container ID和StartedAt与历史相同。所有原始命令、输出、stderr、timeout及源代码SHA保存在相邻phase1–phase5_r01新目录。

当前Docker根盘已经不是满盘。`/var/lib/docker`使用overlay2，位于`/dev/nvme1n1p3`；df总量468,244,996,096B、已用379,994,787,840B、可用64,389,390,336B（59.97GiB）、86%。结束时可用64,389,386,240B，差4KiB。10月8日available=0是历史事实；本轮没有删除任何文件，无法由当前快照确定昨日哪些文件造成满盘、谁释放了空间。没有新建容器、GPU/SMI/KFD访问、HTTP健康检查、signal、删除、截断、重启或清理。

## 根盘上的自有容器

| 自有容器 | 完整ID | StartedAt | daemon SizeRw |
|---|---|---|---:|
| zjn-fluid-glm-full_distributed02 | 0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c | 2026-10-04T01:14:43.800160031Z | 224,260B |
| zjn-keep-v02-n28 | 177035641ac22e59854da0edd9af0941d7599c1820cf686491fea5a2a06c962c | 2026-10-02T03:16:20.975758112Z | 171,369,452B |

两者Running=true，无OOMKilled。SizeRw合计171,593,712B（163.64MiB），是Docker daemon的可写层逻辑大小，未测得宿主upperdir实际分配块数。SizeRootFs分别34,581,694,976B与34,752,840,168B；两者减去SizeRw均得到共同基础image34,581,470,716B（32.21GiB），不能重复计两份，不代表该image物理独占占用。

普通SSH uid1020对两个LogPath执行stat均Permission denied；随后`sudo -n python3 -`返回`sudo: a password is required`，远端phase2脚本没有执行。两个Docker JSON日志大小与host upperdir allocated均unknown，不能写0，也不能据当前SizeRw彻底排除自有日志与昨日满盘的因果。未绕权限创建privileged容器。

容器内只读定点du测得full `/tmp`8,482,816B、`/root`446,464B、`/var/log`1,204,224B；v02 `/tmp`186,740,736B、`/root`7,671,808B、`/var/log`1,261,568B。v02主要是`/tmp/torchinductor_root`135,446,528B，其中triton105,598,976B、fxgraph25,702,400B，以及`/root/.triton`7,016,448B。这里是容器合并视图的allocated，含image内容，与SizeRw不相加；没有在这些位置发现百GiB的SSD分片。

Mounts说明两个容器的RW bind只有`/home/zhujianian`，其宿主文件系统为node27提供的NFS，不扫描。`/data/node28/shared`或其`zhujianian`子目录及`/opt/hyhal`都以ro绑定，不构成根盘上的RW bind。

## keeper仍在，主要保活数据属于内存

full容器的memory owner宿主PID62788、容器PID118/start440237850，以及6个cluster worker宿主PID83571、83577、83579、83580、83581、83582均live。精确owner/worker容器PID-stat start前后一致。cgroup memory.current314,488,619,008B（292.89GiB），其中shmem294,101,880,832B（273.91GiB）、anon17,759,092,736B。memory.stat的file294,780,342,272B包含shmem，不能解释为普通磁盘文件，不能与shmem再相加。

只读取fd指向及stat元数据，未读取权重payload。full owner和worker共享1098个唯一`(device,inode)`的`/memfd:fluidckpt-region (deleted)`，去重后apparent和allocated都为294,078,259,200B（273.88GiB），本次未呈稀疏块差异。owner重复持有每region两fd，worker也重复持有；4392个fd如果逐fd累加将得到1,176,313,036,800B（4倍）。owner与worker的RSS包含同一共享内存，不能累加当物理内存。`/dev/shm`路径df仅28,622,848B已用，与匿名memfd不同；memfd不作为这个目录下的普通磁盘文件出现。

v02的6个serving keeper宿主PID4062669、4062708、4062748、4062792、4062830、4062873均live，容器PID5989、6007、6025、6043、6061、6079，状态sleeping，cmdline明确`--memory-backend numa_registered`。cgroup memory.current7,254,642,688B（6.76GiB）、anon5,464,956,928B、shmem16,384B；只是进程/内存证据，没有调用RPC或验证目录、lease、权重SHA及恢复功能。

额外只读swap accounting：full memory.swap.current=0、memory.swap.max=0；v02 memory.swap.current4,781,793,280B（4.45GiB），max无限。宿主`/proc/swaps`列出既有`/swap.img`，Size8,388,604KiB（约8GiB）、Used约8,187,680KiB。v02有部分页进入swap；swap页记在既有swap文件中，不等同本轮新增同容量Docker可写层。未读取或量化swap文件的实际块分配，也未改swap配置。

## 另外的数据盘确有自有大文件

`/data/node28`位于另一块本地`/dev/nvme0n1p1`，df已用950,664,843,264B、可用842,387,083,264B（784.534GiB）、54%，不与Docker根盘相混。

| 本地自有路径 | allocated | apparent |
|---|---:|---:|
| /data/node28/shared/zhujianian/GLM-5.3 | 755,665,104,896B | 755,664,305,928B |
| /data/node28/shared/zhujianian/Qwen3.6-35B-A3B | 71,927,095,296B | 71,926,895,733B |
| /data/node28/shared/zhujianian/fluidckpt_zjn_baseline_data | 70,330,667,008B | 70,330,626,802B |
| /data/node28/shared/zhujianian/fluidckpt-full_distributed02 | 1,073,745,920B | 1,073,745,920B |
| /data/node28/shared/zhujianian合计 | 899,039,789,056B（837.296GiB） | 899,037,534,770B |

GLM/Qwen为已有模型目录，baseline_data与fluidckpt目录是既有项目数据。这是只读有界du目录元数据，不是读取权重或本轮新增磁盘副本；allocated与apparent接近，未出现将巨大稀疏表观文件误算成等量物理占用的情况。该盘尚有空间，这些目录不能解释另一块Docker根盘当前或昨日满盘。

## 交付、验证与未定项

入口：`python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/disk_attribution/collect_once.py --source <同目录phaseN_host.py> --out <新的version/task2/three_node_recovery/disk_attribution_*目录> --timeout <上限>`。phase2曾加`--sudo`并失败，保留原记录。phase1/3/4/5远端命令成功，所有子命令本轮无timeout。phase3/4原始执行使用内嵌`python3 -c`，偏离保存脚本/stdin约定；收到反馈后已修订当前源码为`docker exec -i ... python3 -`，独立保存`keeper_memory_container.py`和`full_memory_container.py`、记录stdin SHA。原始结果内冻结源码和argv不覆盖，修订没有重跑，不冒领实机验证通过。phase5采用独立保存脚本/stdin入口，成功。所有当前9份Python源码通过AST parse，两份独立payload与host嵌入源码逐字节相同，详见local_validation.json。

当前结论：根盘已恢复空间；已量化的两自有可写层很小，保活大头在memfd内存，另外的数据盘确有837.3GiB自有模型/实验数据但未满。两个Docker日志及根盘其他所有权没有完整量化，因此昨日满盘的完整归因仍未建立。本轮没有全盘扫描、其他用户文件内容读取、GPU访问或任何清理。
