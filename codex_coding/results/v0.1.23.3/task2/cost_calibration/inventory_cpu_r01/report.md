# 库存诊断覆盖层的本地准备结果

新增入口位于 `codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/worker.py`。采用原 RuntimeWorker 子类并原样调用 native.inventory，使用原 native.main HTTP handler。旧 worker SHA256 保持 `b9484b62a7b4ae397b412e995435c90534b620b66253b519ebe1b56b2904104c`。

本地 `cpu_check.py` 11 项通过：AST提取原 runtime_identity，验证全部字段与值等价；假HIP下cached仍调用BDF；假sysfs/proc变动后cgroup current、RSS、PCI NUMA更新；PID不一致重建cache。`cpu_worker_check.py` 10 项通过：AST提取覆盖层类，以假native验证每请求调用原native一次、compact透传、Catalog/epoch/事务与host allocation历史更新、模式切换、timer守恒，以及cached显式gate和模式schema拒绝。两项结果分别为 `qualification.json` 和 `worker_qualification.json`。没有import torch或调用真实HIP，不构成实机资格。

命令：

```sh
python3 codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/cpu_check.py --out codex_coding/results/v0.1.23.3/task2/cost_calibration/inventory_cpu_r01/qualification.json
python3 codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/cpu_worker_check.py --out codex_coding/results/v0.1.23.3/task2/cost_calibration/inventory_cpu_r01/worker_qualification.json
python3 codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/probe_find_library.py --out codex_coding/results/v0.1.23.3/task2/cost_calibration/inventory_local_primitive_r01/probe.json --iterations 2
```

本地纯stdlb查库原语在 `m03r2n07` 分别耗时22.693543与12.078992ms，库查找返回null。此本地环境与实际任务容器不同，且未调用CDLL/native inventory，不能用于断言约62ms真实库存查询根因，也不能用作性能改善数值。实际容器分段与legacy/cached对照由统一execution负责，本目录此时未进行SSH、远端写或GPU操作。
