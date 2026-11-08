# 首操作状态A阶段：主机差额存在，状态规则未识别

本轮A完成8个请求、每请求两copy。按A前冻结判据，state_unidentified，B准入为false；系数诊断保留，frozen=false，B预测为空。
8个请求的nonlog首/次CPU与wall差額均为正，支持本会话有首操作主机成本；当前结果不能把该成本只按thread-first或request-first稳定收费。

| cell/请求 | 执行线程/目标 | op1/op2服务(us) | nonlog首−次CPU(us) | worker(ms) | controller(ms) | full(ms) |
|---|---|---:|---:|---:|---:|---:|
| 1/R1 | F/fresh | 228.393/90.171 | 61.041 | 1.566401 | 4.564922 | 22.006556 |
| 1/R2 | F/fresh | 428.156/92.401 | 225.434 | 1.789634 | 4.996546 | 20.730898 |
| 4/R1 | P/pretouched | 393.025/90.811 | 188.724 | 2.159640 | 4.812225 | 23.198772 |
| 4/R2 | P/pretouched | 384.495/88.001 | 187.743 | 1.534610 | 4.823175 | 20.487106 |
| 3/R1 | F/pretouched | 297.954/101.641 | 110.252 | 1.748404 | 5.113399 | 25.748616 |
| 3/R2 | F/pretouched | 444.866/89.311 | 233.962 | 2.368212 | 5.552804 | 22.596944 |
| 2/R1 | P/fresh | 202.343/87.991 | 45.292 | 1.373859 | 4.542241 | 22.635705 |
| 2/R2 | P/fresh | 186.603/86.791 | 30.440 | 1.317948 | 4.714313 | 20.432254 |

冻结S=5.000us，2S=10.000us。R1四cell及F R2的nonlog CPU/wall首/次差均通过2S与CPU/wall一致门。

P R2首−次CPU：fresh=30.440us，pretouched=187.743us；均大于2S，不能支持纯thread-first重置。

R2 F−P第一op nonlog CPU：fresh=198.243us，pretouched=47.420us；均大于S，不能支持F/P等价的纯request-first解释。

P的fresh与pretouched service首/次差额之差−196.682us，超过目标交互门57.176us；F交互−19.800us通过。A只有每cell一个顺序block，不能把P差额唯一归因为目标写史，也不能排除顺序/执行状态交互。

空成对clock额外p95=0.590us；dummy logger绝对p95=15.650us、波动p95=2.989us。日志有首/次变化，但source_slice/copy_submit CPU也有独立差额，结果不只是logger-only。仪表与日志成本没有从实测扣除。

| 候选 | p(us) | 首项(us) | 解释 |
|---|---:|---:|---|
| C0 | 205.810 | — | 同16op常数 |
| C1 | 90.890 | 229.840 | 同16op请求首项 |
| C2 | 129.822 | 202.635 | thread状态诊断，未准入 |

三候选同A全部16op非负拟合，保留全部首/次与长点；这些是训练诊断，不是B独立预测误差。固定两copy条件下，2*C0.p=2*C1.p+C1.r；若request-first成立，C2与C1同构，无新的请求总成本区分力。本轮因A结构未通过而不打开B，不能宣称完整模型预测收益。

record-gap、set_device、实际线程create/dispatch/join、postdiagnosis、worker其他项按A的F/P与R1/R2分组记账；controller残差、query、prepare（含干预与R1分配）、full其他项全部保留且严格闭合。仅postdiagnosis已有0.664–1.279ms，完整full为20.432–25.749ms；不能把几十us服务改善直接当恢复主指标收益。

测量payload 128MiB、prepare预写/scratch 64MiB、bootstrap 16MiB另列；最终八target全SHA共64MiB，非16轮各独立SHA。独立执行/source/生命周期/清理资格仍由review认证。旧bounded模型未改，本域旧预测null；字节斜率/带宽与published预测均null。

本分析没有新GPU/SSH；只读取A原件。冻结规则和源码未修改，额外脚本仅生成本报告。按预登记止于A，不新增参数、不扩矩阵、不补第三block。

引用：
- /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_actual_A_r01/measure/stage_measurements.json SHA256 d830b88b7005a9ea9249bba3706bc8fed090f22d88780e47601470ea29f15666
- /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/first_op_validation/model_frozen_r01/frozen_prediction.json SHA256 32c7f958a2cdc1b3bb6b9733d031d3d14978398cbe4fd6cff791426a8e36fc79
- /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/first_op_validation/analysis/summarize_A.py SHA256 e5a7e648bdb34e88e4cd6b60a5ec11683bb64637a1874311b1ab96bd31dec615
