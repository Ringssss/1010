# 完整在线代价模型最小协议审查

开发编号v0.1.23.3/task2。本轮仅静态审查与CPU验证，未启动GPU/SSH、未改产品或旧模型。

当前真实processed Qwen TP2共70,329,718,112B，每rank35,164,859,056B。旧bridge来源为qwen_bootstrap_r01历史node27GPU0/1与node29GPU0/1，不证明当前owner可用。旧成本profile均保留；它们的小数据域、旧拓扑与旧执行状态不能升级成GiB模型或当前会话资格。来源SHA与域列表在design.json。

新协议只以完整在线load_begin到全部必要权重可用为主指标，每个场景误差≤10%，不沿用旧50µs和复杂度增量门。全SHA放权重可用之后且仍是执行正确性必需证据；排序另列，不能把场景平均值当逐场景通过。

最小可迁移模型复用原worker顺序DAG。仅需路径按512MiB/1GiB各一次原生32MiB chunk校准；完整模型仍固定1GiB extent quantum、总N/K/C、目标rank布局和次序，末extent805,120,688B落在校准范围。完整合法校准恢复提供一次online−DAG聚合控制/接管残差；禁止空live恢复发READY。共用残差是待验证假说，不是因果逐项识别。

共享receiverHCA场景只在实际绑定与两条disjoint workerpair并发工作点完成后预测。经验共享总字节服务分数与workerDAG取max，不相加、不叫物理容量或严格下界。最终准确性由未经拟合的完整模型请求逐场景检验。

独立留出可以是同一常驻engine/session中的新请求；事前明确registered incarnation、warm状态和run ID，冻结全部预测后才产生留出。原方案默认fresh session的分支仍支持，但本轮不强制跨会话迁移。

候选两类为near本地DRAM+1GiB HBM/rank、remote两helper DRAM+同额HBM。两helper到同node27目标也共享接收HCA，不能重复命名成第三独立布局；若要第三类，必须另有真实不同的同预算来源分配。实际预算、fresh资源、源覆盖和不同校准/留出布局尚待主agent统一冻结。launch_ready=false。

代码为model/predictor.py、freeze.py、evaluate.py和cpu_check.py。CPU18项行为检查通过，包括复用DAG并发、max共享分数、once-only残差、不同布局预测、越域/旧epoch/cold/未测共享拒绝、完整在线10%边界和不能平均消掉失败、冻结变更拒绝及排序反转分列。CPUfixture未获得实际模型预测资格，原中间r01/r02与当前r03均保留。
