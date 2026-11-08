# 当前共置负载只读取证

编号 `v0.1.23.3/task2`。主 agent 暂停后续 GPU 测量后，2026-10-08 11:22:17（node27）/11:22:20（node29）CST 采集当前 CPU、NUMA、cgroup 和 hy-smi display 状态，再读 SMI PID 的宿主 start_time/cgroup。原始 [snapshot.json](snapshot.json)、[identities.json](identities.json) 的准确 UTC/argv/source SHA 已记录；组合 [summary.json](summary.json) 绑定二者 SHA。全部命令只读，无 kill/reset/config 修改。

**两机每张卡都有本任务外 `glm-5.2-int` 容器的 VLLM worker，当前约占 50,350–50,475MiB/卡。该现场明确不独占，适合收口输入图与 channel 事实；与历史耗时直接拼接的性能归因应暂停。**

| GPU | node27 宿主 PID / SMI VRAM MiB | node29 宿主 PID / SMI VRAM MiB |
|---|---|---|
| 0 / PCI09 | 3317580 / 50354 | 3455578 / 50348 |
| 1 / PCI36 | 3317584 / 50474 | 3455579 / 50473 |
| 2 / PCI55 | 3317587 / 50474 | 3455580 / 50472 |
| 3 / PCI77 | 3317592 / 50473 | 3455581 / 50472 |
| 4 / PCI85 | 3317596 / 50473 | 3455582 / 50475 |
| 5 / PCIb5 | 3317600 / 50473 | 3455583 / 50475 |
| 6 / PCId5 | 3317604 / 50473 | 3455584 / 50470 |
| 7 / PCIf5 | 3317609 / 50407 | 3455585 / 50405 |

16 个进程 `comm=VLLM::Worker_TP`、UID=0。node27 cgroup 为 `docker-4c530ca88a972d0d9eafa76c544db9fc9cc8888946aec3681047ba2c802049a0.scope`，node29 为 `docker-2339e1f80470ac5d9710fa84ecbfbf3b0b6f00488ef76bea27d4d17120f63b1b.scope`；docker ID/name 查询均匹配 `glm-5.2-int`，与当前 owned 容器 ID 不同。宿主 `/proc/PID/stat` start_time 推得 node27 10:58:12.58–12.59 CST、node29 10:59:13.64–13.69 CST；精确 ticks 与 boot/uptime 对齐保存在 identities。该占用在当前采样前已持续约25–26分钟。start_time 说明进程何时创建，具体 HBM allocation 时点需要进程连续样本。

2 秒 CPU 样本中这些 worker 每进程约占一核（node27 102.7–113.2%，node29 93.3–114.7%，100%=一个逻辑核）；另外能见 SGLang scheduler 活跃。两机128逻辑 CPU，loadavg 当时分别13.26/13.75/13.94和11.06/10.63/8.85。当前 node29 GPU0 的 HCU utilization 一次观测为63.8%；其余卡0%只表示该采样瞬间，显存占用仍在。

owned 容器与外部 VLLM cgroup 均 `cpu.max=max 100000`、有效 CPUs0–127、NUMAs0–7、`nr_throttled=0`。当前 CPU quota 无限制事实可验，正式窗口各线程的竞争量仍需同步采样。两机各 NUMA MemFree 约120–526MiB，但宿主 MemAvailable 分别328,475,324/117,171,672kB，故单看 NUMA MemFree 不能判定 DRAM 枯竭；CPU/memory/IO PSI 原值均保存。

`hy-smi --showpids --json` 会提示该 query 不支持 JSON，退出码0且保留完整原始文本，本次按 PID/HCU Index/BDF/VRAM 字段解析。SMI 显式列出的 KFD PID 是身份依据；未提权的 `/proc/*/fd` 扫描没有取得 root 进程 device fd，空数组不能解释为没有 GPU 进程。

主 agent 的 `probe_measure_unset_r01/postexit/summary.json` 保留 failed：四 own rank PID 已 Z，保护目录/代码/input检查通过，但四卡 total-free 前后各减少184,549,376B（176MiB）超过128MiB门槛。当前外部每卡约50GiB只证明共置占用；缺少正式窗口前后 per-PID VRAM 样本，176MiB 的精确归属和正式耗时变化因果均保持未知。

本次同时重读两机 RCCL、plugin、builtin XML、默认 mapping XML SHA，独立追加 [runtime_after.json](../provenance_r01/runtime_after.json)。8项逐文件前后比较全部一致，见 summary 的 `after_file_stability`。源取证原始文件不覆盖。

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/collect_runtime.py --mode load --out codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_runtime_r02/snapshot.json
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/collect_runtime.py --mode identities --snapshot codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_runtime_r02/snapshot.json --out codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_runtime_r02/identities.json
python3 codex_coding/src/v0.1.23.3/task2/topology_alignment/forensics/collect_runtime.py --mode hashes --out codex_coding/results/v0.1.23.3/task2/topology_alignment/provenance_r01/runtime_after_r02.json
```

每个输出均要求不存在，不可覆写r01。当前 summary 的哈希对照绑定本次 `provenance_r01/runtime_after.json`；若新复采另改 after 输入路径，再使用新派生目录。后续 GPU 实验与外部服务协调由主 agent/用户决定，本取证保持现存服务和保活底本。
