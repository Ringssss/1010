# 基础场景汇总

开发编号：v0.1.23.3/task2。输入run保持原身份与状态。CPU验证、前检、历史引用和bootstrap不计入新增baseline性能样本。

| run | 类型 | 状态 | 新实测/验收通过 | 失败或清理问题 |
| --- | --- | --- | ---: | --- |
| contracts_review_r01 | cpu_validation | passed | 0/0 | — |
| runtime_review_r01 | cpu_validation | passed | 0/0 | — |
| evidence_r01 | historical_reference | passed | 0/0 | — |
| runtime_preflight_r01 | preflight | failed | 0/0 | RuntimeError('8 retained endpoints unavailable') |
| runtime_preflight_r02 | preflight | failed | 0/0 | RuntimeError('8 retained endpoints unavailable') |
| retained_preflight_r01 | failure_record | failed | 0/0 | URLError ConnectionRefusedError errno111 |
| qwen_bootstrap_register_r01 | bootstrap | registered | 0/0 | — |
| qwen_bootstrap_r01 | bootstrap | passed | 0/0 | — |
| s2_r01 | scenario | passed | 2/2 | — |
| s3_r01 | scenario | passed | 2/2 | — |
| final_fixture_inspect_r01 | preflight | passed | 0/0 | — |
| protected_after_r01 | preflight | failed | 0/0 | RuntimeError('8 retained endpoints unavailable') |

| 场景/方法/run | 状态 | T_ready(s) | 协调确认(s) | prepare/execute(s) | planning/postverify(s) | 完整SHA/文本 |
| --- | --- | ---: | ---: | --- | --- | --- |
| S2/janus_host/s2_r01 | passed | 1.503806 | 1.660651 | 0.138175/0.837673 | 0.000138/85.673606 | True/True |
| S2/resource_lp/s2_r01 | passed | 1.330876 | 1.415874 | 0.162410/0.836650 | 0.002092/84.821098 | True/True |
| S3/janus_d2d/s3_r01 | passed | 2.559229 | 2.716145 | 0.081626/2.035478 | 0.000142/46.894992 | True/True |
| S3/resource_lp/s3_r01 | passed | 2.443636 | 2.596220 | 0.086814/2.040021 | 0.002239/52.934944 | True/True |

S2：源node27→目标node27，slot=target_a。

s2_r01：janus_host, resource_lp 的有序操作相同；标记 same_ordered_decision_not_algorithm_gain。实测差值可以包含规划开销与单次波动，不写成算法增益。

S3：源node27→目标node29，slot=target_b。

s3_r01：janus_d2d, resource_lp 的有序操作相同；标记 same_ordered_decision_not_algorithm_gain。实测差值可以包含规划开销与单次波动，不写成算法增益。

S3源建立86.949962s，建立阶段IPC ready 0.211386s；这是共享源setup，不是独立S1 baseline。

原可访问keeper前后均为14个，VALID副本2196→2196，元数据一致=True；原不可访问worker集合保持一致=True。该检查比较epoch/session及VALID id/identity/TP/区间/tier/generation/state/leases/allocated等目录字段，排除动态headroom，不是内容重新哈希。

最终新平台检查=passed，可访问keeper=4，完整Qwen DRAM来源逻辑字节=70329718112。保留4keeper、完整65.5GiB DRAM及2个parked engine；S3新增HBM owner已清理，本汇总没有执行平台cleanup。

本轮只在同一场景内对照方法。S2是本地node27/target_a，S3是node27→node29/target_b；不能用两场景时延横向断言H2D优于D2D。同有序决策且execute近同支持功能平台通过，没有算法增量。

S3源复制前后生成仅支持存活与正确性，不是持续前台干扰测量。同一源跨方法共享，存活时间从首次源生成后累计，HBM byte-seconds从owner建立起累计到各case后；不相减成每方法独立成本，不补造cleanup尾段。bootstrap的native/target生成、全SHA及建立时间另列于summary.json，不能并入baseline性能。失败与未决状态保留。

新增恢复观察4个，其中内容、文本及run清理验收通过4个；历史引用新增样本0。

来源字节、物理账本、方法身份与冻结版本见summary.json；报告所用JSON/事件输入的读取时指纹及原run版本清单见inputs_manifest.json。追加引擎日志不参与指纹，指纹不声明永久不可变。物理free恢复按注册容差解释，不声称精确归零。
