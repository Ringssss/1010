# Baseline loading 完整证据扩展

本表保留原 complete_table 的事实和原版未测列；以下三身份有不同初始来源、输入格式与启动条件。仅作逐观察描述，不计算速度比或算法增益。

本轮已验收性能样本 2；失败尝试 6；既有引用行 44（本轮新增计数为0）。

## 本轮性能观察

| 身份 / run | 终态 / 验收 | 初始目标与来源 | 格式 / cache | T_ready(s) | 首正确token(s) | setup(s) | 全SHA / 输出 / cleanup |
| --- | --- | --- | --- | ---: | ---: | --- | --- |
| BlitzScale 分发策略 HIP 适配/blitz_s3_r02 | passed/通过 | 既有 eager 引擎 parked; 真实远端 active HBM TP2；完整 DRAM fixture 保留 | 已处理 TP2 packed storages; active_HBM | 2.804378593 | 3.292748142 | source_active_owner_setup_s=121.423649820; source_active_owner_build=0.816737521 | True / True / True |
| 海光 vendor SGLang 原生 checkpoint/native_observed_r01 | passed/通过 | fresh engine; NFS 原生 safetensors / Linux file cache observed | 原生 checkpoint → 原生处理后 TP2; observed_file_cache | 122.266963355 | 399.761977791 | fresh_start_to_model_info_s=326.500086745 | True / True / True |

setup：native fresh启动含独立ready后验证；Blitz源owner建立在恢复窗口外；Store转换和pinned pool建立单列。验证开销见JSON/CSV。

## 每次尝试、gate与失败

| run | 身份 | 类别 | 状态 | 错误 | 原始证据 |
| --- | --- | --- | --- | --- | --- |
| blitz_registered_r01 | BlitzScale 分发策略 HIP 适配 | CPU_or_registration | registered | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_registered_r01/summary.json |
| blitz_registration_freeze_r01 | BlitzScale 分发策略 HIP 适配 | CPU_or_registration | registered | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_registration_freeze_r01/summary.json |
| blitz_s3_r01 | BlitzScale 分发策略 HIP 适配 | failed_before_measurement | failed | ModuleNotFoundError("No module named 'numpy'") | codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_s3_r01/summary.json |
| blitz_s3_r02 | BlitzScale 分发策略 HIP 适配 | performance | passed | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/blitz_s3_r02/summary.json |
| native_cpu_r01 | — | CPU_or_registration | passed | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/native_cpu_r01/summary.json |
| native_observed_r01 | 海光 vendor SGLang 原生 checkpoint | performance | passed | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/native_observed_r01/summary.json |
| native_warm_r01 | 海光 vendor SGLang 原生 checkpoint | failed_before_measurement | failed | RuntimeError('sequential warmup did not achieve >=99% sampled page residency') | codex_coding/results/v0.1.23.3/task2/baseline_loading/native_warm_r01/summary.json |
| serverless_format_r02 | ServerlessLLM Store 数据面 HIP 适配 | format_setup | passed | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_format_r02/prepared_data.json |
| serverless_offset_gate_r01 | ServerlessLLM Store 数据面 HIP 适配 | capability_gate | failed | RuntimeError('actual upstream worker failed; inspect loader.log') | codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_offset_gate_r01/summary.json |
| serverless_offset_gate_r02 | ServerlessLLM Store 数据面 HIP 适配 | capability_gate | failed | TimeoutExpired(['docker', 'exec', 'zjn-serverless-offset-gate-r02', 'env', 'PYTHONPATH=/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib', 'HSA_FORCE_FINE_GRAIN_PCIE=1', 'HIP_VISIBLE_DEVICES=0,1', 'LD_LIBRARY_PATH=/opt/dtk/dcc/gcvm/lib:/opt/dtk/llvm/lib:/opt/dtk/lib:/opt/dtk/hip/lib:/opt/hyhal/lib:/opt/hyhal/lib64:/opt/dtk/.hyhal/lib:/opt/dtk/.hyhal/lib64:/opt/dtk/.hyhal/rocm_smi/lib', 'python', '/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/baseline_loading/serverless/offset_gate.py', '--source-checkout', '/home/zhujianian/zhujianian/fluidckpt_zjn/lib_cite/ServerlessLLM-hip', '--out', '/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_offset_gate_r02/worker'], 180.0) | codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_offset_gate_r02/summary.json |
| serverless_offset_gate_r03 | ServerlessLLM Store 数据面 HIP 适配 | capability_gate | passed | — | codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_offset_gate_r03/summary.json |
| serverless_qwen_full_r01 | ServerlessLLM Store 数据面 HIP 适配 | performance_pending | failed | RemoteDisconnected('Remote end closed connection without response') | codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_qwen_full_r01/summary.json |
| serverless_format_r01 | ServerlessLLM Store 数据面 HIP 适配 | format_setup | failed | 首格式尝试误用旧keeper /save API；当前ClusterWorker没有该端点，零payload，后改为只读自身匿名HostAllocation mmap导出。 | codex_coding/results/v0.1.23.3/task2/baseline_loading/summary_cpu_r01/known_failures.json |

