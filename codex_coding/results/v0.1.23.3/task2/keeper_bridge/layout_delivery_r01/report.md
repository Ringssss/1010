# 冻结布局适配交付与精确 readiness

开发编号 `v0.1.23.3/task2`。任务B交付新增产品 `main_lib/fluidckpt/benchmarks/frozen_layout.py`、`keeper_bridge/layout/run_frozen_layout.py`、CPU测试和README；旧freeze/topology_cases/basic_scenarios/planner/worker源码保持。GPU仅父agent统一启动一次 T2 功能检查，子agent未自主启动。

## 已验收行为

`layout_cpu_r02` 9项通过：所有候选copy独立可选；global与位置页计账相同；重复块physical/build双计；preview/旧epoch/lease/错误allocation拒绝；真实ID/generation/current epoch绑定；注册零HTTP/GPU；完整logical range覆盖；TP互补反例里singleton边际停于空集，而paired策略取得两个rank副本。初始logical/rounded测试错误与更早语法错误均记录 `layout_cpu_initial_r01`，CPU错误不算GPU样本。

三场景使用相同有限candidate/复制机会/预算/目标/expected objective/共同LP评价/共同interleave，将paired_fastest、paired_resource_lp及现有single marginal三种简单选择中的强者与已有joint_scenarios比较。结果均同布局及完整有序取数签名，登记joint→strong_simple alias并停止重复GPU策略比较：

| 场景 | 两者选择缓存 | provisional objective/s | 最终CPU结果 |
| --- | ---: | ---: | --- |
| T1 | 8GiB | 0.9356839986285715 | layout_compare_t1_r01 |
| T2 | 4GiB，2+2 | 1.6572605328761905 | layout_compare_t2_r02 |
| T3 | 0GiB | 2.3443239370666666 | layout_compare_t3_r01 |

T2 6+2/8GiB是预算上限，不强制选满。0.5/0.5为冻结的合成互斥目标权重。以上均为CPU功能估计，既非实测性能，亦不证明模型预测准确或无其他配置收益。T3原profile的0缓存同解不消除其生产共享归因缺口。

## 唯一完整模型功能观察

父agent在CPU与独立静态审查后执行：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/layout/run_frozen_layout.py --case T2_uneven_budget --layout split_rank_prefix --target-slot target_b --method fastest --out codex_coding/results/v0.1.23.3/task2/keeper_bridge/layout_smoke_t2_r01 --execute
```

`layout_smoke_t2_r01` raw summary passed。共用rank_interleaved真实op顺序为 `[0,3,1,4,2]`，TP0从worker0向2恢复，TP1从worker0及1向3恢复。三段HBM逻辑流量合计6GiB，其余从完整DRAM恢复；第四个已建立副本是同rank1前缀的第二份，仍完整计入allocation与建立成本，即使未选入恢复op。四实际缓存ID均为 `basic_84b0cb91_layout_smoke_t2_r01/cache/{0,1,2,3}`，generation=1，epoch绑定当前node27 worker0/1目录，未使用preview ID执行。

| 项目 | 实际结果 |
| --- | ---: |
| 额外HBM allocation / build bytes | 8,589,934,592B |
| worker0 / worker1 | 6,442,450,944 / 2,147,483,648B |
| 唯一 / 重复内容 | 6,442,450,944 / 2,147,483,648B |
| 完整DRAM / 完整目标allocation | 各70,329,720,832B |
| 目标logical SHA字节 | 70,329,718,112B（与页对齐allocation分开） |
| 已有staging | 268,435,456B |
| cache_build_s，窗口外含SHA | 18.2031857566908s |
| online_planning_s | 0.028988306410610676s |
| prepare_s | 0.19209960382431746s |
| T_ready，同目标主机max ready−min begin | 2.827939181s |
| 全量SHA / 固定32-token输出 / 真实worker顺序 | 全部通过 |

owner/format/pool setup本轮为0（复用既有现场），不声称首次建立成本为0。物理峰值仅点采样，`physical_peak_bytes=null`；staging catalog点样本峰值256MiB，不冒领连续硬件峰值。

两个缓存worker catalog/allocated/reserved/free均恢复差0；两个目标worker catalog/allocated/reserved差0，physical free各减少6MiB，落在预登记256MiB容差内。底本、无关catalog、旧14可达keeper及8既有不可达状态、冻结输入源码保持。cleanup_errors、remaining_owned_ids、unreleased_endpoints、pending_resume_endpoints及source_hash_mismatches全部为空。保留两parked引擎和四keeper；成功目标释放command/metrics记录沿原协议保留。

## 参数、readiness与剩余缺口

实机通过范围严格为 **T2 split_rank_prefix / target_b / fastest / rank_interleaved**。T1、T3及T2其他layout/target/route仅具有CPU/薄适配入口，本轮未测，不传播passed。CPU profile仍为冻结functional provisional：local_dram35、local_hbm80、peer35、network20GB/s，GPU ingress/egress80、DRAM50、network_node30GB/s、serial_target_execution=true。本driver不改变NIC、GDR或worker执行器，不把隔离微probe或该功能观察回填成应用有效速率。

T3 `physical_contention_claim_ready=false`，其他应用预测 `prediction_ready=false`；NIC/GDR最终实际transport以及连续物理峰值不是这次功能验收的独立因果测量。下一步如论文决策需要这些参数，按既定最小路径证据门槛处理。当前三组同解无需重复完整模型比较。

## 证据路径及指纹

所有下列路径相对 `codex_coding/results/v0.1.23.3/task2/keeper_bridge/`；此报告是说明派生，新增GPU样本0，引用父执行的唯一完整功能观察1次。

| 原始/CPU证据 | SHA256 |
| --- | --- |
| layout_smoke_t2_r01/summary.json | 6bbaa8904e50785c109f45486e4412e5cec027a7176f499191992f03d26998ee |
| layout_cpu_r02/summary.json | 435352ce7c00c6e59539c82383ca386357f98a54bbc8922da881b58115bf3cf8 |
| layout_compare_t1_r01/summary.json | 87afcbd34a120891ffa18eb9fefc7350184d5443b9d6f28b649f53cfea98d51b |
| layout_compare_t2_r02/summary.json | f3a2258e1ee5cd16e9c2fb71630f9b21f69a21ca9c3e2928761fd1243f0364d0 |
| layout_compare_t3_r01/summary.json | 7b87a7ebab9bc5a622b0490a467633015324b3472be085fe5c713a31339ce652 |

完整命令、资源准入及失败时保留owners的清理风险见源目录README。运行闭包55项、frozen manifest/case/source input及inputs副本hash在driver preflight/cleanup通过；旧manifest仍可单独复核。
