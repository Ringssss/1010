# 首次实机失败与清理独审

结论`failure_cleanup_passed_validation_not_started`，71项独立检查通过。正式模型保持冻结，holdout尚未执行，没有预测误差可报告。审查仅本地CPU读取，没有GPU、SSH或远端写入。

`bounded_cost_runtime_r01`整体19.565762s包含启动和STOP，不是权重恢复时间。新runner在mesh之前调用`Cluster.inventory()`；native worker的rank此时仍为null，严格接口检查连续endpoint/rank顺序而拒绝。原runner在mesh后才使用这一接口，这是新增前置empty检查导致的生命周期顺序regression。stderr、失败journal和旧源码保持；cleanup中的同一库存检查也失败，不能把Catalog层finally宣称完整通过。

startup六worker的replicas、used与transactions均为空，journal owned为空，尚未mesh/warm、创建fixture、绑定模型、冻结预测或执行holdout。实际请求数和校验payload均为0；`experiment_completed=false`、`model_migration_validated=false`、`holdout_prediction_validation_completed=false`、`decision_ready=false`保持。正式模型SHA仍为`e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`。

九STOP写入与九supervisor命令均rc0；九child按owned STOP→TERM/−15退出，无KILL，精确collector核PID absent与group empty。三任务容器为同CID/StartedAt、running、pids1/noOOM；旧13owner、12container、原XML和全部stage资产SHA前后保持。未建立own权重对象且全部owned进程回收，未留下新fixture；Catalog最终API检查失败单独保留。

各node startup同HIP绝对HBM占用上界为675,282,944B，低于2GiB。各node supervisor DRAM采样最大值加256MiB低于6GiB；没有postwarm预算gate、模型payload预算监控或完整连续peak证据。资源情况不能升级为一次成功恢复资格。

可复核命令如下，输出目录已使用，未来复核应换新目录：

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/review/audit_failure.py --actual codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_actual_r01 --out codex_coding/results/v0.1.23.3/task2/bounded_cost/review_final_r01
```

主agent已授权仅修复mesh前后库存接口顺序并增加CPU生命周期fixture；未执行holdout，因此后续新session是脚本修正后的首次有效验证机会。不得改变模型、新增配置或把本次失败当性能重复。
