# native_observed_r01 独立验收与 SLLM full 代码审查

审查日期：2026-10-07。审查只读实验输入和运行实现，仅在本目录新建说明、机器可读重计算结果及当前被审查源码副本；没有启动 GPU、重新加载模型、读取 70GB Store 分区或改动 SLLM 的拥有文件。

## 1. native 的验收结论

`native_observed_r01` 通过正式主指标、两个 TP rank 的完整内容与布局、冻结 32-token 输出及清理证据检查。主指标 **122.266963355 s**，由原始 rank JSONL 独立重算 `max(weights_ready)-min(load_begin)`，与 summary 完全相同。两个 rank 同在 `m03r2n09`，各有且仅有一个 begin/ready 事件，没有跨主机单调时钟相减。到达偏差 0.026004019 s。

| 字段 | rank0 | rank1 |
| --- | ---: | ---: |
| rank load 窗口 s | 122.266784294 | 122.240959336 |
| 真 DefaultModelLoader 窗口 s | 122.258363385 | 120.790742977 |
| model.load_weights 窗口 s | 121.931141853 | 120.462291458 |
| checkpoint iterator 窗口 s | 121.875791416 | 120.385011175 |
| prepare_weights s | 0.003793002 | 0.003720101 |
| Default loader 返回至 weights_ready s | 0.006393297 | 1.448239217 |
| ready 后全量内容/布局验证 s | 73.298129716 | 66.389541323 |
| checkpoint iterator tensor 数 | 1045 | 1045 |

这些阶段互相嵌套，不能相加。iterator 包含权重读取和消费端对每个 yield 的处理，不能当成纯 NFS 读盘时间；rank1 的 1.448239217 s 尾部只定位到 runner/synchronize 区间，没有据此宣称具体硬件根因。

fresh 启动请求至 `get_model_info` ACK 为 **326.500086745 s**；启动请求至首正确 token 为 **399.761977791 s**。启动时间包含进程/库/分布式初始化、正式权重窗口和 ready 后独立验证，也包含引擎后续初始化。首 token 路径还受 fresh JIT/服务预热等开销影响。它们与权重主指标分别报告，不将 bootstrap 或 HTTP 时间替代主指标。

## 2. cache 与格式条件

本 case 明确是 `observed_file_cache`，没有执行本 case 的全量预读或全局 drop cache。此前 `native_warm_r01` 的预读失败记录保留：71,903,776,776 bytes buffered read/237.199063967 s 后 mincore 驻留只有 3810/6656=57.241586538%，正式 GPU loader 没有启动，不能计性能样本。

本 case 的 preparation 采样亦为 57.241586538%。真正紧邻各 rank `load_begin` 的采样均为 **3711/6656=55.754206731%**；完成 ready 后全量 hash 附近采样均为 **6617/6656=99.4140625%**。后者不证明开始加载时 warm。mincore 是每个 shard 均匀抽样 Linux 文件页驻留，不是全页证明，也不认证 NFS server/storage cache 状态，不等于显式 pinned-host 保活。当前数据既不能标为 fully warm，也不能标为 cold。

实际输入为 NFS `/data/node28/shared/zhujianian/Qwen3.6-35B-A3B` 的 26 个原生 safetensors 文件，合计 71,903,776,776 bytes。最终处理后 TP2 权重合计 70,329,718,112 bytes；两种字节口径分开。既有完整 DRAM fixture 同时保留在现场，native 没有从这个 fixture 读权重；不能把它的保留成本隐去，或将现场缓存条件解释成 native 独立最低资源方案。

## 3. 完整校验与清理

每 rank 有 1009 个唯一 storage，逐项 storage 字节之和为 35,164,859,056，checked_bytes 同值。native storage/tensor 布局与冻结 expected 的整个 JSON 结构精确相等，两个 rank layout SHA 均为 `eb584e8de776682ffc564cc3686cf2f82d3d7d709dc2aa3e8b1a67d75cbca498`。

全内容 SHA：

- rank0：`50886ec38c6a85911072eba148e0717dfd34144f99e1863bc016757eb5ea67cd`
- rank1：`fd0f438bcb07669eb3f39f0b4742ea4c15ae94a37c330e31dad04dfa2a5d9311`

二者与此前通过验收的 S2 全 rank source SHA 相同。captured storage 顺序与 packed `source.index` 的每项 offset/nbytes 连续一致，无内部 packing 洞；每 rank keeper 分配尾部 1360 bytes page padding 不在原 logical SHA 范围，native 同样排除。storage 自身 nbytes 内的全部字节均纳入。最早 fingerprint_complete 在两 rank 最晚 weights_ready 之后，代码中 ready 后 TP CPU barrier 防止一个 rank 的 hash 干扰另一个尚未结束的加载。

生成参数为冻结 prompt `The capital of France is`、temperature=0、max_new_tokens=32；最终 32-token text 精确匹配原参考。这里认证记录中的 full SHA 与被保留的冻结参考及布局一致，不声称在引擎清理之后重新读取全部已释放 GPU 字节。

PID/start_time 约束的自有 process stop 返回 0（PID41），自有容器 `zjn-fluid-native-35a026baee571b` 删除返回 0。`cleanup_errors=[]`、`owned_remaining=[]`、`retained_base_unchanged=true`、`source_hash_mismatches=[]`。四 keeper 的 allocated/reserved 各 64MiB，前后不变；node29 worker2/3 所报 HBM headroom 前后精确相等（63,982,010,368 / 63,535,316,992 bytes）。这支持本次资源回收，不替代连续设备峰值采样或其他服务完整物理归因。

