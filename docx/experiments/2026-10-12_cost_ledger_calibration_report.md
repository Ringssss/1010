# 恢复成本账本：八请求校准与独立验证

日期：2026-10-12；开发编号：v0.1.23.3 / task2；总计划18.34、总记录49。

本轮完成计时覆盖层、六请求校准和新会话两请求验证。**成本账本与执行正确性通过；加入操作数的经验模型获得局部worker预测证据；完整恢复模型未获升级。** 本轮没有修改产品加载算法，也没有证明端到端加速或布局规划收益。

设计依据：[成本账本与可辨识校准计划](../plans/2026-10-11_cost_accounting_and_identification_plan.md)。详细实施、失败及命令见[e1–e8总记录第49节](2026-10-04_v0.1.23.3_task2_paper_e1_e8_records.md)。

## 1. 实际执行范围

三个子agent分别建设执行覆盖层、事前冻结分析、独立审查；主agent审代码、协调资源、启动GPU与验收。原生HTTP handler、ClusterWorker.prepare/execute/verify/complete/finish、Catalog租约与复制原语保持。覆盖层只记录真实边界、复制/sync计数与生命周期。

原候选node27 GPU0在mesh后资源门失败：整卡同HIP占用上界约54.22GiB，超过2GiB保守任务门；bootstrap和校准均为0。PyTorch reserved64MiB不能代替完整运行时占用。该启动完整退出，失败保留，不对预算减背景或放宽门槛。

在任何校准点产生前，登记唯一备用候选node27 GPU4/BDF0000:85:00.0/NUMA4，native world_size=1、逻辑rank0。新配置单独冻结；复制/计时/数值门保持，旧GPU0证据不混入。GPU4通过相同资源门，完成C1与C2。未终止其他服务，未扫描更多卡，原XML和全局配置不变。

本轮主测量期间不并发查询HIP资源；保留请求前资源门、前后同HIP采样与supervisor主机内存监控，连续峰值未知。初始化和96MiB源建立单列，不能用整个进程的约20秒作为恢复时延。

最终闭环72项通过：C1/C2 DRAM观测最大分别1,918,480,384/1,909,178,368B，选定GPU同HIP占用上界观测最大均675,282,944B；预算及预留门保持。最终Catalog/used/事务为空，两个worker进程组退出、原容器归于pids=1，157部署资产SHA一致。项目输入XML和默认mapping的可见SHA保持；指定原厂XML路径在取证域前后为null，未据此声称验证了其内容，没有删除或修改该文件。

## 2. 四个时间窗口

| 字段 | 含义 |
| --- | --- |
| service | 原生op记录加总，包含begin日志、分块切片/提交/同步；不包含全部record外工作 |
| worker | 完整原生execute方法外窗，含内部set_device、return/finally、日志与计数；主拟合指标 |
| fullfilled | 请求必要库存查询之前开始，到controller收齐execute响应并确认字节数正确；含查询、准备、控制与观测开销 |
| published | 在fullfilled之后完成既定全SHA验证和副本发布；仍非真实模型weights_ready |

另保留controller execute作为worker与fullfilled之间的层。原float时钟值未改，相邻ns伴随观察与完整父窗独立记录；计费只加同层互不重叠区间。copy-submit是land子窗，不能再次加到worker。sync是设备完成的剩余等待，不是纯DMA；source_slice_and_loop还包含循环/计数开销。

## 3. 全部八个请求

单位均为ms。N为逻辑MiB；K为操作数；C为实际chunk/sync次数。C1顺序在实测前固定，重复L3只用于漂移检查，未加入五点拟合；C2新进程按V2→V1执行。

| 阶段/配置 | N/K/C | service | worker | controller execute | fullfilled | published |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| C1 L3 | 64/2/2 | .461177 | .575768 | 2.474514 | 9.455338 | 222.268659 |
| C1 L1 | 32/1/1 | .352925 | .430086 | 2.116149 | 8.187361 | 118.404502 |
| C1 L4 | 64/2/3 | .534798 | .663269 | 2.518324 | 9.170233 | 218.536937 |
| C1 L2 | 64/1/2 | .423666 | .518466 | 2.281201 | 8.447364 | 207.946326 |
| C1 L5 | 96/1/3 | .540047 | .623648 | 2.363722 | 8.527855 | 313.435244 |
| C1 L3repeat | 64/2/2 | .491027 | .614179 | 2.543154 | 8.879889 | 206.879291 |
| C2 V2 | 80/3/4 | .614589 | .759920 | 2.642725 | 8.635886 | 259.253966 |
| C2 V1 | 48/3/3 | .560336 | .710370 | 2.483633 | 10.271848 | 141.866208 |

