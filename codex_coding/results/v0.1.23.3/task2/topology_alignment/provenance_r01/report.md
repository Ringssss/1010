# node27/node29 物理拓扑与 RCCL 输入来源取证

编号 `v0.1.23.3/task2`，采样时间见 [manifest.json](manifest.json)。本轮执行四条只读采集命令：两机宿主 sysfs/Docker metadata 和容器文件/进程 metadata；GPU 操作、远端文件写入、进程信号均为 0。所有输出环境变量均经过明确白名单过滤。原始 JSON、XML 字节快照及反汇编已冻结，图对齐派生见 [alignment_summary.json](alignment_summary.json)。

**两个节点的 GPU1 物理位置均是 PCI36/NUMA3/CPU48–63，容器 namespace 同样显示这个位置。部署镜像自带的 XML 把它重组到 NUMA1/CPU16–31，并把真实 PCI71 下的 shca1 接到 PCI31。unset `NCCL_TOPO_FILE` 会进入供应商的默认 mapping XML 分支，随后主 agent 的实际 startup 已确认匹配 508/shca 组。独立空种子 XML 的四份实际 dump 则恢复 GPU1 NUMA3/CPU48–63，但两条流依然使用 non-GDR。** 因此“物理拓扑发现”与“GDR 能力选择”需要分别验收。

## ENV 来源与当前 keeper

| 项目 | node27 | node29 |
|---|---|---|
| Docker image ID | `ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588` | 同左 |
| image tag | `sglang:0.5.12-ubuntu22.04-dtk2604-py3.10-sf_b075` | 同左 |
| image digest | `65e60bac61d0be4cd70a774c9da8a361b7fbe75ec353dd134faea7587ef504b1` | 同左 |
| image `Config.Env.NCCL_TOPO_FILE` | `/usr/local/built-in-508-topo-input-tj-default.xml` | 同左 |
| container `Config.Env.NCCL_TOPO_FILE` | 同 image | 同 image |
| keeper namespace PID | 118、136 | 118、136 |
| keeper `/proc/PID/environ` | 同 image；`NCCL_IB_HCA=shca_0` | 同 image；`NCCL_IB_HCA=shca_0` |
| keeper `NCCL_TOPO_MAPPING_FILE` | 未设置 | 未设置 |
| keeper `ROCM_PATH` | `/opt/dtk` | `/opt/dtk` |

证据：[node27/host.json](node27/host.json)、[node29/host.json](node29/host.json) 的 `image/env_comparison/container`，以及 [node27/container.json](node27/container.json)、[node29/container.json](node29/container.json) 的 `processes`。Docker image 本身已定义该变量；当前 container 与 keeper 延续同值。仅凭 inspect 无法区分“直接继承”与启动时再次显式设置相同值，故准确来源写为“镜像已有默认值，当前未见异值 override”。现有 worker process spec 的任务环境字典没有 `NCCL_TOPO_FILE`，也与继承路径一致。

容器挂载列表已保存。`/usr/local`、`/opt/dtk` 文件没有该任务的单文件/bind override；当前内容的镜像层字节归属尚未通过镜像层展开认证。

## 物理图与输入图

两机相同；下表 sysfs 数据来自宿主和容器的双重对照。8 GPU＋4 HCA 的 resolved device path、NUMA、local CPU list 全部一致，8 NUMA 节点 CPU list 同样一致。`namespace_matches_host=true` 见派生 JSON。

| 对象 | BDF | 物理 NUMA / CPU | 物理关键父链 | builtin XML NUMA / CPU / 关键父链 |
|---|---|---|---|---|
| GPU0 | `0000:09:00.0` | 0 / 0–15 | `09→08→07→02:06→01→00:01.1` | 0 / 0–15 / `09→07→01` |
| GPU1 | `0000:36:00.0` | 3 / 48–63 | `36→35→34→32:04→31→30:01.1` | 1 / 16–31 / `36→34→31` |
| shca0 | `0000:03:00.0` | 0 / 0–15 | `03→02:01→01→00:01.1` | 0 / 0–15 / `03→01` |
| shca1 | `0000:73:00.0` | 1 / 16–31 | `73→72:01→71→70:01.1` | 1 / 16–31 / `73→31` |

完整 BDF、root-to-leaf sysfs 路径、每个桥的 vendor/device/class、PCIe speed/width 存于 `host.json.{kfd,hca}`；容器看到的路径存于 `container.json.namespace_sysfs`。GPU0/GPU1 的 render minor 128/129、KFD node 8/9 与旧 HIP inventory 的 BDF 对照一致；render minor 本身不作为 HIP ordinal 的通用保证。

