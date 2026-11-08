# 最小校准与独立验证审查

审查日期：2026-10-10；编号：v0.1.23.3/task2。仅本地CPU审查，不访问SSH或启动GPU。依据当前AGENTS、next_step、debug_philosophy、三层breakdown文章和原service_cost.py。

## 校准的可辨识范围

每条关键路径采用4MiB单op、16MiB单op、总4MiB四个1MiB op。设目标worker请求时间为`r + m*a + B*b`，三行系数矩阵为`[1,1,4]、[1,1,16]、[1,4,4]`（字节以MiB归一化），行列式为−36，满秩。对应`b=(t16−t4)/12MiB`、`a=(tbatch−t4)/3`、`r=t4−a−4MiB*b`。这是仿射假设下的结构可辨识，不是n=1参数稳定性或物理带宽证明。小块分割效应、同步等待和SSD缓存效应均可能使假设失效；原函数的负原始项与clamp必须保留。

原fit_request_model将各path请求项取中位数。三个path各自的raw_request_s与中位数残差需展示，避免模型共同请求项掩盖系统差异。使用该函数的薄适配即可，不新建solver。fit_path只有两个warm大小，只能拟合合并固定项，不能分开per-op和per-request。

warm-only实验没有gamma样本。fit接口需要的cold_small只允许出现在明确的derived fit-input中，标`observed=false`、源warm样本与原因；真实观测sample不得改名cold。模型gamma=0仅作不可达分支占位，输出须标gamma未测，并在实际调用前拒绝未warm pair和不同incarnation。不能报告实测冷启动成本为零。

## 冻结与holdout

固定关键路径local HBM w4→w4、remote DRAM w2→w4、SSD w4→w5。holdout每path12MiB，沿原plan顺序递推source/target ready，检验w4接收后供数依赖。12MiB未参与fit，三path组合也未参与fit。

固定cached→legacy同一计划各一次；cached是唯一预测验证，legacy仅端到端配对观察。来源对象、布局、byte ranges和SHA保持；目标可按明确生命周期退役/重建。固定顺序有时间漂移和页缓存状态混杂，不作统计因果或布局优越性声明。单个组合不能认证排序准确，报告排序证据unknown，另展示关键路径/worker/execute误差与总窗误差，防止公共库存项掩盖数据模型误差。

模型、predictions、fit-input和来源hash先写盘，再执行holdout。仅文件mtime不足以证明事前冻结；同controller日志需要freeze_saved_ns早于cached request_begin_ns，并核执行前后hash相同。失败原件保留，不据holdout调参搜索或替换fit样本。

## 计时与资源准入

workflow请求起点位于必要gate、inventory、在线planning之前；filled与published沿同controller单调钟计算。内层Cluster.transfer窗口和execute/worker数据窗单独保留。预测冻结的研究记录写盘与线上必须工作分别标明，不能把必要工作移出主窗制造提升。全SHA发布和live handoff边界需明确，本轮不能宣称真实模型T_ready。

库存每mode两次可定位约62ms的大瓶颈，不认证精确统计收益。静态身份缓存需有初始化来源与成本，动态Catalog、epoch/generation、预算、进程/cgroup状态必须fresh；同RPC内分段找负担实际位置。不能只删除诊断或计时。

DRAM每node聚合6GiB，HBM每node聚合2GiB含runtime/staging；HBM采用同HIP绝对upper，拒绝跨sysfs/HIP差值精确计费。fresh metadata、SYS_NICE、core0、旧资产保护、自有XML不改、9个own进程plannedSTOP及exact cleanup沿既有workflow验收。校准代码授权不自动授权GPU实验；启动由主agent统一协调。

## 证据验收

检查实际九个warm校准transfer的完整SHA和路径/尺寸/op数，derived fit lineage、原fit函数重算、clamp与state限制；检查唯一cached预测误差、same-controller冻结顺序、legacy配对与来源保持；复用旧审查的实际SHA/清理/日志/预算模式，prepared-only与CPU检查不替代实机。原产品和旧结果保留，review独立snapshot/hash记录供复核。
