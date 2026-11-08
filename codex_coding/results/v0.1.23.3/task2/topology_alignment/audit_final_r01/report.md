# 拓扑对齐最终独立审查

编号 `v0.1.23.3/task2`。本目录 [summary.json](summary.json) 独立检查四个已完成run和一个准入失败run，共472项：466通过、6失败。**拓扑输入、物理位置与实际通信路径事实已认证；性能比较未就绪。** 审查执行GPU/网络操作均为0。

| 验收层级 | 结果 |
| --- | --- |
| `facts_verified` | true |
| `own_process_cleanup` | true |
| `aggregate_HBM_gate` | false |
| `performance_comparison_ready` | false |
| overall `status` | failed，保留unset正式run的物理回收门槛失败 |

## 已认证的配置差异

同一node27→node29 GPU0/1、两流同shca_0:1、OOB ib0，三种输入得到不同运行图。source和receiver均用本rank的实际dump与exact PID日志认证；`NCCL_TOPO_DUMP_FILE_RANK`在两次1MiB startup中实际产生四图，receiver图来自receiver进程。

| 输入 | GPU1图NUMA / CPU | shca0图速率 | actual GPU/net GDR属性 | 双端最终连接 |
| --- | --- | ---: | --- | --- |
| 原builtin XML，正式run | 1 / 16–31 | 400000 | XML显式1/1 | A GDR、B non-GDR |
| unset，startup及正式run | 1 / 16–31 | 200000 | 自动构造0/0 | A/B non-GDR |
| 显式空种子，startup | 3 / 48–63 | 400000 | 自动构造0/0 | A/B non-GDR |

物理GPU1为BDF `0000:36:00.0`、NUMA3、CPU48–63；宿主和容器sysfs一致。独立逐BDF检查区分透明桥压缩与错误祖先：空种子四rank dump内所用GPU和shca0的端点、剩余PCI祖先、NUMA、CPU mask全部相符。builtin和unset图的GPU1 NUMA/CPU不符。新图仅包含所用GPU及shca0，本轮的shca1物理背景来自静态取证，未采其空种子运行图。

unset经容器内`env -u NCCL_TOPO_FILE`清除继承值，四rank pre-Torch effective env确认变量缺失；实际日志同时明确读取`/opt/dtk/rccl/lib/topo_mapping_default.xml`并匹配508/shca组。显式空种子则跳过vendor mapping，运行图恢复所用设备物理位置。配置接口支持、输入实际接受、最终transport各有独立证据。两次startup每rank完整64条send/receive connection均无GDRDMA；dump的gpu/net gdr0与HCA Disabled记录解释GPU距离层决策日志缺失的capability早退。初始化affinity与正式proxy线程、host页NUMA的证据范围分别保留。

镜像和container已定义builtin XML变量，当前keeper继承同值；具体启动时是否重复显式设置同值由现有metadata无法区分。vendor mapping重组设备及覆盖速率是其文件注释和部署ELF支持的机制，重排本集群的设计理由仍缺供应商说明。详见 [原始取证报告](../provenance_r01/report.md)。

## 正式观察及停止原因

| run | 单A ms | 单B ms | pair共同窗口 ms | 本组独立预期 / pair | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| `probe_measure_xml_r01` | 119.888524 | 1540.719394 | 1512.656584 | 1.018552 | 原run通过，当前环境中的一次观察 |
| `probe_measure_unset_r01` | 495.711297 | 1158.001652 | 1170.610064 | 0.989229 | 内容/图证据通过，总体failed |

两组各A单/B单/pair一次；256MiB复用buffer、16次完成调用、每流4GiB、计时外warmup、receiver sentinel重置和最终全buffer SHA通过。使用同receiver node/boot_id monotonic复算共同窗口；两组80% overlap和5% start-skew门槛通过。实际接收同shca0端口的counter联合窗口仅计一次、raw乘4B，payload覆盖通过。旧G0/G1/G2的单流预期未混入本轮复算。

跨输入有效环境除`NCCL_TOPO_FILE`及run证据路径一致，协议设备/资源上限/payload一致，实际NIC端口、启动CPU允许集、运行RCCL SHA一致。原probe的操作、barrier、stream synchronize及warmup完整复用；环境wrapper与图导出在正式计时前，库指纹在正式计时后。图引起的GDR、NUMA/affinity、速率和算法图变化作为中介记录。旧run未保存有效继承环境，跨日期全部隐藏状态无法据旧process spec认证。

unset正式run四卡before→after free HBM各少184,549,376B（176MiB），超128MiB门槛；这对应本审查四个free-HBM检查及raw cleanup/postexit状态共6项失败。独有PID/starttime认证退出、四keeper目录/epoch/事务/底本/allocated/reserved/staging、parked进程和原14可达keeper保护均通过。总free-HBM变化与自有进程清理分别报告，未把失败升级passed。

[两机运行时只读快照](../provenance_runtime_r01/snapshot.json)记录各8个`VLLM::Worker_TP`、每卡约50,350–50,475MiB以及动态HCU/CPU活动。正式两组前后可用HBM已变化；后采样确认环境存在共享GPU工作负载，具体每段传输的干扰强度与176MiB差额归属仍未逐时测量。当前证据不能给三种输入作稳定性能排序，也不能把本轮新ratio替代旧日期的共享路径结论。主agent据此停止后续GPU：empty正式组和原XML重复均未执行。

## 保护、失败与复核

两机RCCL/plugin/builtin XML/mapping XML运行后只读SHA与事前相同，见 [runtime_after.json](../provenance_r01/runtime_after.json)。插件在进程组销毁后可卸载，最终`/proc/self/maps`未保留plugin；实际日志的exact Plugin Path与该路径事前/事后SHA共同认证其身份，RCCL另有逐rank maps指纹。

`probe_startup_unset_r01`因node29 rendezvous port占用在准入阶段停止，GPU launch为0；r02是新run。两次startup是图机制验证，性能样本为0。四已完成run raw、postexit及原始SHA完整保留。`audit_startup_pair_r01`是审查器最初要求plugin卸载后仍出现在maps导致的失败，修订后的r02及最终审查采用实际日志＋事前事后指纹，未新增GPU观察。

独立CPU4项通过，结果 [audit_cpu_r01/output.txt](../audit_cpu_r01/output.txt) 覆盖桥压缩、NUMA/CPU错误、错误PCI祖先和非连续CPU范围。审查源码 [review.py](/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/topology_alignment/audit/review.py) 仅读本地文件；输出新目录，不初始化Torch或访问RPC/SSH。

复核命令（本目录已存在，重跑使用新audit目录；本次按保留的failed门槛返回1）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/audit/review.py --run codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_unset_r02/postexit/summary.json --run codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_empty_r01/postexit/summary.json --run codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_measure_xml_r01/postexit/summary.json --run codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_measure_unset_r01/postexit/summary.json --provenance codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r01 --runtime-evidence codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_runtime_r01/snapshot.json --failed-run codex_coding/results/v0.1.23.3/task2/topology_alignment/probe_startup_unset_r01 --out codex_coding/results/v0.1.23.3/task2/topology_alignment/audit_final_r02
```

后续性能准入应同时核对本run保留对象、物理headroom和其他active GPU owners/cgroup及活动，而不能只由headroom超过2GiB判断资源独占。当前保留keeper、底本与生产XML；物理对齐已验收的空种子作为未来隔离实验输入，最快配置需在合法且稳定资源条件下另行测量。主线继续按计划18.9/e4/e5区分配置改进与placement贡献。
