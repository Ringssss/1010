# 两节点真实模型启动包独审

开发编号v0.1.23.3/task2，2026-10-12。独审结论：`execution_setup_two_node_r01`可以冻结并用于本任务新容器setup及fresh owned HIP admission；没有剩余启动关键源码阻塞。无条件GPU运行或预测资格均未授予；重载前必须通过实际HIP及mesh后资源门。子agent本次GPU/SSH均0。

实际范围为node27 GPU4/5 NUMA4/7两个target rank、node30 GPU4/5 NUMA4/7各保留一个source rank，共4worker+1engine，无SSD，不含node28。node28 GPU0/1/4均超过既定2GiB整卡物理上界，原三节点失败保留，不能将两节点实验称为三节点通过。

具体包审查：初次setup前129个当前源码/输入SHA与冻结map全部一致；两个节点全部base64 staged资产逐项hash通过，setup wrapper与spec SHA一致。目标192GiB任务/224硬限、source30 96GiB任务/128硬限；目标初始同HIPfree≥52GiB/card，source30两个selected card整卡已用上界合计+256MiB≤2GiB。共享cgroup按节点只算一次，pagecache、完整pinned底本和runtime均计费，IPC物理背书不重复算view，不扣背景通过物理门。host初始256/128GiB、持续64/32GiB reserve，完整每rank约32.75GiB再加4GiB NUMA预留，后续只添加尚未分配空间。

实际SAFE metadata的host与selected NUMA4/7粗估均充足，node30 GPU4/5 sysfs各已用2,207,744B；这些仅候选数据，不等于已初始化RCCL/HIP后仍在2GiB上界。当前被引用的项目自有physical gdr0 XML在27/30均包含所有8张GPU，包含BDF85/b5；冻结输入与本任务新dump分文件，原厂及共享输入不修改。

关键正确性路径已核对：native HTTP premesh阶段允许rankNone并检查session/node/gpu/BDF/boot，再由controller同时调用所有mesh；capture同步D2H后逐字节SHA与原engine比对，IPC视图清理完成后才发布captured marker；生成正确参考、park、建立可信helper DRAM与付费1GiB/rank HBM cache；native replica校准必须SHA后Catalog VALID，live模型路径identity/layout/epoch/generation/lease/覆盖/同步/handoff/rank barrier均保留。

完整online主窗同coordinator开始于inventory和plan/resume之前、结束于全部engine ACK；其后先全SHA和32token参考输出，再finish/release和验证persistent源代际/lease。C0完整校准的outer只加一次，Hnear/Hremote两预测在最早留出前一起冻结；每代表场景单次实际whole-online相对误差≤10%，无旧细项门/复杂度升级门。不从留出回填系数或控制残差，排序另列，范围只覆盖同target extent/顺序/N/K/C的不同来源分配。

HostOps修复已包含在review：不做node27 KFD/VRAM owner读取；owned GPU查询验证实际HIP BDF/PCI NUMA；per-node operational cap和host reserve持续监督；所有existing容器不因命名而漏保护；source lease未收口时拒绝source retirement；成功清理后engine STOP等待确认再worker STOP，各own process group/cgroup恢复quiet。失败/重试独立结果目录保留，不伪造ready。

## 初次setup失败与窄范围resume

初次host setup在只创建node27后停止（尚无GPU），因为Docker返回mount列表顺序变化，原保护函数错误地按列表顺序比较。独立核对56个旧容器：id/name/image/running/StartedAt/PID/HostConfig全一致，24个mount列表有旋转，但每个mount多重集合内容完全相同，实际旧容器变更0。

本任务新node27精确ID为 `8b484137e72e13d49b546c9425aea09e643196cdeea93a515dbf3248cb4cfb0d`，StartedAt `2026-10-12T08:59:45.834122443Z`，running且pids.current=1。可以规范化mount比较，并依据原 `after_node27` 的精确ID/StartedAt/boot/image/stagedassetSHA做窄范围resume，不能只凭名字重用或创建重复node27。

本artifact时core controller/worker/contracts/model及产品入口仍与冻结SHA一致，唯一当前SHA差异是本地orchestrate保护/partial resume修订。该本地host-side修订需要新provenance和CPU检查，保留原129资产及原setup失败；不覆盖已部署旧源码，不改变核心实验计时/复制/准入。`qualification.json`记录当前vs冻结差异和完整旧mount多重集合证据，`sources.json`冻结原件SHA。
