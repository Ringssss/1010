# 最小校准独立验收契约

本轮保持 `v0.1.23.3/task2`，独立审查不启动远端或 GPU，不改实现 agent 源码、正式文档、旧资源地图和原始结果。首读计划18.17、报告32.12及 `three_node_recovery/review_runtime_final_r01/qualification.json`。旧资格只证明28 GPU2与30 GPU0经shca_0:1、IBext_v8、gdr0的1MiB功能；不含带宽、独占或真实模型权重可用时延。

## 最小范围及停止条件

优先复用已认证路径、runtime_gate_r03、精确owner/guard、已有资源地图、loading planner和恢复执行器。新增量限于有边界的测量阶段、共享资源有限工作点契约及人工两布局薄接入，不建设新solver、泛用测试框架或拓扑全矩阵。

30 GPU0若仍有外部负载，同次只读库存可以发现另一张无外部owner候选，由父统一分配并先补1MiB运行资格；旧资格不自动转移。没有合格资源则正式校准blocked，CPU接口和明确标注的功能工作继续。同布局、同路径且无机制差异时记录无决策增量并停止完整性能比较。

## 五层验收

| 层级 | 必要证据 | 不据此升级的结论 |
|---|---|---|
| 代码 | 最小改动、原件离线回放、定向拒绝用例、源码SHA | GPU实测通过 |
| 资源准入 | 当前host/boot/container/BDF、owner/独占、授权预算、实际free及年龄、保护与精确清理 | 标称容量可用 |
| 成本测量 | 同进程monotonic差值、设备完成同步、完整SHA、实际IB/HCA/XML/config、原始时间与bytes | 真实模型Tready改善 |
| 合成块端到端 | 真实副本、执行计划、全内容、租约、峰值账本及控制器计时 | 真实模型验收 |
| 真实模型Tready | 固定模型/TP/目标、load start到全部目标weights_ready、正确性另验 | 首token或服务收益 |

## 资源与计时

- Startup/init/comm建立和搬运分段记录。GPU初始化预算沿已验证60s入口；不能将18–22s启动计入纯传输带宽，也不能套旧35s wrapper。通信操作期限、总期限与精确清理仍独立。
- 每个rank仅用本机monotonic相减；多rank聚合时明确聚合durations或控制器同clock的开始/完成确认。不能跨机器monotonic相减。设备搬运结束须有设备同步；SHA及验证拷回开销明确分段。
- GPU共享负载允许标注观察值，正式profile/capacity及performance_ready仍拒绝。未知owner、free、预算、页驻留、干扰和内部网络容量保持未知。
- NUMA受控测量需实际绑定成功及页驻留证据。mbind/get_mempolicy能力按实际容器核验，不能以碰巧N0页驻留宣称可主动绑定。失败原样记录。
- 测量绑定run/source SHA、路径、实际IB后端与HCA、XML/plugin/config fingerprint、预算、样本年龄和工作点。有限joint工作点保留同时活跃路径和各路径速率，不能解释为独立可叠加capacity。
- 每个共享物理resourceID只有一份容量或联合工作点账本；路径引用该ID，不复制容量。未知共享祖先容量不能填猜测值供solver。

## 两布局公平性及保护

同预算须有物理底本、布局副本、host/device staging、target/context及迁移峰值分类账本；共享引用不按FD/RSS重复计量。预算按实际节点/介质核算，常驻与峰值不能混用。

两布局固定bytes、chunk、目标rank、执行器、冷热状态、计时范围、运行配置与资源准入；布局差异人工显式记录。全SHA覆盖所有目标字节，前后租约/事务/catalog和old keeper连续身份保持。正确性检验不冒领计时值，失败/重试用新run ID分别保留。

复用ClusterWorker时，物理staging固定每worker64MiB，不能按chunk切片长度计量；当前支持serial chunk32/64MiB，不扩集合。合成块的replica publish要求先全SHA，因此分别记录控制器同clock的filled buffers与verified published collection；后者包含验证开销。live engine的原模型Tready协议仍独立，不能把合成块publish时间当作真实模型Tready。

本轮不访问27/29、不操作其旧pending，不改原厂/default XML或全局配置，不覆盖raw、不执行archive/history。

## 本轮实测后的范围收敛

新28 GPU0/BDF09→30 GPU4/BDF85的1MiB功能已独立通过。64MiB本地NUMA4 H2D观察通过；64MiB跨节点功能通过，但28相对当轮前置guard的cgroup采样峰值增量超过2GiB约1.88MiB，保留为明确预算失败。两端内核均缺少`memory.peak`，不得把定期`memory.current`采样当成完整全过程峰值。

较大微测保留为prepared且不运行。产品验证先启动新worker、建立mesh并用已有`warm([[0,1]])`的一个float真正实例化pair communicator，随后采集当前guard。只有固定runtime/通信增量加应用缓冲上界满足原2GiB预算时，才执行两套各含两个16MiB块的布局；原32MiB chunk和每worker64MiB staging保持。没有余量则不分配source并精确结束own session。

两个64MiB观察来自不同primitive，不能拟合大小曲线或推断独立硬件容量。16MiB产品集合计时与这些微测继续分别报告，正式planner容量和真实模型Tready均保持未证。
