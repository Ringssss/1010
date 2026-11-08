# 首八请求实机独立证据审查

142 项检查、39 引用 SHA 通过。状态 `local_slope_actual_evidence_passed_cause_unidentified`，唯一实机会话 `local_slope_runtime_r01` 的八个配置与先验合同一致，bootstrap 加测量共96MiB全字节 SHA。原16MiB source与旧内容 SHA 相同，八次共享同 source 指针，目标按尺寸为 exact-size flat HBM。请求全部使用不同 native TID，Python ident 可以复用，不把相同 ident 当成同线程。

| 口径 | 4MiB 两次 record(ms) | 16MiB 两次 record(ms) | 均值16−4(ms) |
|---|---|---|---:|
| 原 native 执行方法 | .253934 / .172842 | .238223 / 1.037124 | +.424285 |
| diagnostic 仪表分支 | .352955 / .301664 | .290974 / .293464 | −.035091 |

native 首对差量 −.015711ms，但反序差量 +.864282ms；全部点保留。新会话没有稳定复现旧负斜率，不能删除1.037124ms观测后宣称再现。该16MiB慢点与同尺寸另一点目标指针相同，地址差异不能独自解释两个值；真实原始 native 没有phase分段，不能猜它慢在哪一段。

diagnostic 小−大均值差35.0905µs，其中 event_begin_record 差18.900µs，source_slice差4.7545µs，copy_submit差1.250µs，device_sync差2.530µs；其余 log/target slice/end event合计7.656µs。四次 source_slice为60.981–68.111µs thread CPU、61.081–67.921µs wall，约65µs的首source slice成本是真实host观察，但尺寸差量只有约4.8µs。本数据不支持 source partial/full 快路径解释旧约100µs负斜率，也不支持归因 GPU 带宽。

首 diagnostic 的 event_begin_record=79.591µs，后三次44.521–48.030µs；该仪表差量本身占新负差量的大部。GPU event窗口4MiB=.106880/.103840ms、16MiB=.094880/.100800ms，两个 event之间的host提交间隙仍在窗口里；不是纯DMA。phase CPU≈wall只支持这些被观测段主要消耗线程CPU时间，不证明具体dispatcher、线程局部HIP机制或OS调度因果。

phase residual=0只是相邻边界闭合；所有额外timer/event观测工作仍计在各phase。post-service diagnosis为.374595–.436086ms，排除在worker execute/service报告之外，实际仍在controller execute/HTTP返回窗中。没有从主窗扣除它，也不能把controller−worker当精确dispatch。两mode分块还保留时间漂移与额外prepare event sync，native/diag差量不构成净性能优化。

冻结模型未改；新数据没有正式预测/拟合。这轮 native 的 .172842 与1.037124ms超出旧四点经验包络，再次说明旧包络不是后续延迟界。历史一次8MiB近似成功不能证明固定服务项具有物理平台机制。主后续若执行恰两条同handler双轮host-only对照，应单独标记重复copy、两轮完整窗与物理2n/逻辑校验n；其目标是区分请求首操作固定项，不能回填旧点或本轮native慢点原因。

最终六worker VALID/used/事务全空、三SSD files=0，九STOP与supervisor rc0，九child planned TERM/-15，PID absent/组空，无KILL。三node容器quiet pids1，原XML、旧owner/container身份、冻结source SHA保持。DRAM采样峰28/30/27=3,470,393,344 / 3,979,497,472 / 4,337,434,624 B，均<6GiB；同HIP绝对采样上界=1,124,073,472 / 1,281,359,872 / 1,826,619,392 B，均<2GiB。完整连续peak未知，不把采样峰认证成瞬时最大值。

独审首次r01误读native TID位置，在输出资格前失败；真实字段为execute_thread.native_tid，原failure.json保留。仅修审计脚本lookup后r02通过，实机原件与worker保持。
