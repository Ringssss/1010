# 三节点六GPU恢复：原件独立breakdown

本解析只读 `operations_actual_r01`，session为`multitier_runtime_r02`。四配方各n=1，冻结布局和暂停配额不同。下表恢复计时来自同一个controller时钟；inventory、prepare RPC与execute用原浮点阶段字段，filled/published及verify+publish用原ns事件，组合差小于0.01ms。外部trusted gate、binding和模型handoff不在窗内。

| 配方 | inventory ms | prepare RPC ms | execute ms | filled ms | verify+publish ms | published ms |
| --- | --- | --- | --- | --- | --- | --- |
| SSD_only | 63.287 | 5.508 | 63.390 | 132.188 | 62.394 | 194.581 |
| C0 | 63.132 | 4.491 | 5.290 | 72.915 | 26.971 | 99.886 |
| C1 | 62.373 | 4.391 | 4.685 | 71.450 | 25.358 | 96.808 |
| C3 | 61.842 | 5.071 | 14.138 | 81.052 | 30.510 | 111.562 |

verify+publish包含每个操作源与目标的全量SHA及complete发布。原件没有单独verify墙钟，complete每worker RPC记录不能相加或直接从总窗扣出验证时间。

## 执行窗口与worker局部计时

| 配方 | controller execute ms | 最大worker/execute ms | 两者差值 ms |
| --- | --- | --- | --- |
| SSD_only | 63.390 | w4/59.633 | 3.757 |
| C0 | 5.290 | w4/2.061 | 3.228 |
| C1 | 4.685 | w2/2.745 | 1.940 |
| C3 | 14.138 | w4/11.411 | 2.726 |

差值只记residual。controller窗口包围六路execute RPC，worker execute从局部执行入口到返回；两者起止不同。该差值不能单独归因于RPC、dispatch、网络或SSD。worker execute减该worker op.elapsed和的局部residual另存在JSON。

op.elapsed从开始日志前采时到每op同步后采时，包含适用的HTTP SSD读取、host/GPU拷贝、NCCL collective等待、target落地及同步。源/目标会在不同时刻进入同一op，因此target.elapsed可能含上游等待。现有日志只有wall t和本进程elapsed，未记录各子阶段；不跨节点相减，也不把两端elapsed差解释成等待。

### SSD_only实际操作

| op/块 | source tier | src→dst | kind | source/both ms | target ms |
| --- | --- | --- | --- | --- | --- |
| 0/b0 | ssd | w0→w0 | local_ssd | 6.579 | 同一both记录 |
| 1/b1 | ssd | w0→w0 | local_ssd | 7.990 | 同一both记录 |
| 2/b2 | ssd | w0→w2 | network | 6.915 | 22.498 |
| 3/b3 | ssd | w0→w2 | network | 8.047 | 7.992 |
| 4/b4 | ssd | w2→w4 | network | 6.571 | 35.728 |
| 5/b5 | ssd | w2→w4 | network | 7.788 | 7.532 |
| 6/b6 | ssd | w4→w5 | peer | 8.036 | 51.728 |
| 7/b7 | ssd | w4→w5 | peer | 7.743 | 7.702 |

局部顺序：w0: 0→1→2→3；w2: 2→3→4→5；w4: 4→5→6→7；w5: 6→7。

### C0实际操作

| op/块 | source tier | src→dst | kind | source/both ms | target ms |
| --- | --- | --- | --- | --- | --- |
| 0/b0 | hbm | w0→w0 | local_hbm | 0.307 | 同一both记录 |
| 1/b1 | hbm | w0→w0 | local_hbm | 0.089 | 同一both记录 |
| 2/b2 | hbm | w2→w2 | local_hbm | 1.694 | 同一both记录 |
| 3/b3 | hbm | w2→w2 | local_hbm | 0.092 | 同一both记录 |
| 4/b4 | hbm | w4→w4 | local_hbm | 1.848 | 同一both记录 |
| 5/b5 | hbm | w4→w4 | local_hbm | 0.094 | 同一both记录 |
| 6/b6 | hbm | w5→w5 | local_hbm | 0.484 | 同一both记录 |
| 7/b7 | hbm | w5→w5 | local_hbm | 0.088 | 同一both记录 |

局部顺序：w0: 0→1；w2: 2→3；w4: 4→5；w5: 6→7。

### C1实际操作

