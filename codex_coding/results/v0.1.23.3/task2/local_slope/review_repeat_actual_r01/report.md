# 最后两请求独立证据与建模含义

78 项检查、33 引用 SHA 通过，状态 `repeat_actual_evidence_passed`。新会话只有4MiB/16MiB各一个逻辑请求，均在同handler中对同source/target地址连续复制两遍，没有CUDA events、预warm或额外prepare同步。metadata仅两轮后采样；同请求两个round的native TID、source/target ptr相同，两个请求的TID不同。旧模型保持。

| round口径(µs) | 4MiB 首轮 | 4MiB 次轮 | 16MiB 首轮 | 16MiB 次轮 |
|---|---:|---:|---:|---:|
| 总round host wall | 346.575 | 89.062 | 289.744 | 123.432 |
| begin log wall | 99.121 | 22.201 | 71.191 | 22.051 |
| source slice wall | 91.422 | 13.860 | 74.341 | 12.940 |
| source slice thread CPU | 90.991 | 13.781 | 74.411 | 12.930 |
| target slice wall | 10.910 | 6.130 | 10.160 | 5.590 |
| copy API wall | 83.161 | 17.750 | 70.401 | 18.330 |
| device sync wall | 61.961 | 29.121 | 63.651 | 64.521 |

first→second的source slice CPU减少77.210µs/61.481µs，wall减少77.562µs/61.401µs，实测证明同handler的首source操作host成本高于第二轮。begin log与copy API也有首轮成本，不把全部减少归给slice。第二轮device sync的尺寸差35.400µs接近整体round正差34.370µs，但sync可能忙等，CPU≈wall并不能排除GPU等待；该阶段也含设备其他待完成工作，不称纯DMA或内存带宽。

首轮16−4为−56.831µs，拆为log−27.930、source slice−17.081、target slice−.750、copy API−12.760、sync+1.690µs；第二轮16−4为+34.370µs。这一受控请求中，变化的首操作host成本确实能覆盖较大尺寸的正完成增量。它没有唯一归因最初旧session每个负斜率点，不能为本轮native1.037124ms慢点补造解释，也没有认证具体线程局部HIP/PyTorch库函数、拷贝算法或物理引擎。

源/目标地址、handler和尺寸在每请求两轮之间固定，但第二轮同时改变host首调用、target首次触达与缓存状态。source slice CPU差单列约束host成本；copy/sync变化仍受这组因素共同影响。完整record包含两轮与两轮间的log/bookkeeping，4MiB=.518927ms、16MiB=.484826ms，没有从请求窗口扣除第一轮，没有将第二轮时间冒充优化收益。

逻辑测量SHA20MiB、实际测量copy40MiB；另bootstrap16MiB，合计全SHA36MiB与实际copy56MiB。verification只校验两轮后的逻辑target n字节，物理2n来自执行协议与两轮记录，不伪造为验证了56MiB独立输出。首八请求的96MiB证据另列，不把round算成独立重复请求。

建模上，原record把begin log、slice、copy API和sync都并入service，单op的首操作host项会污染按两个尺寸拟合出的byte斜率；负系数不能转成物理带宽。后续若定义独立请求首操作项，必须明确从原service中拆出哪部分，避免同时保留含首操作的service又加初始化项。当前每路径最多一op的有限模型只作经验service近似，其冻结系数不因本次diagnostic回调；本次尚未辨识通用first-op/steady-op正斜率模型。

实际安装版的单slice入口强制disable_slice_optimization=true，4/16MiB均落slice_symint，见 `path_slice_installed_r01`；特定“full-source绕过slice”的源码解释已被排除。实际header分支不替代二进制trace，也不能由此进一步认定首slice初始化的具体机制。

最终Catalog VALID/used/事务全空，三SSD files0，九supervisor/STOP rc0，九child TERM/-15、PID absent/组空，无KILL。三node旧owner/container/XML/source SHA保持，quiet pids1。DRAM采样峰28/30/27=3,482,562,560 / 3,980,009,472 / 4,456,787,968 B，均<6GiB；同HIP绝对采样上界=1,124,073,472 / 1,281,359,872 / 1,826,619,392 B，均<2GiB。完整连续peak未知。两会话全部收口，不追加GPU实验。
