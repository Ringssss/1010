# 实际安装版单slice禁优化确认

node27 任务容器的实际安装头文件确认：单slice索引不走 full-slice `return self` 快路径。`TensorIndexing.h` 第648行检查单索引、第653–662行的slice分支调用applySlice，并在第660行强制 `disable_slice_optimization=true`；第225行的return-self条件要求该标志为false，故此入口落入第231–232行的 `slice_symint`。当前4MiB partial-source和16MiB full-source都是一维单slice，不能用“16MiB绕过aten slice、4MiB首次dispatch”解释其差量。

读取原件为 [path_installed_r01.json](../path_installed_r01.json)，SHA `4412ddc002239e0d585d266b6503c09b81936092c4cb59544e01bbe53d2981ca`。唯一安装候选为 `/usr/local/lib/python3.10/dist-packages/torch`，version.py标明torch `2.10.0`、git `28498cd684b22c1f420f7f30c4a5e5771147ca77`、HIP `6.3.26113`。实际header为该包的 `include/ATen/TensorIndexing.h`，SHA `5c2e1d9afce367d606d4f39e0ee8ebd81782f7acdf4032ce64cf1d3d8ff16526`。原件保存了相关完整片段与原行号，检查过程不importtorch、GPU API调用0。

[checks.json](checks.json)保存六项分支断言及版本/原件SHA；全部通过。这是实际安装源码条件的确认，尚未记录二进制dispatcher事件，也不据此归因首slice耗时。此前官方v2.1/v2.4/main核对保留在 [path_slice_source_r01/report.md](../path_slice_source_r01/report.md)，其版本范围未改；原报告与源文件不覆盖。

后续实验由主既定的最后两请求执行，不新增测量建议。stage1 native反转未稳定复现的观察应保持；实际header排除了上述特定源码解释，未把未稳定复现写成已找到负斜率根因。