C1恢复384MiB/9op/13chunk，C2恢复128MiB/6op/7chunk，共512MiB/15op/20chunk。两个新会话各bootstrap96MiB/3chunk另计，执行器payload合计704MiB/26chunk。每个逻辑操作的源与目标均完整SHA比对；704MiB是按op逻辑覆盖计一次的checked_bytes总量，不是总硬件流量，也不是把两端重复加总后的流量。

## 4. 时间到底花在哪里

以留出V1（48MiB、3op）为例，完整worker为0.710370ms：

| 互不重叠worker分段 | ms |
| --- | ---: |
| begin日志 | .106151 |
| end日志 | .095663 |
| source切片与循环 | .122631 |
| land/目标切片与复制提交 | .161193 |
| device sync等待 | .141451 |
| set_device | .002700 |
| 未归因间隙 | .080581 |
| **合计** | **.710370** |

源/目标切片、提交、日志和同步都占有可见时间；不能把整个0.710ms除以字节数称作裸HBM带宽。上述是实际观察mode下的成本，计数和计时开销未从主指标扣除。

同一次请求的库存RPC为4.741324ms，prepare RPC为2.161269ms，execute RPC为2.475213ms；预算检查、计划构造、记录与其余间隙也保留，最终fullfilled为10.271848ms。controller execute的独立边界为2.483633ms，与RPC子窗相近但不同，不能重复加。

这表明**32–96MiB的当前合成小请求中，查询/准备/控制部分比本地worker搬运更大**。它不外推为大模型或跨节点总是由控制主导。

V1的verify RPC为127.331111ms，complete RPC为4.212797ms，published为141.866208ms。C1的verify RPC约108.565–303.249ms，当前完整SHA验证协议是发布阶段的主要开销。它验证了内容正确，但不能把去掉校验后的filled称作published或真实模型可用。

## 5. 哪些成本可预测，哪些仍不能分别识别

预登记候选为a+bN、a+bN+cK、a+bN+cK+dC，均用相同五个C1请求拟合。原始有符号系数保留；非负性为事前准入假设，没有clamp或删长点。

C1的worker锚点漂移38.411µs、service漂移29.850µs，通过对应59.49735/47.6102µs门。观察尺度E=83.030µs由dummy logger变化尺度主导；E不是置信区间。

worker直接对照c=57.302µs、d=87.501µs、32b=0.879µs，均未超过对应信号门；service的32b直接差为−2.880µs，原值保留。四项结构仍未分别识别。增加chunk同时改变chunk尺寸分布，不能把d唯一解释为一次sync费用。

两个准入的worker经验候选（单位µs，N以MiB计）：

- 简单候选：T=368.6854+3.02440625N。
- 计入操作数：T=235.052833+3.02440625N+95.451833K。

这些是冻结配置下的经验聚合。95.45µs项可能吸收相关分块/日志费用，不是已定位的单一API固定费；尺寸系数也不作为已识别物理带宽。两模型的控制残差均由同五点事前冻结：controller-minus-worker中位1.762735ms，filled-minus-controller中位6.166163ms，各只计一次。

## 6. 独立验证：局部更准确，整体升级门未过

以下预测全部在C2首请求前冻结，未使用C2控制段回填。

| 留出 | 实测worker ms | 简单预测 ms | 简单相对误差 | 计入K预测 ms | 计入K相对误差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| V2 80MiB | .759920 | .610638 | −19.64% | .763361 | +0.45% |
| V1 48MiB | .710370 | .513857 | −27.66% | .666580 | −6.16% |

简单候选在V1超过20%相对误差门，不能用于本轮worker新域。计入K候选两个worker点均通过250µs且20%的双门，也在controller/filled两个点通过原固定绝对/相对门，获得有限的局部预测资格。

但**“各点落在误差门内”与“增加复杂度值得升级”是两个判据**。预登记要求更细候选相对原简单候选，在每个留出点的worker/controller/filled各层都有超过该层观察尺度的改善；简单候选失败也不取消这个要求。

计入K后filled绝对误差改善：V2约39.977µs，V1约152.723µs，均小于C1 filled观察尺度575.449µs。完整预测误差仍受控制段迁移影响。因此最终`selected_worker_and_outer=null`、`complete_filled_model_qualified=false`，同时保留`qualified_local_worker_candidates=[a_bN_cK]`。这是整体模型升级未通过，不是八次复制/计时/校验失败。