## 既有S1–S5历史引用

以下保留先前验收/别名/未测状态，全部为本汇总的旧证据引用，不构成本轮新增实测。小IPC hit沿用owner全SHA门槛，未冒称每hit重新全hash。

| 场景 | 方法 / run | 原状态 | 旧验收 | T_ready(s) | SHA / 输出 / cleanup | 原始来源 |
| --- | --- | --- | --- | ---: | --- | --- |
| S1 | sglang_ipc_hip/e1_c02_qwen_ipc_r01 | historical_reference | True | 0.160595298 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e1/e1_c02_qwen_ipc_r01/summary.json |
| S1 | sglang_ipc_hip/e1_c02_qwen_ipc_r01 | historical_reference | True | 0.153791771 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e1/e1_c02_qwen_ipc_r01/summary.json |
| S4 | resource_lp/e3_c02_qwen_r01 | historical_reference | True | 1.277976811 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e3/e3_c02_qwen_r01/summary.json |
| S4 | resource_lp/e3_c02_qwen_r01 | historical_reference | True | 3.798325372 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e3/e3_c02_qwen_r01/summary.json |
| S4 | resource_lp/e3_c02_qwen_r01 | historical_reference | True | 2.624750835 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e3/e3_c02_qwen_r01/summary.json |
| S4 | resource_lp/e3_c02_qwen_r01 | historical_reference | True | 1.237442493 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e3/e3_c02_qwen_r01/summary.json |
| S4 | fastest/e4_c01_qwen_r01 | historical_reference | True | 1.307431102 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | fastest/e4_c01_qwen_r01 | historical_reference | True | 2.483471737 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | resource_lp/e4_c01_qwen_r01 | historical_alias | True | 1.307431102 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | resource_lp/e4_c01_qwen_r01 | historical_alias | True | 2.483471737 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | fastest/e4_c01_qwen_r01 | historical_alias | True | 1.307431102 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | fastest/e4_c01_qwen_r01 | historical_alias | True | 2.483471737 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | resource_lp/e4_c01_qwen_r01 | historical_alias | True | 1.307431102 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S4 | resource_lp/e4_c01_qwen_r01 | historical_alias | True | 2.483471737 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e4/e4_c01_qwen_r01/summary.json |
| S2 | fastest/e6_c01_qwen_curve_r01 | historical_reference | True | 1.269331478 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S2 | fastest/e6_c01_qwen_curve_r01 | historical_reference | True | 2.770961454 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | fastest/e6_c01_qwen_curve_r01 | historical_reference | True | 1.292621019 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | fastest/e6_c01_qwen_curve_r01 | historical_reference | True | 2.501614807 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | fastest/e6_c01_qwen_curve_r01 | historical_reference | True | 1.291709719 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | fastest/e6_c01_qwen_curve_r01 | historical_reference | True | 2.348826159 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | resource_lp/e6_c01_qwen_curve_r01 | historical_reference | True | 1.267926745 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | resource_lp/e6_c01_qwen_curve_r01 | historical_reference | True | 2.355012325 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S2 | resource_lp/e6_c01_qwen_curve_r01 | historical_alias | True | 1.269331478 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S2 | resource_lp/e6_c01_qwen_curve_r01 | historical_alias | True | 2.770961454 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | resource_lp/e6_c01_qwen_curve_r01 | historical_alias | True | 1.292621019 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S4 | resource_lp/e6_c01_qwen_curve_r01 | historical_alias | True | 2.501614807 | True / True / True | codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e6/e6_c01_qwen_curve_r01/summary.json |
| S1 | sglang_ipc_hip/s1_r01 | new_measurement | True | 0.363468802 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s1_r01/summary.json |
| S1 | sglang_ipc_hip/s1_r01 | new_measurement | True | 0.148797435 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s1_r01/summary.json |
| S2 | janus_host/s2_r01 | new_measurement | True | 1.503805855 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json |
| S2 | resource_lp/s2_r01 | new_measurement | True | 1.330876237 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json |
| S3 | janus_d2d/s3_r01 | new_measurement | True | 2.559228948 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s3_r01/summary.json |
| S3 | resource_lp/s3_r01 | new_measurement | True | 2.443636225 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s3_r01/summary.json |
| S4 | janus_host/s4_r01 | new_measurement | True | 1.388548347 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s4_r01/summary.json |
| S4 | fastest/s4_r01 | new_measurement | True | 1.168984821 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s4_r01/summary.json |
| S4 | resource_lp/s4_r01 | new_measurement | True | 1.267901181 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s4_r01/summary.json |
| S5/software_worker_serial/fastest_target_major | fastest/s5_r01 | new_measurement | True | 4.461190392 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_r01/summary.json |
| S5/software_worker_serial/fastest_rank_interleaved | fastest/s5_r01 | new_measurement | True | 2.645741278 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_r01/summary.json |
| S5/software_worker_serial/fastest_rank_interleaved | fastest/s5_r02 | new_measurement | True | 2.867577637 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_r02/summary.json |
| S5/software_worker_serial/fastest_target_major | fastest/s5_r02 | new_measurement | True | 3.672967243 | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_r02/summary.json |
| S2 | fastest/s2_r01 | decision_only_alias | False | — | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json |
| S2 | fastest/s2_r01 | decision_only_alias | False | — | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01/summary.json |
| S3 | fastest/s3_r01 | decision_only_alias | False | — | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s3_r01/summary.json |
| S3 | fastest/s3_r01 | decision_only_alias | False | — | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s3_r01/summary.json |
| S5/software_worker_serial/fastest_target_major | resource_lp/s5_r01 | decision_only_alias | False | — | True / True / True | codex_coding/results/v0.1.23.3/task2/basic_scenarios/s5_r01/summary.json |

