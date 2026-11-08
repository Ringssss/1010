# 两节点单发送端r02实际在线结果独审

完整执行、正确性、资源门与最终回收通过；整体在线成本模型未达到全部留出10%门。独立原件审核61项中60项通过，唯一失败为Hnear精度；不是GPU资源失败、SHA失败或cleanup失败。

| 场景 | 冻结预测 | 完整在线实测 | 相对误差 | 10%门 |
| --- | ---: | ---: | ---: | --- |
| C0_mixed | 用于共同outer校准 | 3.287488677s | 不作留出 | 不适用 |
| Hnear | 1.596965948s | 1.312200619s | +21.701356% | 失败 |
| Hremote | 4.957028519s | 4.560261343s | +8.700536% | 通过 |

六路径各1GiB预热及tail/1GiB各一次（6+12请求）完成，成本仅按具体path+操作尺寸，原生worker-order DAG保持。C0完整模型仅校准一次共同outer=0.8080300058203997s。模型/协议/所有calibration refs与冻结payload SHA核对通过；Hnear/Hremote请求分别在共同freeze后0.370277331s/112.155442059s开始，两份实际plan SHA与各自冻结plan一致。没有用留出重拟或补第三session。

C0及两个留出各完整66操作、70,329,718,112B逐operation SHA通过；全部verification_begin在controller ready之后，参考文本完全一致，两rank weights_ready均先于完整resume ACK的controller终点。每次真实物理park的engineallocated与整卡free增量双90%门通过。远端仅worker2，源NUMA4/7完整副本触页/注册及256页位置抽样、各remote sender本host串行区间覆盖通过。不认证复杂并发多NIC、三节点或SSD。

## 主要误差来自共同outer迁移

| 场景 | 路径+DAG预测 | 实际max worker execute | DAG差量 | 实际online减max execute | 固定outer多计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| C0 | 2.479458671s | 2.471381470s | +8.077ms | 0.816107207s | −8.077ms |
| Hnear | 0.788935943s | 0.778943557s | +9.992ms | 0.533257062s | +274.773ms |
| Hremote | 4.148998513s | 4.141526575s | +7.472ms | 0.418734768s | +389.295ms |

Hnear约96.49%的正向预测差量、Hremote约98.12%落在该outer差量；路径DAG数值分别仅高约1.28%/0.18%。这里online减max worker duration是诊断差量，包含控制与调度迁移，不能当作已经独立识别的因果成本项。没有相加两端worker时窗，也没有相减不同host的绝对时钟。

用同node27 monotonic时钟构成无重叠生命周期分段，C0/Hnear/Hremote开始至最早target_execute_begin分别为0.804985267/0.523328608/0.408757973s；最后copy至ACK分别约11.329/10.071/10.163ms。C0→Hnear的copy前窗下降281.657ms，足以解释主要outer变化。其内部观察为request→首rank load_begin下降166.540ms、rank到达偏差下降214.069ms；latest load→latest export基本相同（247.529/248.370ms），而latest export→latest mapped在Hnear增加98.254ms，部分抵消前两项。原始分段严格加回request→ACK完整实测，见breakdown.json。

C0是该incarnation首次恢复，随后两个请求的到达偏差更小；现有证据支持共同outer不稳定，但不能唯一归因首次调用、缓存、RPC、调度或IPC机制。当前不增加微项门、不改原预测、不从这两次留出保证未来误差。

## 实际布局差异、资源和回收

同一完整模型、相同预算/首1GiB HBM、手动来源赋值下，Hnear比Hremote少3.248060724s，时延约降低71.2253%，Hremote/Hnear约3.47528。预测差3.360062571s、差值误差112.001847ms（约3.44827%），排序一致。这是本轮两个预登记手动布局的比较，不是联合规划器优化结果；不能以排序正确覆盖Hnear整体精度失败。

helper live guard1823个0.5s配置样本：最低实际free6,981,419,008B，最高Torch reserved69,206,016B，均满足free≥2GiB/reserved≤2GiB。三次ready已知唯一tensor仅64MiB staging、allocated/reserved均67,108,864B；own fdinfo因超过128项读取上限返回available=false/unknown，完整本任务driver/RCCL HBM归属保持unknown。不能声称严格完整2GiB进程峰值认证；整卡foreign占用未计入已知tensor预算。任务cgroup memory.current最大采样node27为93,539,540,992B、node30为73,552,076,800B，分别低于192/96GiB；这些为采样值，不冒称连续峰值。

Catalog最后各tier used与transactions为空、来源lease无泄漏；四个准确自有supervisor final_owned_closure通过、最终两node pids.current=1/quiet，host passed=true且failure/cleanup_failure=null。engine原cleanup guard仍false（identity changed before KILL），最终exact leader/group/waitpid closure=true，两项证据分开保留，没有回写原guard。主随后准确停止两个新CID，实际Running=false/Pid0/OOMKilled=false；node30 final state从原stdout最后JSON行派生并记录raw SHA于node30_after_derived.json。旧源、旧run、共享XML及其他任务保持。

本独审只读取收集归档和已有CPU原件；新增GPU、SSH、Docker动作0，模型重拟0。