计入K候选对V1的filled预测8.595478ms，实际10.271848ms，低估1.676370ms。其中worker差约43.790µs、controller残差差约10.528µs，执行以外的查询/准备/记录残差差约1.622052ms。继续只细调本地搬运系数，无法解释这部分偏差。

冻结的执行前段中位数来自C1 L2。直接对照原件，V1的inventory多1.387379ms、prepare多0.153942ms、其余前段多0.080731ms，恰好解释1.622052ms。这里只定位到窗口，库存内部为何变慢仍未唯一定位，未把V1的实测残差回填预测。

四项候选个别留出误差很小，但它已在C1细项信号门被拒绝，不能凭看见留出结果重新准入。service诊断候选均未通过其独立50µs且20%门。published和live预测保持null。没有追加第三session、增加状态参数或调门槛。

service三项候选在V1的误差为50.020833µs，超出50µs门约20.833ns，按原规则拒绝；这是一处严格阈值边界，不夸大为大幅失准，也不通过显示舍入改判。

## 7. 结论与后续边界

已完成的基础能力：原生路径的完整成本账本、实际操作/块计数、状态/源/目标代际证据、不可回填的预测冻结、分层资格及精确回收。C0功能和C1/C2预登记流程已完成。

目前最有价值的建模结论是：只看字节数不足以预测多操作请求；操作数值得保留为局部经验变量。分块独立费用、统一首状态费用、纯带宽仍缺少充分识别证据。控制费用要按实际边界校准，不能统一塞入“通信时间”。

下一步先从已有原件定位控制段的变化，区分必要产品工作和实验诊断成本，并明确哪些字段随请求规模/操作数变化；只在现有证据无法回答且影响规划决策时补最小测量。当前不扩solver、不做全路径扫描，也不把局部0.45%/6.16%误差当作恢复加速。远端/SSD/共享竞争及真实模型权重可用窗口仍需对应域的资格。

## 8. 证据与复现入口

- [C1执行账目](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_gpu4_C1_accounting_r01/report.md)、[C1解释](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/analysis_gpu4_C1_interpretation_r01/report.md)。
- [C1冻结模型](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C1_r01/frozen_prediction.json)、[C2独立验证](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C2_r01/independent_validation.json)。
- [C1独审](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/review_gpu4_actual_C1_r01/qualification.json)、[C2最终独审](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/review_gpu4_actual_C2_r02/report.md)。C1资源173/组合228项、C2资源169/最终组合138项通过（含新会话96MiB源SHA相同），模型升级失败单列。
- [全部成本和误差解读](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/analysis_gpu4_final_r01/report.md)：八请求worker各分段及V1控制误差的原件对照。
- [执行闭环](../../codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_gpu4_closure_r01/report.md)：逻辑/实际copy/digest计数、资源观测最大值、源/目标出生代际、原资产与精确清理。
- 原始GPU4会话：`codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_gpu4_actual_C1_r01/`与`execution_gpu4_actual_C2_r01/`；原GPU0失败为`execution_actual_C1_r01/`，不得复跑覆盖。
- 实验脚本：`codex_coding/src/v0.1.23.3/task2/cost_ledger/execution_gpu4/`；冻结分析为`analysis_gpu4/`，独审为`review/`。旧GPU0源码仍保留，产品worker/control/service_cost与history-chat.txt的SHA未变。

```bash
python3 codex_coding/src/v0.1.23.3/task2/cost_ledger/analysis_gpu4/analyze.py calibrate --bundle codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_gpu4_actual_C1_r01/measure/stage_measurements.json --predeclared codex_coding/results/v0.1.23.3/task2/cost_ledger/analysis_gpu4_predeclared_r01/predeclared_protocol.json --output-dir codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C1_r01
python3 codex_coding/src/v0.1.23.3/task2/cost_ledger/analysis_gpu4/analyze.py validate --bundle codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_gpu4_actual_C2_r01/measure/stage_measurements.json --frozen codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C1_r01/frozen_prediction.json --output-dir codex_coding/results/v0.1.23.3/task2/cost_ledger/model_gpu4_C2_r01
```

以上为已执行命令，结果拒绝覆盖；只读复查使用新目录。本轮仅正常任务增量，无GitHub提交、全历史归档或history覆盖。

最终文档检查`check_documents.py`为PASS，`git diff --check`通过；history及三处产品入口SHA复核与开轮记录一致。
