本轮冻结四布局路径、角色、字节和每端执行顺序，见 predictions.json。A 的两端本地操作可独立推进；D 的两方向网络操作按 block0→block1 推进。

计时采用原 controller 的同钟 load_begin、全部 execute 返回、全 SHA 校验发布边界。缓存构建、源准备与恢复分开。A 最短与 D 最长是独立弱假设，B/C 排序待实测。

资源账本保留每端 32MiB cache 上限、总 32MiB cache、源 DRAM32MiB、目标32MiB及 staging128MiB。准入以启动前 baseline 为基准，保留 8MiB 动态余量。生命周期采样表达观测最大值；完整物理峰值资格保持 false。

现阶段完成只读原 executor 语义审查；本轮实现及 GPU 结果尚待交付。反向通信的实际 IB/GPU/NIC 路径需由新会话认证，gdr0 不能给出 runtime 内部 host 页位置。
