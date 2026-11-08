# 三节点只读调查与新容器隔离入口

当前可读资源快照是 `inventory_r03/node28.stdout.json` 和 `node30.stdout.json`，最终说明为 `inventory_summary_r03/report.md`。node27 未取得完整新快照；own collector4037472/start477235183在KFD vramtbl读取进入D，controller4037469已按父授权精确TERM退出，D child未重复signal。`inventory_r03/partial_completion.json` 和两份own账本记录最终边界。旧r01身份guard失败、r02本地路径错误及所有旧源码副本保留。

后续collector排除vramtbl，仅匹配 `vram_[0-9]+|pasid`，未重扫node27。sysfs余量不替代HIP free；0 VRAM的外部KFD owner仍为活动上下文，不能推断独占。

`fresh_container.py` 默认只生成计划。父审查后才使用 `--execute`；该入口只建空容器或读取保护元数据，不执行probe。最终计划为 `inventory_setup_plan_r06/node28.spec.json` 与node30对应文件，其中最新init/copy/NCCL单文件init_gate和namespace源码以SHA/base64冻结。两个新container均采用准确本地image，8GiB memory、512pids、privateIPC/1GiB shm、IPC_LOCK、memlock unlimited/core0、host network、四设备及hyhal只读，显式sleep entrypoint并关闭healthcheck。`/tmp/fluidckpt_three_node/isolated_r01_n*/project` 映射到容器内项目绝对目录，只含本任务资产，不依赖远端home共享、不挂模型。

父可执行（输出目录必须不存在）：

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/inventory/fresh_container.py \
  --spec codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_setup_plan_r06/node28.spec.json \
  --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_setup_node28_r01 \
  --mode setup --execute
```

node30使用其独立spec/out。返回完整Docker State/HostConfig/mounts和设备路径检查，父据此取fullID、StartedAt及host boot。probe的selected visibility只由本次docker exec `-e`设置，不写镜像或全局环境。

上传先在远端`mktemp -d`建立独立bootstrap，scp保存实际fresh_host.source.py与effective.spec.json，再python3执行该文件；没有inline实验Python或巨大spec argv。上传argv、返回码、原/effective spec SHA、两源码副本都保存在父指定新输出目录。

保护快照使用同CLI `--mode guard`，并传成对的 `--expected-container-id FULL_ID --expected-container-started STARTED_AT`。guard只读两机boot、完整容器身份、所登记旧容器、KFD PID/start/cgroup/vram字段、host/cgroup内存、所选GPU sysfs、冻结资产SHA；node28严格只GET29702–29707的旧6keeper compact inventory，canonical catalog SHA排除dynamic free/sample字段。另keep-v02六PID/start/cgroup窄保护，不额外扩展RPC。load_session仅提取旧独立函数AST，不导入discovery。父必须核所有identity checks、6keeper passed与catalog前后完全一致；rc0不自动代表保护/准入通过。缺权限或owner不可见保持unknown。旧node29从未访问，两个旧pending保持独立。

源码变更会使spec中的host wrapper SHA失配，必须新建计划目录再执行；不得覆盖旧计划。后续NCCL/XML新增资产同样必须显式冻结、复制与验证，不把旧stage静默升级。GPU启动、run ID、清理统一由父协调。

## node28根盘容量阻断

`inventory_disk_node28_r01`唯一一次df/findmnt只读检查确认 `/tmp` 与 `/var/lib/docker` 位于同根ext4，available=0B、inode未满；`/dev/shm` tmpfs余270,356,140,032B，本地`/data/node28` ext4余842,387,083,264B，`/home`为node27 NFS。未清理任何文件，未重试同/tmp。

只为28新增`fresh_host_alt.py`及离线 `inventory_setup_plan_r07/node28.spec.json`，bootstrap与stage移到严格白名单/dev/shm；原fresh_host/probe源码与node30已有/tmp容器不动。脚本/host冻结资产共277,669B，spec374,549B；仅少量脚本/结果，不挂模型。创建默认Dockerroot可能仍受根盘满影响，单改stage不能证明新container可建；由父据Docker根与容量证据决定下一步。不移动Docker全局data-root、不删除其他用户文件。
