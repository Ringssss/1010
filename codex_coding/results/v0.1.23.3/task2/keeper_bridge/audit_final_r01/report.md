# Keeper 桥接、冻结选择与完整模型功能独立审查

状态：passed；独立检查366项；新增GPU样本0。

独立复算冻结契约、CPU选择、真实小extent生产路径，以及指定时的完整模型功能smoke。物理共享资源与策略性能增量分别判断。

全部冻结来源保持一致。

T1_simple_control：strong_simple/joint完整布局与有序操作同解=True；复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。

T2_uneven_budget：strong_simple/joint完整布局与有序操作同解=True；复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。

T3_shared_resources：strong_simple/joint完整布局与有序操作同解=True；复制按独立allocation页计账，共同LP评价和rank_interleaved顺序通过；CPU结果不增加GPU样本。

## 生产 keeper 路径桥接

仅引用三个正式小extent配置各一次观察，另有32MiB pair预热；审查新增GPU样本0。小extent以合法replica目标走原Cluster.transfer serial，不构成完整模型T_ready或算法增量。

| 配置 | payload B | receiver RPC包络 ms | worker接收端单op ms |
| --- | ---: | ---: | ---: |
| warmup_pairs（不计正式样本） | 67108864 | 5.220720 | 3.067087/2.957857 |
| single_A | 1073741824 | 45.164698 | 43.646280 |
| single_B | 1073741824 | 59.922063 | 58.526945 |
| pair | 2147483648 | 69.974211 | 68.169260/66.077948 |

共同窗口为node29同host/boot的execute HTTP RPC包络，包含dispatch、response；不等于设备kernel占用或worker同步monotonic窗口。阶段coordinator时间含200ms空闲采样、inventory与日志落盘，均标instrumented；prepare、全SHA验证和publish不混为copy时间。

隔离微probe采用synthetic GPU buffer、独立pair communicator与重复broadcast；应用桥接读取实际DRAM、源HBM staging、broadcast、目标land并每chunk同步。因此微probe36.46GB/s与0.85判据均不直接搬入应用服务模型。当前RPC时间没有提供可信worker共同窗口；生产执行器争用、S5-P和共享成本项保持未定。完整模型或同预算选源实测出现决策及T_ready差异后，再判断是否需要新增共享成本项。

NIC计数仅在execute前后读取，每HCA/port只计一次；idle窗口估计背景，不能排除突发其他用户流量。实际运行环境和完整初始化日志独立保留，GPU tensor接口不自动代表GDR。

实际NIC证据由原完整初始化日志的`Using [0]shca_0:1/IB`与独立counter复算连接。node29接收port结果如下，不累加两个worker看到的同口计数。

| 正式配置 | shca_0:1接收delta B | payload B | idle外推背景比例 |
| --- | ---: | ---: | ---: |
| single_A | 1,080,753,680 | 1,073,741,824 | 4.6784e-7 |
| single_B | 1,080,756,808 | 1,073,741,824 | 7.6580e-7 |
| pair | 2,161,381,452 | 2,147,483,648 | 3.0970e-7 |

其他HCA该窗口仅0或264B。全4keeper实际环境均选择`NCCL_IB_HCA=shca_0`，无`FLUIDCKPT_EXECUTE_PROFILE_DIR`；此处认证实际channel，而非仅靠请求环境推定模式。

| 生产pair路径 | node27 sender证据 | node29 receiver证据 | 可支持模式 |
| --- | --- | --- | --- |
| GPU0，9000→9000，nRanks02 | 31完整channel带GDRDMA；channel22被Torch logger撕裂 | 32完整接收channel全部GDRDMA | A使用GDR；不声称sender32行全部完整 |
| GPU1，36000→36000，nRanks02 | 32完整channel无GDRDMA | 32完整接收channel无GDRDMA | B非GDR |

