# node27 XML 小功能入口 CPU 交付

编号继续 v0.1.23.3/task2。新源仅在 resource_map/local_xml/，没有改产品、冻结探针、原 XML/default mapping 或任何保留服务；本子任务没有启动 GPU，也没有执行 remote。

CPU 14 项全部通过（tests.txt）；--emit-plan 零 GPU 操作且只验证 node27 XML（plan.json）；四个实现文件 py_compile 通过。冻结源 SHA 见 source.sha256。测试覆盖真实输入/source SHA/8GPU 结构、错误 NUMA/PCI 祖先/rank/dev/gdr 政策、重复/缺失 BDF、full catalog metadata/index/epoch/lease 变化、原 128MiB 单向未回收门槛、独立本机 spec、实际 PID 日志/loading/localRanks/transport 及 fallback mapping 拒绝。

交付 run_local.py、read_local.py、rank_entry.py、common.py、test_local.py 和 README.md。正式父启动使用新目录 local_xml_node27_r01、port34310：

~~~bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/run_local.py --xml-directory codex_coding/results/v0.1.23.3/task2/resource_map/xml_owned_r01 --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/resource_map/local_xml_node27_r01 --port 34310 --timeout 120 --execute
~~~

显式资产为2rank、GPU0/1每卡1MiB，预热一次+功能一次（source总发送2MiB），CPU SHA临时约2MiB/rank；Torch context/communicator另记物理占用，不声称只占1MiB。启动/分配前每卡free严格>2GiB，host/cgroup有2GiB guard+512MiB context allowance。Rank import前120s alarm，host hard rank deadline125s；snapshot import安全阶段alarm30s+GNU timeout35s；每个控制/stop/取证命令独立有界。通信库仅RCCL/plugin（单库最大256MiB），artifact最多16MiB，不hash数GB torch库。

完整底本/meta/index/catalog/epoch/lease、3个protected spec/PID/starttime/argv hash、parked command、host boot/container ID/StartedAt与原厂/default mapping SHA前后保护。登记PID即rank PID，cleanup只对两个本run live/argv/PGID一致对象调用旧process.py，Z/absent只证退出。source保护、实际mapping/content/log、回收、代码/input SHA任一失败保持failed。八卡SMI free双向漂移单列，不归因到本probe。

summary.status/runtime_accepted仅本机GPU0/1范围；whole_machine_runtime_accepted=false、nic_gdr_accepted=false、performance_ready=false。runtime_evidence.graphs包含rank/BDF/input/actual ancestry与dump SHA；postexit有保存后的原始环境、RCCL日志、transport.json、两份actual dump与全buffer结果和逐artifact SHA。输入路径/hash在input_sha256和protocol中，collective_primitive为nccl_broadcast_uint8_local_rank0_to_rank1。后续地图派生只能引用本机两GPU runtime范围，不能将HCA图存在认证成实际网络/GDR，也不能填写有效性能速率。