## 原完整表状态矩阵（全部历史引用）

| 场景 | sglang_original | sglang_ipc_hip | janus_original | janus_host | janus_d2d | fastest | resource_lp | blitzscale_original | blitzscale_distribution | serverlessllm_original | serverlessllm_locality |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | 引用/not_adapted | 引用/new_measurement | 引用/not_adapted | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted |
| S2 | 引用/not_adapted | 引用/infeasible | 引用/not_adapted | 引用/new_measurement | 引用/unmeasured | 引用/decision_only_alias | 引用/new_measurement | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted |
| S3 | 引用/not_adapted | 引用/infeasible | 引用/not_adapted | 引用/unmeasured | 引用/new_measurement | 引用/decision_only_alias | 引用/new_measurement | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted |
| S4 | 引用/not_adapted | 引用/infeasible | 引用/not_adapted | 引用/new_measurement | 引用/unmeasured | 引用/new_measurement | 引用/new_measurement | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted |
| S5 | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured | 引用/unmeasured |
| S5/software_worker_serial/fastest_rank_interleaved | 引用/not_adapted | 引用/unmeasured | 引用/not_adapted | 引用/unmeasured | 引用/unmeasured | 引用/new_measurement | 引用/unmeasured | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted |
| S5/software_worker_serial/fastest_target_major | 引用/not_adapted | 引用/unmeasured | 引用/not_adapted | 引用/unmeasured | 引用/unmeasured | 引用/new_measurement | 引用/decision_only_alias | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted | 引用/not_adapted |

## 身份与剩余缺口

- 海光 vendor SGLang 原生 checkpoint：installed vendor loader; 非冻结 upstream 原版 daemon。
- BlitzScale 分发策略 HIP 适配：冻结上游选源/TP-chain 策略；既有 HIP/RCCL 执行器，非原版 CUDA/RDMA/live-serving。
- ServerlessLLM Store 数据面 HIP 适配：真实上游 Store 数据面；非原版整系统/选址调度/frontend。
- 原版系统未测、S5-P物理共享、e7/e8与既有表其他缺口保留；新身份数据不填入旧原版列。
- Store cache仅有prepared新写入证据时，加载前驻留未测；native的mincore也是抽样而非全页/NFS server cache证明。
- 尚未终态passed的JSON只显示pending/未验收；不能用小gate时间或source建立时间代替完整模型恢复窗口。
