# 独立物理地图

从两机原始 sysfs 与 NUMA meminfo 独立生成，不读取资源地图实现。GPU标签使用DRM minor，避免将其未经认证地替代HIP ordinal。每个NUMA的CPU集合、DRAM总量、GPU端点及真实PCI root、局部HCA列出；400Gb/s仅为标称端口速率。网络fabric总容量和全GPU实测带宽矩阵保持未知；图中没有虚构边。
