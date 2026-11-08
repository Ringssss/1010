# 资源地图运行失败增量

本次最终增量为[resource_map.json](resource_map.json)，派生自冻结build_r04；[readback_validation.json](readback_validation.json)确认结构、全部顶层source SHA和fingerprint通过。`structure_ready=true`，`runtime_ready=false`，`performance_ready=false`。两node自有XML的runtime_observed/runtime_accepted仍false，observed_resource_ids仍为空，无新有效速率或性能样本。

node27新控制证据通过boot、完整container ID及StartedAt连续性检查，旧两个own探针为Z；本地两个keeper完整catalog metadata/index、原底本identity及70,329,718,112B范围保持，使用既有内容身份，没有重读权重。这个窄保护结论不认证parked引擎后置、整机物理free回收或全部旧14/new4服务。本次local XML startup在准入阶段失败，零通信rank实际启动，原failed摘要保留。

新进程初始化仍未确认：all_visible观测到HIP库加载返回、hipInit进入，随后10s metadata alarm中断，没有hipInit返回事件；gpu01_visible在library_load_enter后以SIGSEGV(-11)退出，未记录library_load_exit或hipInit_enter。两个metadata child退出已证，零payload、零communicator。这里没有将两种失败合并为HIP timeout，也没有建立visibility配置的因果结论。

GPU0/1的sysfs total-minus-used与keeper physical-free差值分别34,033,664B和787,984,384B。这些值单列在诊断对照中，`availability`完全沿用历史快照，不替换free、不刷新owner/预算、不宣称live资源状态。新control或keeper RPC响应不升级GPU runtime。

node29仍控制不可达、boot/container当前身份未知，PID21883/475875814和21901/475875846继续待证。node29完整runtime config/fingerprint保持原样。全局protected_postflight_complete=false、protected_state_corrupted=null；未推断底本损坏或全部服务保护完成。

物理结构、availability及预算、measurements原rate_Bps与runtime fingerprint、historical profile和session_ref逐对象与build_r04完全相同。只有node27失败诊断config去掉旧fingerprint后，按原builder的`SHA256(json.dumps(config, sort_keys=True).encode())`重新计算。历史已观察配置的原事实保持；没有将旧速率迁移到自有XML。

source refs绑定control_r02、local failed、metadata diagnosis、控制/目录独立审核及[独立机器结论](../resume_audit_state_delta_r01/state_delta.json)。原startup的12源码与修订/diagnosis的7源码均引用保存副本及冻结manifest，不要求当前活动源码等于历史SHA。summary另绑定本派生生成器保存副本。前一派生resume_map_r01保留；本r02增加独立机器结论引用，作为本轮最终入口。

CPU7项通过，结果为[测试日志](../resume_map_cpu_r02/test_finalize_runtime.txt)，覆盖输入不变、观测不扩大、拒绝伪成功/已启动rank/payload/错误hipInit边界及独立ready升级。派生与readback均只访问本地文件；新增远端/GPU操作为0。

```bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/resume/test_finalize_runtime.py
python3 codex_coding/src/v0.1.23.3/task2/resource_map/resume/finalize_runtime.py --out codex_coding/results/v0.1.23.3/task2/resource_map/resume_map_r02 --source-manifest codex_coding/results/v0.1.23.3/task2/resource_map/local_xml_source_snapshot_r01/manifest.json --source-manifest codex_coding/results/v0.1.23.3/task2/resource_map/local_xml_source_snapshot_r02/manifest.json
```

上述派生命令已经执行；未来必须使用新目录。下一步需要恢复node29精确身份与保护核账，并查明node27新进程初始化边界；本轮停止新的HIP/SMI/payload和性能实验。
