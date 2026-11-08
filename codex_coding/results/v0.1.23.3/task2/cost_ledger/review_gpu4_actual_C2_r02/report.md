# C2独立审查：原始数据有效，模型提升门未过

原始账本/冻结预测/独立判据138项、物理资源及清理169项通过。审查PASS表示证据和协议执行正确；本轮模型提升失败，最终worker/outer与service选择均为null。

唯一C2 `execution_gpu4_actual_C2_r01` 为新worker incarnation，保持GPU4/NUMA4/BDF85、原96MiB source内容SHA、原执行器/仪表/分块/目标政策。V2→V1两个请求128MiB、6op、7实际copy/sync，另做96MiB bootstrap。完整逐op源/目标SHA、fresh目标及实际copy/land/sync向量通过，最终Catalog/used/tx空。与C1合计恰好8个测量请求、512MiB测量payload、15op、20chunk；两session bootstrap另计192MiB，未增加第三session或状态参数。

仅从C1前五点独立重构Fraction系数及两层中位残差，逐候选核对C2的事前预测、实际值、绝对/相对误差和保守复杂度选择。每个请求的prereg引用均为原冻结SHA `da6b816bb2e3ded4b62997e760055f8aa68aeb5dba3dc9fb955d008b0dba86c4`，提前保存；没有用留出控制开销回填预测。

| 主候选a+bN | worker预测/实际 µs | worker误差 | controller预测/实际 ms | full filled预测/实际 ms |
|---|---:|---:|---:|---:|
| V2 | 610.6379 / 759.920 | −19.6444% | 2.3733729 / 2.642725 | 8.5395359 / 8.635886 |
| V1 | 513.8569 / 710.370 | −27.6635% | 2.2765919 / 2.483633 | 8.4427549 / 10.271848 |

V1 worker违反≤20%相对门，虽绝对误差196.5131µs在250µs内，仍拒绝；外层各自通过不掩盖worker失败。主service的105.2859/144.5939µs绝对误差均超过50µs。

已在C1准入的三项a+bN+cK在C2两点worker/outer数值门均通过，worker误差+0.4528%/−6.1644%。但相对主候选的完整filled绝对误差改善仅39.977267/152.722933µs，均未超过冻结E_filled=575.449µs，所以保留为局部诊断资格，不能提升成完整恢复模型。三项service的V1绝对误差50.020833µs严格超过固定50µs门；不因差量小而放宽或四舍五入救门。四项候选虽留出误差通过，C1结构准入失败仍有效，不能由留出反向救准入。

资源观测最大DRAM1,909,178,368B、同HIP HBM675,282,944B，原预算/安全余量满足；测量期按冻结协议无并发HIP monitor，连续峰值未知。原4容器/0登记旧owner、157部署资产与原XML保持；唯一supervisor rc0、精确TERM后child−15，PID组为空，collector未发额外信号。

独立资格不涉及布局决策或live权重可用：published/live预测null，decision_ready=false，future_error_bound=false。按最多八请求规则收口，不扩C3/C4，不补新样本、参数或会话救模型。r01的137项审查保留；r02补充独立跨session完整source SHA一致性检查。
