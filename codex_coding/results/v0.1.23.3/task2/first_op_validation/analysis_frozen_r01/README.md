# 首操作分析接口

本目录仅离线CPU分析，不启动GPU/SSH、不修改产品或旧bounded模型。预登记规则在results/task2/first_op_validation/analysis_protocol_r01/protocol.json。A前冻结本目录源码与protocol；A后只能以新结果目录拟合，不调门槛或参数结构。

`analyze.py A --stage <A stage_measurements.json> --out <new model.json>`生成A因素表、逐项门操作数、同集C0/C1/C2 NNLS、共享成本及8条确定B预测，附同stem中文报告。A状态不明确时不生成B预测；schema失败时不拟合。`analyze.py B --stage <B stage_measurements.json> --model <frozen A model.json> --out <new summary.json>`只评分已冻结预测，并拒绝改变A原件/protocol/分析源码。输出必须是新文件，失败或重试不能覆盖。

输入为execution canonical schema `first_op_validation.v1`。8条请求按A 1/4/3/2、B 2/3/4/1，每cell严格R1/R2。每请求两op，含实际birth/op累计序号、prepare隔离、目标写史、CPU/wall phase和record_total。共享成本包括record_gap、set_device、postdiagnosis、dispatch_create_join、worker_other、controller residual、query、prepare、full_other；worker/controller/full必须整数ns严格闭合。P R2必须提供executor_alive_between_requests=true。R1 fixture分配保留在prepare组；共享成本只由A按F/P和R1/R2冻结。

顶层observer_check提供实际w4独立probe的empty_paired_clock_extra_p95_ns、dummy_log_p95_ns及dummy_log_fluctuation_p95_ns，并保存采样来源。空成对clock额外成本进入非log噪声尺度，dummy log绝对耗时不进入该尺度；log波动只参与最终主指标准入尺度，所有实测开销不减除。B沿用A冻结S。

资格范围必须区分：本程序仅检查canonical schema、可观测状态和算术闭合；原语保持、source lineage、真实线程存活/目标lease、全SHA、预算及清理由执行回放和独立审查认证。新session/birth是必要条件，不能替代完整runtime版本与资源资格。frozen=true只表示系数/规则/确定预测已形成不可覆盖文件，不表示主agent已批准B或模型进入产品。

state_transferred与state_model_admitted分开：B可以复现首操作结构，但完整execute/full增量不超过共同控制波动时仍拒绝复杂模型。C1/C2同构优先强简单C1；需胜C0主指标后才作为候选，产品准入始终另审。固定8MiB不拟字节带宽；旧两copy域外预测和published预测均null，不新增“正率”等无事实量。

CPU检查：`python3 codex_coding/src/v0.1.23.3/task2/first_op_validation/analysis/cpu_checks.py --out <new qualification.json>`。制造fixture只认证公式/拒绝逻辑，不是GPU数据、不计算性能成功率。
