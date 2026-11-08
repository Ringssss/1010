# 双流路径诊断最小入口

沿用 `v0.1.23.3 / task2`。`read_inventory.py` 只读两机全部 HCA/port、PCIe 全祖先、NUMA 和保护进程绑定；既不初始化 GPU，也不修改 NCCL 拓扑 XML。已保存 `diagnosis_inventory_r01`。两机 `shca_0:1` 与 `shca_1:1` 均 ACTIVE、400Gb/s，分别 NUMA0/1；GPU1 的实际 NUMA3 不能据此称为 shca1 近端。

`run_diagnosis.py` 通过显式局部适配复用冻结 `run_calibration.py` 的启动、保护、SHA、超时和清理事务，以及原 `probe_rank.py` 的全部计时/搬运代码；不改冻结源码。默认 A(rank0/2) 使用 shca0，B(rank1/3) 使用 shca1；OOB、Gloo 均仍为 ib0。每组仍为 matched singleA、singleB、pair 三配置，每 rank 256MiB、每窗口16次、一次预热、core=0。GPU启动必须显式 `--execute`，由主agent统一进行。

新适配仅改变新目录命名、每 rank HCA spec、额外只读准入/绑定证据和逐实际端口计数。接收计数在每端口内部做快照并集，同端口计一次，拆端口分别按对应 flow 累加；不重复累计共享计数。保留全 channel transport/GDR决定/affinity/init core/topo-file 原日志行。不添加 taskset、GDR_LEVEL 或 debug SUBSYS 变化。实际 NCCL GDR 或 affinity 随 HCA 变动是干预结果，不能当固定控制。

G0 使用原 frozen runner `--matrix network`，G1 使用本入口；各自单流作为各自 pair 的独立预期。内置 topology XML 与真实 sysfs 存在差异，G1 可能同时改变 GDR模式、PCIe路径和NUMA关系。因此这是 HCA 选择引起的资源路径包干预，不能解释为纯 NIC capacity 或唯一网卡饱和因果。原主进程未显式绑定CPU，当前 main-PID/cpuset 证据与 NCCL 初始化 affinity 日志应分别解释；没有运行时全线程 affinity trace。

CPU验证：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/test_diagnosis.py
```

6项覆盖同口去重、拆口对应、错口拒绝、两端per-flow环境/命令/控制及SHA、B专用LOC，以及后置采集不得掩盖真实失败。注册only目录 `diagnosis_probe_split_registration_r01` 已产生，只有只读 preflight 和spec文件，没有PID或GPU观察；该目录不可覆盖，后续执行用新目录。最新代码hash按新run重新记录。

主agent G1命令（目录和端口由主agent最终协调）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/run_diagnosis.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_probe_split_r01 --port 33971 --protect-session codex_coding/results/motivation/fig4_lanes11/session.json --execute
```

G1后置结果 `diagnosis_recollect_split_r01` 已通过独立审查；初始failed summary保留，后置补齐日志新增0样本。G1 driver/read_inventory/test/recollect源码另存 `diagnosis_source_snapshot_g1_r01`，hash与当时摘要及manifest一致。

G2仅新增 `--flow-b-disable-gdr`，使rank1/3设置 `NCCL_NET_GDR_LEVEL=LOC`，rank0/2保持默认。执行后在原cleanup/保护/code/input全部通过且3配置完整时，自动运行 `recollect.py`，写新派生目录；异常搬运/SHA/保护/清理失败不进入补采。后置summary检查B实际GPU-specific disabled且无GDRDMA，A仍enabled且有GDRDMA；不符合则派生failed。HCA探测层Disabled行仍保留，GPU布尔仅匹配GPU-specific决定。

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/run_diagnosis.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_probe_split_boff_r01 --port 33972 --flow-b-disable-gdr --protect-session codex_coding/results/motivation/fig4_lanes11/session.json --execute
```

自动派生目录为 `diagnosis_recollect_split_boff_r01`；原run与派生是同3观察，不能累计为6样本。暂停模型底本、4keeper、2parked engine、旧14可访问keeper原样保护；通过不等于生产executor应用归因。
