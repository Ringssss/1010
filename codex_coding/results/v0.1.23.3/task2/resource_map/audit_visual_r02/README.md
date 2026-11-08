# 独立物理地图

从两机原始 sysfs、NUMA meminfo和SMI独立生成，不读取资源地图实现。GPU ordinal引用冻结discovery01的BDF映射，同时显示DRM minor；不把当前未重新启动HIP验证的历史ordinal证据伪称fresh。每个NUMA的CPU集合、DRAM总量、GPU端点与HBM报告总量、局部HCA列出；400Gb/s仅为标称端口速率。网络fabric总容量和全GPU实测带宽矩阵保持未知；图中没有虚构边。
