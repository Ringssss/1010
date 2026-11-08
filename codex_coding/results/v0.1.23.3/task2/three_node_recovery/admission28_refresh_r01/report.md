# node28 再准入准备

2026-10-09 06:17:34 UTC一次只读采样通过，同hostname/boot。Docker根盘available64,389,271,552B（59.97GiB），旧storage_blocked不再用于当前准入。GPU2/BDF0000:55:00.0/NUMA2普通sysfs total68,702,699,520B、used1,906,843,648B，差66,795,855,872B（62.21GiB）；HIP free尚未取得，不能认证GPU运行健康。

两旧容器完整ID/StartedAt连续，full owner+六worker和v02六keeper共13个PID/start/cgroup身份通过。仅已知29702–29707读取inventory，共1098条VALID/lease0记录、transactions为空，目录canonical SHA为后续guard保护基准。不调用health，不扫描未知端口，不读取权重payload；不认证新的全权重SHA/恢复通过。可见KFD owner只读取普通vram_[0-9]+与pasid，排除vramtbl。

新配置见同级admission28_plan_r01/node28.spec.json。复用r06准确cached image、host source和全部assets；init_gate保持927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b。新container为zjn-fluid-three-node-isolated_r02-n28，stage为/tmp/fluidckpt_three_node/isolated_r02_n28。维持privateIPC、8GiB内存、1GiBshm、IPC_LOCK、host network、memlock无限/core0、设备及hyhal只读挂载。node28与node30对比是跨节点描述性对比，不是严格单变量因果实验。

父统一执行setup，完整ID/StartedAt的before guard通过后，native docker exec设置OMP_NUM_THREADS=1/MKL_NUM_THREADS=1/PYTHONDONTWRITEBYTECODE=1，仅一次zero-payload init（15s总deadline，外层35s）；成功且exact child退出/后置目录保护通过后才进行一次1MiB copy。shared run_gate当前缺两线程环境，因此使用与node30 r01相同native调用。子agent没有启动容器或GPU；未删除/清理/改XML或全局配置，没有node27/29调用。原始stdout、stderr、argv、冻结只读源和SHA在admission28_refresh_r01；setup/guard/GPU结果由父补充，未冒领完成。
