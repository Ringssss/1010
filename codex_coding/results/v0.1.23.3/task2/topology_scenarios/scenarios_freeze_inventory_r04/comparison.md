# 同预算拓扑场景冻结（CPU入口）

完整DRAM、TP2格式和底本身份固定。预算是外部指定实验限制；0.5/0.5为预登记合成需求。互斥恢复场景不会合并成并发需求。

| case | 类别 | 每位置extra HBM(GiB) | 目标 | 状态 | profile |
| --- | --- | --- | --- | --- | --- |
| T1_simple_control | simple_control | 0:hbm=4, 1:hbm=4, 2:hbm=0, 3:hbm=0 | target_a | frozen_functional_contract | functional_provisional_assumption |
| T2_uneven_budget | uneven_location_budget | 0:hbm=6, 1:hbm=2, 2:hbm=0, 3:hbm=0 | target_a, target_b | frozen_functional_contract | functional_provisional_assumption |
| T3_shared_resources | shared_resources | 0:hbm=4, 1:hbm=4, 2:hbm=0, 3:hbm=0 | target_b | frozen_shared_claim_conditional | functional_provisional_assumption |

所有方法共用rank_interleaved执行改进及合法IPC机会。8GiB预算不能建立完整约65.5GiB目标owner；fragment来源不表示原版外部系统复现。

CPU预览的估计值不支持速度排名或真实收益。单流有效速率不是真实容量上界；RCCL并发微probe也不自动证明当前串行恢复执行器物理共享争用。

T1/T2具有合法预算、snapshot余量与functional CPU注册入口；预测校准与物理争用论证分别gate。T1既有runner只需配置接受共用rank交错；T2还需接受外部layout/per-location caps并逐次绑定互斥目标。register_case.py可生成runner所需base/candidates/targets/profile/budget与CPU计划，实机适配属下阶段。

有限候选joint接口、完整底本账本与固定布局取数对照已复用。尚需同复制机会的强简单placement对照；本轮不建设新求解器。

planning/prepare/cache建立/owner建立/格式转换/host pool建立与restore窗口分别记账；目标完整分配、staging和运行峰值单列。

CPU复核命令（使用新output目录）：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/scenarios/freeze_case.py --session /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --preflight /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/basic_scenarios/topology_stage12_preflight_r01/summary.json --inventory codex_coding/results/v0.1.23.3/task2/topology_scenarios/calibration_inventory_r01/summary.json --output codex_coding/results/v0.1.23.3/task2/topology_scenarios/scenarios_freeze_inventory_r04_new --preview
```
