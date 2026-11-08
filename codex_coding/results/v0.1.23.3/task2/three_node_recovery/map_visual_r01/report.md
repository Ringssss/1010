# 资源地图证据说明

图中绿色仅表示28/30已验收的两rank路径功能通过；黄色node27为历史控制/DRAM底本角色且新GPU运行与D进程退出未重新确认；灰色为结构信息或本轮排除的node29。未进行远程查询或实验。

物理快照为2026-10-08 08:00:58 UTC（16:00:58北京时间）；功能资格时间2026-10-09 07:25:33 UTC（15:25:33北京时间）。两机各128逻辑CPU、8NUMA、8GPU、4HCA、主存528095244KiB=503.630870819GiB。CPU插槽数未推断。

GPU静态序号按renderD128–135排列，只有最终资格中的28 GPU2/BDF55及30 GPU0/BDF09由fresh HIP BDF绑定；其他GPU没有逐卡runtime验收。两机静态映射一致，BDF是物理身份。PCI NUMA归属是设备拓扑信息，不能表示数据页实际placement。每卡DRM total68702699520B约63.984GiB（10/08快照），不等于HIP free或可用预算。NUMA0–7是机内内存域，node27/28/30是服务器。

| 物理GPU | 完整BDF（两机一致） | NUMA | local_cpulist |
|---|---|---:|---|
| GPU0 | 0000:09:00.0 | 0 | 0-15 |
| GPU1 | 0000:36:00.0 | 3 | 48-63 |
| GPU2 | 0000:55:00.0 | 2 | 32-47 |
| GPU3 | 0000:77:00.0 | 1 | 16-31 |
| GPU4 | 0000:85:00.0 | 4 | 64-79 |
| GPU5 | 0000:b5:00.0 | 7 | 112-127 |
| GPU6 | 0000:d5:00.0 | 6 | 96-111 |
| GPU7 | 0000:f5:00.0 | 5 | 80-95 |

| HCA（两机一致） | 完整BDF | NUMA | local_cpulist | 10/08端口1快照 |
|---|---|---:|---|---|
| shca_0 | 0000:03:00.0 | 0 | 0-15 | ACTIVE / LinkUp / 400 Gb/s |
| shca_1 | 0000:73:00.0 | 1 | 16-31 | ACTIVE / LinkUp / 400 Gb/s |
| shca_2 | 0000:86:00.0 | 4 | 64-79 | ACTIVE / LinkUp / 400 Gb/s |
| shca_3 | 0000:f6:00.0 | 5 | 80-95 | ACTIVE / LinkUp / 400 Gb/s |

实测仅node28 GPU2/0000:55:00.0/NUMA2 → shca_0:1/0000:03:00.0/NUMA0 ↔ node30 shca_0:1/0000:03:00.0/NUMA0 → GPU0/0000:09:00.0/NUMA0，实际IBext_v8、gdr0。两端全1048576B SHA=631b84027d6b9e52b539c4e8373622d23032dfadc64d60af87339c9037e4f769，19项验收通过、自然退出、无signal、组空。staging仅按gdr0机制示意，未测实际页NUMA与分段复制。

边界：GPU非独占；带宽、共享竞争与weights-ready未测；其他设备组合没有runtime资格。IB交换网络内部路由、共享上行与容量未取证，图中网络框不代表两NIC线缆直连。node30根盘在10/09资格记录中available=0B，小资产在私有shm。node27 PID4037472/start477235183及node29 PID21883/start475875814、21901/start475875846没有新退出证明。原XML/default mapping保持。node27历史结构来自topology_alignment/provenance_r01/node27/host.json，仅历史物理事实，不合计成三节点可用预算。

证据：evidence_refs.json包含两份inventory原件、最终资格、独立验收索引及其45份取证原件SHA；生成时逐项验证最终索引原件SHA。resource_summary.json保存精确映射和时间。

本地图为派生说明，不覆盖旧resource_map、solver、正式文档或历史记录。

复现：在仓库根目录执行独立脚本，选择新结果目录；使用Python标准库及系统librsvg/cairo，不需要GPU。

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/map_visual/build_map.py --output codex_coding/results/v0.1.23.3/task2/three_node_recovery/map_visual_r02
```
