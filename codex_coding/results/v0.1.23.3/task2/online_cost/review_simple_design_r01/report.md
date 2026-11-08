# 简化两节点单发送端：启动前独审

独审结论：可以由主agent按新源码冻结、新容器、新鲜准入启动本轮完整模型验证。当前无启动阻断项；实际在线10%资格仍待GPU结果，不认证复杂多NIC并发、三节点或SSD。

固定三worker：目标node27 GPU4/5，唯一helper node30 GPU4。两rank远端DRAM分别NUMA4/7，共70,329,718,112B，所有完整布局66操作、2096个32MiB块；C0为首1GiB HBM、连续16GiB远端、再本地。原生worker-order DAG把两个remote路径共同串行在worker2，context/shared_samples为空，不使用旧共享双流系数或重叠准入。

原native capture唯一IPC映射/完整SHA和物理pause门继续复用；online开始包含必要inventory/plan，到完整resume ACK结束。raw ready时钟先落盘，live资源门、全SHA和固定输出随后执行；每场景再次实际physicalpark。共同outer残差只用C0一次；两个独立留出在执行前一起冻结，各自整数10%门，不平均抵消失败。

helper整卡foreign已用只作为观察；fresh free≥4GiB，live free≥2GiB及0.5s自有worker guard，PyTorch reserved/已知去重tensor各≤2GiB。完整任务driver/RCCL显存归属仍unknown，常驻采样非连续峰值证明；可以验证实用共存，不声称严格完整2GiB进程上限。helper任务DRAM96/hard128GiB，目标192/hard224GiB和48GiB/card保持；新增分配按NUMA分别检查。

此次独审发现并修正：Catalog字典被当entry.buf对象使用；缺少完整权重resident的live资源门；helper guard缺正常样本。实现agent已修复，当前源码及证据SHA见qualification.json。独立CPU14项、core最终49项、ops最终23项全部通过。独立CPU r01因审查脚本误用src_numa字段而退出（未运行GPU）；已改为实际native path字段，r02通过，原目录保留。未重跑旧全测试或追加性能重复。

后续实际结果独审需核对源冻结、源NUMA注册驻留、12个路径样本、C0、预测freeze时序、Hnear/Hremote整体误差、ready后70.33GB SHA/参考输出、每次physicalpark、helper watcher样本及最终所有自有进程closure/quiet。
