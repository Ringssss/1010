# 三节点六 GPU 的多层保存契约

开发编号 `v0.1.23.3/task2`。本轮从 HBM 位置竞争扩到同一暂停模型的 HBM/NUMA DRAM/SSD 保存层选择；只交付机器契约和离线结构检查。

固定 TP4 已处理字节快照、四个同时必要目标、每 rank 两块，总八块。`q=4MiB` 只是未来可选 payload 尺度，脚本只计算整数，不分配任何权重。块身份包含快照、rank、offset、length；保持 TP4，不重分片。原始 checkpoint 的解析、转换、packing 和分片属于另一个冷加载协议，不能从这里的零成本假设推导冷加载收益。

| 节点 | GPU0 | GPU1 | NUMA DRAM | SSD 完整底本分布 |
|---|---|---|---|---|
| n0 | w0/target rank0 | w1/helper | u0/u1 两个独立容量池 | b0–b3，4q |
| n1 | w2/target rank1 | w3/helper | u0/u1 两个独立容量池 | b4–b5，2q |
| n2 | w4/target rank2 | w5/target rank3 | u0/u1 两个独立容量池 | b6–b7，2q |

节点/GPU/NUMA 都是逻辑角色，未绑定现有机器。每块在全部六 HBM 和六 DRAM 池有可选副本位置，另有一个固定 SSD 来源；共104候选与104 direct 路径。全策略都可读完整 SSD，不通过禁用后备制造差异。SSD 总底本始终8q，没有隐含三份全量复制。任意可选副本逐份按物理字节收费，不增加 SSD 复制能力。

| 阶段 | HBM 容量向量 w0..w5，单位q | DRAM 容量向量，单位q | 共享模式 | 给定覆盖 witness |
|---|---|---|---|---|
| C0 | 2,0,2,0,2,2 | 2,2,2,2,2,2 | 独立资源控制 | HBM8q |
| C1 | 1,0,1,0,1,1 | 同C0 | 同C0 | HBM4q+DRAM4q |
| C2a | 1,1,0,1,1,0 | 2,0,1,1,0,2 | 同C1 | HBM4q+DRAM4q |
| C2b | 同C2a | 同C2a | node/NUMA共享 | 同C2a |
| C3 | 同C2b | 1,0,1,0,0,1 | 同C2b | HBM4q+DRAM3q+SSD1q |

C0→C1 只减少 HBM；C1→C2a 只变内存配额；C2a→C2b 只合并共享资源；C2b→C3 只减少 DRAM。独立控制仍保留同 worker 前序；它把物理共享工作点按路径分开，是离线控制，不能冒称三节点真实独立吞吐。supergraph 的 node NIC ingress/egress、PCI、NUMA DRAM、跨 NUMA、SSD queue、GPU copy、host staging 和 worker service IDs 不变；阶段只改变聚合约束的关联。

C2a/C2b/C3 的witness把b1(rank0尾块)保存在n1/helper w3、把b2(rank1块)保存在n0/helper w1，两个helper实际作为跨节点HBM来源。所有六GPU位置都至少有一个阶段能容纳q；helper不是恒为零容量的装饰。此结构修订发生在运行前，速率仍unknown，没有根据性能结果调参。不同worker的DRAM/SSD按已有staged primitive假设计入source GPU staging的copy/PCI和host staging；不把未来direct host RDMA路径冒作现执行器已实现。SSD分成storage→proxy HTTP腿与proxy→target GPU恢复腿：本合同proxy与storage同node，第一腿为local HTTP/CPU/host-staging假设，第二腿按worker是否相同区分H2D与RCCL。没有另加跨节点storage代理机会。精确本地路径与物理峰值仍需绑定。

设总需求 W=8q、HBM限额H、DRAM限额D。无SSD覆盖必要条件为DRAM≥max(0,W−H)、HBM≥max(0,W−D)；SSD来源逻辑字节至少max(0,W−H−D)。C1 无SSD需DRAM≥4q；C2a/C2b 无SSD既需DRAM≥4q也需HBM≥2q；C3 H+D=7q<W，因此每个合法恢复至少q来自SSD来源，给定witness达到该覆盖下界。Linux page cache 命中可能避免磁盘读，这个结论不证明至少q实际磁盘I/O。

这些是不等式必要条件与可行覆盖，未选择延迟最优层、未证明某tier比其他tier更快。C0的HBM全覆盖可行；SSD全覆盖在所有阶段也可行。DRAM和HBM没有被预算强迫为性能最优，层的实际收益由后续同原语成本和强baseline决定。没有参数扫描、求解器或全候选指数枚举。schema允许5–12块有限manifest；冻结builder只生成这个八块实例，结构验证逐候选和路径线性检查。

保活预算只计可选HBM/DRAM副本，固定SSD8q单列。恢复target HBM8q不占暂停保活额度，但必须进入resume峰值：默认HBM峰值至少保留副本+target8q，另外加暂存/runtime/切换临时空间，实际in-place adoption资格成立前不给别名抵扣。原串行ClusterWorker每worker64MiB HBM暂存是已知保守预留，六worker合计384MiB、每node128MiB；合同用`declared_executor_staging_*`单列，条件为复用原串行executor，不表示已实际分配。DRAM峰值也需要retention+runtime+host staging+page cache。actual staging、runtime与物理峰值仍未知，物理准入false，历史2GiB/node限制未扩大。

全部physical速率、latency、SSD backing、本地盘/NFS归属、模型SHA、runtime和schedule均unknown。逻辑SSD假设不证明现有模型文件是本地盘。共享incidence只表达应聚合的工作量，资源负载下界不能代替有限执行时间；没有GPU命令、SSH或产品修改。

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/multitier_scenarios/contract/build_contract.py --out codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r02
```

输出目录必须不存在。`contract.json`冻结候选、资源与五阶段；`structural_checks.json`给出逐候选/路径检查、byte账本和必要条件；`provenance.json`记录源码来源。正式baseline与当前实现能力由主计划和独立review记录；本脚本未调用现有joint/strong_simple，也不宣称它们已支持三层比较。
