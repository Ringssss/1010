# G2 单变量禁 GDR：部署二进制核对与判别契约

`v0.1.23.3 / task2`；本报告只读，GPU操作0。本机精确部署 RCCL 支持 `NCCL_NET_GDR_LEVEL=LOC` 字符串，证据超出单纯环境名 strings：两端 SHA 同为 `32aa13da8bbd4d9a4ec37be08014f0783f5f576c7439dc10a3e1c661aa35427a`，保存 [node27_binary.txt](node27_binary.txt)、[node29_binary.txt](node29_binary.txt) 和 [summary.json](summary.json)。不加载或调用 RCCL，不修改生产 XML。

## 精确二进制证据

1. `strings -a -n 3` 得到 `NCCL_NET_GDR_LEVEL` 与偏移 `0x18fd1` 的字符串 `LOC`；默认 strings 最少4字符会漏掉三字母 LOC。
2. `nm` 显示 `ncclGetLevel`、`ncclTopoCheckGdr` 和 `topoPathTypeStr`，可定向只读反汇编。
3. `topoPathTypeStr` 第0项的 ELF relocation `0xa59e800 → 0x18fd1`，后者rodata明确 `LOC\0`；不能只读.data中的0而忽略加载重定位。
4. `ncclGetLevel` 在 `0xa4a4c3e` 调用strcmp匹配该第0字符串，在匹配分支 `0xa4a4d00` 将解析level置0，并保存到传入level变量。
5. `ncclTopoCheckGdr` 在 `0xa4a5352` 获取 `NCCL_NET_GDR_LEVEL` 字符串地址，`0xa4a535b` 调用该parser；`0xa4a568c` 比较distance与level，只有distance≤level才到`0xa4a56bc`写useGdr=1，否则保留入口设置的0并记录Disabled。

所以 G1 B路径若distance4（PXB），LOC将使4>0而禁用GDR。官方同版本 [NCCL 2.22.3变量说明](https://docs.nvidia.com/deeplearning/nccl/archives/nccl_2223/user-guide/doc/env.html#nccl-net-gdr-level-formerly-nccl-ib-gdr-level)也定义LOC关闭GDR，但本判断已有当前供应商部署binary的解析/比较证据，不只依赖上游兼容假设。

## G2 改什么、如何认定干预成立

仅在新的 B source/target 进程环境中设置 `NCCL_NET_GDR_LEVEL=LOC`。A两个独立进程不设该变量；四rank的group、可见GPU、buffer256MiB×16、NIC绑定、OOB、chunk/同步、AMD_SERIALIZE_KERNEL、CPU与XML全部沿用G1。每配置matched singles和pair各一次，不对既有keeper/engine施加变量。

LOC作用于进程内该RCCL阈值，非按channel随意指定；B两端都设才能把send和receive都恢复为non-GDR。A是独立进程，未设不会受到B环境影响。正式前warmup吸收communicator建立，接收sentinel重置/最终SHA、同host完成区间和各HCA计数沿用现有门槛。

必须以G2运行日志确认：B两端出现env LOC、GPU/HCA Disabled且send/receive连接均无 `/GDRDMA`；A两端仍Enabled并有 `/GDRDMA`；actual HCA仍A shca0/B shca1。若只记录设置变量而未证实这些，不能认定干预成功。channel数量、affinity和payload counters也要保持可解释，防止切换transport附带调度参数变化被遗漏。只有初始化线程log时仍不推广全部TID affinity。

## 三组的因果比较

| 组 | A NIC/GDR | B NIC/GDR | 能回答什么 |
|---|---|---|---|
| G0 | shca0/on | shca0/off | 已复现原共享端到端退化 |
| G1 | shca0/on | shca1/on | 拆路径加transport改变后可并行；不能单独归因拆NIC |
| G2 | shca0/on | shca1/off | 对G1仅改变B GDR阈值；对G0保持Aon/Boff transport模式，改变B所选NIC/PCIe/NUMA路径 |

若日志confirm，G1↔G2可检验当前split路径上GDR选择的效果；G0↔G2可检验相同Aon/Boff模式下路径分离的效果。G0与G2仍不只相差NIC芯片：B真实NIC NUMA0→1、PCI分支01→71，NIC与XML所选初始化CPU16–31的亲和随之改变。因此结论应为“共享通信路径/所选路径”，不把这些关联资源拆成已证明单组件饱和。

| G2结果（matched singles为本组基线） | 支持的结论 | 仍不能推出 |
|---|---|---|
| Bsingle变慢，但pair ratio接近1 | split路径在B non-GDR时仍消除额外并发退化；原退化不能只归因B缺GDR。GDR影响B自身供给，分离路径影响并发干扰 | NIC硅片唯一根因、host页NUMA/PCIe占用实测 |
| G2相对G1又出现明显并发额外退化 | non-GDR transport对split配置并发仍敏感，G1解除退化含transport作用 | Boff必然同一NIC瓶颈、CPU饱和或全系统host staging字节已实测 |
| pair窗口较G1拉长，但仍接近本组maxsingle | 单流B变慢充分解释窗口，不构成额外共享退化 | 不能仅按G1/G2pair绝对时长差说共享资源竞争重新出现 |
| variable/路径未生效或清理/校验失败 | G2不具判别效力，记录失败 | 不强制SYS、不改XML、不把失败当性能样本 |

三组并非完整2×2因素实验：缺少sameNIC且B GDRon条件，因此不估计可加的NIC与GDR独立系数，也不声称唯一物理瓶颈。G2可为本轮最小区分与收口提供证据，无需扩大全拓扑或强制SYS。

## 命令与验证

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/static/loc_support.py --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_static_loc_r01 --container zjn-fluid-basic-1bce3897a2075b
```

两个节点只读检查通过，GPU操作0；summary逐项保留命令、返回码、输出SHA和冻结地址适用性。反汇编地址仅对本次准确binary SHA有效；脚本发现SHA变化会拒绝据此接受。原static_r01保持不覆盖。本报告未运行G2。
