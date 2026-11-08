<!-- TASK2_DETAILED_EVIDENCE_BEGIN -->

## 附录 H：从原始结果自动摘录的可核对证据

本附录由`codex_coding/src/diag/extract_task2_archive_evidence.py`离线生成，
仅摘录已有结果，不重新求解、不启动GPU、不覆盖原summary/analysis。
对应机器可读数据与输入SHA256见`codex_coding/results/task2/archive_20260929/detailed_evidence.json`。

### H.1 placement01协调器时间线

下表时间为协调器events.time转Asia/Shanghai，日期2026-09-28；不是跨主机monotonic相减。

| 时间 | event | phase/path/说明 |
| --- | --- | --- |
| 21:59:16.716 | isolated_container_created | node28 |
| 21:59:17.370 | isolated_container_created | node29 |
| 21:59:18.277 | preflight_passed |  |
| 21:59:52.843 | mesh_ready |  |
| 22:02:30.705 | model_captured |  |
| 22:02:36.690 | model_gpu_replicas_ready |  |
| 22:05:00.071 | model_migrated_to_node28 |  |
| 22:05:08.255 | path_calibrated | network:dram |
| 22:05:11.711 | path_calibrated | network:hbm |
| 22:05:16.432 | path_calibrated | local_dram:dram |
| 22:05:21.894 | path_calibrated | local_hbm:hbm |
| 22:05:24.356 | placements_solved | balanced_local |
| 22:07:27.110 | weights_transferred | placement_existing |
| 22:10:49.547 | model_restore_passed | placement_existing |
| 22:11:10.148 | optimized_layout_ready | balanced_local |
| 22:12:48.216 | weights_transferred | placement_optimized |
| 22:14:35.536 | model_restore_passed | placement_optimized |

### H.2.1 placement_existing：实际操作及来源

JSON路径：summary.restores[].flow.plan.ops；每行是逻辑区间，n和off单位均为bytes。

| op | src→dst | replica_id | off | n | source_offset | kind |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 0 | 4→6 | model/remote/0 | 0 | 1073741841 | 0 | network |
| 1 | 6→6 | model/joint/0 | 1073741841 | 1073741824 | 0 | local_dram |
| 2 | 2→6 | model/gpu/0 | 2147483665 | 3221225472 | 1073741824 | network |
| 3 | 4→6 | model/remote/0 | 5368709137 | 29796149919 | 5368709137 | network |
| 4 | 5→7 | model/remote/1 | 0 | 1073741841 | 0 | network |
| 5 | 7→7 | model/joint/1 | 1073741841 | 1073741824 | 0 | local_dram |
| 6 | 3→7 | model/gpu/1 | 2147483665 | 3221225472 | 1073741824 | network |
| 7 | 5→7 | model/remote/1 | 5368709137 | 29796149919 | 5368709137 | network |

| op | source角色耗时s | target/both角色耗时s |
| ---: | ---: | ---: |
| 0 | 0.045159013 | 0.042918218 |
| 1 | 0.023963793 | 0.023963793 |
| 2 | 0.178920682 | 0.108782384 |
| 3 | 1.293381337 | 1.161719411 |
| 4 | 0.046304045 | 0.044637778 |
| 5 | 0.024214705 | 0.024214705 |
| 6 | 0.186000154 | 0.114244344 |
| 7 | 1.306329489 | 1.169002232 |

源/目标两列可并行且起止点不同，不能相加；both为同一worker，重复显示不是两次拷贝。
实际跨机总字节：68,182,234,464（63.49965414 GiB）。
全量校验：70,329,718,112 bytes；以下为其中一个op的原始记录：

```json
{
  "op_id": 2,
  "n": 3221225472,
  "source_sha256": "e850fed6f22c8c0d779c325173070a59e85a15b780bbc69666ab50c4a0be2998",
  "target_sha256": "e850fed6f22c8c0d779c325173070a59e85a15b780bbc69666ab50c4a0be2998"
}
```


### H.2.2 placement_optimized：实际操作及来源

