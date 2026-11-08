# 同handler两次相同复制：最后两请求

|size / phase|first wall us|second wall us|first CPU us|second CPU us|
|---|---:|---:|---:|---:|
|4MiB / begin_log|99.121|22.201|90.541|22.080|
|4MiB / copy_submit|83.161|17.750|83.372|17.920|
|4MiB / device_sync|61.961|29.121|62.391|29.030|
|4MiB / source_slice|91.422|13.860|90.991|13.781|
|4MiB / target_slice|10.910|6.130|10.630|6.120|
|16MiB / begin_log|71.191|22.051|68.671|21.970|
|16MiB / copy_submit|70.401|18.330|70.671|18.500|
|16MiB / device_sync|63.651|64.521|63.911|64.581|
|16MiB / source_slice|74.341|12.940|74.411|12.930|
|16MiB / target_slice|10.160|5.590|9.910|5.500|

逻辑请求4/16MiB各1，实际每请求复制两遍，logical测量SHA20MiB、actual测量copy40MiB，另bootstrap16MiB；filled包含两轮，不能将第二轮时间当优化收益。

首→次source_slice的thread CPU变化用于检验首操作host项；copy/sync同时受目标first-touch/cache改变影响，不能归为纯host或物理带宽。无events、无prewarm、metadata仅两轮之后采样。

这是最后两个GPU请求；旧模型参数与原结果保持，无拟合或继续补测。
