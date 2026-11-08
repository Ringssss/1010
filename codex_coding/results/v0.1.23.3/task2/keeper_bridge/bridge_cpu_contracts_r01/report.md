# Keeper bridge CPU 建设验收

编号 `v0.1.23.3/task2`。本目录仅记录代码建设与 CPU 检查，未执行 SSH/HTTP/GPU，无性能观察。

入口为 `codex_coding/src/v0.1.23.3/task2/keeper_bridge/run_bridge.py`，纯契约为 `main_lib/fluidckpt/benchmarks/keeper_bridge.py`。不修改存活 keeper、冻结 executor 或 baseline。node27 父 controller 只读取节点保护与运行事实；node29 子入口复用 `Cluster.transfer` serial。完整源 meta/index/identity 保留，target 是独有 run ID 的合法小 replica extent。

默认预热32MiB pair一次（覆盖A/B两通信对，完整SHA、退役，独立角色）；正式1GiB单A、单B、pair各一次。rank0 worker0→2，rank1 worker1→3。sourcelease在prepare/execute/complete后取证，finish后归零；target全SHA后publish，按独有ID退役。before/after目录、epoch、allocator allocated/reserved、staging、完整DRAM、parked命令、精确PID及旧keeper保护复用既有只读工具。physicalfree容差128MiB并与严格allocator/catalog核对。源码直接依赖和输入SHA前后核对。

实际keeper环境从精确PID `/proc/environ`白名单读取，注册日志及实际`NCCL_DEBUG_FILE`完整保留并带SHA；环境取证在正式父运行中执行，不从独立probe推断当前GDR/HCA。正式execute及独立200ms idle窗口保留每receiver HCA/port原始counter，端口归因依赖actual初始化日志。

计时限制：receiver execute RPC envelope在node29/boot_id的同主机monotonic可计算，包含dispatch/response；真实worker绝对monotonic事件缺失为null。worker elapsed与wall日志独立保留。coordinator prepare/execute/verify/publish含监控inventory及idle采样开销，phase RPC envelope独立列出。不报T_ready、device kernel窗口或共享硬件归因，不套0.85微probe门槛。

验证：`python3 codex_coding/src/v0.1.23.3/task2/keeper_bridge/test_bridge.py`，6项通过，见 `cpu_tests.log`。`python3 -m py_compile codex_coding/src/v0.1.23.3/task2/keeper_bridge/run_bridge.py main_lib/fluidckpt/benchmarks/keeper_bridge.py`通过。CPU契约覆盖真实底本1GiB索引合法性、非零off、越界/gap、错源/错namespace、active target不能隐藏、mixedclock/missingreceiver和sourcelease门槛。

待主agent正式执行与独立审查；本目录的pass仅是CPU检查，不代表真实bridge已经通过。
