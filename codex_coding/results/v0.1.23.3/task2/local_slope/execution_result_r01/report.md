# localHBM 分段诊断原始结果

|mode / size|service 两点 ms|均值 ms|
|---|---:|---:|
|native / 4MiB|0.253934/0.172842|0.213388|
|native / 16MiB|0.238223/1.037124|0.637673|
|diagnostic / 4MiB|0.352955/0.301664|0.327310|
|diagnostic / 16MiB|0.290974/0.293464|0.292219|

原路径16−4MiB差量 0.424285ms；诊断路径 -0.035091ms。

|phase|4MiB wall 均值 ms|16MiB wall 均值 ms|大−小 ms|
|---|---:|---:|---:|
|begin_log|0.061171|0.057306|-0.003865|
|copy_submit|0.070101|0.068851|-0.001250|
|device_sync|0.023745|0.021215|-0.002530|
|event_begin_record|0.063810|0.044910|-0.018900|
|event_end_record|0.032481|0.030020|-0.002460|
|source_slice|0.067071|0.062316|-0.004755|
|target_slice|0.008931|0.007600|-0.001331|

设备event夹窗大−小 -0.007520ms，含copy提交与host间隙，不能当纯kernel耗时。

native原_execute计时边界保持；diagnostic额外prepare事件初始化/同步、展开本地分支及计时器会改变观测。两模式分块顺序将时间漂移与仪表影响混在一起，差量仅诊断。metadata在service外，controller/RPC仍计其成本。

本脚本只作读本地原件与无拟合算术；根因与后续干预须独立审查。旧冻结模型未更新，布局排序未认证。
