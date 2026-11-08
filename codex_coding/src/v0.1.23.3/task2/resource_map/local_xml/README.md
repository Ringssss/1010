# node27 自有 XML 本机双 rank 功能验证

本入口继续 `v0.1.23.3/task2`，只使用 node27 的既有容器及 GPU0/1。两个独立 rank 用 loopback Gloo rendezvous，建立一个两 rank NCCL group。每卡一个 1MiB uint8 buffer；source=31、target=0。预热一次后双方再次填充 31/0、全 buffer SHA 验证 reset，再执行一次功能 broadcast 并逐 rank 验全量 SHA。总 source 发送量是预热 1MiB 加功能 1MiB，不计算吞吐或性能样本。

源输入为 `resource_map/xml_owned_r01/node27.physical.gdr0.xml`，只读其 manifest/source SHA 并做 CPU 校验；不访问 node29 的 XML 或控制服务。每个 rank 独立请求自己的 communicator local rank dump。保存实际 BDF、dump SHA、原始 RCCL 日志、有效环境及 import 前 PID/starttime/core=0，并核所选 BDF 完整 PCI 祖先链、NUMA、affinity、rank/dev 和 gdr0 policy。实际 transport 从原始日志检查；本机可能使用 P2P 或 SHM，不把 net 叶节点存在理解为 NIC 数据经过或 GDR 认证。本机两卡通过不认证其余 GPU、node29、跨节点 NIC/GDR、整机 16 GPU 或性能就绪。

前后独立本机保护包括 worker0/1 full `/inventory` 的全部 catalog/meta/index/epoch/lease，完整两 rank 70,329,718,112B DRAM 底本、源 parked engine 的命令、原 spec/PID/starttime/live argv hash、container ID/StartedAt 与 host boot，原厂及 `/opt/dtk/rccl/lib/topo_mapping_default.xml` SHA。只读 catalog/身份保持不新增实际模型权重 SHA 读取，不调用 parked engine health，不向 node29 RPC。host SMI/owner/cgroup 和全部八卡 available 双向漂移另存；正式性能始终 blocked。

snapshot/helper 进程早期 alarm=30s、GNU timeout=35s、kill-after=2s，docker 客户端=40s；设备发现进入/退出写事件。rank alarm 在 Torch import 前设置，默认 120s；controller 的 rank hard deadline=125s。rank 直接由旧 `cluster/process.py` 启动，登记 PID 就是 rank PID。回收先核本 run spec SHA、PID/starttime、live argv/PGID，只对 live 的自有 group 调原 exact stop；Z/absent 只取证。所有读、stop 和取证命令有界；保留 failures。输出目录必须全新 `resource_map/local_xml_*`。

CPU 验证：

```bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/test_local.py
python3 codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/run_local.py --xml-directory codex_coding/results/v0.1.23.3/task2/resource_map/xml_owned_r01 --emit-plan
```

父 agent 审查并单独安排一次 GPU 功能运行；本轮代码建设不自动执行：

```bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/run_local.py --xml-directory codex_coding/results/v0.1.23.3/task2/resource_map/xml_owned_r01 --session codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json --out codex_coding/results/v0.1.23.3/task2/resource_map/local_xml_startup_r01 --port 34300 --timeout 120 --execute
```

显式资产为两个独立 rank、每卡 1MiB synthetic HBM、每 rank 全量验证 CPU 临时 buffer 约 2MiB，无模型加载/新增 keeper/DRAM 副本；context/communicator HBM 开销不假定为 1MiB，另记物理 free 和 Torch allocation/reservation。每卡启动及分配前 physical free 必须严格大于 2GiB，host/cgroup 余量须有 2GiB guard 加 512MiB context allowance。原 128MiB `max(0,before-after)` 未回收门槛不放宽；其他 owner 释放空间另记正漂移，不归因给本 probe。每个取证 artifact 最大 16MiB，必要 RCCL/plugin SHA 最大单库 256MiB，排除数 GB 的 libtorch hashing。通过要求功能、实际 mapping、保护、exact cleanup、代码/input SHA 全部通过；源保护或回收失败不能改成 passed。
