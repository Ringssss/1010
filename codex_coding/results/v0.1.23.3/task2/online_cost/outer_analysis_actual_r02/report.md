# 新 outer 校准实测独审

功能与协议：通过；两留出整体10%：全部通过。未改模型或原件。

| 请求 | 完整在线时间（s） | 冻结预测（s） | 相对误差 | 用途 |
| --- | ---: | ---: | ---: | --- |
| C0_prime | 3.024892887 | — | — | 首次冷恢复，独立记录 |
| C0_cal | 2.903039095 | — | — | 唯一outer校准 |
| Hremote | 4.581127216 | 4.572578937 | -0.187% | 留出 |
| Hnear | 1.290400136 | 1.212516366 | -6.036% | 留出 |

旧outer=0.808030006s；新outer=0.423580424s。新参数只由一次C0_cal完整在线减固定原DAG得到。C0_prime时间和三项正确性单列，首次冷恢复预测资格仍未知；模型域仅为同一prime后的controller/engine/keeper incarnation。

| 同node27窗口（ms） | 旧C0 | 新prime | 新cal | 新Hremote | 新Hnear |
| --- | ---: | ---: | ---: | ---: | ---: |
| request_to_first_rank | 247.042 | 147.765 | 79.711 | 81.456 | 80.429 |
| rank_arrival_skew | 216.691 | 36.107 | 0.994 | 3.984 | 0.508 |
| last_rank_to_last_export | 247.529 | 239.933 | 251.416 | 254.057 | 246.895 |
| last_export_to_last_map | 90.831 | 87.498 | 83.086 | 90.808 | 170.178 |
| last_map_to_first_execute | 2.893 | 2.515 | 3.263 | 3.311 | 3.334 |
| last_copy_to_ACK | 11.329 | 10.267 | 10.974 | 8.006 | 8.763 |

窗口均由各自同node27单调时钟重建，比较时只比较时长，不跨主机或跨run相减时间戳。每rank内部窗口相互重叠，不能另求和当作唯一因果。完整窗口、搬运DAG及剩余误差见breakdown.json。

所有四次各恢复70,329,718,112B/66extent，完整SHA/固定输出/真实物理park独立核验；同controller PID=7613。helper共1846个0.5s配置采样，最低free=6958350336B，最高reserved=69206016B；连续峰值及完整driver/RCCL任务归属未知。精确源/NUMA/epoch、sender串行与最终Catalog/owned closure/两节点quiet详见qualification.json。

新结论仅覆盖预登记两节点单sender后续恢复域；首恢复、复杂拓扑与规划器收益资格分别报告。复用旧路径成本作为跨incarnation迁移假设接受实测留出检验，未重测路径/拓扑或使用留出拟合。

PID连续性仅在本次online_outer_two_node_r01及node27容器fd8fcf443d1a443d88cfdd8f8d3f628e481d4812075c8a72397b999642cfaca3内成立；与历史容器相同的数字PID不代表复用旧进程。容器StartedAt/boot与bound plan SHA单列于qualification.json。

Hnear的accepted→mapped逐rank窗口仍为134.47/149.29ms，高于C0_cal57.56/57.59ms与Hremote63.04/63.31ms；与旧顺序相比，本轮Hnear位于最后仍出现较长映射窗口，不能单独归因为第二个请求或统一首调用开销。当前后续恢复整体10%目标已达，不再追加精度实验。
