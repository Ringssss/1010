# 第二次实机失败与清理独审

结论`failure_cleanup_passed_holdout_not_started`，74项检查通过，postwarm/monitor预算补充6项通过。冻结模型保持，两项正式holdout均未执行，没有预测误差。仅本地CPU审查，无GPU/SSH。

`bounded_cost_runtime_r02`整体48.923171s含启动、mesh/warm、fixture bootstrap和STOP，不是恢复时延。六worker mesh和实际必要pair24/45已通过。measure在首次source0的16MiB DRAM→HBM bootstrap之后检查旧内容时触`KeyError('0')`：实时`Cluster.verify`用整数op_id作为operations的键，JSON旧证据将键转为字符串。错误不是SHA不相等；它发生在成功transfer及代码中的全字节校验要求之后。bootstrap返回尚未写盘，因此该次具体SHA摘要没有持久化，不补造digest证据。

仅source0两个own对象建立，finally按完整身份精确release，final Catalog无VALID、used与transactions全零；两个RELEASED历史保留，三个SSD store files=0。尚未创建remoteDRAM/SSD后两来源、bind模型、冻结预测或执行新holdout。正式模型SHA仍为`e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`，`decision_ready=false`。

九STOP与九supervisor命令rc0；九child为owned STOP→TERM/−15、无KILL，fresh PID absent/group empty。三任务容器同CID/StartedAt、running、pids1/noOOM；旧13owner/12container、原XML与全部stage资产SHA保持。没有own模型权重残留。

postwarm同HIP绝对上界node28/node30/node27分别1,124,073,472 / 1,281,359,872 / 1,811,939,328B，各加64MiB reserve仍低于2GiB。DRAM postwarm与supervisor样本加256MiB仍低于6GiB；active monitor一次样本通过，但完整连续peak未测。`budget_supplement.json`保留真实postwarm/monitor核验，并纠正通用失败审查器在qualification资源字段沿用的保守premesh标志；原qualification保持。

主已授权仅规范化live/JSON operation键并增加真实旧bootstrap/synth回放的CPU链检查。新版本需先保存bootstrap再做来源断言，防止再次丢失失败上下文；不改变模型、配置或holdout。此次同样不是性能重复。
