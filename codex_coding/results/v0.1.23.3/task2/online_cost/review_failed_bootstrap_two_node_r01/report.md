# 首次engine bootstrap失败与回收独审

开发编号v0.1.23.3/task2。实际r01完整模型capture、calibration、online观测均0；不能作为成本拟合或性能验证样本。已经完成的native四worker mesh/warm和资源门仅作setup功能证据：27/30 selected-card总已用上界各1,438,646,272B，source30加256MiB仍小于2GiB。

独立读取最终两个节点tar（17/9文件），没有任何capture pickle/captured marker或calibrate/validate结果。engine日志169条OpenBLAS线程建立失败，请求64线程；supervisor记录cgroup pids.current最大512，等于pids硬限。engine child rc1、rank1 initialization -11被保留。证据支持先设置process-local pre-import thread caps；没有单独证明SIGSEGV唯一底层机制，也没有放宽pids、DRAM或HBM门。

五个supervisor的exact group cleanup全部passed且pending live group列表空；四个worker按STOP退出，engine提前退出的failure仍为false。原Run summary仍记录bootstrap失败和quiet closure失败。node27只余sleep+4个orphan Z导致pids5；主agent保留原tar/proc/cgroup之后仅重启本任务精确ID8b484137...，新StartedAt明确09:14:48.364461376Z。old boundr01文件SHA未变，collection-only恢复plan显式记载此transition，不伪称StartedAt保持。

最终 `execution_host_collection_two_node_r03` 双节点raw收齐、own cgroup pids1、OOM0。独立比较run-before与最终metadata的67个非本任务old容器：identity、StartedAt/PID、HostConfig和完整mount多重集合均不变；原XML SHA保持。node30原StartedAt保持。reviewerGPU/SSH动作0。

重试r02必须使用新的runtime/run ID和当前确切StartedAt、fresh HIP admission，并在worker/engine/controller启动前设置OPENBLAS/OMP/MKL/NUMEXPR等线程上限。所有129核心/产品冻结资产、相同预算和whole-online≤10%准则保持。r01原失败不覆盖，重试成功也不把它变为性能样本。

逐项数据与tar SHA见 `qualification.json`。当前model prediction资格false，待实际完整C0和Hnear/Hremote独立预测验证。
