# 有界模型新会话留出验证：最终独审

结论`evidence_review_passed_prediction_observed`，96项独立检查全部通过，48条来源SHA引用保持。只有`bounded_cost_runtime_r03`产生两项有效holdout，各一次；前两失败都未产生holdout。正式模型与两条事前预测未按新结果调整。本轮完成有限工作点的预测观察，`decision_ready=false`保持。

审查只读本地原始日志、冻结源码与实际结果，没有GPU、SSH或远端写入。入口`qualification.json`，复核脚本及执行时副本为`audit_success.py`。

## 冻结、迁移与正确性

正式模型SHA仍为`e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`，系数digest仍为`3eaac58b63756c6fb0328eec4e6168195fba23467c5da744d843325d27cb61fb`。独立从原训练重算的22项资格保持；旧36MiB组合、两失败和新holdout没有用于fit。新binding与旧model除binding字段外精确相等。

六worker实际BDF、node/GPU/NUMA、PCI NUMA/HCA元数据、通信环境及staging与原校准匹配，当前session/epoch独立建立，mesh与两pair24/45重新warm。源创建后才绑定；HBM/SSD两个16MiB bootstrap完整SHA与旧训练相同。DRAM跨会话只确认冻结generator/seed/schema，未直接比较完整旧源SHA，当前8MiB请求的源/目标全部字节独立校验。

两个plan/prediction在首个request_begin前共同保存，preregistered.saved_ns≤freeze_saved_ns<两request_begin_ns。预测在新请求内按fresh身份重算并与冻结记录精确一致；模型、bound、freeze和两prediction引用前后SHA保持。请求计时从fresh inventory之前开始，必要gate、plan、引用核查和journal等仍在窗内，使用相同controller clock_domain。bootstrap32MiB＋两个holdout8/24MiB共64MiB、六op全部SHA通过。

## 实际误差

误差为(预测−实测)/实测，每配置n=1。单位ms。

| 口径 | local8预测 | local8实测 | 误差 | mixed24预测 | mixed24实测 | 误差 |
|---|---:|---:|---:|---:|---:|---:|
| 服务DAG | 0.280796 | 0.268654 | +4.52% | 13.562767 | 未直接测得 | null |
| 最大目标worker | 0.331326 | 0.311994 | +6.20% | 13.613298 | 14.133330 | −3.68% |
| controller execute | 3.005772 | 3.347495 | −10.21% | 16.287744 | 17.285672 | −5.77% |
| 完整请求→filled | 21.880706 | 23.309653 | −6.13% | 35.162678 | 37.902339 | −7.23% |
| 完整请求→published | null | 46.963841 | null | null | 88.079182 | null |

local新8MiB服务处于原四点经验范围内，支持4–16MiB单op固定项在这个新工作点的粗描述。mixed的最大worker与execute误差较小，但不据此认证所有路径组合精度。其operation记录包含等待：w4 local0.265984ms、remote DRAM target0.732100ms、SSD source12.914913ms，w5 SSD target13.885566ms。两端SSD窗口不能相加，target DRAM也不等于孤立校准的固有服务；mixed精确纯service观测/误差保持null。

实际verify/publish为23.653308/50.175703ms，完整published没有隐藏。正式published预测保持null，因为按校验字节数/对象数的成本模型未识别。full-filled仍只是旧训练控制中位数的粗迁移假说，不用其误差替代服务模型审查。

## 预算、清理与下一步资格

整体session48.450669s含启动、warm、fixture、两请求和STOP，不是恢复时延。DRAM supervisor采样最大值node28/node30/node27为3,483,189,248 / 4,011,237,376 / 4,409,393,152B，各加256MiB低于6GiB。postwarm、monitor及请求前gate的同HIP绝对采样最大上界为1,124,073,472 / 1,281,359,872 / 1,826,619,392B，符合2GiB配额及相应reserve。node27的postwarm/唯一monitor值均为1,811,939,328B，较大的1,826,619,392B来自源建立后的请求前gate。runtime/staging已包含，完整连续peak仍未知。

最终无VALID Catalog，used/transactions全零，三个SSD files=0，RELEASED历史保留。九STOP与九supervisor rc0；九child为owned STOP→TERM/−15、无KILL，fresh PID absent、group empty，三任务容器pids1/noOOM。旧13owner、12container、原XML和全部冻结stage资产SHA保持，没有活跃GPU实验。

这两请求没有比较同内容、同target、同HBM/DRAM预算的候选布局，无法认证细排序或joint/solver收益。三条路径都经过w4，原serial DAG仅调整顺序仍同总服务。本轮可作为后续少量、粗差异同预算合同的依据；域外path/cold/大尺寸/多op共享争用和live模型T_ready均未认证。两个既定请求完成后停止实验，不追加误差驱动补测。

正式报告44.1–44.8已只读核对，表格、错误来源、终态与限制一致；已向主指出44.8应把node27最大上界来源标成“postwarm/monitor及请求前gate”，避免把源建立后的样本错称monitor峰值。

可复核命令，现有输出目录不可覆盖：

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/review/audit_success.py --actual codex_coding/results/v0.1.23.3/task2/bounded_cost/execution_actual_r03 --out codex_coding/results/v0.1.23.3/task2/bounded_cost/review_final_r03
```
