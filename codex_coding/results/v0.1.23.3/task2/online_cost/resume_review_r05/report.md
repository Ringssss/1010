# 完整Qwen局部暂停实测独立验收

v0.1.23.3 / task2。仅读取`execution_host_local_pause_run_r01/collection_node27.tar`及已保存主机原件；审查者GPU/SSH/Docker调用0，无新实验。31项原件交叉检查通过，源码/归档/成员SHA见`qualification.json`。

**单映射修复后的完整Qwen TP2捕获和真实物理暂停通过。原engine监督器guard失败保留，后续subreaper及最终quiet另行证明回收。完整在线成本资格仍未知。**

| 实际证据 | 每rank/整体结果 |
| --- | --- |
| 完整权重SHA | 每rank35,164,859,056B，33段连续覆盖；整体70,329,718,112B、66段源/引擎SHA逐项相等 |
| engine allocated | 每rank36,496,468,480→1,315,722,240B，减少35,180,746,240B |
| 整卡free增长 | 每rank35,213,279,232B；与before/after keeper库存独立差值相等 |
| 物理暂停门 | 两rank allocated减少和整卡free增长均≥完整权重90% |
| release_end整卡free | 两rank各65,101,889,536B；随后keeper库存各65,078,820,864B，时间点分别保留 |
| DRAM保活 | 66个VALID区域身份/epoch/generation/NUMA不变、lease0、transaction空；每rank页对齐占用35,164,860,416B，HBM/SSD catalog占用0 |
| 固定参考输出 | 原生generate完成，文本已留存 |

捕获SHA使用同一唯一IPC映射，SHA原件包含每段off/n和两个摘要。完整暂停后只检查同一DRAM来源的所有权与准确占用，未重新导入旧IPC凭证。region逻辑字节与page-aligned分配字节区分正确；尾页每rank多1,360B不应误判为泄漏。

三role STOP都绑定同一实际plan SHA。两个worker监督器均passed=true、TERM后原进程消失、child_returncode=-15、subreaper无待回收子进程，host client rc0。engine的原cleanup记录`identity changed before KILL; no further signal`、passed=false、child_returncode=null、host client rc2，**原失败不改判**。engine subreaper另行passed=true、回收159/262/264/265且pending_children空；最终collection passed=true、容器pids.current=1。host run_summary仍passed=false，唯一cleanup failure为engine监督器；该状态与物理暂停成功并存。

最终138个冻结资产SHA逐项一致，保护容器的身份/StartedAt/配置/挂载与run_before一致。共享XML/mapping观测值与before相等，其中原厂XML哈希记录为null，仅说明未获得哈希，不能称取得了完整文件校验。

所有监督器DRAM采样最大94,178,459,648B、pids最高152、主机MemAvailable最低421,455,745,024B，均在原192GiB任务/224GiB hard/pids512与主机余量内；OOM事件计数0。memory.peak不可读，连续峰值未认证。before与after资源门均通过。

356.879426979秒是load/capture/SHA/reference/park诊断全过程，不能当恢复时延或性能收益。该局部运行无helper/cache transfer、pathcal/C0/Hnear/Hremote，online_cost_samples=0、online_cost_qualified=false。后续node30 fresh容量仍失败，因此完整两节点成本矩阵不可继续，不放宽2GiB helper预算；本实测已验证capture/park功能修复，成本准确率和跨节点收益保持未知。
