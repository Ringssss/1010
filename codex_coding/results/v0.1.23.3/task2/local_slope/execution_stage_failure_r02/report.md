# 第二次stage：helper主控依赖误引入

主的stage_r02在node28追加mode_file并更新prepare成功后，mode-file实际CLI仍失败。新helper顶层导入common，经bounded/costcal common串到host-only operations/control和minimal_calibration/orchestrate_integration；这些主控脚本不在已冻结任务容器内，报FileNotFoundError。GPU0，模式文件0，node30/node27仍未append。node28已实际有九个task源码，r01八源追加与r02两项变化及snapshot均保留。

stage_r02的append stdout逐项给出mode_file before=null、prepare before=前版SHA；modefile command rc1与stderr保留真实依赖失败。本机selfcheck能import仓库全依赖，未模拟仅helper可用的容器CLI，这是前一次CPU检查覆盖缺口。

最小修订让普通 `--path` 分支仅依赖stdlib，只有host selfcheck分支才lazy importcommon/prepare。`execution_stage_cpu_r02`将仅mode_file.py复制到独立目录，用 `python -I` 真实CLI创建cached模式文件，rc0且内容精确；再次真实CLI拒绝重复。额外验证partial chain：把r02.prior node28全SHA应用实际changed before/after，必须等于r02.setup全SHA；其余节点沿真实prior无变化。

`execution_prepared_r03`据stage_r02真实状态冻结，package仍九源，无额外旧依赖部署。worker、runner、session、八请求矩阵、模型均保持；更新只涉及mode-file依赖修复与prepare对多级partial-stage的必要SHA递推。原stage_assets负责备份node28已存在源的变更，不删除原snapshot。

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/mode_file.py --self-check --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r02 --partial-stage codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r02 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_cpu_r02
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py build --session local_slope_runtime_r01 --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r02 --partial-stage codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r02 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r03
```
