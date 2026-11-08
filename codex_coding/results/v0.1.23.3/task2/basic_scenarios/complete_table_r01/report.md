# S1–S5 × baseline 完整证据表

开发编号：v0.1.23.3/task2。单元格表示当前证据状态；时延为目标同主机权重可用窗口。小表不混合目标或预算求均值。

新=输入run的独立新实测；历史=保留原run身份的引用；历史相关=场景仅部分覆盖；别名不计独立样本。未适配、未测、不适用、明确不可行和缺输入分开记录。

| 场景 | SGLang 原版加载/native | HIP IPC 适配 | Janus 原版 | Janus warm 机制 | Janus D2D 机制 | strong simple | Fluid LP | BlitzScale 原版 | BlitzScale 适配 | ServerlessLLM 原版 | ServerlessLLM 适配 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | 未适配 | 新 0.363469s, 0.148797s (n=2) | 未适配 | 未测 | 未测 | 未测 | 未测 | 未适配 | 未适配 | 未适配 | 未适配 |
| S2 | 未适配 | 不可行 | 未适配 | 新 1.503806s (n=1) | 未测 | 决策别名/时延未测 | 新 1.330876s (n=1) | 未适配 | 未适配 | 未适配 | 未适配 |
| S3 | 未适配 | 不可行 | 未适配 | 未测 | 新 2.559229s (n=1) | 决策别名/时延未测 | 新 2.443636s (n=1) | 未适配 | 未适配 | 未适配 | 未适配 |
| S4 | 未适配 | 不可行 | 未适配 | 新 1.388548s (n=1) | 未测 | 新 1.168985s (n=1) | 新 1.267901s (n=1) | 未适配 | 未适配 | 未适配 | 未适配 |
| S5-P 物理共享 | 未适配 | 未测 | 未适配 | 未测 | 未测 | 未测 | 未测；历史软件相关 n=4 | 未适配 | 未适配 | 未适配 | 未适配 |
| S5-S rank_interleaved | 未适配 | 未测 | 未适配 | 未测 | 未测 | 新 2.645741s, 2.867578s (n=2) | 未测 | 未适配 | 未适配 | 未适配 | 未适配 |
| S5-S target_major | 未适配 | 未测 | 未适配 | 未测 | 未测 | 新 4.461190s, 3.672967s (n=2) | 决策别名/时延未测 | 未适配 | 未适配 | 未适配 | 未适配 |

详细数值、run/target/预算/来源/完整性证明与别名见 measurements.csv 和 summary.json。

| 场景/变体 | 方法 | run / 原标签 | 目标 | T_ready(s) | 独立/新增 | 证据 |
| --- | --- | --- | --- | ---: | --- | --- |
| S1 | sglang_ipc_hip | e1_c02_qwen_ipc_r01 / e1_c02_qwen_ipc_r01_ipc_hit0 | m03r2n07 | 0.160595298 | 1/0 | 历史引用 |
| S1 | sglang_ipc_hip | e1_c02_qwen_ipc_r01 / e1_c02_qwen_ipc_r01_ipc_hit1 | m03r2n07 | 0.153791771 | 1/0 | 历史引用 |
| S4 | resource_lp | e3_c02_qwen_r01 / concentrated | target_a | 1.277976811 | 1/0 | 历史引用 |
| S4 | resource_lp | e3_c02_qwen_r01 / concentrated | target_b | 3.798325372 | 1/0 | 历史引用 |
| S4 | resource_lp | e3_c02_qwen_r01 / distributed | target_b | 2.624750835 | 1/0 | 历史引用 |
| S4 | resource_lp | e3_c02_qwen_r01 / distributed | target_a | 1.237442493 | 1/0 | 历史引用 |
| S5 | resource_lp | e3_c02_qwen_r01 / concentrated | target_a | 1.277976811 | 1/0 | 历史引用 |
| S5 | resource_lp | e3_c02_qwen_r01 / concentrated | target_b | 3.798325372 | 1/0 | 历史引用 |
| S5 | resource_lp | e3_c02_qwen_r01 / distributed | target_b | 2.624750835 | 1/0 | 历史引用 |
| S5 | resource_lp | e3_c02_qwen_r01 / distributed | target_a | 1.237442493 | 1/0 | 历史引用 |
| S4 | fastest | e4_c01_qwen_r01 / 8gib_greedy | target_a | 1.307431102 | 1/0 | 历史引用 |
| S4 | fastest | e4_c01_qwen_r01 / 8gib_greedy | target_b | 2.483471737 | 1/0 | 历史引用 |
| S2 | fastest | e6_c01_qwen_curve_r01 / 0gib_greedy | target_a | 1.269331478 | 1/0 | 历史引用 |
| S2 | fastest | e6_c01_qwen_curve_r01 / 0gib_greedy | target_b | 2.770961454 | 1/0 | 历史引用 |
| S4 | fastest | e6_c01_qwen_curve_r01 / 8gib_greedy | target_a | 1.292621019 | 1/0 | 历史引用 |
| S4 | fastest | e6_c01_qwen_curve_r01 / 8gib_greedy | target_b | 2.501614807 | 1/0 | 历史引用 |
| S4 | fastest | e6_c01_qwen_curve_r01 / 16gib_greedy | target_a | 1.291709719 | 1/0 | 历史引用 |
| S4 | fastest | e6_c01_qwen_curve_r01 / 16gib_greedy | target_b | 2.348826159 | 1/0 | 历史引用 |
| S4 | resource_lp | e6_c01_qwen_curve_r01 / 16gib_joint | target_a | 1.267926745 | 1/0 | 历史引用 |
| S4 | resource_lp | e6_c01_qwen_curve_r01 / 16gib_joint | target_b | 2.355012325 | 1/0 | 历史引用 |
| S1 | sglang_ipc_hip | s1_r01 / s1_r01_ipc_hit0 | m03r2n07 | 0.363468802 | 1/1 | 新实测 |
| S1 | sglang_ipc_hip | s1_r01 / s1_r01_ipc_hit1 | m03r2n07 | 0.148797435 | 1/1 | 新实测 |
| S2 | janus_host | s2_r01 / basic_3edadcae_s2_r01_janus_host_0 | node27 | 1.503805855 | 1/1 | 新实测 |
| S2 | resource_lp | s2_r01 / basic_3edadcae_s2_r01_resource_lp_0 | node27 | 1.330876237 | 1/1 | 新实测 |
| S3 | janus_d2d | s3_r01 / basic_3edadcae_s3_r01_janus_d2d_0 | node29 | 2.559228948 | 1/1 | 新实测 |
| S3 | resource_lp | s3_r01 / basic_3edadcae_s3_r01_resource_lp_0 | node29 | 2.443636225 | 1/1 | 新实测 |
| S4 | janus_host | s4_r01 / janus_host_target_major | node27 | 1.388548347 | 1/1 | 新实测 |
| S4 | fastest | s4_r01 / fastest_target_major | node27 | 1.168984821 | 1/1 | 新实测 |
| S4 | resource_lp | s4_r01 / resource_lp_target_major | node27 | 1.267901181 | 1/1 | 新实测 |
| S5/software_worker_serial/fastest_target_major | fastest | s5_r01 / fastest_target_major | node29 | 4.461190392 | 1/1 | 新实测 |
| S5/software_worker_serial/fastest_rank_interleaved | fastest | s5_r01 / fastest_rank_interleaved | node29 | 2.645741278 | 1/1 | 新实测 |
| S5/software_worker_serial/fastest_rank_interleaved | fastest | s5_r02 / fastest_rank_interleaved | node29 | 2.867577637 | 1/1 | 新实测 |
| S5/software_worker_serial/fastest_target_major | fastest | s5_r02 / fastest_target_major | node29 | 3.672967243 | 1/1 | 新实测 |

