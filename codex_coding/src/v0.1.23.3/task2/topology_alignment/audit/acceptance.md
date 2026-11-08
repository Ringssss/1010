# 拓扑对齐独立验收清单

编号 `v0.1.23.3/task2`。审查只读本地源码、静态取证和新原始结果，GPU启动与运行资源由主agent协调。

## 判定对象

本轮检验拓扑输入是否实际生效，以及图改变经NIC/GDR/亲和性等中介如何影响固定通信路径。物理图正确性由sysfs映射和实际RCCL图认证；性能由各组matched singles/pair衡量。二者分别判定。旧G1来自vendor图，不能作为sysfs真实图的认证。计划18.10先用现有XML与显式unset两组；unset仍回退时，再由主agent按证据决定显式诊断图控制。

## 必要证据

| 检查域 | 验收条件 | 缺失时结论 |
| --- | --- | --- |
| 硬件与工作负载 | 四rank固定node27/29 GPU0/1，source/receiver角色、HCA/port、OOB接口、256MiB复用buffer、16次正式搬运、计时外warmup一致 | 控制变量比较未通过 |
| 有效环境 | Python导入Torch前保存effective env；除NCCL_TOPO_FILE和run专属证据路径外通信/运行env一致；unset在容器内部真正执行 | 自动发现组未认证 |
| 输入身份 | vendor和诊断XML为只读快照，SHA及路径逐rank对应；源码、RCCL/plugin身份冻结；旧结果保持 | 溯源未通过 |
| 实际图 | 每rank exact PID/starttime的RCCL日志与实际dump/graph，dump SHA与来源明确；自动发现组有运行图且无vendor fallback | 仅支持输入请求，不能认证干预生效 |
| 物理映射 | GPU logical ordinal→BDF→sysfs父链/NUMA/CPU允许集；NIC→BDF→父链/NUMA/port；诊断图中的这些节点逐项一致 | 物理图正确性未通过 |
| 实际路径 | 每rank全部GPU-specific GDR decision、send/receive后缀、actual HCA/port、初始化affinity/channel，以及实际图距离 | 中介作用未认证 |
| 双端一致 | source与receiver均保存实际路径证据；不能由receiver GDR推断source模式 | 对应流端到端模式未认证 |
| 计时 | receiver同node/boot_id monotonic共同窗口独立复算；80% overlap、5%较短matched single skew门槛；pair比本组独立预期 | 并发性能归因conditional |
| 内容 | source/receiver最终全256MiB SHA匹配；sentinel重置、16次完成调用、每流4GiB字节与interval一致 | 内容正确性未通过 |
| 端口计数 | 根据实际receiver HCA/port合并共享counter窗口一次，raw×4B，payload覆盖；记录idle背景 | 路径归属未通过 |
| 保护与清理 | 最新session输入，原14可达keeper与新4目录/epoch/事务/底本/staging保持；parked进程身份；独有PID/starttime退出；四卡free恢复 | 运行保护未通过 |
| 运行范围 | 先1MiB启动/图导出验证，再现有XML与unset两组×A单/B单/pair各一次；必要显式诊断图由主agent决定；失败、retry、补证新目录；不强制SYS GDR | 超出预登记需主agent明确解释 |

## 最终报告必须保留的区分

- 图输入环境变量接口可用、实际解析图、实际GDR/非GDR路径分别给证据。
- 拓扑改变是干预变量，图引起的GPU—NIC距离/GDR/CPU affinity变化是中介；固定请求HCA不保证模式固定。
- sysfs一致图的性能可以比vendor慢；选择最快配置与认证物理图分别报告。
- 初始化affinity支持初始化线程选择；正式proxy线程与host页位置由实际采样支持。
- counter证明payload经过端口；硬件饱和与唯一瓶颈需要另外的busy/stall证据。
- 本轮工作量是隔离RCCL微probe，应用keeper恢复与完整模型指标沿计划18.9分别验收。

## 旧证据定位

`topology_scenarios/diagnosis_audit_final_r02`：G0同shca0、A-GDR/B-off，pair237.870ms；G1 split、A/B-GDR，pair98.194ms；G2 split、A-GDR/B-off，pair205.220ms。G0→G2证实路径拆分可解除额外干扰，G1→G2区分B模式对自身速度的影响。各组使用本组matched singles；所选NIC/PCIe/NUMA路径组合仍为已有归因层次。

`topology_scenarios/diagnosis_static_r01`：vendor XML的GPU1 CPU16–31/NUMA1、shca1 PCI31父链，与sysfs GPU1 CPU48–63/NUMA3、shca1 PCI71父链不一致。此静态事实是本轮诊断图的验证基准。
