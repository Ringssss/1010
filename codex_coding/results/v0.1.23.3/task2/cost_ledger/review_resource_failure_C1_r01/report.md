# C1现场资源停止与清理独立审查

本次现场准入失败，清理证据审查160项通过。实际session为 `cost_ledger_runtime_C1_r01`，GPU worker启动一次，生命周期18.584300s；校准请求0、source bootstrap0，无C1样本或新系数，C2因资源准入失败跳过。该审查PASS只表示停止原因与清理证据一致。

| 同HIP观测 | total bytes | free bytes | total−free bytes | GiB | 本任务PyTorch reserved |
|---|---:|---:|---:|---:|---:|
| startup，mesh前 | 68,702,699,520 | 10,708,058,112 | 57,994,641,408 | 54.01171875 | 0 |
| mesh后最终inventory | 68,702,699,520 | 10,487,857,152 | 58,214,842,368 | 54.216796875 | 67,108,864 |

`execution/common.py:51` 检查同HIP设备占用上界＋96MiB预留＋256MiB安全余量≤2GiB，postmesh原始值明显超过该门；即使不计预留，上界也已超过。此处是保守任务占用证明未满足，不是设备剩余空间不足以容纳192MiB payload的证明。原门槛保持，无测量后放宽或复跑。

64MiB是原生world1 mesh的staging以及PyTorch allocator计数，不能代替包含context/runtime/其他分配的任务HBM总账。54GiB级设备占用在本任务mesh之前的startup inventory中已观测到，但该时点晚于worker创建；现场只读前置readback未调用GPU API，也没有与该占用对应的外部进程/分配身份。因此其归属为设备级匿名占用未知，不能指认某个其他用户、容器或预存在工作负载，也不能把54GiB都称成本任务实际申请。

最终Catalog/used/transactions空，SSD不存在；唯一supervisor精确对应冻结plan，STOP后仅TERM，child rc−15，PID6869及进程组为空，collector未再发信号。node27原4个受保护容器、0个登记旧owner、原XML SHA保持，原135个资产版本和新增11项部署资产合计146项前后SHA一致；所用原容器incarnation不变、pids.current=1、新master29981端口空、无OOM。DRAM采样仍在6GiB上限内，连续峰值未知。

原始引用与逐项结果见 `qualification.json`。后续若要再次申请现场实验，应先建立符合原任务上限的运行时/设备占用归属证据或等待现场条件变化，使用新run目录与fresh准入；本次不产生预测资格，不补留出、不调模型。
