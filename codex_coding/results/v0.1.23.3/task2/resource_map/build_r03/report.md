# node27/node29 资源地图建设

纯CPU读取冻结证据，新增GPU运行0。物理身份、runtime、可用量与历史观测分层。

node27：128逻辑CPU，DRAM 503.63087 GiB；采样 2026-10-08T04:00:02.358306+00:00。
node29：128逻辑CPU，DRAM 503.63087 GiB；采样 2026-10-08T04:00:02.841886+00:00。

GPU标称64GiB；hy-smi报告总量65520MiB，不能冒领整64GiB为可分配容量。Available与total-used均按原字段记录。
共享PCI对象仅一个稳定node/BDF身份，所有叶路径引用它；容量及互扰未知，不累加端口标称线速。

当前结构可读，性能准入为false：请求/预算未授权、外部owner活跃、历史微probe和RPC包络不充当当前速率。生成XML尚未获得实际runtime验收。

失败与未定：fresh snapshot随时间过期；无当前合法预算；反向网络、fabric内部、SSD和完整GPU组合速率未知。历史HIP ordinal映射独立标注原采样时间，fresh KFD/sysfs/BDF校验一致。

接口：resources/resource/shared_ancestors/path_evidence/validate_resource_map/planner_readiness/planning_input；无solver。
