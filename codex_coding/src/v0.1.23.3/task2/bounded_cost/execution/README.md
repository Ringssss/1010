# 有限成本模型的独立会话验证

复用已修订的 `cost_calibration` r02、三 quiet 任务容器、原 six-worker/serial、九 supervisor/STOP/collector、原 input XML 与全部 frozen assets。只追加本目录资产和新 `bounded_cost_runtime_r01` 会话；GPU/SSH/stage 的实际执行由主 agent 负责。本轮不修改旧训练、源代码或结果。

固定 cached；实际 warm 仅 24/45。先准备原 16MiB synthetic fixture（三源、两 bootstrap），再核原路径物理 node/GPU/NUMA/BDF/HCA/PCI/通信配置、完整部署源码及 XML SHA、fresh boot/PID/start/epoch、当前预算。新 source ID/epoch 必须新建；HBM/SSD 两 bootstrap 的完整 16MiB SHA 与原训练一致，DRAM 使用原 frozen generator/seed/schema 并独立核每个请求的全部字节。原训练身份保留，迁移模型的 fresh incarnation 与实际 warm 另存证据。

仅两个正式请求，各一次：local w4 HBM→w4 单 op 8MiB；mixed w4 HBM→w4、w2 DRAM→w4、w4 SSD→w5 各单 op 8MiB，共 24MiB。两 plan/prediction 与 bound/source model 的 SHA 同时先于首测冻结；local 实测不改变 mixed 参数。全过程控制、fresh inventory、gate、planning、内层 prepare/execute、全部 SHA 和发布证据保留。filled 粗基准与 service/execute 误差分列；published 预测为 null，实际 published 独立报告。

每 node 6GiB DRAM / 2GiB HBM；fresh 准入保留 256MiB DRAM / 64MiB 新 HBM 上界，监控只解除 idle 谓词，身份/配额仍完整核验。监控采样不是完整连续 peak。确定失败统一 STOP，不替换容器/boot/GPU，不启动训练/AB/补测。finally 精确释放 own epoch/identity/range 的源与目标，核 Catalog/used/transaction/SSD 清零；九 child/supervisor 与保护旧 owner/container/XML 收集到新结果目录。

先做只读 preflight，允许冻结模型尚未生成时使用原 r02 prepared：

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/prepare.py preflight --prepared codex_coding/results/v0.1.23.3/task2/cost_calibration/execution_prepared_r02 --out codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_preflight_r01
```

模型最终冻结后，子 agent 只执行本地 CPU 验证与 build；主审查新 launch/artifact 后执行 stage/session。结果目录不覆盖，重试使用新编号。

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/cpu_check.py --model codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/frozen_model.json --out codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_cpu_r01
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/prepare.py build --model codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/frozen_model.json --session bounded_cost_runtime_r01 --out codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_prepared_r01
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/prepare.py stage --execute --prepared codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_prepared_r01 --out codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_stage_r01
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/session.py --execute --plan codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_prepared_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_actual_r01
```

紧急停止只写冻结计划对应的九 STOP 标记，不发送宽泛信号：

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/stop_owned.py --execute --plan codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_prepared_r01/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_stop_r01
```
