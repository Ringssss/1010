# 两节点单发送者在线恢复：执行记录

开发编号v0.1.23.3 / task2；总计划18.35的简化分支、实验记录50后续。对应[简化计划](../plans/2026-10-12_simple_two_node_online_validation.md)。最终状态：r02完成完整在线恢复闭环，功能/正确性/回收通过；Hnear误差21.70%未过10%，Hremote误差8.70%通过，整体精度尚未认证。所有自有实验容器已停止，旧复杂方案保持。

**主结果：**约70.33GB权重的混合布局C0恢复3.28749s；主要本地DRAM的Hnear实测1.31220s，预测1.59697s；主要远端DRAM的Hremote实测4.56026s，预测4.95703s。预测正确选择本地更快，但共同外层常数高估了后续开销。r01正斜率拟合失败与r02新操作成本表分域保留，不改判历史。

## 1. 范围与验收

node27 GPU4/5运行完整Qwen TP2并作为worker0/1，node30只有GPU4/worker2作中转，完整两个rank源分别放在CPU NUMA4/7。已有实际完整权重字节数70,329,718,112B、66操作/2096个32MiB chunk。远端两个目的路径共用同一发送者，按原生worker依赖DAG串行计费；不使用旧disjoint双流共享因子或并发重叠门。只验证当前简化域，不认证多NIC/三节点/SSD。

在线主时钟与原协议一致：必要查询和规划前开始，全部rank weights_ready及resume ACK后结束；全SHA、参考生成与物理park检查在主窗后。路径校准六条各512MiB/1GiB一次，C0完整恢复一次确定外层残差，然后提前冻结Hnear/Hremote两份预测，各一次留出逐案判断≤10%。无新solver或五轮重复。

## 2. 资源口径与前置检查

helper原“整卡used≤2GiB”保守门不再等于本任务预算。新数据前登记：初始actualfree≥4GiB，运行actualfree≥2GiB，Torch reserved/明确已知tensor分配≤2GiB分别检查；完整driver/RCCL任务归属unknown，不用整卡前后差量冒充独占任务量。0.5s常驻线程检查并保存有界样本；样本不等于连续峰值。

目标DRAM任务192GiB/hard224GiB，初始52GiBfree/card、运行48GiB/card上界保持；helperDRAM任务96GiB/hard128GiB，同一Catalog96GiB，两rank显式NUMA4/7，不把70GB都放一个NUMA。每个节点仍有主机余量门、实际触页/固定注册和精确生命周期。

`review_simple_metadata_r01`为最初两卡helper只读候选；`r02`精确helper GPU4、CPU NUMA4/7和一个36014端口。两node主机内存/镜像/名称/端口门均通过，无GPU调用。核心49项CPU、ops23项CPU、独审14项通过。原complex/product/model未改，新脚本适配；metadata与prepare仅增加可选参数，默认旧行为保持。

## 3. 冻结、部署与实际准入

结果根为`codex_coding/results/v0.1.23.3/task2/online_cost/`。`execution_setup_simple_two_node_r01`冻结151资产；`execution_host_simple_setup_r01`新建：

- node27 CID `094b387d2413664539289964cdea612686f6e9f036bfda3c9dd96524634bb169`，StartedAt `2026-10-12T12:07:06.169410797Z`。
- node30 CID `4c03c1b233fce8c12b436f34066643d249cc15ae8360bab8caf9467b1d3fe1c4`，StartedAt `2026-10-12T12:07:07.589725996Z`。

`execution_host_simple_admission_r01`真实HIP全部通过：三卡各total68,702,699,520B/free68,365,058,048B/used337,641,472B，实际BDF与PCI NUMA匹配。此刻helper也已空闲，不能将本次通过宣称在foreign高占用下完成了性能验收。source30 NUMA4/7可回收估算63,923,630,080/61,256,040,448B，各满足35,164,860,416B计划加4GiB余量。估算不是触页成功的替代，实际分配后还要核验。

独审`review_simple_admission_r01`22项通过，151冻结载荷SHA、角色/绑定/预算和输入/dump路径分离均核对。`execution_host_simple_bind_r01`后由主启动`execution_host_simple_run_r01`。

初始native mesh与warm通过；helper PyTorchallocated67,108,864B、reserved69,206,016B，已知唯一tensor为64MiB staging，整卡free67,945,627,648B，cgroup约2.76GB。这里只是初始化采样，不当作运行全过程峰值或完整driver归属。真实Qwen加载已开始，后续capture/helper/calibration/validation事实继续追加。

## 4. r01原始结果：布局完成，正斜率假设未通过

完整70,329,718,112B捕获/全SHA、参考输出、物理park、远端NUMA4/7两个完整分片及本地1GiB/rank缓存构建全部通过。12路径测量均全SHA及临时副本退役通过。`phase_calibrate/failure.json`记录`ValueError('invalid or non-increasing calibration samples')`，无C0/留出。