JSON路径：summary.restores[].flow.plan.ops；每行是逻辑区间，n和off单位均为bytes。

| op | src→dst | replica_id | off | n | source_offset | kind |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 0 | 4→6 | model/remote/0 | 0 | 1073741841 | 0 | network |
| 1 | 6→6 | optimized/1 | 1073741841 | 4294967296 | 0 | local_hbm |
| 2 | 6→6 | optimized/0 | 5368709137 | 1073741824 | 0 | local_dram |
| 3 | 4→6 | model/remote/0 | 6442450961 | 28722408095 | 6442450961 | network |
| 4 | 5→7 | model/remote/1 | 0 | 1073741841 | 0 | network |
| 5 | 7→7 | optimized/3 | 1073741841 | 4294967296 | 0 | local_hbm |
| 6 | 7→7 | optimized/2 | 5368709137 | 1073741824 | 0 | local_dram |
| 7 | 5→7 | model/remote/1 | 6442450961 | 28722408095 | 6442450961 | network |

| op | source角色耗时s | target/both角色耗时s |
| ---: | ---: | ---: |
| 0 | 0.044243573 | 0.042277064 |
| 1 | 0.036058360 | 0.036058360 |
| 2 | 0.023937483 | 0.023937483 |
| 3 | 1.180071371 | 1.121156477 |
| 4 | 0.044503006 | 0.043663852 |
| 5 | 0.039479660 | 0.039479660 |
| 6 | 0.024236894 | 0.024236894 |
| 7 | 1.201747008 | 1.139147026 |

源/目标两列可并行且起止点不同，不能相加；both为同一worker，重复显示不是两次拷贝。
实际跨机总字节：59,592,299,872（55.49965414 GiB）。
全量校验：70,329,718,112 bytes；以下为其中一个op的原始记录：

```json
{
  "op_id": 0,
  "n": 1073741841,
  "source_sha256": "19f7d7b10083bcada91fdb7a8a28e73d013a0192e235d8e445418a1ff59c3dac",
  "target_sha256": "19f7d7b10083bcada91fdb7a8a28e73d013a0192e235d8e445418a1ff59c3dac"
}
```

### H.3 完整窗口变化的加法账本

定义Δ=旧−新；正数表示该阶段缩短，负数表示该阶段变长。这是观察差值，不是随机化因果估计。

| 阶段 | 旧s | 新s | Δ ms |
| --- | ---: | ---: | ---: |
| entry_and_start_skew | 0.003083107 | 0.076077562 | -72.994455 |
| layout | 0.318216568 | 0.305098325 | 13.118243 |
| export | 0.034879113 | 0.034776522 | 0.102591 |
| detect_plan_dispatch | 0.201219648 | 0.042266295 | 158.953353 |
| map | 0.187918773 | 0.071878770 | 116.040003 |
| execute_dispatch | 0.007210971 | 0.008381256 | -1.170285 |
| last_copy_worker_execute | 1.353961939 | 1.248007481 | 105.954458 |
| after_all_copies_until_ready | 0.027058970 | 0.026189275 | 0.869695 |
| 合计 | 2.133549089 | 1.812675486 | 320.873603 |

coordinator execute的106.294ms改善与目标时间线执行段的105.954ms改善口径略有不同，不再次叠加。

### H.4 compact控制面原始字段

| 指标 | legacy | compact |
| --- | ---: | ---: |
| plan_wall_s | 0.21794445673003793 | 0.008241783827543259 |
| planner_request_bytes | 3548589 | 2203 |
| planner_response_bytes | 591005 | 1833 |
| inventory_response_bytes | 4738500 | 11796 |
| prepare_request_bytes | 4756312 | 635856 |
| control_json_bytes | 13636747 | 654025 |
| control_rpc_calls | 41 | 33 |
| epoch_snapshot_reused | False | True |

### H.5 profile01线程与CPU调用证据

wall、线程CPU和runqueue不是互斥相加项；runqueue只描述被采样的执行线程，不覆盖所有RCCL后台线程。

