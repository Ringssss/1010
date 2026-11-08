# 隔离拓扑对齐微探针

复用原calibration/probe_rank及runner，原脚本、生产XML、keeper和底本不改。默认四rank在node27/node29 GPU0/1，A/B均shca_0，ib0仅OOB。两组只改变NCCL_TOPO_FILE请求，诊断输出路径随run独立。unset的命令为`env -u NCCL_TOPO_FILE python3 rank_entry.py ...`，确保process.py合并父环境后确实取消变量。

rank_entry在import Torch前记录NCCL/RCCL/HIP/ROCM和设备/库路径控制白名单、PID、affinity与core=0；每rank独立图路径，并请求NCCL_TOPO_DUMP_FILE_RANK=pair-local rank（源0，接收1）。参数是否受部署库支持以startup实际四图为准。vendor candidate文件存在/哈希不表示已加载；unset不命名为automatic discovery，须解读实际日志与图及mapping fallback。

正式协议为256MiB×16=4GiB/flow、A单/B单/pair各一次、窗口外一次warmup、接收sentinel重置、完整最终buffer SHA。startup仅两flow并发1MiB×1+各一次warmup，不记性能样本。startup可验证dump支持、NIC/GDR/channel和内容，但小字节计数不用于5%背景噪声性能门槛。

原runner先完成原始内容/计时采集及保护/精准PID清理；四个libraries.json生成前禁止进入cleanup（90s有界），避免Torch库哈希被TERM打断。退出后从所属容器取exact PID的日志、图、环境与加载库hash，核验PID/start_time退出及远端/本地内容SHA。`postexit/summary.json`才是完整证据结果，含每rank路径/模式/图、artifact SHA、per-port payload、matched singles与shared gate。四图缺失即failed并列missing_ranks；不补造receiver图。

原`summary.json`刻意不在退出前认证binding（binding=[]），但继承runner的空列表验收可能仍使原status为passed，也可能因其他检查而failed。binding为空时原status不认证transport；只有`postexit/summary.json`含退出后实际binding、四图与完整checks才作最终认证。派生结果记录raw_stage_status和raw summary SHA，原文件不覆盖。真正rank失败、内容失败、超时、保护/回收失败也保留，不能解释为仅transport pending。缺日志不重新跑GPU。

本次固定三种输入身份，必须分别报告：

- **原XML**：显式`NCCL_TOPO_FILE=/usr/local/built-in-508-topo-input-tj-default.xml`，读取现有部署输入。
- **unset-mapping**：exec层取消`NCCL_TOPO_FILE`。已通过startup四图，实际进入vendor默认mapping，图中net speed=200000、gdr=0；不能称unset为自动发现。
- **emptyseed**：显式`NCCL_TOPO_FILE`指向本目录`empty_seed.xml`，以空system输入绕mapping进入共用fill。已通过startup四图，实际GPU1 NUMA=3/CPU affinity=48-63、net speed=400000、gdr=0。该startup验证图构造与路径机制，性能仍须由本组matched单流/双流对照确定。

图中`gdr=0`是图属性，GPU级实际GDR与最终传输模式仍须依据每rank日志的GPU决策和channel；图更符合物理sysfs不等于性能必然更高。三身份均使用原`--topology-mode`/`--xml`接口，Python无需新增模式。

startup最终证据分别为`topology_alignment/probe_startup_unset_r02/postexit/summary.json`及`topology_alignment/probe_startup_empty_r01/postexit/summary.json`。早期`probe_startup_unset_r01`因node29端口34010占用被准入阻止，没有启动GPU；原记录保留。下方命令是初始建议，已存在的run不可再次执行，后续须由主agent指定新run与空闲端口。

GPU启动由主agent统一协调。必须保护旧session至少14个可访问keeper、新4keeper、两个parked engine；preflight要求每GPU free≥2GiB、每node host/cgroup free≥576MiB+2GiB；正式新增synthetic≤256MiB/GPU、临时SHA缓冲≤32MiB/rank，context/communicator开销另采样，物理回收容差128MiB/GPU。startup synthetic≤1MiB/GPU。core=0，timeout默认600s、上限900s，唯一run/端口，不启动模型或RPC事务。`--flow-b-hca shca_1`仅提供有界可控splitNIC，不自动运行。

CPU验证：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/probe/test_probe.py --out codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_cpu_r01/revision02
```

先启动原XML的机制验证（端口仅建议，runner实时preflight检查）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/probe/run_probe.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_xml_r01 --port 34010 --topology-mode xml --stage startup --timeout 180 --protect-session codex_coding/results/motivation/fig4_lanes11/session.json --execute
```

再启动unset机制验证：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/probe/run_probe.py --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_unset_r01 --port 34011 --topology-mode unset --stage startup --timeout 180 --protect-session codex_coding/results/motivation/fig4_lanes11/session.json --execute
```

仅经主agent核对startup四图/实际路径后做正式原XML与unset，分别用新run`probe_xml_r01`/`probe_unset_r01`、端口34012/34013，省略`--stage startup`，timeout600。各组各一次；不得复用已有run目录。
