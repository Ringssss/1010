# C0原生CPU账本独立审查

独立检查215项通过。证据为 `qualification.json` 中的逐项检查及SHA引用；输入为 `execution_cpu_r04`，未启动GPU、SSH或修改产品。

核对原始请求与规范化行、完整原生execute方法父窗、日志/切片循环/land/sync互不重叠子窗、copy子窗嵌套、N/K/实际chunk向量，以及独立CPU后端的实际copy/sync字节和源/目标偏移。同时检查原生浮点记录与原生clock调用差值、源epoch/generation/identity、预登记先于在线查询、完整SHA逐op覆盖、精确释放结果、最终空Catalog/used/tx。

源码边界：产品 `main_lib/fluidckpt/cluster/worker.py:203`、`:217`、`:239`、`:260`、`:268`、`:275`；覆盖层 `execution/worker.py` 继承原执行循环，完整父窗包住 `super().execute(body)`。clock proxy仅替换原生模块的time引用，保留原浮点返回值；相邻ns伴随采样与原浮点计时的差额留作诊断，1µs仅用于同一原始值的表示检查。service包含begin log、在end log之前结束，worker还包含set_device与方法退出。

闭合证据是独立父窗对原始子窗的包含/顺序/重叠检查，并显式留下无归因空隙；不是定义residual再证明加总。copy_submit与device_sync按真实wall语义记录，不解释为裸CPU或裸DMA。CPU后端、注册NUMA owner、inventory与synthetic checksum是mock，完整bytearray复制及全SHA走原生协议；CPU结果不认证物理性能、现场资源或模型预测。

前置问题已反馈并修订：C2不能仅凭prediction引用存在而启动；需冻结重放、非CPU校准、独立原始资源/模型审查与新session。最终包及角色隔离CLI审查待主agent生成冻结包后执行。服务/worker系数和预测资格要由真实C1及预冻结门判定。