| worker | execute wall s | thread CPU s | runqueue ms | 同步调用数 | 同步self s |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 1.751293268 | 0.431521185 | 0.118772 | 921 | 1.167503868 |
| 5 | 1.916278808 | 0.408221924 | 0.289984 | 921 | 1.394740364 |
| 6 | 1.757714098 | 0.648572491 | 0.111397 | 1049 | 1.081770597 |
| 7 | 1.921868889 | 0.648214619 | 0.074727 | 1049 | 1.298896122 |

### H.6 补测trace完整性与厂商标签纠错

| worker | op | 应拷贝bytes | trace拷贝bytes | 通过 |
| ---: | ---: | ---: | ---: | --- |
| 4 | 0 | 8589934609 | 8589934609 | True |
| 5 | 1 | 8589934609 | 8589934609 | True |
| 6 | 0 | 8589934609 | 8589934609 | True |
| 7 | 1 | 8589934609 | 8589934609 | True |

以下为原trace字段摘录，非重构的示意日志：

```json
{
  "copy_name": "DeviceToHost",
  "copy_args": {
    "BeginNs": "1790600477510648955",
    "EndNs": "1790600477510700155",
    "devId": 0,
    "queueId": "0",
    "pid": 104,
    "tid": 0,
    "procId": 337,
    "index": 35,
    "DurationNs": 51200,
    "Throughput": "610.351562 G/s",
    "source memory kind": "Device",
    "destination memory kind": "Device",
    "Bytes": 33554432,
    "kind": 4595
  },
  "correlated_api_args": {
    "BeginNs": "1790600477509939151",
    "EndNs": "1790600477510003521",
    "pid": 104,
    "tid": 337,
    "name": "34",
    "args": "hipMemcpyAsync(dst=0x7ef385c00000, src=0x7ef5dde00000, sizeBytes=33554432, kind=3, stream=0)",
    "index": 35,
    "state": 0,
    "DurationNs": 64370
  }
}
```

### H.7 实际worker日志摘录

来源：placement01/worker_node29_0.log；精确匹配新布局transaction及op，保留原行。

```jsonl
{"ev": "cluster_op_begin", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 0, "src": 4, "dst": 6, "n": 1073741841, "kind": "network", "t": 1790604766.9542038, "node": "node29", "gpu": 0}
{"ev": "cluster_op_end", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 0, "elapsed_s": 0.042280054185539484, "t": 1790604766.99648, "node": "node29", "gpu": 0}
{"ev": "cluster_op_begin", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 1, "src": 6, "dst": 6, "n": 4294967296, "kind": "local_hbm", "t": 1790604766.9965265, "node": "node29", "gpu": 0}
{"ev": "cluster_op_end", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 1, "elapsed_s": 0.036060800310224295, "t": 1790604767.0325873, "node": "node29", "gpu": 0}
{"ev": "cluster_op_begin", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 2, "src": 6, "dst": 6, "n": 1073741824, "kind": "local_dram", "t": 1790604767.0326278, "node": "node29", "gpu": 0}
{"ev": "cluster_op_end", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 2, "elapsed_s": 0.023939712904393673, "t": 1790604767.056567, "node": "node29", "gpu": 0}
{"ev": "cluster_op_begin", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 3, "src": 4, "dst": 6, "n": 28722408095, "kind": "network", "t": 1790604767.0566003, "node": "node29", "gpu": 0}
{"ev": "cluster_op_end", "tx": "010ffd17140d4076a9b6c577450878db", "op_id": 3, "elapsed_s": 1.1211595367640257, "t": 1790604768.1777613, "node": "node29", "gpu": 0}
```

### H.8 原始状态与证据级别

- placement01：完整验收passed、无源码漂移、无清理错误。
- profile01：模型/CPU记录passed；完整GPU trace缺失。
- bulk_profile01：传输与trace覆盖通过，原始run严格指纹failed，保持诊断定位。
- 本附录只读取原始结果，生成独立归档证据；没有将failed重写成passed。

<!-- TASK2_DETAILED_EVIDENCE_END -->
