# bootstrap实时/JSON边界修订独审

结论`launch_ready`，31项条件通过；仅本地CPU审查，未GPU/SSH。

从已stage r03冻结包还原runner，独立逐字节比对r04。唯一源码修改是按实际plan op_id识别整数/字符串operations键的content helper，以及先保存bootstrap再核对旧来源。helper核single-op、成功全字节验证、op_id/n、源目标SHA相等，再比较n与SHA；不比较跨session对象ID。其他runner字节保持。

独立重跑整条准备链CPU回放：原native synth_adopt重放三条真实旧source schema，真实`Cluster.verify`合并source/target返回整数op_id，两个旧JSON bootstrap对照通过，实际native planner→迁移attestation→bind→两预测事前冻结→local in-window精确相等后，在第一模拟holdout transfer之前主动停止。没有生成伪造性能观测，native exact_release全清。十项通过；仅输出目录改为审查拥有的review目录。

额外从回放journal及保存binding重构local/mixed runtime plan/predict。两条plan与prediction的JSON往返均精确相等，分别与preregistered结果精确相等，八项边界条件通过。首次重构使用预transfer journal target缺generation，审查fixture失败记录保留，再按保存的绑定lineage补齐真实Catalog generation后通过；不涉及实际源码或模型修改。

r04仅更新一个runner，以已stage r03为准确prior。三node其余setup/资产、原XML、shared helper、配额、process角色与通信配置保持；仅session/mode/dump/output名称换为`bounded_cost_runtime_r03`。正式模型SHA仍为`e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`，两个预定holdout不改，前两失败未执行正式请求。

主可执行新session；实际holdout和误差继续等待实证，`decision_ready=false`保持。
