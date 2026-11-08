# TP4 分层场景独立审查

开发编号：v0.1.23.3/task2。最终资格为 `passed_cpu_preparation_physical_deployment_unadmitted`。机器契约采用 `contract_cpu_r03`，部署账本采用 `feasibility_r03`；本审查只执行本地 CPU，GPU/SSH/产品修改均为0。最终 `qualification.json` 保存源文件 SHA 与契约绑定。

## 审查结论

44项契约检查、4项原执行器依赖探针、28项独立部署账本条件通过。四个不同目标对应 TP0–3，每个 rank 两个4MiB块；完整 DRAM 底本32MiB、目标32MiB、候选缓存和8×64MiB staging分别计账。所有策略共享完整候选集、底本、合法路径与位置预算。C2合并的两个 source 使用不同 GPU，稳定资源映射只合并出口 U，DRAM/目标入口/worker出口保持原关联。

瓶颈均衡按单候选、全局分数与降序目标服务向量的字典序改善前进，接受全局零收益但向量严格改善。它没有读穷举答案。穷举被单列为固定路由和字节取整下的有限参考。

下表为固定 symbolic 资源/目标容量评分的最优值及达到该评分的最小缓存；时间单位为归一化理论单位。

| 场景 | 最优容量评分 | 最小缓存 | 策略结果解释 |
|---|---:|---:|---|
| C0_local_sufficient | 0.5 | 32MiB | 本地填充、均衡和 joint 同分；原 strict-positive strong_simple 停于空缓存2 |
| C1_equal_plateau_control | 1.25 | 16MiB | 均衡和 joint 同分；原 strong_simple 停于空缓存2 |
| C1_uneven_preregistered | 2 | 0 | base_only、strong_simple、joint均为零缓存2；均衡/填充16MiB同分，被空间—主目标支配 |
| C2_shared_source_export | 2 | 8MiB | strong_simple与joint均缓存rank0的两块；均衡/填充16MiB同分，多用8MiB |

独立计数式为 `max_i(2−3k_i/4)`；C2另外受 `4−k_0−k_1` 的共享出口负载约束。四层都保留同一候选与成本口径。主指标无增量时，降低非关键 rank 的时间属于次级收益，不能抵消额外缓存占用或用来宣称 joint 优势。

## 容量评分与执行依赖

直接调用原 `service_cost.estimate_execution` 的四项探针验证：四个不相交 pair 的8单位服务同时完成于8；一个 source 服务四个目标完成于32；同目标先本地4单位、再远端4单位完成于5；四个不同 source worker仍可能共有一个出口，独立 DAG 分数8与共享 cut下界32不能合成执行保证。

上述最优评分对应容量模型。C2的另一同分布局若rank0/1各保留一块，先本地服务各0.25后才能送尾部，出口U的两单位工作最早完成于2.25，容量评分仍为2。当前 strong_simple/joint保留rank0完整两块避开这一特定延迟，但实际共享服务、启动与模型绑定需要对应执行证据。真实有限恢复与模型 `T_ready` 均保持 unknown。

## 物理部署审查

两/三节点8worker方案和4worker角色复用方案都保持 `ready=false`、`launch_command=null`。单worker约2GB DRAM观测乘以worker数，是保守预算投影；页共享和新增通信组使它不能成为实际峰值或物理下界。两节点8worker的含应用投影约8.14/8.21GB，每node原额度仍2GiB；多worker实际增量未测。每节点缓存允许上界已按四个最终case的位置cap独立映射，staging已包含在历史 HBM runtime观测中，投影没有再次加入。

28/30各一次08:59:04UTC metadata刷新来自 feasibility agent，两次只读SSH；本审查没有SSH。刷新确认容器/boot/已知进程身份，fresh GPU健康、端口、模型内容和 keeper目录完整性未升级。目录/catalog仍引用07:48旧证据。28/30同构8GPU/8NUMA/4HCA及已通单pair不能证明四pair的独立容量；旧 keeper的rank/session/mesh和保护底本也不能充当本轮空闲worker。27/29旧pending原样保留。

## 复现与保留的失败

最终命令均用已有sglang环境，未安装依赖：

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/tp4_scenarios/review/dependency_checks.py --out codex_coding/results/v0.1.23.3/task2/tp4_scenarios/review_dependency_r01
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/tp4_scenarios/review/audit_contract.py --contract-dir codex_coding/results/v0.1.23.3/task2/tp4_scenarios/contract_cpu_r03 --out codex_coding/results/v0.1.23.3/task2/tp4_scenarios/review_contract_r04
/home/zhujianian/miniconda3/envs/sglang/bin/python3 codex_coding/src/v0.1.23.3/task2/tp4_scenarios/review/finalize_review.py --feasibility-dir codex_coding/results/v0.1.23.3/task2/tp4_scenarios/feasibility_r03 --out codex_coding/results/v0.1.23.3/task2/tp4_scenarios/review_final_r02
```

原run不得原名重跑。review_contract_r01采用了过时的staging unknown预期；r02看到中间构建源SHA变化；r03看到中间C2重复(node,gpu)及源SHA变化。最终r03契约修正角色一致性，review_contract_r04通过。review_final_r01用对称选用缓存代替节点允许上界检查保守 HBM 投影，修正审查公式后 review_final_r02通过。所有失败原件保留，源SHA与最终文件SHA见 qualification.json。
