# ServerlessLLM full r01 独立审查

当前验收结果：**failed_incomplete**。summary=failed，错误 `RemoteDisconnected('Remote end closed connection without response')`。

r03 的66B PyTorch malloc IPC framing、小共享allocation非零offset和guard检查已通过。本轮真实加载了两份完整processed TP文件，通过上游pread→pinned pool→chunk dispatcher→HIP IPC destination，Store已产生完成记录；这不等于引擎恢复成功。

失败边界有明确证据：目标引擎 `FLUIDCKPT_TIMEOUT=120` 秒，日志 `TimeoutError: keeper did not fill rank 0 within 120.0s`；Store wall-clock加载窗口为 143.28751254081726 秒，超过该budget。两rank引擎事件只有load_begin/layout_end/target_exported，没有weights_ready。主指标 **未测得**，不填写无限时延或假定恢复成功。

当前没有完整目标SHA、32token输出、release_end或物理清理通过证据。summary保留pending_worker/pending_resume_preserved；该状态须交由主agent恢复处理，不主动向引擎发请求，不把完成了copy的部分样本记成passed。

来源为S2冻结源逐rank完整SHA：processed总量 70,329,718,112 bytes，磁盘格式含padding总量 70,329,720,832 bytes，每rank padding1360 bytes。与原raw checkpoint约71.904GB不同。

上游完整pinned pool为66GiB（70,866,960,384 bytes），构建 32.239958s，在request前完成，未计入引擎T_ready。源格式建立两rank分别 128.567509/128.006032s，亦须单列。

实际来源/实现：冻结ServerlessLLM Store commit9f50241baa5386e06a9321c51f19a9ef5f964c2b；只改HIP构建兼容与目标handle/framing/offset映射。每rank1009个storage、209个allocation、801个非零offset，连续source extents匹配processed index。日志两rank各分配1048×32MiB pinned chunks（共2096，pool2112），真实执行4 IO线程及device0/1 copy。

计时边界：引擎主指标仅允许目标同host的monotonic load_begin/weights_ready差；Store window为同一worker的Unix wall-clock start/ready差，是次指标。禁止把协调器monotonic、workerUnix及不同主机事件混减。本次没有weights_ready，无法核对主指标。

NFS/OS运行cache未控制/未测量。源码先尝试O_DIRECT、遇EINVAL/EOPNOTSUPP才回退；本轮日志未出现显式fallback，不代表数据来自未缓存物理介质。不得与native时延直接归因或宣布算法速度差。

元数据来源保护与code before/after hash在失败snapshot一致；完整消费者/临时container清理仍未验收，详见review.json的物理snapshot与pending字段。

本审查只读已有文件，不导入GPU模块，不发HTTP/SSH请求。原运行记录与冻结实现保持不变。
