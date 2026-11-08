# full-source slice 假说的官方源码核对

官方 upstream 不支持把当前一维 Python `source[:n]` 的 full slice 直接解释为 bypass。v2.4.0 的 Python binding 在 `python_variable_indexing.cpp` 第347行设置 OptionalDeviceGuard，第367–375行将单个 slice 交给 `get_item`；`TensorIndexing.h` 第618–627行的单slice分支强制 `disable_slice_optimization=true`。因此 full/partial 均到 `slice_symint`。`applySlice` 第200–228行虽有 return-self 优化，但该优化要求 disable 标志为false。v2.1.0 Python binding 第351–359行采用相同入口；main 的header也保留单slice禁优化规则。[v2.4.0 binding](https://github.com/pytorch/pytorch/blob/v2.4.0/torch/csrc/autograd/python_variable_indexing.cpp#L367)、[v2.4.0 header](https://github.com/pytorch/pytorch/blob/v2.4.0/aten/src/ATen/TensorIndexing.h#L618)、[v2.1.0 binding](https://github.com/pytorch/pytorch/blob/v2.1.0/torch/csrc/autograd/python_variable_indexing.cpp#L351)。

这属于官方版本条件下的源码结论。实际vendor版本/header尚需主读取；未把upstream当实际加载库。默认本地python无torch，常见安装/头文件目录和lib_cite均未发现header。[sources.json](sources.json)保存三官方源码文件URL/tag/SHA；原源码完整保存用于核对，没有安装库或触发GPU。

若新实测差量确实落在首个 source-slice，最小干预是同handler在stamp前仅构建并保留 `source[:4MiB]` view，然后执行原slice与原copy；不提前copy、不换source allocation，判断首getitem成本是否转移。OptionalDeviceGuard只是待测边界，不能据源码证明60µs成本来自它。4MiB fullsource对4MiB partialsource若新分配源会同时改变地址、allocator与身份，优先级低；也不能依据fullslice优化假说期待它跳过dispatcher。

主可用 `src/v0.1.23.3/task2/local_slope/path/inspect_installed.py` 从stdin执行远端python。脚本仅stdlib读version.py/header，含SHA及原行号，不importtorch、不调用GPU。
