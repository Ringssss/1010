# 六 worker 最小多层恢复控制入口

编号保持 `v0.1.23.3/task2`。只执行已冻结合同的 `SSD_only/C0/C1/C3`，不扫 104 候选，不新增 solver。使用原 `Cluster.transfer`、`ClusterWorker`、Catalog、SSD HTTP 和冻结 SHA 为 `5038e02d38a53a327a826be32aa935a3c0694d56c6244f61656a4c1b4097f646` 的旧 adapter。

`runtime_worker.py` 是继承原 worker 的实验 launcher，调用原 HTTP handler，仅补实际 HIP BDF、sysfs NUMA、cgroup/进程身份、HCA 环境与端口、实际触页注册诊断。分配记录为历史记录，不能当当前空间账本。产品唯一修改为 `/synth` 支持 `tp_size`（缺省 2），在分配前拒绝非法 size/rank，并返回 `synthetic` 标签。所有新内容是合成 processed TP4 schema 字节，不是真实模型。

主协调器/operations 启动隔离容器、六 worker 和三个 SSD 服务后，在本机 CPU 环境依次执行：

```bash
PY=/home/zhujianian/miniconda3/envs/sglang/bin/python
RUN=codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/run.py
$PY "$RUN" --setup SETUP.json --stage mesh --output NEW_mesh
$PY "$RUN" --setup SETUP.json --stage qualify --budget BUDGET.json --output NEW_qualify
$PY "$RUN" --setup SETUP.json --stage cases --budget BUDGET.json --qualification NEW_qualify/qualification.json --release-base-at-end --output NEW_cases
```

每次输出目录必须不存在；失败、重试另用目录。建议各阶段独立外层 1200 秒上限，但只能由主统一管理 GPU/session/端口和超时清理。CPU runner 不启动 GPU worker，也没有 SSH 入口。

## 输入

`setup` 顶层包含 `purpose=physical/session/valid_until/workers/ssd/evidence_refs`。六 `workers` 顺序固定为 node28 GPU0/1 NUMA0/3、node30 GPU4/5 NUMA4/7、node27 GPU0/4 NUMA0/4，各行含 `worker/node/gpu/numa/endpoint/container_id/boot_id/pid_start_ticks/actual_bdf`；`pid_start_ticks` 为容器内 worker 的 `/proc/PID/stat` 第22项，不是 docker init host PID。原 worker 的 premesh GET inventory 允许 rank 未初始化，能取得这些实际诊断；主/operations 用外部容器身份原件交叉核对。`actual_bdf` 是候选期望，postmesh 必须与实际 HIP 返回值一致。

`ssd[n0/n1/n2]` 包含 `endpoint/storage_node/directory/container_directory`。`directory` 为宿主真实本地 SSD 路径，`container_directory` 为服务 `/status.root` 的挂载路径，二者关系由主审 mount 证据；不能拿共享远端目录称本地 SSD。`evidence_refs` 为绝对本地证据 path/sha256。当前 HCA 实际传输仍需审 worker RCCL init 日志；环境和端口 metadata 不自动认证选中传输。

`budget` 顶层为 `purpose=physical/session/valid_until/runtime_staging_inclusion=included/budget_accounting_complete=true/nodes`。每节点包含 `dram_limit_bytes/hbm_limit_bytes/physical_dram_available_bytes/cgroup_available_bytes/hbm_increment_bytes/selected_gpu_free_bytes`。新任务 DRAM 聚合上限 6GiB、HBM 增量上限 2GiB，容器硬上限 8GiB。runner 保守要求完整当前共享 cgroup（含 pagecache、两个 worker 和服务；不重复求和）加 256MiB 余量低于任务上限，每选定 GPU 仍有 2GiB 物理 reserve。postwarm HBM 增量由主审外部采样给出，后续以同期 free 为锚追加当前下降量，不能把 torch reserved 当包含驱动的物理增量。空间约束有界验收不等于完整峰值实测。

## 阶段与生命周期

`mesh` 初始化原 mesh，仅 warm 正式 recipes 必要 pair，保留 premesh/postwarm inventory。`qualify` 在正式底本创建前，用 4MiB 小载荷对 23 个必要原生 path 分别全 SHA 验证；所需 HBM/SSD 小来源自身也用原 transfer 建立，全部精确退役，三 SSD 小 extents 不冒称正式 32MiB 底本。六 NUMA 通过原 `HostAllocation` 实际 mbind、触页、256 页采样和 HIP 注册。该独立资格模式打破“正式案例跑完才能准入”的循环。

`cases` 要求同六 worker epoch/current identity、仍新鲜预算和真实 qualification SHA。先建立四个各8MiB的合成 processed shard，按八个4MiB extents建立 SSD 底本，n0/n1/n2分16/8/8MiB；原 complete 先文件 fsync 后 Catalog 发布，每逻辑字节全 SHA。保留当前活 owner，不能按文件名重开 extent，不能称重启持久恢复资格。随后四配置各一次，全部四8MiB目标 HBM 满字节 SHA 验证，再按旧 adapter 清理自身 optional/target。八底本直到四case结束均保留；仅显式 `--release-base-at-end` 在本run结束后允许精确释放本轮新底本，绝不触旧 keeper。

每阶段保留 journal 与 source SHA；所有 target ID 在 transfer 前写 journal。异常保留当前计划与 exact own scope 并尽量精确清理。原 API 没有 release_shard：如 synth 成功但 adopt 失败，journal 的 `unadopted_shards` 指明只能在最终 cleanup 退出本轮 owned worker释放，不能伪称已清完或碰旧进程。

`summary.json`/每case原 transfer输出保留同钟 save、prepare、filled、verify+publish，时钟域是原 CPU coordinator。模型 handoff 未运行，不能当 `T_model_ready`。服务 HTTP logical reads、fsync bytes、`/proc/self/io` kernel read/write bytes 分列，buffered read 不能声称冷盘；verification 自身 SSD reread也包含在服务 I/O 账本。

## CPU 验证

```bash
/home/zhujianian/miniconda3/envs/sglang/bin/python codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/cpu_check.py --output NEW_cpu
```

r02 实际复用原 synth HTTP 分支做14个接受/拒绝/分配前校验，并以原 serial API 在 CPU tensor/mock collective 上对23必要path每条4MiB全SHA、三个真实本机文件HTTP服务和精确cleanup验证。产物明确 `cpu_api_fixture/physical_admitted=false`；不能传给正式可信 gate。r01 是 fixture inventory 仍列 RELEASED row 导致测试误判，失败和日志保留。