端点bus号、two-rank communicator和send/receive方向匹配实际0→2、1→3路径，避免把其他communicator混合日志泛化成所有路径模式。两条生产路径仍是A-on/B-off；不因tensor接口称两流都直接GDR。

原worker JSON日志按本run精确tx/op_id匹配，同host wall间隔与各record单op monotonic elapsed差异均小于1ms，四正式接收op的差异实际在微秒级。pair两op wall起点差约58µs，仅为交叉核对；wall时钟不替代缺失的true worker monotonic共同窗口。instrumented coordinator execute分别268.829/283.744/294.105ms，含200ms idle采样和观测，不能替代表中45.165/59.922/69.974ms RPC包络。

## 完整模型功能smoke

T2固定split_rank_prefix、互斥target_b、fastest单次完整恢复T_ready=2.827939181s；全70,329,718,112逻辑字节SHA及固定生成输出通过；真实epoch/generation与catalog映射、共用rank_interleaved与worker实际操作顺序独立通过。

缓存建立18.203186s，规划0.028988s，prepare 0.192100s，ready后SHA 44.228262s。全DRAM底本与完整目标页分配各70,329,720,832B分别计账。实际缓存8GiB按6+2计账，unique6GiB、重复2GiB，建立traffic仍8GiB；执行只使用其中3份共6GiB缓存，其余2GiB副本仍占用并建立。

这个8GiB固定复制布局功能smoke与CPU两个策略各选4GiB的结果不同，不构成joint性能执行、策略对照或新算法收益。cache worker物理free恢复0；目标两卡各-6MiB在登记256MiB回收容差内，catalog/allocated/reserved恢复；完整底本、旧14可访问keeper及parked release证据通过。只保存离散内存采样，continuous physical peak未认证。

实际恢复按op复算读取HBM 6,442,450,944B、DRAM 63,887,267,168B，总70,329,718,112B。第四份2GiB复制副本没有成为本次恢复来源，其建立、分配和退役仍全部计账。这个观察区分了保活空间、复制建立成本与实际读取量，不能将8GiB physical cache说成8GiB有效读取。

## 审计修订与复现

`audit_bridge_cpu_r01`和`audit_bridge_cpu_r02`的failed结果保持：首次审核错误地将`Cluster.transfer`补入的`executor=serial`元字段视为计划改变；修正后第二次将被Torch logger撕裂的source channel22误判为完整无GDR行。最终只认证31完整sender行及32完整receiver行，未改原日志、GPU时序或SHA，未新增GPU样本。

审核脚本：[review.py](../../../../../src/v0.1.23.3/task2/keeper_bridge/audit/review.py)、[evidence.py](../../../../../src/v0.1.23.3/task2/keeper_bridge/audit/evidence.py)。5项关键CPU反例通过，覆盖cross-host/cross-boot monotonic、RPC overlap不能提升物理gate、复制重复计账与page-rounded location cap；builder的6/9项CPU契约作为实现验证另记录。主判定与下一步见[summary.json](summary.json)、[next_step.md](next_step.md)。本审计自身仅本地文件读取，不执行HTTP/SSH/GPU；引用GPU配置3项及完整功能恢复1项，不把预热、CPU同解或审核重试算作样本。

```bash
python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/audit/review.py --freeze codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_freeze_probe_r08/manifest.json --placement codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_compare_t1_r01/summary.json --placement codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_compare_t2_r02/summary.json --placement codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_compare_t3_r01/summary.json --bridge codex_coding/results/v0.1.23.3/task2/keeper_bridge/bridge_real_r01 --restore codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_smoke_t2_r01 --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/audit_final_r01
```

最终目录已经存在；复验须使用未使用的新输出目录。停止GPU扩矩阵，三场景同解不支持新solver或重复完整策略比较。当前生产路径正确性、实际资源回收与完整模型功能已具证据；应用物理共享归因、可靠预测profile和共享成本项仍需匹配决策证据。现证据不足以新增共享成本项，下一步方向见[next_step.md](next_step.md)。
