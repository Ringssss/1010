# C1资源门失败后的最小只读选择

本报告只读取仓库源码与已有结果，无GPU、SSH、Docker调用或现场采样，不改冻结analysis.py、仪表、模型、门槛或旧owner。本次C1已启动一次runtime，physical budget门在bootstrap之前失败，0校准请求；不能产生系数或留出资格。

## 判定

保持每node任务HBM 2GiB（含runtime/staging/source/target）。当前`execution/common.py:budget_snapshot`使用同HIP整卡`total-free`作绝对保守上界，再加预留192MiB及256MiB应用margin。失败证明这个保守准入无法通过当前GPU0，尚不能证明本任务真实占用了58GB；也不能因此用Torch reserved替代完整任务用量或调整模型门。

`execution_actual_C1_r01/startup.inventory.json`：total=68,702,699,520B，free=10,708,058,112B，整卡used=57,994,641,408B，Torch allocated/reserved=0。`mesh/final_inventory.json`：free=10,487,857,152B，整卡used=58,214,842,368B，Torch allocated/reserved=67,108,864B（64MiB staging）。相邻整卡差为220,200,960B，扣掉Torch reserved后的差为153,092,096B。后者只能叫观察差额，不能归因runtime或认定任务上界。

## 为什么当前同进程HIP差不能替代上界

G(t)=本任务T(t)+其他owner O(t)+共享/驱动记账R(t)。G(post)-G(pre)=任务增量+其他及记账增量。其他owner释放显存会抵消任务增量；当前pre还是已经导入Torch/初始化并调用HIP的任务进程，不能把pre之前的本任务context成本当零。两次O/R稳定没有证据，采样也没有连续峰值。即使差额约210MiB，本任务完整T仍未被其上界住。旧成功采用整卡绝对保守上界恰因当时全卡低占用；不得事后把旧值改成当前基线差。

## 已有归属机制及适用边界

- `multitier_runtime/operations/host_readback.py`：Docker容器完整身份、host PID/start/cgroup、指定旧owner保护，node27只取普通PCI vendor/device/class/NUMA。该脚本明确仅node28/30读取`mem_info_vram_*`与KFD进程数字字段，当前node27 baseline无全卡数值/每PID显存账。
- `multitier_scenarios/mapping/owner_metadata.py`与`three_node_recovery/inventory/fresh_host.py`：可读取`/sys/class/kfd/kfd/proc/<hostpid>/vram_<gpu_id>`及pasid，并核进程start/cgroup和限定GPU visibility；成功范围28/30。数字字段是潜在进程归属证据，但没有当前node27/runtime全开销覆盖资格；0也不证明无活动context。
- `resource_map/local_xml/diagnose_admission.py`及`topology_alignment/forensics/runtime_snapshot.py`：host `/proc/PID/fd`只readlink，能找到renderD/KFD候选并关联cgroup；不能从打开FD得显存字节，KFD alone不定位一张卡。历史node27 GPU4对应BDF0000:85:00.0、NUMA4、renderD132，KFD gpu_id60191，来自旧boot c9bb...，只能作查找线索；当前boot359f...必须重新绑定。
- 旧`runtime_snapshot.py`调用hy-smi showpids/showpidhcus/全卡显存显示；这些是新GPU管理/驱动调用，不属于纯CPU next step，而且旧全卡数值/进程卡映射仍不是经验证的当前perPID全HBM上界。仓库没有已合格node27 perPID完整runtimeHBM方案，也没有已落地drm fdinfo显存计费实现。

`three_node_recovery/inventory/README.md`记录旧node27 collector读取`vramtbl_45786`进入D态。后续代码仅允许`vram_[0-9]+|pasid`，但没有重扫node27；后来node27新boot与新runtime资格恢复，现host guard仍特意排除驱动owner表。不能为此次58GB问题重开全量旧脚本，尤其不能读vramtbl，或把process超时当成能清理D态的保证。

## 最小下一步

优先发现已有GPU4上下文，继续使用现有整卡绝对门；不先建设新的perPID计费器。当前失败会话的`baseline_node27/stdout.json`已经记录两个运行中的自有容器：

| 自有容器 | 完整ID | 当前证据 |
| --- | --- | --- |
| zhujianian-fk-six-20261012-v2-node27-32081 | 16b22d7d7c83f0c7f1913026903896d63ec551cf0da6208cd898f474ea1f5664 | running、Up20min、端口32081在监听 |
| zhujianian-fk-six-20261012-v2-node27-32082 | 9757ec0c31d6d2c6a487f2a5b9937add73f1af29d673b2c50d03bd30162b2c76 | running、Up20min、端口32082在监听 |

这些名字/端口不证明GPU映射，更不授权停止它们。主agent可先执行以下纯元数据命令，核完整ID/当前StartedAt/host init PID，然后用限定`docker top`或host `/proc`的argv白名单识别实际`--gpu/--gpu-id/--node/--port-base`及PID start/NSpid/cgroup。读取范围限定这两个已证自有容器，不exec其他用户容器，不导入Torch，不打开设备，不读取KFD/显存sysfs。

```bash
docker inspect --format '{{json .Id}} {{json .Name}} {{json .State}}' 16b22d7d7c83f0c7f1913026903896d63ec551cf0da6208cd898f474ea1f5664 9757ec0c31d6d2c6a487f2a5b9937add73f1af29d673b2c50d03bd30162b2c76
```

如确认其中一个现有自有worker确为GPU4且接口身份/epoch/runtime BDF可信，主agent再协调一次该现有endpoint的只读inventory：它会真正调用HIP查询，须明确作为新的GPU状态观测记录，不能由本报告的纯CPU阅读替代；不新建context、不mesh、不prepare、不启动payload。不盲猜旧35104：当前快照该端口不在listener集合。GPU4若HIP绝对used+192MiB+256MiB仍≤2GiB且free≥2GiB+32MiB，继续原保守预算路径可行；否则保持停止，不能拿仅sysfs free或reserved准入。GPU4换卡须更新新run配置、BDF/NUMA/自有XML引用与预声明源码后独审，不修改失败run、不借用现有worker执行恢复或静默复用GPU0模型域。

若GPU4也高占用，才考虑狭窄perPID方案：首先确认本任务host PID/NSpid/start/container/cgroup及当前gpu_id↔BDF映射，并证明所选计数器覆盖runtime/driver、非Torch分配和所有本任务进程；仍同时核物理HIP free与保留空间，未知开销保守补入。没有这种覆盖证据时只能留unknown/停止。此方案是新的资源账本资格工作，当前冻结执行器和分析不应在失败后临时改成Torch-only或pre/post subtraction。

原始证据：`execution_actual_C1_r01/startup.inventory.json`、`mesh/premesh.inventory.json`、`mesh/final_inventory.json`、`baseline_node27/stdout.json`及`summary.json`；代码定位见上述相对路径。GPU4当前free、owner完整性和是否满足2GiB仍未知。