| op/块 | source tier | src→dst | kind | source/both ms | target ms |
| --- | --- | --- | --- | --- | --- |
| 0/b0 | hbm | w0→w0 | local_hbm | 0.319 | 同一both记录 |
| 1/b1 | dram | w0→w0 | local_dram | 0.175 | 同一both记录 |
| 2/b2 | hbm | w2→w2 | local_hbm | 2.388 | 同一both记录 |
| 3/b3 | dram | w2→w2 | local_dram | 0.274 | 同一both记录 |
| 4/b4 | hbm | w4→w4 | local_hbm | 0.369 | 同一both记录 |
| 5/b5 | dram | w4→w4 | local_dram | 0.169 | 同一both记录 |
| 6/b6 | hbm | w5→w5 | local_hbm | 0.355 | 同一both记录 |
| 7/b7 | dram | w5→w5 | local_dram | 0.555 | 同一both记录 |

局部顺序：w0: 0→1；w2: 2→3；w4: 4→5；w5: 6→7。

### C3实际操作

| op/块 | source tier | src→dst | kind | source/both ms | target ms |
| --- | --- | --- | --- | --- | --- |
| 0/b0 | hbm | w0→w0 | local_hbm | 1.833 | 同一both记录 |
| 1/b1 | hbm | w3→w0 | network | 0.740 | 0.719 |
| 2/b2 | hbm | w1→w2 | network | 0.889 | 2.292 |
| 3/b3 | dram | w0→w2 | network | 0.335 | 0.580 |
| 4/b4 | hbm | w4→w4 | local_hbm | 0.372 | 同一both记录 |
| 5/b5 | dram | w2→w4 | network | 0.338 | 3.129 |
| 6/b6 | dram | w5→w5 | local_dram | 0.968 | 同一both记录 |
| 7/b7 | ssd | w4→w5 | peer | 7.719 | 9.982 |

局部顺序：w0: 0→1→3；w1: 2；w2: 2→3→5；w3: 1；w4: 4→5→7；w5: 6→7。

SSD_only的w0先完成b0/b1本地读入，再供b2/b3给w2；w2须接收b2/b3后再从自己的SSD供b4/b5给w4；w4须接收b4/b5后再供b6/b7给w5。w5首个op6 target为51.728ms，内含等上游推进；不能把它当4MiB peer链路时延。该串行参与关系解释尾worker窗口约59.6ms，也说明不能把三个SSD各8/16MiB简单假设全并行。

C0、C1只在w0/w2/w4/w5各自顺序填两块；四worker之间可独立推进。C0/C1的首op局部耗时和末op差异较大，现n=1和无子阶段记录不足以识别原因或断言DRAM比HBM快。

C3两helper实供：b1为w3/node30/GPU5/HBM→w0/node28/GPU0，source/target为0.740/0.719ms；b2为w1/node28/GPU1/HBM→w2/node30/GPU4，source/target为0.889/2.292ms。b3是w0 DRAM→w2，b5是w2 DRAM→w4，b6为w5本地DRAM。原pair参与顺序使op3要在w0的op0/1、w2的op2后推进，op5在w2的op3及w4的op4后推进，op7在w4的op5及w5的op6后推进；这是结构依赖，尚无去除等待后的数值关键路径。

C3尾段b7位于node27 SSD，endpoint=http://12.11.3.27:35402、代理w4/GPU0/NUMA0、目标w5/GPU4/NUMA4。RemoteExtent.read以HTTP读出CPU bytearray/tensor，跨worker代码复制到w4 GPU staging后由45 peer collective传输并落地w5 HBM。w4局部op4→5→7分别0.372/3.129/7.719ms，execute11.411ms；w5 op6→7分别0.968/9.982ms，execute11.038ms。7.719ms是读取加发送及同步整体，9.982ms还受供方前序影响，均不能拆为纯SSD/peer成本。

## SSD来源、服务读量与校验读量

八SSD底本共32MiB，按node28/30/27分16/8/8MiB。各配置save+restore窗口实测HTTP读取与服务/proc/self/io kernel read_bytes均为64MiB，各node32/16/16MiB。逻辑传输来源与源端SHA再次读取分别列出；下表阶段分配按原plan、verify_run代码推导并核对窗口总量。边界没有kernel counter采样，因此各阶段kernel读量保留null。

