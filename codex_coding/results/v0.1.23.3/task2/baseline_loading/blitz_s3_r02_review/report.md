# BlitzScale S3 r02 独立验收

验收结果：**passed**，25/25 项证据检查通过。

实际恢复完整 Qwen TP2：70,329,718,112 bytes（65.49965 GiB）；node27 GPU0→node29 GPU0、node27 GPU1→node29 GPU1，来源为本 run 完整活跃 HBM 逻辑实例。

源码策略为 Steersman 缓存准入 + 非 unidirect sender 顺序 + source pop + TP stride；没有 fastest/LP 测量阶段取源或后备替换。当前唯一 source/target，单目标分发退化为按 TP rank 的两条 P2P。

4条实际执行records包含2源/2目标，每条op两端字节一致。两个rank内容身份和SHA不同，各自source/target全量SHA相同，且与源owner建立时的SHA相同。ready时观察到每源1个read lease，校验/目标释放后lease归零；source generation/epoch/content保持。

目标主机同一host原始rank事件独立重算 T_ready=2.804378593s；请求→ready ack=2.910670s，请求→首正确token=3.292748s。

在线inventory+plan=0.033446s，plan=0.029280s，prepare=0.184835s，copy执行wall=2.161819s，后置全SHA=43.652801s。这些来自不同阶段的窗口，不强行相加重建主指标。

源setup总wall=121.423650s，其中owner构建协调窗口=115.056768s、copy=0.816738s、IPC加载窗口=0.223506s。源HBM=65.49965GiB，IPC消费者共享同一owner allocation；此专门建立的source计入增量驻留成本。

源setup/复制后的两次32token输出与目标输出完全一致。报告源存活=59.128044s，owner byte-seconds=12698145109704.385；该值结束于postcopy source check，不包含最后cleanup尾部。

清理独立比较：4worker catalog HBM从0返回0，allocated/reserved delta均0；源worker物理free各+18MiB，目标worker各−6MiB，在256MiB容差内。无残留transactions、owned IDs、pending resume或未释放consumer；DRAM底本与无关catalog签名一致，cleanup_errors/source_hash_mismatches为空。

| 既有S3方法 | T_ready(s) | 路由/内容/源tier相同 | executor/chunk相同 |
|---|---:|---|---|
| janus_d2d | 2.559228948 | True | True |
| resource_lp | 2.443636225 | True | True |

本次与既有S3的实际执行路径相同；独立source/cache/TP策略已可执行，但该拓扑未产生不同决策，不把时延差记为算法或拓扑增益。身份限定为BlitzScale分发机制HIP适配，不是原版live-serving或完整广播性能。

r01系统Python缺numpy，在活跃源构建规划阶段失败，未执行GPU恢复；r02使用已有conda成功。原始失败记录保留，不将r01计入性能观察。

逐rank分项、原始SHA、执行records、全部检查与输入文件hash见review.json。审查只读既有文件，没有发起worker RPC或GPU工作。
