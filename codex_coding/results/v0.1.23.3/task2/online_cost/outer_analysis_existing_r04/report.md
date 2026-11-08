# 已有两节点恢复外层开销分析

仅分析 r02 原件；未启动 GPU、SSH、Docker，未改预测或历史结果。

| 窗口（ms） | C0 首次恢复 | Hnear 后续 | Hremote 后续 |
| --- | ---: | ---: | ---: |
| 请求→首 rank | 247.042 | 80.502 | 78.507 |
| rank 到达差 | 216.691 | 2.622 | 0.322 |
| 末 rank→末导出 | 247.529 | 248.370 | 242.843 |
| 末导出→末映射 | 90.831 | 189.085 | 84.672 |
| 末映射→首复制 | 2.893 | 2.750 | 2.414 |
| 末复制→ACK | 11.329 | 10.071 | 10.163 |

以上互不重叠窗口与复制跨度合起来精确重建在线主指标。逐 rank 窗口相互重叠，只用于定位，不额外相加。

两 rank 的 load_begin→model_layout_end 为196.29–205.13ms，后续导出窗口39.73–43.24ms，总 load→export 为236.02–248.37ms；当前证据中基本稳定。前者包含布局/materialize相关工作，后者包含IPC句柄导出、同步与文件发布；已有事件不能继续拆出各自纯开销。

C0 请求到首 rank、rank 到达差明显更大。Hnear keeper accepted→mapped 为147.21/164.45ms，C0为62.26/64.60ms，Hremote为60.32/61.72ms；prepare RPC壁钟相应为82.43/182.84/79.55ms。后续两次之间仍有约100ms映射差异，外层不是已识别的固定首调用常数。

C0 calibrate controller为PID7613，Hnear/Hremote validate controller为PID9259；引擎rank PID259/260、keeper PID46/47相同。因此恢复序号、布局、时间/调度、controller进程同时变化；首次恢复状态迁移是强假设，尚未证明唯一首因。

Hnear/Hremote的搬运DAG高估9.992/7.472ms，C0共同外层高估274.773/389.295ms；后者解释约96.49%/98.12%的带符号总误差。保留原Hnear10%失败与整体未认证结论。

建议下一新会话只做四次完整恢复，每次一次：完整C0 prime→相同C0外层校准→同时冻结两留出→Hremote→Hnear。顺序反转用于打破旧Hnear第二次/Hremote第三次的固定关联；单会话不能独立证明顺序因果。默认不追加第二个warm C0或第五次恢复。

六路径×两实际尺寸成本表直接复用r02冻结原件，来源版本/raw引用不变；新incarnation与旧成本表的迁移是事前声明的待验证假设。新source/epoch身份另核，不冒充同一incarnation或重写原校准来源。预测只替换一次新warm C0精确残差，保留原DAG；不平均留出、逐layout拟常数或把实测mapping窗口当oracle预测输入。

prime仍是70,329,718,112B完整真实恢复，记录首次冷恢复主指标，SHA/固定输出/真实物理park三项全查。若prime单列setup成本，模型认证范围必须明确为同一已prime incarnation的后续恢复；首次恢复成本不被隐藏，也不因prime移出留出而声称冷启动准确率通过。

prime、校准、freeze、两留出由一个controller进程贯穿；每次主时钟仍在必要inventory/plan之前开始，含materialize、IPC、GPU同步、TP barrier并在双rank weights_ready和完整resume ACK后结束。SHA和固定输出仍在主窗后，物理park和资源/lineage/NUMA/epoch/cleanup门保持。

留出逐案10%门，任一失败保留原件并停止，不回填或继续试到通过。此分析不认证新成本模型。完整提案及冻结表来源见measurement_proposal.json；逐rank原始窗口与来源SHA见analysis.json/provenance.json。
