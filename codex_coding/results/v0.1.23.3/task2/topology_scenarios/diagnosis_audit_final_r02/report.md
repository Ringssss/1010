# 跨节点双流退化：最终独立因果审查

编号 `v0.1.23.3/task2`。263项独立检查通过；本审查新增GPU样本0，仅引用原观察与本轮G0/G1/G2各三项网络配置。每配置只有一个观察，16次搬运为配置内迭代。本目录 [summary.json](summary.json) 是独立复算与三组contrast最终入口，旧 `diagnosis_audit_final_r01` 为补充配置一致性核查之前的中间结果。

**同一混合GDR/非GDR通信路径共用shca0时出现并发干扰；把B改到shca1后，B继续采用非GDR仍近似独立，因此拆分所选通信路径确实消除了额外干扰。GDR另影响B自身单流速度。** 目前的因果定位止于所选HCA/PCIe/NUMA通信路径组合，尚未分别定位NIC芯片、PCIe上行或host-backed缓冲供给中的唯一主因。

## 实际干预与结果

| 组 | A路径/模式 | B路径/模式 | 单流A/B GB/s | 并发A/B GB/s | 本组独立预期ms | 并发共同窗口ms | 独立预期/实测 |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| 原观察 | shca0/GDR | shca0/非GDR | 43.61 / 25.53 | 18.48 / 18.23 | 168.255970 | 235.603975 | 0.714147 |
| G0，同路径复现 | shca0/GDR | shca0/非GDR | 43.63 / 24.86 | 18.26 / 18.06 | 172.742962 | 237.870026 | 0.726207 |
| G1，B选另一HCA | shca0/GDR | shca1/GDR | 43.62 / 43.65 | 43.81 / 43.74 | 98.470972 | 98.193661 | 1.002824 |
| G2，split路径仅B禁GDR | shca0/GDR | shca1/非GDR | 43.60 / 20.95 | 43.75 / 20.93 | 204.995921 | 205.219691 | 0.998910 |

每流正式窗口传输4,294,967,296B，由同一256MiB buffer连续搬运16次。共同窗口由同一node29/boot_id的接收端monotonic区间 `max(end)-min(start)` 计算；未跨主机相减。各组独立预期采用本组两条匹配单流耗时的较大值，不能用G1较短窗口直接判G2“恢复了争用”。G1/G2调用窗口充分重叠、起点差分别15.52µs和37.26µs，远低于匹配短单流5%的门槛。所见是完成同步调用窗口，不是设备kernel占用轨迹。

## 三个可支持的因果对照

1. **G0→G1检验路径选择组合的缓解作用。** 并发窗口缩短58.72%，两条流各恢复到约43.7GB/s。但B同时更换HCA及GPU—NIC模式，这一对照不能独立分离GDR作用。
2. **G1→G2检验split路径上的B transport模式作用。** 只在B两端设置 `NCCL_NET_GDR_LEVEL=LOC`，实际日志确认B两端GPU-specific Disabled、send/receive均无GDRDMA，A仍Enabled且带GDRDMA。B单流由98.403542ms增至204.995921ms，耗时2.0832倍；其并发205.219691ms几乎等于自己的单流。因此G2较G1窗口拉长主要由B自身供给速度下降解释，没有再次出现同等级额外干扰。
3. **G0→G2检验保持A GDR/B非GDR模式时的路径分离。** B单流反而慢18.67%，A单流保持约98.5ms，但A并发由18.26恢复43.75GB/s，B并发接近自己的单流，独立完成ratio由0.7262升至0.9989。故原并发退化不能仅归因“B缺GDR”，也不能把G1解除干扰全部解释为“B变快”。B换路径同时变更实际NIC NUMA0→1及PCI分支01→71；这仍是路径组合因果，未独立定位NIC硅片。

G1/G2额外9项配置对照全部通过：四rank环境除专属日志路径和B的GDR阈值相同，实际HCA/port、初始化affinity mask和连接数相同，workers/cases/resource_limits一致。四rank均保留64条send/receive连接行，A为`ffff`、B为`ffff0000`；这是初始化日志证据，不代表整个Python进程或全部proxy线程永久绑定。G0的原summary只保存8条transport节选，未包含完整affinity字段；不存在“因为G0字段空就证明亲和性改变”的依据。

## 端口、内容、保护与失败记录

