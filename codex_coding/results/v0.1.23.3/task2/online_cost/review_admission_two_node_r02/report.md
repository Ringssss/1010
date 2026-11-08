# 两节点owned HIP准入独审

开发编号v0.1.23.3/task2。实际 `execution_host_admission_two_node_r02` 独立重建104项通过。reviewer没有GPU/SSH执行，新增模型恢复样本0。

target27/source30的精确新容器ID、StartedAt、boot、image与setup r02一致；两节点各selected GPU4/5的actual HIP BDF85/b5、PCI/host NUMA4/7一致。每卡total68,702,699,520B、free68,365,058,048B、整卡used上界337,641,472B。target每卡free通过52GiB门；source30两个selected card合计675,282,944B，加256MiB margin仍小于2GiB。

两节点查询前后所有129冻结资产、hard DRAM cap、pids.current=1、所有old容器identity及完整mount多重集合、原XML SHA均保持。GPU查询明确只在精确新容器执行；参数为5个whitelist字段和两个selected角色，序列化小于16KiB，GPU源码不带base64资产到argv。旧admission r01的E2BIG发生于spawn之前、未产生GPU查询，保留失败；本次是独立新结果目录。

这里只准入空owned HIP context，尚未包含RCCL mesh、完整模型、HBM cache和calibration target。实际Run仍须postmesh aggregate upper、source/target重载期间资源门、Catalog/lease与精确停止验收。真实whole-online预测及恢复性能资格保持false，等待C0和Hnear/Hremote原始结果。

重现命令：`python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/audit_admission.py --setup codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_setup_two_node_r02 --prepared codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_two_node_r01 --admission codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_admission_two_node_r02 --out codex_coding/results/v0.1.23.3/task2/online_cost/review_admission_two_node_r02`。重审使用新review输出目录，既有目录拒绝覆盖。