具体不满足假设的是worker2 NUMA4→worker0：512MiB最大native op elapsed为73.126384ms，1GiB为66.851419ms。两端记录取max，不求和。它不能解释为负物理带宽，亦不足以唯一归因缓存、首调用或调度；本轮不为微项原因追加无界探索。

独审`review_simple_actual_r01`保留全部12行原始样本、完整源身份/NUMA/资源与回收。helper共1280个配置0.5s样本，最低free33,478,934,528B、最高Torch reserved69,206,016B；未宣称连续峰值或准确driver归属。Catalog最终used/transactions空，四个final_owned_closure过，两节点quiet pids1，cleanup_failure=null；host整体失败仅来自校准假设，不能改写通过。主最后停止准确r01容器，记录`simple_r01_owned_stop`。

## 5. r02预登记修订与实际准入

在任何r02性能数据之前，把策略登记为`exact_extent_service_points_v1`。每路径1GiB bulk预热一次，随后805,120,688B与1GiB各测一次；六预热、十二校准，原件分别绑定warmup_ref/source_ref。模型只接受这些具体路径和尺寸，不要求正斜率，不插值或外推。

执行计划与源身份/NUMA/epoch/目标66操作保持。内部只把path+尺寸映射成原DAG评分的别名，使每个操作成本精确等于相应实测服务时间；归一化系数不声称物理带宽。既有worker顺序与sender2串行关系不改，没有逐layout常数。完整C0只校准共同外层残差一次，Hnear/Hremote在运行前同时freeze，原逐场景10%门仍生效。旧复杂模式和产品评分器未修改。

核心60项CPU通过，独审`review_simple_point_design_r01`通过。新`execution_setup_simple_two_node_r02`冻结152资产，其中model SHA11db8392...、controller d9a41497...。新容器27 CIDd3dbb383...、30 CID42568717...。`execution_host_simple_admission_r02`实际目标两卡free68,365,058,048B，helper free7,411,335,168B、wholeused61,291,364,352B，通过当前free4GiB门；整卡占用保留为观察，不算任务独占。独审`review_simple_point_admission_r02`14项过。

主绑定并启动`execution_host_simple_run_r02`，本轮不再额外增加第三次同目标完整会话来救微项。r02是否支持整体10%须由未见过的完整在线验证决定。

## 6. r02完整结果与主指标

六次1GiB预热、十二个实际尺寸校准、一次C0、两个独立新请求Hnear/Hremote全部完成。每个完整请求恢复70,329,718,112B、66extent，正确性与物理暂停检查通过。两个留出使用同一初始化引擎/keeper incarnation，预测在二者开始前一起冻结，未用留出拟合。原件在`execution_host_simple_run_r02/collection_node27.tar`与`collection_node30.tar`；主host run_summary为passed=true、failure=null、cleanup_failure=null。

| 方案 | 布局（每rank） | 实际完整在线时间 | 预先预测 | 相对误差 | 10%门 |
| --- | --- | ---: | ---: | ---: | --- |
| C0 | 首1GiB HBM、后16GiB远端、其余本地DRAM | 3.287488677s | 用于一次外层校准 | 不算留出准确率 | 不适用 |
| Hnear | 首1GiB HBM、其余本地DRAM | 1.312200619s | 1.596965948s | +21.701356% | 失败 |
| Hremote | 首1GiB HBM、其余远端DRAM、单发送者串行 | 4.560261343s | 4.957028519s | +8.700536% | 通过 |

Hnear比Hremote少3.248060724s，约缩短71.2%、快3.48倍。这是同模型/目标/缓存前缀下两个指定来源布局的实测差异，**不是新规划器或联合优化算法的收益证明**。预先预测的差距3.360062571s，差距误差112.001847ms（相对实际差距约3.45%）；快慢排序正确。不能以排序正确替代每场景10%总时延门，也不能凭两个探索样本外推所有布局。

独审`review_simple_actual_r02/qualification.json`61项中60项通过，唯一失败为Hnear整体10%门；完整模型全SHA/输出、源身份/NUMA、冻结时间/实际plan与成本表、原生单发送者顺序、三次物理pause/ready资源门、回收均通过。`all_scenario_error_gates_passed=false`、`actual_model_online_qualified=false`保留；Hremote单场景通过。

直接证据：[独审报告](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/report.md)、[资格结果](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/qualification.json)、[同主机时钟分解](../../codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_actual_r02/breakdown.json)。

## 7. 主要偏差落在哪里

C0预测搬运DAG2.479458671s，完整在线3.287488677s，因此预先冻结的共同外层残差为0.808030006s，包含必要控制、materialize/IPC接管、完成确认等未被DAG覆盖的工作，不是独立识别的物理常数。