| 并发组 | 实际接收端口 | 端口接收delta B | 正式payload B |
| --- | --- | ---: | ---: |
| 原观察 | shca0:1，两流并集计一次 | 8,643,065,468 | 8,589,934,592 |
| G0 | shca0:1，两流并集计一次 | 8,643,064,468 | 8,589,934,592 |
| G1 | shca0:1对应A | 4,321,543,904 | 4,294,967,296 |
| G1 | shca1:1对应B | 4,321,542,292 | 4,294,967,296 |
| G2 | shca0:1对应A | 4,321,544,612 | 4,294,967,296 |
| G2 | shca1:1对应B | 4,321,552,028 | 4,294,967,296 |

独立读取原始counter快照，IB raw单位乘4B，按实际receiver rank选择HCA/port后分组；同端口取联合窗口一次、不同端口分别计数。各端口payload都覆盖，未重复累加两rank读到的共享计数。NIC计数包含协议和其他用户，idle背景估计不能排除正式突发背景。

每配置source/target最终256MiB全buffer SHA相同，每流4GiB总字节及16次完成调用匹配，接收sentinel重置和计时外warmup通过。全SHA覆盖最终复用buffer，未声称16份不同内容逐次验证。原始源码前后hash、输入hash、4keeper目录/epoch/事务/allocated/reserved/staging、两个parked命令、旧14个可访问keeper、独有PID/starttime退出和4卡物理HBM回收独立通过；对原8个不可达端点未重新宣称保护访问成功。

G1原 `diagnosis_probe_split_r01/summary.json` 的failed状态完整保留：计时与SHA已完成，但collect时receiver日志缺失，无法认证实际端口。独立审查第一次因此停止；空目录 `diagnosis_audit_split_r01` 和结构化失败 `diagnosis_audit_split_failed_r02` 保留。清理后exact PID日志的原始/本地/派生SHA一致，`diagnosis_recollect_split_r01` 恢复缺失证据，原summary与raw timing不变。G2自动沿相同后置只读协议，最终使用 `diagnosis_recollect_split_boff_r01`。这些补采和独立审查均不增加性能样本；具体是logger buffering还是文件可见性未作唯一判断。

## 论文中共享资源如何定义

资源身份可按真实方向列出 `node27/shca0:1/tx` 和 `node29/shca0:1/rx`，两流原配置都经过它们；split后B使用对应shca1端口。当前测量不能分别推定发送与接收两端各自容量或哪一侧主导，应把数值校准挂到明确端点、HCA选择、GPU—NIC transport模式、RCCL/插件、块大小和同步条件的**端到端共享service group**。

同口A单流43.61GB/s高于双流共同36.46GB/s。因此36.46只能代表当前混合模式双流工作点，不能作为所有状态共同的固定物理capacity，否则连已测A单流都不符合。标称400Gb/s为50GB/s；pair payload约36.46GB/s，端口计数覆盖不证明链路打满。保留单流与同时活动流组合的有效service，若生产桥接实际影响决策，再增加必要的组合服务项或评分；不因本轮诊断重建求解器。

B非GDR涉及host-backed通信缓冲是日志及参考源码支持的机制解释，实际页NUMA和每stage字节未测；内置RCCL XML与sysfs的GPU1/NIC1位置不一致已取证，它会影响GDR及初始化affinity判定，尚未证明是唯一性能故障。这些均应作为路径条件记录，不擅自赋予未测DRAM bank容量。

## 收口与下一入口

本轮已获得重复共享退化、路径拆分缓解以及保持B非GDR的有效反事实，停止GPU扩矩阵。没有必要强制SYS、改生产XML、全面扫描channel/CPU或推断唯一硬件根因。当前证明的是隔离RCCL路径；生产keeper_serial的DRAM-staged路径、完整模型S5-P、规划增量和权重可用时延收益仍按既定最小应用桥接及e4/e5门槛验证。

最终命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/diagnosis/audit/review_runs.py --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_probe_r01 --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_contention_repro_r01 --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_recollect_split_r01/summary.json --run codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_recollect_split_boff_r01/summary.json --labels ORIGINAL G0 G1 G2 --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/diagnosis_audit_final_r02
```

本目录已存在，重跑使用新目录。静态driver review与CPU6项测试通过；审查脚本全部操作为本地读文件和独立新输出，不发HTTP/SSH、不初始化GPU。
