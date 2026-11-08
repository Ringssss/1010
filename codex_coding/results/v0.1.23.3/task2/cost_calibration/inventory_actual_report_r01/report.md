# 实际库存查询根因与薄缓存对照

完整会话 execution_actual_r02 的主要负担是重复库解析。六worker每模式两次原字段库存查询后，仅复用库路径/CDLL句柄，把controller完整查询中位数从60.575624ms降至6.189593ms；HIP BDF和所有动态来源继续重新读取。不是传输带宽优化。

| 会话 | 模式/序号 | Controller ms | max worker ms | max 查库 ms | max transport+server ms | max deserialize ms |
|---|---|---:|---:|---:|---:|---:|
| execution_actual_r01 | legacy/0 | 86.394280 | 76.005711 | 62.344607 | 80.115416 | 0.128552 |
| execution_actual_r01 | legacy/1 | 71.732244 | 65.313307 | 59.644372 | 67.261113 | 0.109871 |
| execution_actual_r01 | cached/0 | 12.907823 | 2.675596 | 0.001380 | 7.636012 | 0.116732 |
| execution_actual_r01 | cached/1 | 11.351163 | 2.753337 | 0.001140 | 5.908739 | 0.114582 |
| execution_actual_r02 | legacy/0 | 61.584077 | 58.645706 | 55.737034 | 60.572693 | 0.084921 |
| execution_actual_r02 | legacy/1 | 59.567170 | 57.152060 | 54.688303 | 58.697128 | 0.071001 |
| execution_actual_r02 | cached/0 | 6.388856 | 2.692769 | 0.001710 | 5.846739 | 0.060260 |
| execution_actual_r02 | cached/1 | 5.990330 | 2.752396 | 0.001140 | 4.255587 | 0.067701 |

## 根因对照

execution_actual_r01：controller中位下降66.933769ms，最大查库/CDLL中位下降61.084511ms，比例0.912611。分段与整体改善对应。各round最大worker可能不同；这些中位差不能作为精确可加成本归因。
execution_actual_r02：controller中位下降54.386031ms，最大查库/CDLL中位下降55.289505ms，比例1.016612。分段与整体改善对应。各round最大worker可能不同；这些中位差不能作为精确可加成本归因。

r01 的查询对照在后续正常active事务被monitor错误当作idle拒绝前已经完整记录；该会话整体failed，保留为独立查询证据，不并入r02性能均值。r02是主完整运行，summary标为completed_pending_independent_review，完整执行/清理由独立review验收。

## 身份与实时性

48个A/B RPC的全部原字段、分段守恒、绑定BDF/boot/PID，以及每会话六worker的epoch/session/node/GPU/NUMA和完整静态runtime_identity均核对。缓存origin指向每worker前一次已计时legacy请求，而非窗外预热；cached中PID保持同一值、library_cache_hit=true、HIP BDF分段继续大于零。PID改变时重建仅由CPU夹具认证，实机没有强制PID变更。

两个会话每worker的headroom_sample_ns严格递增；cgroup current实际变化，DRAM headroom与部分RSS也变化。未改变的RSS/HBM free不意味着缓存；源代码每次调用native inventory和读取动态identity，CPU夹具已验证字段改变时刷新。空Catalog/事务的A/B阶段没有制造ledger变化，因此fresh ledger状态改变主要依据原native委派和CPU验证，不宣称该空库存对照验证了状态转换。

## 完整请求中的查询位置

| r02请求 | plan前库存 ms | transfer内库存 ms | execute ms | request→filled ms | request→published ms |
|---|---:|---:|---:|---:|---:|
| holdout_prediction | 5.553745 | 5.205240 | 23.142321 | 42.207327 | 114.391726 |
| holdout_legacy_control | 62.551200 | 60.326000 | 23.286452 | 156.026045 | 228.259556 |

完整请求保留两次fresh inventory。legacy−cached的两个库存窗差合计112.118215ms；filled差113.818718ms，published差113.867830ms，execute差0.144131ms。该同会话cached→legacy对照各一次、顺序固定，包含源/目标状态和顺序混杂；用来解释控制成本，不能报告统计稳定的传输收益或跨布局排序。

## 计时覆盖和证据

worker timer包含模式文件读取、原native库存、全部identity来源和原字段组装；结束后的诊断字典组装、HTTP JSON序列化与传输由controller完整库存窗覆盖。缓存填充已包含在origin legacy窗。独立诊断锁等待及Python计时/结构开销包含worker residual。controller−max worker是持续时间残差，含线程启动、HTTP及调度等，不能精确命名dispatch；不跨进程直接减时间戳。模式切换、进程/mesh初始化不在A/B查询窗内；所有样本此前已初始化且warm，结果不代表首次启动或cold库/链路成本。

本报告只读既有原件、无GPU/SSH/新采样。329项本地审计通过；精确48个RPC及全部service分段见per_rpc.csv，cache origin/PID/动态值、holdout 24个RPC计时和源/结果SHA见summary.json。原source SHA和三节点各会话baseline deployed assets均匹配。
