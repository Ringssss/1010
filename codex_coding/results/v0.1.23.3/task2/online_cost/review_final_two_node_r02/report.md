# 本轮在线恢复成本最终独审

开发编号v0.1.23.3/task2。本轮科学资格为 **未测/资源阻塞**：path calibration、完整C0、Hnear/Hremote留出均0，整体预测误差null，不能认定满足10%。代表场景仍按完整在线误差≤10%，历史细项门及复杂度升级门不适用。实际范围两节点27/30四GPU、无SSD，不称三节点通过。reviewer GPU/SSH均0。

两轮bootstrap结果、资源失败及窄范围恢复原件保留。r02已加载完整Qwen并完成capture marker/full-SHA成功标记、参考输出、park接口和helper/cache复制；阶段最终资源门失败，原控制器没有保存具体失败card/upper/cap，故最终失败卡身份仍null。原release_end两rank allocated几乎无下降，不能称物理暂停成功；后来新GLM服务的启动时间晚于该失败，不能倒推解释先前失败。

已独立审查并实际运行最终CPU契约35项通过（`review_core_final_cpu_r01`）：nativecapture每export只导入一次，可选`verify_full_sha=False`保持旧默认；启用时在同一映射上完成SHA之后才Catalog publish，失败只退役本次新region、无captured marker；实验wrapper不再重新消费同一IPC凭证。`capture_s_scope`明确bootstrap可选SHA计入capture_s。资源snapshot收齐所有四worker/card事实并在抛错前落盘；物理park须同时看到engine allocated下降、实际整卡free增加均≥完整rank的90%，不扣背景漂移。该90%是物理回收正确性要求，与成本10%准则无关。

上述修复只获CPU资格，尚无postfix实际HIP权重释放证据。32MiB单导入probe在tensor/share/consumer之前被拒绝：Torch allocated/reserved0，actual整卡已用34,833,694,720B，超过原2GiB诊断上界；payload0、实际counter读值0、双导入配置未执行。源码重复计数hazard成立，实际下溢机制仍待部署二进制probe验证，不能将CPUfixture当实机修复完成。

回收口径严格区分原supervisor与最终容器状态：r02 engine原cleanup passed；四个worker原cleanup false，原因 `identity changed before KILL; no further signal`，raw child rc当时null。`execution_host_collection_two_node_r04`双节点tar和metadata全部收齐，但原quiet仍false（pids7/3）；失败原件不改判。后来主agent只停止两个精确本任务CID，最终Running=false、PID0、OOMKilled=false，ExitCode137来自有时限docker stop sleepPID1，最终没有本任务前台GPU进程。不能据最终stop把四个旧cleanup失败改写为通过。

目前不追加GPU工作，不改资源门、不覆盖旧raw、不触动外部GLM作业。后续只需在资源满足fresh准入时执行有界IPC单/双消费probe，确认物理park，再冻结新capture/诊断/subreaper及线程cap进入完整校准和两留出。whole-online的真实主窗和正确性步骤已明确；何时达到10%仍由未来完整实际原件决定。

`qualification.json`保存最终raw门状态、两个r02 archive SHA、原5个supervisor结果、final stop、拒绝probe数值与最终core源码SHA。正式叙述见 [在线验证报告](../../../../../../docx/experiments/2026-10-12_online_ready_cost_validation_report.md)。前版局部审查结果继续作为历史，不增加新样本。
