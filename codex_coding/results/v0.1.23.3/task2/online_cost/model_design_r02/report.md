# 完整在线可迁移代价模型建设

开发编号v0.1.23.3/task2。已实现可迁移路径费用、现有worker DAG复用、完整在线残差、全部预测事前冻结与逐场景10%验收。CPU23项通过，实机资格待fresh准入、真实校准与两新请求留出；新GPU/SSH/产品改动均0。

真实processed Qwen TP2总70,329,718,112B，两rank各35,164,859,056B；新目标node27GPU4/5与helper node28GPU4/node30GPU4待current BDF/epoch/源资产核验。历史node27GPU0/1+node29来源仅保留出处，不能作为当前owner资格。旧6个profile的小数据域与SHA在design.json，均未升级或重写。

本轮新工程额度在采样前由用户批准：target host任务DRAM192GiB/hard224GiB，helper每host96/128GiB；target HBM48GiB/card、96GiB/node、加载前每卡free≥52GiB；helper2GiB绝对上界加margin，Catalog cache/校准临时3GiB/card。目标保活HBM1GiB/rank，每实际物理DRAM/HBM副本仍计费。这里只登记新额度，不代表fresh当前实际满足，不改旧微实验的失败门。

仅6条必要路径各512MiB/1GiB一次，原生serial32MiB chunk。完整模型统一1GiB extent，总N/K/C=70,329,718,112/66/2096，33ops/rank，最后805,120,688B。完整C0布局为1GiB HBM前缀→每rank16GiB连续remote→剩余15GiB+tail local；它只校准共用online−DAG控制/IPC/布局/接管残差。两个完整留出Hnear/Hremote改变来源分配，固定同N/K/C、目标、rank布局、次序与允许预算，无完整布局记忆拟合。

必要共享工作点为两条实际disjoint workerpair各1GiB同发。使用paired concurrent/max isolated同字节两腿服务的经验比值，只施加到matched offset/n连续remote阶段的两腿，再复用原worker DAG。C0与Hremote均须保留实际phase overlap证据；普通共享worker仍由原DAG串行，不捏造硬件共享。比值不是物理capacity，完整留出10%检验决定本域是否足够。初稿whole bytefloor max方案已在本轮建设中撤回，原r01设计保留，不宣称通过。

load_begin在必要inventory/planning/resume RPC之前，all_weights_ready为同controller观察全部必要模型权重完成与ACK之后；空live sentinel禁止。校准与留出时钟域显式一致。全SHA和正确输出在ready后验证，仍是实验正确性必需项。主指标只要求每个独立场景≤10%，不沿用50µs/旧复杂度差量门，不平均消去失败，不回填留出。

常驻engine可同incarnation/session发独立新请求；冻结记录实际worker BDF、epoch/source lineage证据、所有训练run ID与允许incarnation。所有预测同时在留出之前冻结；训练请求不能当留出。另保留fresh_session模式，跨会话迁移不由本轮默认认证。

排序记录原始有符号差量。预测对应的10%尺度区间相交时abstain；该分辨率约定不是未来误差界。场景精度与排序、容量资格分别列示。

接口和CLI见src/.../online_cost/model/README.md。当前CPU入口为model_cpu_r07/qualification.json：23项包括路径尺寸域、当前BDF/epoch/暖化/源绑定、未测共享组合拒绝、DAG并发/经验factor/once-only残差、calibration clock错配拒绝、fullfreeze、允许同incarnation新请求与训练run复用拒绝、整数10%边界、无法均值隐藏失败、预测变更拒绝、近差排序abstain。CPUfixture实际模型资格始终false。