XML 有正常桥层省略，GPU0 的剩余桥仍在实际父链上。GPU1 的 PCI31/34/36 父链也仍在物理链上，但 numaid/affinity 错位；shca1 的 PCI31 祖先则实际不存在于其父链。这个区分由派生 JSON 的三个检查分别表示：`numa_equal`、`cpu_mask_equal`、`xml_ancestors_exist_in_physical_chain`。其余 GPU/HCA 的全设备对照保存为只读背景，当前探针重点仍为 GPU0/1。

## 库/XML 指纹

两机当前文件 SHA256 完全相同，RCCL/plugin/builtin SHA 亦与旧 `diagnosis_static_r01` 一致。

| 文件 | SHA256 |
|---|---|
| `/opt/dtk/lib/librccl.so.1`（resolved `.1.0`） | `32aa13da8bbd4d9a4ec37be08014f0783f5f576c7439dc10a3e1c661aa35427a` |
| `/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so` | `e0c3fd25c91cdb19c078b8350dd9daadded89d66600b0e5ed298840658af3af6` |
| `/usr/local/built-in-508-topo-input-tj-default.xml` | `5a10dbdec6e762ceb18861e98a556422e197a3351cf2d17df3246415fb4236ef` |
| `/opt/dtk/rccl/lib/topo_mapping_default.xml` | `ecd54d0e0cef17db7736688b0531ee98ade49ac0a69e87b1a04054f4519c965a` |

原始 builtin XML 为每节点的 `usr_local_built-in-508-topo-input-tj-default.xml.original`；原始 mapping XML 为 `opt_dtk_rccl_lib_topo_mapping_default.xml.original`。保存前后按原始字节计算 SHA，保留 mapping 的 CRLF。`/etc/nccl.conf` 和 `/root/.nccl.conf` 在这两容器中均不存在。

## unset 的供应商路径与最小 bypass 候选

此结论直接读取上述部署 ELF，未用上游源码替代 vendor 验证。文件指针均在 `container.json.commands.*.saved_file` 中。

1. `ncclTopoGetSystem` 从字符串地址 `0x1b2b5` 读取 `NCCL_TOPO_FILE`，在 `0xa4c4a45` 调 `ncclGetEnv`；空指针分支 `0xa4c4a4d→0xa4c4b84` 调 `ncclTopoGetXmlFromMappingFile`（`0xa4c4b8a`）。非空分支调用显式 `ncclTopoGetXmlFromFile`（`0xa4c4a8a`），跳过 mapping 分支。
2. `ncclTopoGetXmlFromMappingFile` 在 `0xa4d3200` 用 `ncclGetEnv` 查询字符串 `0x152f3=NCCL_TOPO_MAPPING_FILE`。变量存在时用该路径；缺失时在 `0xa4d3235` 用 `getenv` 读取 `0x23732=ROCM_PATH`，再以 `%s%s` 拼接 `0x105e7=/rccl/lib/topo_mapping_default.xml`，随后 `0xa4d3425` 调 `fopen`。
3. 当前 `ROCM_PATH=/opt/dtk`，默认 mapping 文件确实存在，含 `gfx936_8_x86_64_HygonGenuine_shca_4_InfiniBand_400-400-400-400_1_8_1` 组。该组将 GPU1、GPU3、shca1 放到 NUMA1 同一 PCI 组，将 shca 速率设为 `200000`。其注释明确解释 mapping 可重组设备和覆盖 PCI link/network speed，支持“有意识提供重映射机制”的结论；对本集群这样重排的设计动机与正确性仍待设计文档。后续 startup 的确切组选择已在下节认证。
4. 显式文件和 mapping 两分支汇合后，`0xa4c4dde` 调 `ncclTopoFillGpu`；同一函数还调用 `ncclTopoFillNet`。由此向主 agent 提交独立 `<system version="2"></system>` 空种子候选，跳过 mapping 后采集 actual discovery 图。version=2 与本部署 builtin/mapping 和空根合成相同。随后四 rank startup 已接受该种子并输出与所用 GPU/shca0 物理归属一致的实际图。

对应反汇编：[GetSystem](node27/elf_d323a8f1ad1b.txt)、[MappingFile](node27/elf_f6385042e720.txt)、[GetXmlFromFile](node27/elf_5529595afc90.txt)、[GetEnv](node27/elf_f8f4b253f385.txt)。node29 保存同类独立读取结果。`NCCL_TOPO_DUMP_FILE_RANK` 和 `NCCL_BASIC_TOPO_DUMP_FILE` 均有部署字符串及相关参数分支；communicator 的实际验证由主 agent 所有的 startup run 提供。

## 三条路径的实际含义

主 agent 随后完成的两次独立 1MiB startup 已通过 postexit 回收、四份拓扑 dump 和保护检查。本 forensics 只读这些已完成结果，抽取带文件 SHA 的逐 rank 图、affinity、exact mapping 日志和全部 64 条 channel connection（每 rank）。派生证据为 [startup_runtime_evidence.json](startup_runtime_evidence.json)，其生成脚本在源码同目录 `summarize_startup.py`。

