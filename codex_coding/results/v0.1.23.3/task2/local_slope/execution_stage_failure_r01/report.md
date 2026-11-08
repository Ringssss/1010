# 首次stage部分成功与最小修订

主 agent 的 `execution_stage_r01` 在node28追加八个新源码成功后失败：复用了严格限定 `bounded_cost` 目录及session前缀的旧 `bounded_cost/execution/mode_file.py`，向其传入local_slope模式文件路径，返回 `ValueError: fresh bounded task mode path required`。三节点before metadata均成功，但仅node28实际append；node30/node27未append，任何mode-file未创建，GPU启动数0。不是未部署包；不删除node28已追加源或原snapshot。

证据为 `execution_stage_r01/append_node28/stdout.json` 的八条before=null/after SHA、对应command rc0，以及 `modefile_node28/command.json` rc1/stderr。`execution_prepared_r01`与原文件保持。

最小修改只追加local_slope自己的mode_file helper并将prepare引用改为本目录，旧helper不改。新 `execution_prepared_r02` 使用真实各node prior：node28纳入八条实际已append的SHA；node30/node27保持原prior。新package九文件，仅node28已存在prepare会更新，原stage_assets在新revision下备份前版prepare；其他原先追加文件SHA相同可no-op，新helper为追加。新增记录明确引用实际partial stage证据。

`execution_stage_cpu_r01/qualification.json` 通过正确CLI路径、cached精确内容、重复拒绝、错误scope拒绝、node28实际八SHA、其他两node未append及prior选择。GPU矩阵、worker计时器、模型、session角色不变；session仍local_slope_runtime_r01，因为此前GPU未启动且modefile未创建。

修订命令由主审查后执行：

```bash
python3 codex_coding/src/v0.1.23.3/task2/local_slope/execution/prepare.py stage --execute --prepared codex_coding/results/v0.1.23.3/task2/local_slope/execution_prepared_r02 --out codex_coding/results/v0.1.23.3/task2/local_slope/execution_stage_r02
```
