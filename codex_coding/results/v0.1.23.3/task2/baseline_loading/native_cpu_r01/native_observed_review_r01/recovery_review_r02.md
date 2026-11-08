# recovery_r01 失败后的实际路径修正

`serverless_target_recovery_r01` 的实际终态为failed。真实transfer已全量SHA通过（70,329,718,112 bytes），生成text_correct=true；失败发生在读取ready inventory，尚未完成release验收或overlay登记。

原因已由实际调用顺序和helper源码确定：`Cluster.transfer(...retain=True)`保留自己的transaction，`ready_workers=retained_inventory(state)`在`cluster.finish(flow['tx'])`之前执行；runtime_support.retained_inventory明确要求所有worker.transactions为空，因此拒绝的是本次自己的合法保留transaction，报`ValueError('mixed keeper sessions or active transactions')`，不能解释成keeper session损坏或内容错误。

本agent前一静态review注意了finish→release次序，但漏查ready_inventory之前helper的这个隐含前提。已经直接给负责SLLM agent修订反馈：finish和flow=None移到ready inventory前，仍保持importer先detach、exporter后release；CPU fake应在transaction存活时主动拒绝retained_inventory，覆盖这条真实流程条件。父agent独立定位与此相同。

finally里的cluster.finish成功（无transaction_cleanup_error），仅新owned engine PID9565经process.py记录停止成功，未覆盖原session，未登记成功overlay。完整SHA和输出作为functional中间证据保留，不把failed恢复当Store或baseline新性能样本。

新修订和recovery_r02由负责agent建设、父agent排程。本agent没有修改其运行文件或启动GPU。
