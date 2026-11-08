# GPU4 C1执行账目（原始账本只读汇总）

状态：六个校准请求已完成；独立raw/resource审查与C2资格由主agent另验。此汇总不拟合，不改变门或源代码。GPU0资源准入失败保留，不作为C1模型门失败。

| 配置 | N MiB | K | 实际copy/sync | 原service µs | 完整native worker µs | controller execute µs | 完整filled ms | published ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| L3 | 64 | 2 | 2/2 | 461.177 | 575.768 | 2474.514 | 9.455 | 222.269 |
| L1 | 32 | 1 | 1/1 | 352.925 | 430.086 | 2116.149 | 8.187 | 118.405 |
| L4 | 64 | 2 | 3/3 | 534.798 | 663.269 | 2518.324 | 9.170 | 218.537 |
| L2 | 64 | 1 | 2/2 | 423.666 | 518.466 | 2281.201 | 8.447 | 207.946 |
| L5 | 96 | 1 | 3/3 | 540.047 | 623.648 | 2363.722 | 8.528 | 313.435 |
| L3_repeat | 64 | 2 | 2/2 | 491.027 | 614.179 | 2543.154 | 8.880 | 206.879 |

六请求测量payload384MiB，9op，13copy/13sync。source bootstrap另为96MiB及3copy，不进六点拟合。每个op源与目标全SHA按协议执行；源读取和目标校验的流量不冒充恢复payload。

worker主窗为完整原execute方法，原service记录为内部诊断。accounting.json的worker_disjoint_ns是相邻不重叠分区，总和精确等于worker主窗；copy_submit_child_ns嵌套于land_submit，不相加。source_slice_and_loop包含循环与hook，device_sync是复合等待，unattributed_gap不指认机制。controller分区独立同域闭合；worker与controller只能比较局部duration，不能相减绝对timestamps。

目标allocation birth/generation首次写史、真实HTTP actor born/generation、实际N/K/chunk向量、成功copy/sync返回及SHA原件均在每请求raw_ref。metadata与原方法外组装尾仍计入controller/fullfilled。published的主要追加工作包含完整SHA及complete，不能把其差额归为搬运速度；weights_ready仍null。

每请求预算原样保留在accounting.json，包含runtime和其他选定设备占用的sameHIP absoluteupper，不扣未归属baseline；256MiB应用margin不变。监督主机cgroup样本与精确STOP/collection原件在执行目录，连续HBMpeak未知。

独立新session预测与数值门尚需C2结果；C1训练通过不能作为独立准确率或细排序能力。
