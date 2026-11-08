# 27/28/30 最小 fresh 初始化关卡

本入口保持 v0.1.23.3/task2，只使用已存在且父 agent 准入的容器。当前仅完成 CPU 验证，未启动 GPU/HIP/SMI，也未创建容器。node29 不在 CLI 的合法节点集合。

`init_gate.py` 在容器内启动新的 Python 子进程。父 agent 必须先从 host 核实际 hostname、boot_id、Docker 完整 ID、StartedAt、运行状态和选定 GPU 的当前 owner/候选余量；结束后复核身份及保护状态。CLI 中的容器身份来自 host 已核事实，容器内脚本不独立证明 Docker ID。清理只在创建时同 boot/PID namespace 内，对新 PID/starttime/argv/PGID/session 精确匹配的进程组发 TERM/KILL；不存在或已 Z 的 leader 不发信号。若 leader 已退出但组内仍有进程，只记录 pending，不泛 kill。

`--stage init` 为默认，零 payload：Torch import → set_device → HIP 实际 BDF → 实际 mem_get_info。`--stage copy` 需要初始化通过后另起新 run，重新初始化、BDF 与 HIP free > 2GiB + 1MiB 准入，执行 1MiB 非恒定字节 H2D/D2H 和全 SHA。两个阶段均从 Popen 起 15 秒外部硬 deadline，另有 ≤1.5 秒 exact 清理窗口。超时/SIGSEGV/初始化后退出失败均是失败；不得凭 child.result 中的 passed 忽略 supervisor timeout/cleanup。

CLI 默认 `--visibility all` 清除三个可见性变量，用传入 GPU ordinal 选择并核实际 BDF。父 agent 本次选择 `selected`，显式设置 HIP/CUDA visibility，逻辑 GPU 为 0，仍必须与请求物理 BDF 相符；初选 node28 GPU2/BDF0000:55:00.0、node30 GPU0/BDF0000:09:00.0。不会更改容器或镜像全局环境、原厂 XML/default mapping。core limit 在 supervisor 和 fresh child 均为 0。

下面为父 agent 待执行模板，尖括号值来自当前 inventory/inspect，不是可直接复制的资产。父 agent 使用已有严格 SSH 及有界 docker exec 外层，首次选健康节点，勿自动重跑 node27 旧失败矩阵。脚本路径与结果目录须在容器中可见。

```bash
python3 /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py \
  --node node28 --container-id <64hex> --container-started-at <StartedAt> \
  --host-boot-id <boot_id> --expected-container-hostname <actual_hostname> \
  --gpu 2 --bdf 0000:55:00.0 --visibility selected --stage init \
  --out /home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_node28_init_r01
```

每 run 保存 registration/source.py、child.owner/process/log/events/result、summary。node tag 和实际 hostname 分开，不假设 node28 的 hostname 是 node28。sysfs total-used 不作为 HIP free；owner unknown 仍为 unknown，共置节点只做功能验收，不形成性能样本。node27 还须沿现有窄保护入口保存 keeper 全 catalog/正常 RELEASED+lease0/完整 DRAM base VALID/parked source/原 XML SHA；本脚本不调用 keeper API，不认证这些保护。

`--stage nccl` 为同一薄入口的后续功能模式，需各参与节点 init/copy 通过后由父统一启动。各 rank 独立 supervisor 和新 probe_* 目录，传 `--rank N --world-size 2 --master tcp://<实际IP>:<未占用port> --xml-manifest <本node.manifest.json> --scope local|cross`。单机两卡或跨机两卡都沿同一命令接口。NCCL模式保持 15 秒 init 子 deadline，并给 fresh child 总 45 秒 deadline（外层建议50秒）；init事件晚于15秒或退出时缺事件也失败。直接 TCPStore/NCCL 初始化，不建额外 Gloo。communicator后再次实际 HIP free 准入、1MiB broadcast、全字节 SHA、每rank独立实际dump与selected BDF/全PCIchain/真实NUMA/affinity/rank/logicaldevice/gdr0对照；原XML与owned input SHA前后比较。必须有本child PID/rank的精确owned input Loading topology file、无mapping fallback、实际channel transport及communicator节点数；cross额外必须实际NETchannel且nNodes>1，local必须nNodes1，不能把dump中NIC叶当传输。保守 gdr0 是本次输入policy，不能当硬件GDR能力结论。

