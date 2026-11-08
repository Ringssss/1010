# 同预算多节点场景契约

本目录只构建、校验与冻结少量代表case，复用现有`basic_scenarios`源准入/账本、`restore_plan`及`joint_scenarios`接口。没有GPU、SSH、keeper mutation或实机恢复入口。

`T1_simple_control`为本地4+4GiB强简单控制；`T2_uneven_budget`为外部指定6+2GiB、两个互斥目标各0.5合成需求；`T3_shared_resources`为4+4GiB共享来源的条件物理场景。三者global额外HBM均8GiB，完整DRAM、TP2身份、目标全量分配与staging固定计账。外部cap不表示物理free容量，候选标`preview_only`，不能注册为已存在来源。

校准输入为`topology_calibration.v1`，绑定`topology_id`与owned overlay SHA256。校准agent可输出RCCL/H2D微probe证据；只有额外的`application_profile`明确`executor=serial`、`measurement_status=verified`且含逐路径有效速率与可达路径，才使用其profile。缺失时CPU预览显式采用`functional_provisional_assumption`，不能据估计值宣布优势。未知反向/peer路径不从其他方向推断为已测。RCCL并发压降不自动证明现有serial恢复执行器存在物理共享瓶颈。

使用已有包含numpy/scipy的环境：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/scenarios/test_contracts.py --output codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_tests_r01
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/scenarios/freeze_case.py --output codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_freeze_r03 --preview --include-joint
```

冻结目录包含`inputs/`原始快照、`cases/`、`adapter_inputs/`、`previews/`、`manifest.json`与`comparison.md`。输入/代码/config哈希与已排序operation签名可核对；目录已存在即拒绝覆盖。`--calibration <json>`可以绑定新校准证据，输出必须选新目录。`register_case.py --manifest <manifest.json> --case T2_uneven_budget --scenario target_b --output <new_directory>`核所有冻结hash，生成单目标的`runtime_case_preview.json`、已有planner的`method_plan_preview.json`与`registration.json`；它只注册CPU计划，不联系keeper。

readiness拆分为`plan_adapter_ready`、`restore_runner_ready`、`prediction_ready`与`physical_contention_claim_ready`。T1/T2可功能冻结与计划注册，即使全profile未校准；当前`restore_runner_ready=false`。T1既有run_layout需接受共用rank交错schedule；T2还需外部layout/per-location caps及每次一个互斥target绑定，下一阶段实施这些薄适配。本轮不把JSON与CPU计划宣称为完整模型一键恢复。`--inventory <readonly summary.json>`可将更新的GPU/NUMA/IO snapshot用于余量检查，要求owned overlay SHA、resource admission与protected catalog签名通过；快照不会自动变成带宽校准证据。

baseline身份分开：现有host/fastest/LP是共同执行器机制；native/Blitz/Store完整TP2路径为实际硬件适配，其部分来源或同预算路径尚未映射；原版完整系统仍未复现。Blitz完整active source与Store66GiB pinned pool不隐藏计费，也不会用fragment共同执行器冒充原系统。

所有方法共用rank交错执行改进、合法IPC机会与ready后校验位置。CPU同操作签名形成decision alias，阻止没有其他机制差异的重复GPU比较。`joint_scenarios`有限候选预览只检查已有接口：其可复制extent，固定人工布局不构成同复制机会的强简单placement对照；该接口缺口必须在后续e4登记解决，不能把预览optimal当性能证据。

下阶段先补当前执行器所需路径证据、同机会强简单placement对照和既有runner的冻结配置适配。setup账本须拆cache/owner/format/pool/planning/prepare；runtime preflight与实测物理峰值仍是实机门槛。此交付不启动大规模e4/e5、不扩新求解器。
