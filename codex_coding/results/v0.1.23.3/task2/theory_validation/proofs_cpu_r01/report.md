# 固定理论例子的 CPU 验证

本轮参数为理论符号值，无硬件测量、GPU、远端操作或产品改动。

- 等大、固定独立收益、基数约束：top-k 的交换论证与六个 subset 一致。
- 稀缺位置：特定顺序 whole-block 策略 8s，联合 assignment 及分片最优 4/3s。分片最优不唯一。
- TP4 plateau：所有 proper subset 都为 8s，四个 prefix 全选为 5s；单点/两点严格正收益贪心停在空集。
- 共享 cut：H 两源共享一个 8GiB/s 出口，另一独立池 6GiB/s；whole-block 最优 4/3s，流体分片最优 8/7s。
- 独立 DRAM fallback 能破坏稀缺位置下界；快速 relay 能拯救原贪心布局，假设须明确。

已直接调用原 strong_simple 和 joint_scenarios：前者三种 control 均选空、8s；后者选四个候选、5s。有限枚举 16 个 subset 复核。简单 bottleneck-balanced 策略同为 5s，所以不证明 MILP 必需。

具体输入、源码 SHA、枚举与结果见 validation.json。理论时延不可写入正式硬件 profile；真实 source/pair 依赖、setup、DRAM 条件与资源账本仍须独立验证。
