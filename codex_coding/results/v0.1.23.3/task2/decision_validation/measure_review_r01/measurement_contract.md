# 受限双目标场景的最小产品测量契约

状态：CPU契约已冻结，case gate尚未就绪，future_GPU_pairable=null。本轮无GPU启动；不建设新runner。机器入口为同目录measurement_contract.json。

固定两个16MiB目标均在28GPU0；完整两块32MiB DRAM底本在28。HBM cache总32MiB、28/30各上限16MiB。原Worker/serial/default32MiB chunk/每worker64MiB staging不变。待决策的同一逻辑块有两种来源：28本地DRAM直接→28HBM，或30HBM经原pair→28HBM；来源identity/tp/off/n及完整SHA一致，remote cache由原DRAM块建设，建设在恢复窗外。

必要校准只有两个16MiB单op，各一次：localDRAM28→28HBM、remoteHBM30→28HBM。原pair先warm，同worker session/epoch贯穿。localHBM28单op默认关闭，仅当case的未定成本不等式需要它才做一次control。两个单op只产生有限服务观察；保留RPC skew、collective等待的诊断，不能反算恒定GBps或单尺寸fixed/byte参数。

校准后先冻结使用的标量口径、预测输入、误差准则和选择签名，才执行一次独立两op混合holdout：tp0 localDRAM28→28HBM，再tp1 remoteHBM30→28HBM。每块16MiB、两个target共享worker28，检验依赖组合，不宣称并发容量。holdout永不回拟合或事后重算预测；若只能声明观察而无可靠预测，明确prediction未就绪，不虚构公式。该holdout本身也不是两种策略的成对性能收益证据。

计时直接复用Cluster.transfer与execute_collection：controller prepare、execute、filled、verify/publish分开；两端worker角色/op/elapsed作诊断。不跨机相减、不把双端elapsed相加。完整SHA、source epoch/generation、lease0/transactions空与副本退役必核。

始终按新worker启动前baseline计算每node2GiB增量。32MiB底本分块复用，不额外增加DRAM底本；每stage目标退役后再下一stage，HBM cache只按16/16上限。runtime/staging/hash临时另计；前次28仅余29,224,960B，fresh postwarm若不满足则拒绝，绝不改预算或postinit基线。

准备的复用调用序列见JSON prepared_reuse_plan：parent freshguard/baseline/端口及准确新run→原mesh/warm→预算→原synth/adopt/Cluster.transfer两个单op→冻结组合→一次holdout→原cleanup/匹配STOP/精确退出/保护验收。既有parent-run硬编码历史输出名，不能直接原名重跑；GPU_launch_command保持null，case成立后才由parent绑定新plan/run及必要薄controller。当前只读refresh已通过，不代替未来GPU/runtime准入。
