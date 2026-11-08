# local slope CPU 与启动合同独立审查

`execution_prepared_r01` 与 `execution_cpu_replay_r02` 的 89 项检查、20 个引用 SHA 通过，状态 `local_slope_cpu_launch_review_passed`。检查只读取本地资产、真实原 schema 与 CPU 回放结果，未调用 SSH 或 GPU。主 agent 可以按冻结合同启动；GPU 行为与负斜率归因仍待实测。

原六 worker、九进程、28/30/27 三节点、容器、资源与寿命配置保持；新 overlay 仅 w4，native 分支直接调用保存的原 `ClusterWorker._execute`。预登记恰八请求，native [4,16,16,4] 后 diagnostic 同序，16MiB source/w4→w4/新 exact-size target/serial32MiB chunk 不变。append 包内源码及 base64 均匹配 SHA；三 node spec 的所有既有资产保持，只追加新版本资产；拓扑输入保持，dump 为本 session 自有路径。冻结模型文件 SHA `e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f` 保持且不参与新拟合/预测。

CPU 回放使用真实原 source0、bootstrap 和 verification schema，通过原 Cluster.transfer/native Worker 的 prepare→execute→verify→complete→finish 与 exact_release，bootstrap 加八请求共96MiB的标准库 CPU buffer 实际逐字节复制检查通过。CPU tensors/events 为 doubles，旧 SHA 值是 schema oracle，CPU buffers 另有逐字节相等断言；这证明流程与分支协议，不证明真实 GPU SHA/事件时长/拷贝引擎。原 missing-numpy 回放失败保留，最终 r02 无新增依赖。

diagnostic 的 event 初始化与一次同步在 prepare 外加；其单 local 分支展开略去 native land 的 dispatch 检查。原 host record/service、worker execute 和 controller 完整窗保持各自定义，event window 标明含 host 提交间隔。额外计时/事件工作计入阶段；相邻边界 residual=0 是账目恒等，不是零观测开销。post-service metadata/memory/event 读取的 `post_service_diagnosis_wall_ns` 单列，仍含在 RPC/controller 窗中，不从主指标减除。native 块先于 diagnostic 块，仪表与时间漂移未独立分离，不能由两块均值差直接估精确仪表成本或净加速。

审查脚本 `src/v0.1.23.3/task2/local_slope/review/audit_prepared.py` 初次误把冻结 JSON 文件 SHA 与其内部 model payload digest 比较，`review_launch_r01/failure.json` 保留。仅修审查条件为文件 SHA 后用新 r02 输出通过；实验资产和原模型没有变动。
