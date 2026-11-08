# 四布局验证薄入口

runner 复用原 minimal_calibration/integration/run_session.execute(mesh_only=True) 的完整身份、资格、freshguard、空会话与 mesh/warm 检查；应用循环显式执行 A/B/C/D，每布局一次。原产品及历史脚本不改。布局键 A/B/C/D 对应目标本地、集中28、集中30、交叉。原32MiB chunk、每worker64MiB staging、全SHA后发布不改。父控制器负责启动、freshguard、实物资源采样和匹配实际plan SHA的finally STOP；本入口不执行SSH或supervisor启动。

CLI：`python3 codex_coding/src/v0.1.23.3/task2/layout_validation/runner.py --spec SPEC --out codex_coding/results/v0.1.23.3/task2/layout_validation/NEW_RUN --execute`；mesh阶段另加 `--mesh-only`。应用spec保持session=minimal_calibration_r01、block_sizes_mib=[16]，并设置mesh_initialized/pair_warmed及原baseline_guard_ref/post_mesh_guard_ref。执行必须提供actual_launch_plan_ref绑定父实际启动计划，避免将原函数内29970预览误作本次29972启动命令。目录必须新建，原结果不可覆盖。

固定总cache32MiB、每location cap32MiB、source DRAM32MiB在worker0、target各16MiB。预算复用原sampled runtime门槛，但两端统一应用HBM上界48MiB，每资源再加8MiB动态余量，仍在原2GiB内。每布局重核同postwarm证据SHA/身份/时效和静态上界，并不是每布局重新物理采样；完整物理峰值仍未证。

执行前保存contract、源码和spec SHA与pending/失败元数据；恢复plan严格核对op_id/方向/kind/bytes，完成后核真实transfer.execution逐worker角色（本地both）和顺序。缓存建成、finish后、退役后都保留完整inventory，检查lease、事务和own副本。synth后adopt前失败可能有未入catalog的临时source，父STOP负责整个worker进程释放；失败不冒认物理回收通过。

A两local可独立推进，B local0→forward1，C reverse0→local1，D reverse0→forward1；D不是同时双向实验。仅冻结A通常较短、D通常较长的弱filled假设，B/C未知；不以64MiB DRAM微测预测16MiB HBM，不建立绝对毫秒预测、formal profile或solver增量。

CPU验收：`PYTHONDONTWRITEBYTECODE=1 python3 codex_coding/src/v0.1.23.3/task2/layout_validation/test_runner.py`。