Hnear搬运DAG预测0.788935943s，实际最大worker执行约0.77894s，差约9.99ms；实际完整在线减去该worker时长约0.53326s，明显小于沿用的0.80803s外层项。整体高估284.77ms中，约274.77ms在这个外层差额，约10ms在搬运预测。由现有时间账可判断，继续微调路径吞吐不是当前最大价值所在。

Hremote也相同：搬运DAG高估7.47ms，外层剩余高估389.30ms，合计396.77ms。两案例分别约96.49%和98.12%的带符号总偏差落在外层剩余项。主指标仍按完整时间评价，不能只报告搬运小误差替代整体结果。

这里的online减最大worker duration是用于定位误差的代数剩余，不将跨主机时间戳相减，不声称唯一因果。C0两rank load_begin到达差约216.69ms，后续请求的控制/布局时序不同；尚不能把所有剩余差额直接命名为“首调用固定开销”。完整原件将用于下一步必要控制项/状态的最小校准，当前不回填新的常数以冒充留出通过。

同一node27时钟的“请求开始→首个复制操作开始”分别为C0 0.804985267s、Hnear 0.523328608s、Hremote 0.408757973s，已经直接显示复制之前的变化。C0请求→第一个rank load_begin 0.247041673s，Hnear 0.080502112s；rank间到达差从0.216690944s缩到0.002621536s，但export→mapped窗口又部分反向变化，不能把这些重叠/抵消项简单相加。独审的互不重叠时间划分保存在breakdown原件中。

## 8. 资源、回收与下一步

r02辅助卡存在其他任务占用时仍成功运行。持续guard有1823个配置0.5s样本，最低actualfree6,981,419,008B（约6.50GiB），最高Torch reserved69,206,016B（66MiB），均在当前门内；它们是已观察采样极值，完整driver/RCCL本任务HBM仍unknown。两rank实际DRAM驻留NUMA4/7、固定注册、source lineage和预算检查通过。

原生Catalog最终used/transactions为空、源lease解除；四个精确supervisor最终closure全部通过，两节点collection quiet pids1。新最终closure账本在本次实际运行中生效，保留原signal guard结果，不对PID复用放松检查。随后主停止两个准确r02容器，`simple_r02_owned_stop`显示Running=false/PID0/OOMKilled=false；137来自sleep PID1的限时stop而非OOM。r01容器也已停止。无后台本任务GPU，原XML与并行服务不改。

本次两节点简化系统已具备真实完整恢复、校准、冻结预测、独立验证和回收闭环。下一步优先基于已收集时间账处理共同外层残差的迁移；若后续需要新测量，先限定能验证该项的最小方案，不重做拓扑、不追全部微项、不追加本轮第三会话。整体10%通过后再推进候选布局/规划器收益。复杂拓扑方案保留，资源合适时再恢复其并发/共享资源校准，当前资格不自动扩域。

【关键执行命令】每个旧out目录均不可原名重跑：

```bash
python3 codex_coding/src/v0.1.23.3/task2/online_cost/review/collect_metadata.py --scope two_node --helper-gpus 4 --out codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_metadata_r03 --run-id online_simple_two_node_r02
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/prepare.py --simple --run-id online_simple_two_node_r02 --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_simple_two_node_r02 --metadata-root codex_coding/results/v0.1.23.3/task2/online_cost/review_simple_metadata_r03 --model-metadata codex_coding/results/v0.1.23.3/task2/online_cost/execution_metadata_local_r01.json
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/simple_host.py setup --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_simple_two_node_r02 --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_setup_r02 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/simple_host.py admission --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_simple_two_node_r02 --bindings codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_setup_r02/setup.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_admission_r02 --execute
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/simple_host.py bind --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_simple_two_node_r02 --bindings codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_setup_r02/setup.json --admission codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_admission_r02/admission.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_bind_r02 --main-approved
python3 codex_coding/src/v0.1.23.3/task2/online_cost/execution/simple_host.py run --setup-dir codex_coding/results/v0.1.23.3/task2/online_cost/execution_setup_simple_two_node_r02 --plan codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_bind_r02/launch_plan.json --out codex_coding/results/v0.1.23.3/task2/online_cost/execution_host_simple_run_r02 --execute
```

r01相同入口使用各自r01的run/metadata/freeze目录；原始拟合失败与后续修订分别记录，CPU夹具失败也保留。文档为正常增量，无全历史归档、history-chat覆盖或GitHub发布。

## 后续进展：外层校准通过（2026-10-12）

上述r02原始误差与失败判断保持。用户授权的新独立单控制器会话已完成：旧12路径成本不变，只校准后续共同outer，remote−0.187%/near−6.036%均通过整体10%，详见[外层校准最终报告](2026-10-12_outer_online_calibration_report.md)。它覆盖同controller首次完整恢复之后的后续域，不回填本报告r02，也不认证首次或复杂拓扑。当前停止本域精度实验，转向规划决策价值。