NCCL仅在本rank dump与内容及子进程回收通过后 `xml_runtime_accepted=true`，scope限定本rank所选BDF与这次communicator；父必须聚合所有rank及前后保护才验收整次单机/跨机功能。超时在process.json.last_stage/events区分Torch import、set_device、communicator或teardown；不推断永久硬件故障，不把事件间隔作为带宽样本。

`container_namespace.py` 为 CPU-only 补证入口，在选定容器内读取 inventory 中已有 PCI paths 的实际 namespace numa_node/local_cpulist，核 actual hostname/boot 与 host 原始证据一致，8 秒 alarm；父 host 外层应给 10 秒总限时。产物符合既有 XML validator 的 namespace_sysfs schema。`build_xml.py` 接收重复 `--node-evidence NODE HOST_JSON CONTAINER_JSON`，复用产品 `physical_xml/validate_physical_xml` 建完整物理 gdr0 XML，不填假 NUMA、动态 rank/dev/GUID，不把 CPU 通过当 runtime 接受。node↔host 关联由当前 inventory 及父的参数审查保证，host 字段原样保留。

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/container_namespace.py \
  --host codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_r03/node28.stdout.json \
  --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_namespace_node28_r01

python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/build_xml.py \
  --node-evidence node28 codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_r03/node28.stdout.json codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_namespace_node28_r01/container.json \
  --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_node28_r01
```

已执行 CPU 验证：

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/test_cpu.py --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_cpu_r05
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/build_xml.py --node-evidence node27 codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r01/node27/host.json codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r01/node27/container.json --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_cpu_r01
```

21 CPU 检查通过，包括 BDF mask/余量边界、五个身份字段错配、自然及立即退出、TERM 无响应后 KILL、跨 boot 不发信号、已回收 PID 缺字段安全返回、init及时准入/未及时准入外部截止/早退出缺事件，以及错误PID、mapping回退、cross只有SHM、相似input路径误命中拒绝。CPU run保存三个源码副本及SHA。XML CPU 验证仅重用旧 node27 静态证据验证参数化入口，不能当作新 28/30 实机 namespace 或 runtime 证据。旧结果未覆盖，产品及旧源码 freeze 未修改。

# 已存在 fresh 容器的 host launch/collect

`run_gate.py` 是父本机入口，默认仅生成可审计划，`--execute` 才调用严格 SSH。`host_gate.py` 是上传到远端的独立小入口。读取已冻 setup spec、实际 setup stdout 与已核 guard stdout，交叉核 boot/完整ID/StartedAt/运行态/image/实际源码SHA；远端执行前后再核同身份和本任务label。不会修改已经 staged 的 init_gate 源码。父已经 native 执行的 node30 `probe_node30_init_r01` 绝不重跑。

当前 host wrapper 限于 init/copy，bootstrap+上传+远端执行共用35秒外层预算，docker exec单项20秒，容器内探针仍15秒及exact清理。外层超时只处置新SSH/docker CLI，明确不当作GPU清理证明；容器身份连续才额外运行CPU-only child_state，absent或同starttime的Z可作退出，复用Z/live错身份不接受，不发GPU信号。远端状态未知保留pending，父另协调精确清理。NCCL的45秒薄入口已有，但这层35秒wrapper没有宣称可启动NCCL。

只归档8个明确本run文件为≤4MiB tar，单项≤2MiB，并逐文件/归档SHA回传验证。父本机安全逐条解析tar，拒绝陌生路径、重复/非普通文件、超限或hash变化；不从远端同步完整项目或其他结果。结果 `result.json` 仅认证本run进程/身份；keeper/catalog/owner/原XML等全保护仍由父前后guard聚合。

已生成 `probe_launch_plan_node30_copy_r01`，仅CPU审阅，使用当前已核before guard，并未承诺copy准入。实际copy须init成功及最新after/before guard通过后，用新result目录和相应guard路径执行，例如：

```bash
python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/run_gate.py \
  --spec codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_setup_plan_r06/node30.spec.json \
  --setup codex_coding/results/v0.1.23.3/task2/three_node_recovery/inventory_setup_node30_r01/stdout.json \
  --guard <最新通过guard的stdout.json> --stage copy --run-name probe_node30_copy_r01 \
  --out codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_launch_node30_copy_r01 --execute
```

`probe_launch_cpu_r02/summary.json` 保存9项CPU负例与三个源码副本/SHA，零SSH、Docker、GPU调用；`run_gate.py/host_gate.py`语法检查通过。原r01记录不覆盖。
