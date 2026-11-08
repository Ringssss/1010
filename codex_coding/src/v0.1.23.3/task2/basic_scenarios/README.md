# 基础场景入口

开发编号：`v0.1.23.3/task2`。正式计划见 [e1–e8计划第18节](../../../../../docx/plans/2026-10-04_v0.1.23.3_task2_paper_e1_e8_plan.md)，实测见 [报告第19节](../../../../../docx/experiments/2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)。

本目录是共同执行器的场景基建。Janus式host/D2D不是原版Janus或其HIP移植；源版本位于 `lib_cite/baselines.lock.json`，方法注册表位于 `main_lib/fluidckpt/benchmarks/basic_scenarios.py`。S1使用原IPC专用驱动，S2/S3有完整Qwen运行入口，S4及S5-S有固定布局/软件顺序控制入口；S5-P仍要求实测物理共享资源证据。BlitzScale/ServerlessLLM尚未映射的端到端策略明确unsupported。

后续S4与S5-S软件控制已完成实测，见[完整表最终版](../../../../results/v0.1.23.3/task2/basic_scenarios/complete_table_r01/review_r02/report.md)和正式报告第20节。S5-P物理共享仍未测。ServerlessLLM新增CPU估价/候选筛选适配，但没有端到端恢复计划；原版/机制身份继续分开。

## 已有结果与平台

结果根为 `codex_coding/results/v0.1.23.3/task2/basic_scenarios/`。`evidence_r01`只读旧run；`qwen_bootstrap_r01`为隔离新平台建立；`s2_r01`、`s3_r01`各有两种方法的一次实测。原始目录不可重用或覆盖。

可复用会话为 `qwen_bootstrap_r01/session.json`：node27/29各GPU0/1，完整Qwen TP2 DRAM底本和两个parked引擎。本次保留平台便于后续工作，但下次仍先做只读检查，不把文件中的parked字段当作实时状态。旧22worker平台的node27容器已停止，不能继续把旧session当完整来源。

## 下一次运行

在仓库根目录执行，示例新目录若已存在必须换run ID。仅在现场检查及parked/source准入通过后运行场景。

```bash
python3 codex_coding/src/v0.1.23.3/task2/basic_scenarios/inspect_runtime.py --session codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --out codex_coding/results/v0.1.23.3/task2/basic_scenarios/preflight_next_r01 --nodes node27 node29
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py --session codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --scene S2 --target-slot target_a --out codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r02 --execute
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py --session codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --scene S3 --source-slot target_a --target-slot target_b --out codex_coding/results/v0.1.23.3/task2/basic_scenarios/s3_r02 --execute
```

默认每方法一次；重复两次须有生命周期或歧义验证理由。S2在本地node27恢复，S3从node27活跃源向node29复制，不能跨场景直接比较H2D与D2D优劣。每次恢复的完整SHA在weights-ready后；建立、额外验证、源存活累计成本另列。无持续源端负载测试，不声称已测服务干扰。

失败时检查summary、pending消费者与owner。禁止对parked引擎调用 `/health`，禁止写 `error.done` 将未填充权重宣布就绪。只管理本次bootstrap明确记录的资源，原14keeper与node28服务受保护。

## 显式释放整个平台

确定不再需要本次新DRAM底本和parked引擎时，可用以下入口释放新平台；该操作会丢弃其保活内存，必须按任务需要安排。它不清理旧mesh。

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py --cleanup-bootstrap codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --out codex_coding/results/v0.1.23.3/task2/basic_scenarios/fixture_cleanup_r01 --execute
```

CPU契约、失败路径与汇总器测试分别是 `test_contracts.py`、`test_runtime.py`、`test_evidence.py`、`test_summary.py`。CPU合成fixture不作为性能样本。

## S4/S5与完整表入口

`run_layout_scenarios.py`复用现有session和ScenarioRun。S4在target_a创建固定8GiB缓存，比较host/fastest/LP；S5-S在source位置每rank分两段保留，向target_b恢复，只改变全局操作顺序。S5-S显式复用S4预算/覆盖准入并增加软件依赖验证，不宣称通过物理fabric门槛。

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_layout_scenarios.py --session codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --scene S4 --out codex_coding/results/v0.1.23.3/task2/basic_scenarios/s4_r02 --execute
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_layout_scenarios.py --session codex_coding/results/v0.1.23.3/task2/basic_scenarios/qwen_bootstrap_r01/session.json --scene S5 --reverse-schedules --out codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_r03 --execute
```

这些是后续入口示例，不表示应继续重复当前已通过配置。场景配置在`configs/scene45_protocol.json`，反向开关不修改该文件；原始输出目录不重用。`test_layout_scenarios.py`检查同操作多重集合、各目标顺序及实际worker记录等约束。

`complete_table.py --help`列出完整表生成参数，输出`report.md/summary.json/table.csv/measurements.csv/inputs_manifest.json`。当前权威导出是`complete_table_r01/review_r02`，首版与review_r01保留为导出修订历史，不混为新GPU样本。S2/S3 fastest与S5原序LP的离线预览只提供决策别名，不生成时延或新增样本。