SGLang冷加载/native未完成统一权重窗口测量，未标为不可行；Janus warm/D2D为共同执行器机制。历史强简单/Fluid别名保留原决策与样本来源。S3源建立IPC接入不是独立S1样本；bootstrap不是baseline性能样本。

S4若使用共同8GiB缓存fixture，Janus host的实际取数仍可全部来自DRAM；共享fixture预算用于同条件时延对照，不作为Janus单独方案的最低HBM保活空间。详细表保留实际来源字节与共享缓存预算。

S2：same_ordered_decision_not_algorithm_gain；单轮T_ready观察差值0.172929618s。
S3：same_ordered_decision_not_algorithm_gain；单轮T_ready观察差值0.115592723s。
S4：single_configuration_observation_not_algorithm_gain；单轮T_ready观察差值0.219563526s。
S4：single_configuration_observation_not_algorithm_gain；单轮T_ready观察差值0.120647166s。
S4：same_ordered_decision_not_algorithm_gain；单轮T_ready观察差值-0.098916360s。

s5_r01 软件执行控制：顺序fastest_target_major → fastest_rank_interleaved；S5/software_worker_serial/fastest_target_major=4.461190392s，S5/software_worker_serial/fastest_rank_interleaved=2.645741278s，观察差值1.815449114s。来源、字节、布局、目标与预算匹配；algorithm_gain=false，物理共享资源仍未测。

s5_r02 软件执行控制：顺序fastest_rank_interleaved → fastest_target_major；S5/software_worker_serial/fastest_rank_interleaved=2.867577637s，S5/software_worker_serial/fastest_target_major=3.672967243s，观察差值-0.805389606s。来源、字节、布局、目标与预算匹配；algorithm_gain=false，物理共享资源仍未测。

原版SGLang、Janus、BlitzScale、ServerlessLLM均未完成本平台baseline运行适配/测量；源码获取与这些机制结果不替代原版复现。

serverlessllm_whole_model_loading_and_target：decision_only_not_runtime，整模型pinned命中/磁盘等待估价与迁移候选节点排序；保留原版分支与稳定排序；executable_restore_plan=false，没有端到端恢复性能样本。
blitzscale_cache_and_tp_distribution：source_audit_only，源码具备机器缓存TTL/已加载rank、NVLink semaphore准入及完整TP组分发；本轮只审查；executable_restore_plan=false，没有端到端恢复性能样本。

最终保护检查：原可访问14个keeper的VALID目录与epoch/session元数据保持一致=True；原不可访问集合保持一致=True。旧node27的8个拒绝连接端点延续既有失效，未当作本轮损坏；此处没有重新哈希旧内容。
平台检查stage2_final_fixture_r01：status=passed，可访问workers=[0, 1, 2, 3]，不可访问=[]；不计性能样本。
平台检查stage2_protected_after_r01：status=failed，可访问workers=[8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]，不可访问=[0, 1, 2, 3, 4, 5, 6, 7]；不计性能样本。
平台检查s5_control_review_r01：status=passed，可访问workers=[]，不可访问=[]；不计性能样本。

历史引用与别名均不增加新样本；一个原始观测映射多个场景仅计一次。；原版、硬件适配与共同执行器机制保持分列。；不同目标、预算、格式或合法来源不计算比较；同操作不宣称算法增益。；S5软件串行控制不是物理出口争用实验。；新实测数来自输入run；此CPU汇总没有新增GPU样本。

唯一历史独立观察=16，输入新观察=13，新观察验收通过=13；本汇总新增GPU样本=0。
