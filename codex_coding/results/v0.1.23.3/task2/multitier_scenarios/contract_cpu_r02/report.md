# 三层保存合同 r02 与首版差异

日期2026-10-10，开发编号v0.1.23.3/task2。本次只修订原执行器的条件暂存账本和SSD读取代理路径，无容量/候选/布局/速率参数改动。contract_cpu_r01原件保持。

合同内部hash `741def7882e9163fb6afd299c27c1bff9c0207529d9d555c754d66f5492111fe`。Draft2020-12 schema与249结构检查通过，自然rc0，无GPU/SSH/payload分配/产品修改。

| 阶段 | HBM限额 | DRAM限额 | witness HBM/DRAM/SSD来源 | SSD逻辑来源下界 | 包含条件暂存的已知HBM峰值项 |
|---|---:|---:|---|---:|---:|
| C0 | 8q | 12q | 8q/0q/0q | 0q | 448MiB |
| C1 | 4q | 12q | 4q/4q/0q | 0q | 432MiB |
| C2a | 4q | 6q | 4q/4q/0q | 0q | 432MiB |
| C2b | 4q | 6q | 4q/4q/0q | 0q | 432MiB |
| C3 | 4q | 3q | 4q/3q/1q | 1q | 432MiB |

q=4MiB只是整数账本，不代表已分配权重。峰值列按retention+target32MiB+条件暂存384MiB保守相加，未计runtime/transition；actual peak与准入仍false/unknown。原串行ClusterWorker每worker64MiB暂存已知、每node两worker128MiB，是否实际部署/分配另列。历史2GiB/node限制保持。

SSD的storage→proxy HTTP腿与proxy→target恢复腿独立序列化。本冻结合同storage与proxy同node，HTTP腿声明本地loopback与CPU/host staging资源，不增加跨nodeHTTP代理机会；第二腿根据是否同worker选择H2D或GPU staging/RCCL。按worker.py实际串行分支，DRAM/SSD发往不同worker均经过source GPU staging，包括同node不同worker；不漏source copy/PCI，也不冒称未来direct host RDMA已实现。资源ID合并的语义仍是每字节占用工作点一次，速率、队列与具体物理路径未知。

五stage仍保持104候选/path、同完整SSD8q底本与四target。C2a→C2b仅共享；C2b→C3仅DRAM容量。C2a/b/C3两个helper均实际跨node供HBM来源；C3内存4q+3q<需求8q，因此至少q来自SSD逻辑来源。Linux page cache命中不意味着真实磁盘读q，冷/热cache协议尚未认证。

实际命令：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/multitier_scenarios/contract/build_contract.py --out codex_coding/results/v0.1.23.3/task2/multitier_scenarios/contract_cpu_r02
```

仅覆盖可行witness与容量必要条件，无solver/枚举/策略优势/finite schedule声明。源码snapshot与原执行器/SSD源码SHA在provenance；baseline能力由capability_r01独立盘点。后续必须绑定真实路径、预算与同原语成本再决定是否执行。