| 配方 | 阶段 | node28 传输/SHA MiB | node30 传输/SHA MiB | node27 传输/SHA MiB |
| --- | --- | --- | --- | --- |
| SSD_only | save | 0/0 | 0/0 | 0/0 |
| SSD_only | restore | 16/16 | 8/8 | 8/8 |
| C0 | save | 16/16 | 8/8 | 8/8 |
| C0 | restore | 0/0 | 0/0 | 0/0 |
| C1 | save | 16/16 | 8/8 | 8/8 |
| C1 | restore | 0/0 | 0/0 | 0/0 |
| C3 | save | 16/16 | 8/8 | 4/4 |
| C3 | restore | 0/0 | 0/0 | 4/4 |

C0/C1恢复SSD逻辑来源为0，但save已从SSD读取32MiB建fixture并再次读32MiB校验。C3 save传输28MiB/源SHA28MiB，restore传输4MiB/源SHA4MiB，合计同为64MiB。当前是buffered IO+fadvise记录，不是冷盘读时延试验。save从已有底本建缓存的成本不能代替真实暂停保留已有权重成本。

## inventory约62ms：已有证据与候选定位项

每次查询并行请求六worker，最大请求transport_and_server字段占大部分窗；JSON序列化/反序列化也有原controller字段。transport_and_server合并urllib请求/响应读取和服务器处理，没有独立网络或服务打点。CPU compact仍返回历史replica和host allocation；RuntimeWorker.inventory仍调用父inventory，查询NUMA meminfo、HIP mem_get_info和allocator计数，再附加HIP BDF、proc/cgroup/sysfs/HCA诊断。runtime_identity每次调用ctypes.util.find_library并CDLL。

以上是实际加载源码中的定位候选，不是62ms根因。查询负载、library lookup、HIP API、锁等待、sysfs/proc访问或控制网络分别多少时间，当前均unknown。未运行新profiling，未以源码猜测代替测量。

## 可支持的成本模型结论

实际来源、串行worker顺序与跨worker前序依赖符合当前执行器语义；SSD fallback增加读取/代理/传输步骤。数值预测精度仍未验收：四原计划estimated_transfer_s与bandwidth_lower_bound_s均为null，没有当前六worker的隔离service参数和独立组合holdout。把本次含等待的op.elapsed反填参数再重放同一次结果属于拟合，不能作为模型验证。inventory和全SHA/发布还必须与data execute分别建账，才对应更广的恢复指标。

## 可复现解析与SHA

运行（需使用新的输出目录，脚本拒绝覆盖已有结果）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/cost_model_breakdown/evidence/parse_actual.py --repo-root /home/zhujianian/zhujianian/fluidckpt_zjn --output codex_coding/results/v0.1.23.3/task2/cost_model_breakdown/evidence_r02
```

本次parser SHA256 `eb8798d5342ffe13e6c23f1588718e299a093f1fb3e00f3940eb02fcbf473d8b`；summary SHA256 `4fd4bded529331340fd7dffa1e9cf4bf570ccf12b41edc77df52993aee9aeff9`。所有读取原件、部署来源与当前对应源码的SHA保存在summary.input_references；九个关键执行source均与cases/source_shas.json一致，service_cost/keeper与部署manifest一致。源/目标全SHA、六worker原日志op顺序及阶段HTTP读量和窗口总量均核对通过。未复制完整raw、未修改产品/原结果。

执行来源：

| 源码 | 实机source SHA256 |
| --- | --- |
| run.py | 3ad32a56cb02e18b26fb10d0add9b214dd070b67f0dbac79cf0551b04c1e9c7f |
| runtime_worker.py | b9484b62a7b4ae397b412e995435c90534b620b66253b519ebe1b56b2904104c |
| adapter.py | 5038e02d38a53a327a826be32aa935a3c0694d56c6244f61656a4c1b4097f646 |
| catalog.py | ee92437f234849523bc8cff1362a912e08357599e67a23f479c190817dd0906d |
| control.py | 3dfa23b478831752e42d6dc9aaf16d4e0d60eaff6c3a00f69b6048df9cf06fed |
| worker.py | 267429220f7a3f2fe46d9a3ab36bdf1822e3f2d0604a4924244b61c2b1a752d9 |
| host.py | 88c7935fd5e077cf737f69540ee2f1b15c0ec8804ca4200458844622a15cffbc |
| ssd.py | 3b78bc04537108129045350e642bd2e5e68b308984fd0dab06bcbfb8ce6eecf5 |
| ssd_store.py | ae8674271eb83fc1d2d78f88cfe0f422b5aa8ec0a9037a8854b4bb5d8ab0d164 |

