# 首操作状态实验执行收口

A 实际会话完成八请求，执行生命周期、SHA、线程 birth/累计 op、准备隔离与目标 lease、预算样本和精确清理均通过。原始长点全部保留，九 supervisor/child 正常按本任务 STOP→TERM 退出且组为空。

事前 A 状态门槛未通过：`state=unidentified`。按协议 B skipped，原因固定为 `predeclared_A_state_gate_failed`；本轮止于一次 A 会话的八测量请求，不生成 B 性能样本或新模型系数。

128MiB 测量复制、64MiB 准备干预、16MiB source bootstrap 分别留账；八目标逻辑 SHA 为64MiB。CPU A/B 回放、B drybuild 和本地报告均不计真实性能样本。

旧13owner/12container及原XML保持，实际结束的 VALID/used/transaction/SSD均清空。完整连续峰值未知。八个冻结执行源 SHA 核对未变；本收口只新增本地证据，不新增 GPU、SSH、远端写或清理动作。