| 路径 | GPU1 NUMA / CPU | shca0 speed | GPU / net gdr 属性 | 实际 channel |
|---|---|---|---|---|
| 原 builtin XML | 1 / 16–31 | 400000 | XML 显式 1 / 1 | 历史 calibration/diagnosis：A 为 GDR，B 为 non-GDR；精确证据沿用旧静态报告 |
| unset，`probe_startup_unset_r02` | 1 / 16–31 | 200000 | actual dump 0 / 0 | 四 rank 全部 64/64 connection 无 `/GDRDMA`，non-GDR |
| 空种子，`probe_startup_empty_r01` | 3 / 48–63 | 400000 | actual dump 0 / 0 | 四 rank 全部 64/64 connection 无 `/GDRDMA`，non-GDR |

unset 的四 rank 日志明确 `parseing topology mapping group: gfx936_8_x86_64_HygonGenuine_shca_4_InfiniBand_400-400-400-400_1_8_1`。空种子四 rank 日志记录独立 seed `Loading topology file`，所收集 topology 相关行没有 mapping-group 选择；rank1/3 的 dump 为 `numaid=3`、affinity=`00000000,00000000,ffff0000,00000000`，初始化 affinity 行为 `ffff0000,00000000`。空种子仅包含本 flow 的所选 shca0，故 shca1 的新运行时图仍未采样。

HCA 级 plugin 检测、拓扑属性和最终连接模式是不同层。新两次 startup 的 plugin 日志均包含 `GPU Direct RDMA Disabled for HCA 0 'shca_0'`，自动构造 GPU/net 属性为 0，channel 全部 non-GDR。原 builtin 则主动提供 `gdr=1` 和 speed400000 属性，旧 A channel 实际出现 GDRDMA，尽管旧初始化同样曾报告 HCA 级 Disabled。这说明原 XML 与自动发现路径同时改变拓扑归属、设备属性和 transport 的条件；两路径性能比较不能归因成纯 NUMA 修正。缺少 GPU 距离层 Enabled/Disabled 行不改变本次 channel 已充分可见的 non-GDR 结论。

原始实际结果：[unset postexit](../probe_startup_unset_r02/postexit/summary.json)、[empty postexit](../probe_startup_empty_r01/postexit/summary.json)。每份图和 summary SHA 已绑定到本取证派生；完整原始日志/远端 SHA 的复核链沿用两个 postexit manifest。

## 可复核命令与边界

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/collect_provenance.py --out codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r02
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/analyze_provenance.py --run codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r02
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/summarize_startup.py --probe codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_unset_r02 --probe codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_empty_r01 --out codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r02/startup_runtime_evidence.json
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/verify_provenance.py --run codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r02 --out codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r02/validation.json
```

collector 要求新输出目录；r01 原始结果不可覆盖。远端脚本通过 `python3 -` stdin 执行，stdin 来自已保存 `.py` 文件，准确 argv/source SHA 记在 manifest 中。采集代码不 import Torch、HIP 或 FluidCKPT，不初始化模型/communicator，也不调用 RPC。派生图对照在本机纯 CPU 执行，命令成功，两机 namespace 对齐均为 true。

本次独立 CPU 证据检查 [validation.json](validation.json) 为 **92/92 passed**，覆盖原始文件 SHA/大小、准确采集源码 SHA、命令退出码、两机 namespace 一致性、原 XML 字节 SHA、四 keeper PID/start_time 与当前 ownedoverlay keeper记录、两次 startup summary/actual graph SHA、全 channel non-GDR、GPU 实际 NUMA，以及全部取证 Python 源码语法。后续重采集使用同一 checker 时，PID/start_time 校验绑定这些现存 keeper owner 记录。

主 agent 暂停后续 GPU 后追加 [runtime_after.json](runtime_after.json)，两机四文件的 SHA 全部与本静态快照一致。另见 [当前共置负载取证](../provenance_runtime_r01/report.md)：两机任务外 `glm-5.2-int` 各8个 VLLM worker、每卡约50GiB；此证据支持停止当前性能归因。新增只读取证源码后语法及原冻结证据复核 [validation_r02.json](validation_r02.json) 为 **96/96 passed**，保留原92项结果。

现阶段三层均有证据：部署 ELF 支持两条输入分支；startup 接受 unset fallback 和独立空种子；实际 dump/log 验证各自 NUMA/CPU、speed/gdr 属性及 non-GDR 连接模式。供应商重排设计原因、shca1 空种子实际图和性能因果仍为未定项。运行时派生保留静态采样时间与后续 startup 时间的区分；旧 `alignment_summary.json.unknown` 描述生成时的静态知识范围，当前运行时结论以此节和 `startup_runtime_evidence.json` 为准。
