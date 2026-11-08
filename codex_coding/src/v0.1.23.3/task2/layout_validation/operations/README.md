# 四布局资源与监督薄入口

开发编号保持 `v0.1.23.3/task2`。`control_layout.py` 导入冻结的 `minimal_calibration/measure/orchestrate_integration.py`，复用 SSH、原件记录、keeper/source 守卫和有界 HTTP 启动等待。产品 Worker、输入 XML、原始 r02 与实验配额保持。具体运行由主 agent 在审查后启动。

只读准入已落在 `results/.../layout_validation/operations_plan_r01/`：两端 fullCID/StartedAt/boot/原 source 资产集合、原 keeper/catalog、空容器 pids1、34500/29972 listener 均通过；还刷新了 NUMA/DRAM/可见 owner 与 storage。metadata 独立读取原厂 XML、default mapping、自有 input XML 与远端旧 r02 plan 的 SHA，未借远端自报期望当证据。GPU0 当前非零 VRAM/shared load 保留，不认独占。这里只调用安全 metadata/sysfs，未导入 Torch/HIP、SMI、27/29或发送 signal。

新 `operations_plan_r01/launch_plan.json` 相对旧 r02 仅将两 Worker master 改为 `12.11.3.28:29972`、dump 改至 `minimal_calibration/integration_layout_worker_session_r01/`。RPC 34500、GPU mask0/4、NUMA0/4、gdr0/IBext_v8、原输入 XML、session `minimal_calibration_r01`、每节点新增 HBM/DRAM 2GiB和容器8GiB外层保护不变。两 `*.deploy.command.json` 只用 noclobber 在现隔离 stage 新建该 plan，不部署新产品代码。

主 agent 一次启动命令（下列实际运行会启动已授权 GPU worker；本子 agent 没有执行）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/layout_validation/operations/control_layout.py parent-run --execute --plan codex_coding/results/v0.1.23.3/task2/layout_validation/operations_plan_r01/launch_plan.json --spec codex_coding/results/v0.1.23.3/task2/minimal_calibration/integration_actual_plan_r01/session.mesh.spec.json --runner codex_coding/src/v0.1.23.3/task2/layout_validation/runner.py --out codex_coding/results/v0.1.23.3/task2/layout_validation/operations_run_r01
```

启动链：fresh preworker baseline→监听端口复核→只新增 plan→独立读回 SHA→两原 exact-owner supervisor（startup60s/lifetime300s）→HTTP metadata 等就绪（已知正常约16s，最多60s）→fresh guard→原 `run_session.execute(mesh_only=True)` 与 pair warm→postwarm guard→新本地四布局 runner 的原2GiB+8MiB余量 gate/ABCD应用→finally 匹配 SHA 的 STOP→等待两个 supervisor rc→新 after guard/原 XML SHA→fresh exact-owner absent/group-empty→只收集新 worker/log/dump。原 mesh 的记录以 `actual_launch_plan_ref` 绑定实际29972，不生成旧29970的误导 launch preview。

baseline、control、collect 分别新增 `operations_baseline_r01`、`operations_control_r01`、`operations_collect_r01`。应用为 `layout_validation/execution_r01`，原 mesh 为 `minimal_calibration/integration_layout_mesh_r01`，监督目录为 `minimal_calibration/integration_worker_node{28,30}_layout_r01`。原件 stdout/stderr/returncode 保存在每步 command.json/原字节文件。STOP 到达不等于退出，只有两新 supervisor cleanup、fresh exact proc 与后置 guard 全齐才可独立验收。kernel `memory.peak` 缺失保持未知，0.25s supervisor 样本是下界。

`operations_plan_r01/manifest.json` 保留准备时 source SHA；其后薄脚本收口修改由父独立审查及 `operations_run_r01/summary.json.source_refs` 冻结。准备结果不覆盖，任何失败/重试均须新目录并重新明确 run 路径；本入口固定 r01 防止误重跑。

CPU 验证：三文件 `python3 -m py_compile` 通过；只读 prepare 实机通过，GPU 启动数0。四布局执行与资源验收由 runner/独立 review 负责。

## 实际运行后的收集兼容修订

主 agent 已执行上述 r01；原应用/controller/两 supervisor 均 rc0。最后本地 tar 解包因当前 Python 不支持 `TarFile.extractall(filter='data')` 报 TypeError，使父外层 rc1，原 summary 的 `prelaunch` 标签未被最终更新。该原件和执行源码 SHA `f3154366...` 保留，不能把外层 rc1改记为全链自然成功。

新增 `collect_compatible.py` 只重新收集已退出会话到 `operations_collect_r02`，先检查目录、类型、路径与64MiB体积限制，再手工创建普通文件，拒绝链接与其他特殊类型。该 collect-only 已 rc0，未重复任何 GPU 应用、未发送 signal；原 `operations_collect_r01` 部分取证与失败保持。执行源码另存 `operations_collect_r02/executed_control.source.py`，补充结论在 `collection_result.json`，独立 review 再审实际日志/XML与预算。

同原 preworker baseline 的0.25s全寿命采样：28增量2,118,258,688B（余量29,224,960B），30增量2,089,222,144B（余量58,261,504B），均在原2GiB内。两端新 exact owner absent/groupempty、后置 pids1/noOOM与旧保护通过；kernel peak缺失保持，不升级完整物理峰值证明。
