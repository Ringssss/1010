# 首操作验证独立审查：实施前门

日期：2026-10-11；v0.1.23.3/task2。依据冻结的首操作状态验证计划和 local_slope 最后两请求/最终独审。此文件登记审查标准，不认证尚未产生的 CPU 或 GPU 结果；未使用 SSH、GPU 或远程写入。

本轮仅检查新增覆盖层和本次证据，复用前轮已认证的原始复制路径与资产保护规则。计划的 F/P×fresh/pretouch 四 cell、固定 8MiB、A 顺序 1,4,3,2、条件 B 顺序 2,3,4,1 没有设计级阻断。

实施资格必须证明实际 executor birth，而不是仅打印配置或 OS TID。F 两请求新建并 join；P 跨两请求同 birth 持续存活，再 join。两个模式使用相同单任务 queue/return/序列化，handler 不得持 worker.lock 等待还需同锁的执行器。执行器在首测量任务前不得 view/copy/log 或设备查询预热。

每 cell 在 R1 前同时持有不重叠的 A/B/scratch；target 使用 empty，不能初始化。每请求在同一个独立 prepare 上下文执行等大的 pretouch 或 scratch copy+sync；prepare birth 与 executor birth 不同。写史、分配代际、source/target lease、持有期间与清理均须来自运行证据。禁止把 FILLED 改回 PREPARED。每目标最终全 SHA 一次，只认证最终逻辑内容；16 轮测量与 8 次干预字节另算。

CPU 资格须调用真实覆盖层和真实 runner（设备可 mock），证明实际 slice/copy/synchronize 原语与闭合时钟。schema 合成回放只补拒绝门，不能替代真实原语回放。隔离 remote CLI 检查必须仅含部署包，并冻结全部入口依赖 source closure。旧模型域外预测 null、unknown birth/state 拒绝、observer 成本不从实测减除。

完整请求起于 controller fresh inventory 前。prepare 干预、fixture 分配、线程创建/queue/等待/join、set_device、endlog/bookkeeping、postdiagnosis 都计入对应实际窗。详细地址/stream/meminfo 在两轮后采样；预赋 birth/token 在 raw op 中。完整 worker/controller/full 分层闭合与单机时钟域必须验证。

A 完成后先核运行资格，再独立判断 B 门。无明确 thread/request 状态结构，或 target/observer 交互无法分离、差额与观测/调度变化同尺度时，止 A。存在一致的可证伪预言才允许 B；logger 单独结构须限定为观察策略，不能泛化为原语物理规律。这里没有固定带宽、旧模型扩域或统计显著性主张。

C0/C1/C2 使用相同全部 A op 数据与 service 定义；两个 copy、间隙及已登记 bookkeeping 保留。record 外 F/P 生命周期差异只能由 A 估计；不得从 B 控制段回填。B 首请求前须冻结系数、状态规则、拒绝域、每 case 分层预测和引用 SHA。若 C2 与 C1 同构，采用 C1。C2 准入要求独立 B 的 worker execute 与 full 关键误差均优于 C0/C1，且改善超过 A/B 观察与共同控制变化尺度；只改善 service 不升级产品成本。

待实施证据：新增源码、CPU 真实原语回放、source closure/isolated CLI、prepared 冻结包；随后 A raw、保护与清理、独立因素判断。任何失败保留新 revision，不覆盖原结果。主 agent 统一 GPU 与运行资源，独审本身不启动实验。
