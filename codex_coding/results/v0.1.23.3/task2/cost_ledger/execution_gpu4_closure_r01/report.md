# GPU4成本实验执行闭环

执行和数据闭环通过；独立预测资格未通过。GPU4完成C1六请求及新session C2两留出，八请求达到协议上限。C1冻结与全部源码SHA保持；不补第三session、不改参数或门。原GPU0仅启动一个worker后在绝对HBM准入门停止，bootstrap与测量均为0，失败原件保留；这不是模型门失败。

| 会话 | 测量MiB / op / copy=sync | bootstrap MiB / copy | 请求前DRAM最大B | supervisor DRAM采样最大B | sameHIP HBM观测最大B |
| --- | ---: | ---: | ---: | ---: | ---: |
| C1 | 384 / 9 / 13 | 96 / 3 | 1794973696 | 1918480384 | 675282944 |
| C2 | 128 / 6 / 7 | 96 / 3 | 1785507840 | 1909178368 | 675282944 |

两个成功会话测量payload共512MiB、15op、20copy/20sync；各自完整96MiB source bootstrap合计192MiB、6copy/6sync。恢复payload总704MiB、26copy/26sync。两次synthetic DRAM source建立的192MiB fixture生成工作另列，不能计入恢复payload或藏作免费准备。

native verification.checked_bytes按每op逻辑范围计一次：测量512MiB+bootstrap192MiB=704MiB。每op分别digest源与目标，source digest逻辑工作704MiB、target digest逻辑工作704MiB，合计1408MiB。该计数包括跨请求重复读取同一source区间，不能称独有内容字节或完整硬件总流量；协议/allocator/CPU材料化额外流量未知。源与目标全SHA逐op相等。

C2使用新的worker PID/epoch、handler出生UUID及source allocation birth，重新建立并验证96MiB源；两session完整源SHA均为`0f6501993f27c1c0224672c0bd9cf0ebbf7310615ebcc00c8be0b62fdbd57653`。八个目标和实际handler birth均唯一，目标measurement前没有本任务写入、measurement后各一轮写史。实际N/K/chunk/copy/sync、source generation/epoch与偏移，完整native execute方法外窗、相邻ns分区、filled/published/finish边界及全SHA均有raw引用与独审。

6GiB DRAM/2GiB HBM上限、256MiB应用margin与请求目标reserve均未变。每请求sameHIP绝对upper+目标reserve+margin最大C1为1,044,381,696B、C2为1,027,604,480B；不扣其他占用baseline。supervisor分别保留83与80个cgroup采样（以summary.json字段为准），memory.peak文件不可用；测量期间没有并发HIP monitor，连续DRAM/HBM峰值未知。

各session最终Catalog有效副本、used预算、事务及SSD均为0。两supervisor rc0，精确STOP后child−15，收集时PID/进程组为空、collector没有追加信号，原container归于pids.current=1。现有4个受保护container及0个登记owner身份保持；最后157项部署资产逐SHA一致。项目原input XML SHA `602fd985fac595c2f0520708efbb272e40eac2591bc15bc6366dce6f01d47938` 和default mapping SHA保持。指定原厂路径`/usr/local/built-in-508-topo-input-tj-default.xml`在读取证据域中前后均为null，不据此声称文件存在；没有删除或修改。无本任务GPU后台进程的结论来自精确收集原件，本闭环未做新的活体/设备探测。

独立C1 raw/model228项与资源173项通过，C2 raw/validation137项与资源169项通过。资格范围分别保留。冻结最简单a+bN在V1 worker相对误差27.663%超过20%，service V2/V1绝对误差105.286/144.594µs超过50µs；完整候选保留规则未通过。原件保留局部候选资格，但`selected_worker_and_outer=null`、`selected_service=null`、complete_filled_model_qualified=false、decision_ready=false。published和live预测仍null，不能把数据/清理通过写为成本模型或真实权重可用时延通过。

summary.json逐项闭环检查、两session资源/出生代际/计数及原始SHA引用可复算；没有修改冻结measurement代码、C1模型或留出结果。
