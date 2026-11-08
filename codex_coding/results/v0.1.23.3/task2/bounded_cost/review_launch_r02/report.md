# 启动包r02命名空间修订独审

结论`launch_ready`，18项独立条件通过，无新增阻塞项。仅本地CPU审查，没有GPU或SSH。

stage_r01在共享helper最早的`archive.mkdir(exist_ok=False)`失败：新包使用`execution_prepared_r01`作为revision，与旧cost_calibration快照同basename。原失败stderr保留；没有删除旧snapshot、修改shared helper或启动GPU。

已从r01冻结包的base64还原prepare源码，与r02逐字节对比。唯一源码修订为`revision=out.name`改成`revision='bounded_cost_' + out.name`，独立diff在`prepare.diff`。新revision为`bounded_cost_execution_prepared_r02`；唯一资产SHA变更为prepare.py。新launch除setup_directory外与r01完全相同，session仍为`bounded_cost_runtime_r01`，正式模型SHA保持`e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`。

三node全部其余setup字段与资产保持，shared helper原SHA保持，修订CPU检查通过。主agent可stage r02并按原协议启动既定两个限定holdout；fresh身份/暖态/预算、全字节校验和最终精确退役仍须运行时证据。`decision_ready=false`保持。
