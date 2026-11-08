# serverless_qwen_full_r02 独立审查

当前验收结果：**failed_incomplete**。summary=failed，错误 `RuntimeError('actual upstream worker failed; inspect loader.log')`。

r03 的66B PyTorch malloc IPC framing、小共享allocation非零offset和guard检查已通过。本轮真实加载了两份完整processed TP文件，通过上游pread→pinned pool→chunk dispatcher→HIP IPC destination，Store已产生完成记录；这不等于引擎恢复成功。

目标引擎 `FLUIDCKPT_TIMEOUT=900` 秒；Store wall-clock加载窗口为 132.039701461792 秒。主指标原始目标同host事件独立重算：132.324249761 秒；完整目标SHA证据=False，精确32token输出=True，物理回收=False。部分指标可观察，整体验收必须满足全部条件。

worker exit=139；worker/result.json存在=False；verification trigger存在=True。两rank最终事件={0: 'release_end', 1: 'release_end'}，release末allocated bytes={0: 36496541696, 1: 36496541696}。summary cleanup={'container_removed_before_release': True, 'worker_closed_before_release': True, 'release_ack': None, 'original_command_restored': True}。恢复/清理由主agent处理，本审查不发请求。

来源为S2冻结源逐rank完整SHA：processed总量 70,329,718,112 bytes，磁盘格式含padding总量 70,329,720,832 bytes，每rank padding1360 bytes。与原raw checkpoint约71.904GB不同。

上游完整pinned pool为66GiB（70,866,960,384 bytes），构建 36.691053s，在request前完成，未计入引擎T_ready。源格式建立两rank分别 128.567509/128.006032s，亦须单列。

实际来源/实现：冻结ServerlessLLM Store commit9f50241baa5386e06a9321c51f19a9ef5f964c2b；只改HIP构建兼容与目标handle/framing/offset映射。每rank1009个storage、209个allocation、801个非零offset，连续source extents匹配processed index。日志两rank各分配1048×32MiB pinned chunks（共2096，pool2112），真实执行4 IO线程及device0/1 copy。

计时边界：引擎主指标仅允许目标同host的monotonic load_begin/weights_ready差；Store window为同一worker的Unix wall-clock start/ready差，是次指标。禁止把协调器monotonic、workerUnix及不同主机事件混减。

NFS/OS运行cache未控制/未测量。源码先尝试O_DIRECT、遇EINVAL/EOPNOTSUPP才回退；本轮日志未出现显式fallback，不代表数据来自未缓存物理介质。不得与native时延直接归因或宣布算法速度差。

来源/实现冻结before-after检查=True，overlay=/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r02/session_overlay_owned.json。实际900s准入和PID/starttime/spec哈希（若提供）单独检查，使用新overlay保持所有原source/keeper记录；详见review.json。

r01的120s handoff超时失败记录独立保留；本run出现的后续错误单独记录，不把两个失败合并成同一原因。

本审查只读已有文件，不导入GPU模块，不发HTTP/SSH请求。原运行记录与冻结实现保持不变。
