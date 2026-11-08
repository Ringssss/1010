四布局交付已通过本地代码审查与 7 项必要 CPU 测试，达到按冻结协议启动一次 ABCD 会话的条件。实际 launch plan、32MiB chunk/64MiB staging、同 controller 时钟、全 SHA 发布、每布局退役和外层 STOP 均复用原组件。

本轮修订明确了两端 48MiB 应用 HBM 上界、8MiB 动态余量；controller 要求四布局完整通过，退出收口增加 fresh exact owner 只读复核。每布局门槛重核共同 postwarm 样本和静态上界，当前 HBM allocator 由 inventory/check_peak 补充。

源与路径硬条件按 review_preflight_r01/predictions.json 独立冻结。实机完成后核角色/字节/顺序、内容、时间分段、运行路径、生命周期资源和精确退出，并分别判断 A 最短、D 最长两个弱时间假设。
