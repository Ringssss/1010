四布局已通过 116 项独立检查，绑定 48 份原件，完成本轮选定设备组合的路径与时间认知验收。实际运行保持原 Worker/Cluster.transfer、32MiB chunk、64MiB staging；A/B/C/D 各一次，目标是两个 16MiB synthetic 块。

| 布局 | 恢复路径 | prepare ms | execute wall ms | filled ms | 验证发布 ms | 全部发布 ms |
|---|---|---:|---:|---:|---:|---:|
| A | 28→28、30→30 | 2.362221 | 1.840575 | 4.203326 | 45.677803 | 49.881059 |
| B | 28→28、28→30 | 2.181089 | 3.631409 | 5.813058 | 64.031048 | 69.844086 |
| C | 30→28、30→30 | 2.291970 | 3.376436 | 5.668976 | 61.792258 | 67.461154 |
| D | 30→28、28→30 | 2.244860 | 4.663133 | 6.908463 | 39.719302 | 46.627805 |

实测前冻结的路径、source/target、role、bytes、每端顺序、epoch/generation/identity、两块全 SHA 均符合。A 两端 local 可独立推进；B 在 28 local0 后 forward1；C reverse0 后在 30 local1；D reverse0 后 forward1。D 的两方向顺序执行。实际有效网络 payload 为 0/16/16/32MiB。每布局实缓存 32MiB，位置允许上限均 32MiB，DRAM 底本32MiB、目标32MiB与 staging128MiB单列。建立、恢复和退役库存均 lease0、transactions空；退役后 HBM catalog 计账0，底本最终退役。

本次 filled 为 A<C<B<D，支持 A 最短和 D 最长两个弱假设。B/C 原先排序未知，本次 C 比 B 少 0.144082ms。A→D filled 增加 2.705137ms，execute wall 增加 2.822558ms，prepare 减少 0.117361ms；差异主要进入执行阶段。对应原始 op record 证实 reverse/forward 依赖顺序。

全部发布为 D<A<C<B。D 的验证发布39.719302ms，小于 A 的45.677803ms；A/D 两端 SHA 实际读取分布均32/32MiB，仍相差5.958501ms。B/C 两端读取分布48/16与16/48MiB，是发布阶段变量之一。现有记录支持阶段分解与排序判断，唯一差异原因尚未识别。worker elapsed 含调用、对端等待及同步；同方向也随布局变化，两端窗口重叠。

两端原 worker 的实际 communicator ID 均为 0x47c0a7c7dd419ea5。实际 IBext_v8、shca_0:1、nNodes2、gdr0、28GPU0/BDF09/NUMA0与30GPU4/BDF85/NUMA4的完整GPU/NIC XML链通过严格 raw→派生视图核对。C/D 的完整实际反向 payload 与 B/D 正向 payload 均全SHA通过。runtime内部host页驻留仍未知；gdr0与XML无法提供页位置。

| 资源/退出 | node28 | node30 |
|---|---:|---:|
| 启动前 cgroup baseline B | 19,976,192 | 33,345,536 |
| 生命周期采样最大 B | 2,138,234,880 | 2,122,567,680 |
| 观测新增 DRAM B | 2,118,258,688 | 2,089,222,144 |
| 原2GiB预算内观测余量 B | 29,224,960 | 58,261,504 |
| 高于含8MiB余量准入估算 B | 13,242,368 | 9,048,064 |
| exact PID/start_ticks | 214 / 488590799 | 217 / 488591600 |
| 生命周期采样数 | 101 | 102 |

两端均接到 matching plan SHA 的 STOP，精确 TERM 后 absent、groupempty；补收时 fresh boot/PIDnamespace/owner SHA 再次核实。后 guard 为 pids1/noOOM，原 keeper owners/catalogs、原厂XML/default mapping/输入XML SHA 保持。每0.25s采样给出观测最大值，kernel memory.peak缺失；两端观测均高于准入估算，估算与8MiB经验余量应继续作为准入措施记录。

原GPU应用与controller、两supervisor均rc0。外层首次本地解包因不支持 TarFile.extractall 的 filter 参数返回rc1，原 operations_run_r01 的 stale prelaunch 标签与 operations_collect_r01 部分证据保持。operations_collect_r02 独立补收完成，GPU_launches=0、signals=0；执行过的 f315 控制源码原件与 6c740 plan SHA 均保留。旧remote64超预算失败SHA未变。

本次验收范围是同会话、固定ABCD顺序、当前共享负载下的有限块恢复观察。地图选定GPU/NIC路径、双方向payload正确性和弱filled关系已验证；完整模型T_ready、正式容量profile、顺序无关稳定性能和solver增量资格均保持false。无需为本次结论追加GPU复核。

机器结果：qualification.json。逐文件SHA：evidence_refs.json。派生路径：node{28,30}.runtime_path.json及topology.parse_view.{xml,json}。脚本入口：codex_coding/src/v0.1.23.3/task2/layout_validation/review/review_final.py；输入为operations_collect_r02。
