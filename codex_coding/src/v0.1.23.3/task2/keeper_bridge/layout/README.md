# 冻结布局薄适配与强简单布放对照

编号固定 `v0.1.23.3/task2`。唯一冻结输入是 `topology_scenarios/scenarios_freeze_probe_r08/manifest.json`，现场为 `baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json`。本目录代码建设及 CPU 验收不自行启动 GPU。

## 复用与边界

新增产品 `main_lib/fluidckpt/benchmarks/frozen_layout.py` 验证 manifest canonical hash、原输入及冻结副本、旧源码、case、session；旧 freeze/topology/planner/worker 源码未修改。实机 driver 继承 `LayoutScenarioRun`，复用 `ScenarioRun` 的现有 owner 传输、live 目标、全字节 SHA、固定输出、parked 释放及保守清理。每次只绑定一个互斥目标和一个方法，共用 `rank_interleaved`。

CPU 候选 ID 仅用于选布局。建立时创建本 run 独有 ID，逐副本从完整受保护 DRAM 建立并全 SHA 验证；随后以真实 inventory 的 ID/generation/当前 epoch 重绑定。实际字段必须与冻结 identity/TP/位置/范围相等，allocation 精确等于页对齐预算；preview、旧 epoch、缺失副本和 lease 均拒绝进入恢复。

缓存总上限和每位置上限独立验证，允许策略少用预算。重复内容每位置各计一次 allocation、各计一次建立传输；唯一内容字节另列。目标完整 allocation、底本 DRAM、已有 staging 和 guard 分开计。建立及 prepare/在线规划成本记录；物理峰值仅有 inventory 点采样，`physical_peak_bytes=null`，不声明连续峰值已测。

代码冻结复用已有 `blitz_freeze.project_imports` 的 AST 导入闭包，并明确加入 retained worker/engine 入口，避免把无关新模块变更认定为本 run 漂移。全冻结输入在 preflight/cleanup 再核，旧 manifest 仍独立有效。T3 保留 `physical_contention_claim_ready=false`，provisional profile 不能支持预测或生产争用因果主张。

## 强简单对照

复用既有 singleton/paired-rank 边际密度贪心和 `placement_plan(... joint_greedy)`。每个 candidate 复制位置作为独立请求，不按唯一内容去重。pair 集合包含全部不同 TP、相同 off/n 的候选组合，允许跨位置；singleton 包含全部候选。成组分别用 fastest/LP 评分，单项边际用 LP，最终用相同 LP、相同目标权重和 expected objective 选择三者中的强者；相同评分优先少 allocation，再确定性方法名。使用既有 LP/MILP，不增加求解器。

三场景 CPU 对照全部同布局及完整有序取数操作签名，记录 joint 对 strong_simple 的 `do_not_repeat_GPU` alias。CPU 结果如下，数字均为 provisional 服务模型估计，新增 GPU 样本为 0：

| 场景 | strong_simple 与 joint 实际缓存 | 共同模型 objective/s | 决策 |
| --- | ---: | ---: | --- |
| T1 | 8GiB | 0.9356839986 | 同解 |
| T2 | 4GiB（2+2） | 1.6572605329 | 同解；6+2/8 是上限 |
| T3 | 0GiB | 2.3443239371 | 同解；旧模型下无边际收益 |

最终结果为 `layout_compare_t1_r01`、`layout_compare_t2_r02`、`layout_compare_t3_r01`。不以这三组同解启动 placement 的重复完整模型比较。T2 `split_rank_prefix` 固定布局功能检查则覆盖 6+2GiB、8GiB 物理副本、6GiB 唯一内容、2GiB 重复内容及真实绑定，属于必要新接口 smoke，与 4GiB 优化策略不混为算法比较。

## CPU 命令与结果

使用已有 NumPy/SciPy 环境：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/test_frozen_layout.py --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_cpu_r02
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/run_frozen_layout.py --case T1_simple_control --compare-placement --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_compare_t1_r01
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/run_frozen_layout.py --case T2_uneven_budget --compare-placement --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_compare_t2_r02
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/run_frozen_layout.py --case T3_shared_resources --compare-placement --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_compare_t3_r01
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/run_frozen_layout.py --case T2_uneven_budget --layout split_rank_prefix --target-slot target_b --method fastest --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_register_t2_r02
```

以上均已执行，目录不能覆盖；重复检查应使用新 run ID。`layout_cpu_r02` 9 项通过：同复制计账、global 合法但 location 超限、未知/重复候选、真实 incarnation 重绑定、preview/旧epoch/lease/allocation 负例、TP 最大值目标的 singleton 零收益反例、共同复制机会与覆盖、真实建立只使用 base、注册零 HTTP/GPU。

初始 CPU 开发失败保留于 `layout_cpu_initial_r01/summary.json`：测试错误地把 logical copy 与 rounded target allocation 比较（相差2720B），修订为分别验证 logical coverage 和页对齐 allocation。更早的 multiline with 语法错误也登记；均非 GPU 样本。中间 `layout_compare_t2_r01` 和 `layout_register_t2_r01` 保留，最终使用 r02。

## 父 agent 执行的单次功能 smoke

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/run_frozen_layout.py --case T2_uneven_budget --layout split_rank_prefix --target-slot target_b --method fastest --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_smoke_t2_r01 --execute
```

启动与 run ID 由父 agent 协调。driver 设置 core=0。复用现有四 keeper 与两个 parked engine，不创建/停止进程、容器或更改 NIC。新缓存 worker0/1 allocation 为6/2GiB；选中 target_b worker2/3 的完整 rank allocation 各约32.75GiB；已有 staging 各64MiB，准入 guard 默认每位置2GiB。新缓存建立流量8GiB，完整恢复逻辑流量约65.49965GiB，验证另有成本；底本和旧14可达/8不可达 keeper 保持。

父 agent 已执行上述唯一 smoke，`layout_smoke_t2_r01` passed：缓存建立18.203186s，在线规划0.028988s，prepare0.192100s，T_ready2.827939181s；70,329,718,112逻辑字节 SHA、固定32-token输出、四worker实际顺序全部通过。四缓存各2GiB的真实ID/epoch/generation重绑定可查 `builds[0].candidate_runtime_bindings`。最终两个缓存worker allocated/reserved/free差均0；两个目标worker allocated/reserved差均0、free各-6MiB，在256MiB预登记容差内。底本/无关catalog/旧14keeper/冻结输入源码保持，pending/unreleased/remaining为空。

实机 readiness 只覆盖 T2 `split_rank_prefix → target_b / fastest / rank_interleaved` 这一组合。T1/T3及其他T2布局/目标/路由有CPU/薄适配入口，未作本轮实机验收；T3生产共享归因仍conditional。该观察是一次必要功能检查，不作为strong_simple与joint的性能比较。交付报告见 `keeper_bridge/layout_delivery_r01/report.md`。

fresh preflight 查四 worker 事务、完整底本、两个 parked 引擎、catalog/物理 headroom，并只读保护旧22端点的14可达状态。只 cleanup 本 run cache/transaction 和本次 consumer；若 resume RPC 未结束、映射未释放或 consumer 状态不确定，继承链保留 owners，summary 列出 pending/unreleased/remaining IDs，交父 agent 精确诊断，禁止泛杀或清空其他服务。清理后验证旧 catalog、底本、旧14 keeper 元数据、四卡 catalog/allocated/reserved/free 和输入源码。目标成功释放保留本次 release command/metrics 证据，符合既有 runner 协议。