## 4. native 身份

身份为 **海光 vendor SGLang 原生 checkpoint 加载路径**，method=`sglang_vendor_native`。实跑调用 `DefaultModelLoader.load_model(AUTO)`、`_prepare_weights`、`_get_all_weights`、`Qwen3_5MoeForConditionalGeneration.load_weights` 的 source path/行号/文件 SHA 均有事件证据。实现保留原函数，只增加边界/阶段观测，没有借 Fluid restore 执行器填权重。

包版本 `0.5.12+das185.dtk2604.torch2100.2608121934.gcbf025`，packaged build revision=`cbf025`；安装目录无 `.git`，无 direct_url，完整 installed source commit 未认证。不可冒称 lib_cite 冻结 upstream 原版 daemon。镜像以不可变 ID `sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588` 启动；loader/weight_utils/model_runner 与实际 model 文件 SHA 分别冻结。

## 5. SLLM full 实现审查

当前源码副本位于本目录，其 SHA 见 `reviewed_code_sha256.txt`。本审查确认路径/控制契约，不给未执行的 full 性能验收。

真实路径为 Python adapter → 编译的 `sllm_store._checkpoint_store.CheckpointStore` → upstream `LoadModelFromDiskAsync`/`Model::ToHost` → `DispatchToGpu` → 同步 `cudaMemcpy`（HIP 构建转换）→ `WaitModelInGpu`。独立 worktree 的两处 patch 仅变更构建/未用头；Model 数据读取、dispatcher、H2D 实现没有替换。该身份是 **官方 Store 数据面 HIP 适配**，不是 ServerlessLLM 原版整系统/选址调度/frontend。

当前 adapter 仅接受 raw64 或已确证的 PyTorch malloc frame `02 63 + 64-byte raw`，解帧后按 raw handle 去重，并保留每 storage `handle[3]` allocation byte offset；不同 storages 同一 allocation 不重复 open。上游 dispatcher 对每条 storage 再按 host chunk 切分，GPU offset 从该条 `dst_offset` 连续递增，和 adapter offsets 一致。全 rank packed index 连续性、storage 名称集合、storage bytes、single device 及完整逻辑长度在写之前核验；frame 不支持的值直接拒绝。

Store `WaitInGpu` 在各 copy future 完成并置 LOADED 后返回；H2D 使用同步 copy。rank.done 在 Wait 返回后写入，两个 scheduler rank 再 synchronize/barrier 才 emit weights_ready，正式主指标仍来自同 host engine monotonic 事件。Store 局部的 wall-clock窗口是辅助字段，不能与 coordinator 或 engine monotonic 数值相减。

正常生命周期为 pool setup（窗口外）→ live engine skeleton exports → 上游 Store填充 → ready → exact生成 →触发全 SHA → loader.close/进程exit0 → engine release。源码要求 worker 进程退出后再释放 exporter，避免 C++或PyTorch importer仍映射时释放 engine storages。

失败时已经确认 resume ACK 才移除自有 adapter 容器、等待其关闭，再 release；未确认或 resume exception 的状态保留为 pending_resume_preserved，run failed，交主 agent 恢复，不擅自释放。底本仅做 metadata保护；全内容依据 exporter streamed SHA 及最终目标 SHA。新 CPU exporter 只读实际匿名 HostAllocation mmap，并与 S2 full SHA比对；旧 `/save`/FIFO 路径不适用于 ClusterWorker，不能引用为当前有效导出实现。

## 6. SLLM 后续需明确的准入/标注边界

1. **运行时 cache 仍未测。** 当前 `load_engine_storages.py:40` 直接继承 preparation 的“newly written/no drop”描述，不能认证 delayed full run 真正读取时的 Linux residency。汇总中应写“准备阶段新写入；加载前驻留未测”，有条件补同 partition 的只读 mincore。不能与 native55.7542% initial residency 混算 cache 相同的速度比。
2. **mtime 不是可靠的跨节点时钟新鲜性证明。** `load_engine_storages.py:50` 将 coordinator传入的 wall-time与 NFS文件mtime比较，可能因 host/NFS时钟偏差拒绝真正新 export。新 phase 唯一目录、rank/identity/layout及 producer PID与当前rank load事件的绑定更直接；若保留mtime，仅视为辅助准入条件。这不影响正式同host monotonic主指标。
3. **冻结参考 helper 可收紧。** `full_protocol.frozen_rank_hashes` 已检完整bytes/identity/off/n/rank，但未验证 baseline/case passed+correct、op.source_offset=0、replica_id属于完整source_ids、verification单op.n及source/target SHA一致。当前真实S2证据这些条件均成立，未造成此次错误参考；复用脚本时宜补为显式拒绝条件。prepared.status也应要求passed。最终full目标SHA仍须匹配冻结参考，不能用尺寸相等替代。

已保留 offset gate r01/r02 的失败身份。r02首错是 raw framed handle 被上游按64B ABI误读导致 invalid device pointer，随后 timeout；这是已确证并修正的原因，本review没有重复GPU排查。gate r03与full由主agent排程，成功前不计SLLM性能样本。

机器重计算结果见 `analysis.json`，原 native 输入 SHA 见 `input_sha256.txt`。本轮只有独立观测，无稳定排序、统计加速或跨不同初始条件的速度比。
