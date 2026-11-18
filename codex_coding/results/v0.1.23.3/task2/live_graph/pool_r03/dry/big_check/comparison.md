schedule: {"seed": 1, "requests": 300, "calm": 15, "burst_load": 4.0, "amp": 60.0, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "fd69c9ab289e2ed79ed90f6dbfdf9f3488ed2a17351c29f2380f839a0588323c", "events": 196, "by_kind": {"ssd_rd": 33, "hbm_idle": 18, "hbm_serve": 23, "dram": 46, "h2d": 21, "ib": 42, "ssd_wr": 13}, "during": 57, "mean_concurrency": 3.31, "max_concurrency": 8, "requests_without_burst": 7}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool_v2 (mean, p95) | sglang / fluidpool_v2 (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 300 | 0.14 / 0.01 / 0.80 / 0.80 | 0.69 / 0.01 / 0.85 / 11.09 | 1.72 / 0.01 / 11.09 / 11.09 | 4.9x, 1.1x | 12.3x, 14.0x |
| misses only | 63 | 0.63 / 0.77 / 0.80 / 2.87 | 3.25 / 0.80 / 11.09 / 59.44 | 8.14 / 10.70 / 11.09 / 59.44 | 5.1x, 14.0x | 12.9x, 14.0x |
| calm, all | 15 | 0.24 / 0.01 / 0.80 / 0.80 | 1.76 / 0.01 / 11.09 / 11.09 | 3.14 / 0.01 / 11.09 / 11.09 | 7.2x, 14.0x | 12.8x, 14.0x |
| calm, misses | 5 | 0.71 / 0.80 / 0.80 / 0.80 | 5.26 / 2.55 / 11.09 / 11.09 | 9.38 / 11.09 / 11.09 / 11.09 | 7.4x, 14.0x | 13.2x, 14.0x |
| burst part, all | 285 | 0.14 / 0.01 / 0.80 / 0.80 | 0.63 / 0.01 / 0.80 / 11.09 | 1.64 / 0.01 / 11.09 / 11.09 | 4.7x, 1.0x | 12.2x, 14.0x |
| burst part, misses | 58 | 0.62 / 0.77 / 0.80 / 2.87 | 3.08 / 0.80 / 11.09 / 59.44 | 8.03 / 10.70 / 11.09 / 59.44 | 4.9x, 14.0x | 12.8x, 14.0x |
| misses, a burst on the node | 41 | 0.58 / 0.77 / 0.80 / 0.80 | 2.12 / 0.80 / 11.09 / 11.09 | 7.07 / 10.70 / 11.09 / 11.09 | 3.7x, 14.0x | 12.2x, 14.0x |
| misses, no burst on the node | 17 | 0.74 / 0.77 / 2.87 / 2.87 | 5.37 / 0.80 / 59.44 / 59.44 | 10.35 / 10.70 / 59.44 / 59.44 | 7.2x, 20.7x | 14.0x, 20.7x |
| misses, burst fired in flight | 29 | 0.71 / 0.77 / 0.80 / 2.87 | 3.78 / 0.80 / 11.09 / 59.44 | 9.67 / 10.70 / 11.09 / 59.44 | 5.3x, 14.0x | 13.7x, 14.0x |
| misses, dram on the node | 10 | 0.62 / 0.80 / 0.80 / 0.80 | 1.66 / 0.80 / 11.09 / 11.09 | 7.64 / 11.09 / 11.09 / 11.09 | 2.6x, 14.0x | 12.2x, 14.0x |
| misses, ib on the node | 16 | 0.57 / 0.77 / 0.80 / 0.80 | 2.60 / 0.80 / 11.09 / 11.09 | 6.99 / 10.70 / 11.09 / 11.09 | 4.6x, 14.0x | 12.3x, 14.0x |
| misses, ssd_rd on the node | 4 | 0.51 / 0.80 / 0.80 / 0.80 | 3.08 / 0.80 / 11.09 / 11.09 | 6.25 / 11.09 / 11.09 / 11.09 | 6.1x, 14.0x | 12.3x, 14.0x |
| misses, ssd_wr on the node | 3 | 0.80 / 0.80 / 0.80 / 0.80 | 4.23 / 0.80 / 11.09 / 11.09 | 11.09 / 11.09 / 11.09 / 11.09 | 5.3x, 14.0x | 14.0x, 14.0x |
| misses, hbm_idle on the node | 11 | 0.47 / 0.38 / 0.80 / 0.80 | 1.41 / 0.38 / 11.09 / 11.09 | 5.24 / 2.55 / 11.09 / 11.09 | 3.0x, 14.0x | 11.1x, 14.0x |
| misses, hbm_serve on the node | 12 | 0.65 / 0.80 / 0.80 / 0.80 | 1.51 / 0.80 / 11.09 / 11.09 | 8.18 / 11.09 / 11.09 / 11.09 | 2.3x, 14.0x | 12.6x, 14.0x |
| misses, h2d on the node | 6 | 0.66 / 0.80 / 0.80 / 0.80 | 0.66 / 0.80 / 0.80 / 0.80 | 8.24 / 11.09 / 11.09 / 11.09 | 1.0x, 1.0x | 12.6x, 14.0x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool_v2 (mean, p95) | sglang / fluidpool_v2 (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 300 | 0.14 / 0.01 / 0.80 / 0.80 | 0.69 / 0.01 / 0.85 / 11.09 | 1.72 / 0.01 / 11.09 / 11.09 | 4.9x, 1.1x | 12.2x, 14.0x |
| misses only | 63 | 0.63 / 0.77 / 0.80 / 2.87 | 3.25 / 0.80 / 11.09 / 59.44 | 8.14 / 10.70 / 11.09 / 59.44 | 5.1x, 13.9x | 12.9x, 13.9x |
| calm, all | 15 | 0.24 / 0.01 / 0.80 / 0.80 | 1.76 / 0.01 / 11.09 / 11.09 | 3.14 / 0.01 / 11.09 / 11.09 | 7.2x, 14.0x | 12.8x, 14.0x |
| calm, misses | 5 | 0.71 / 0.80 / 0.80 / 0.80 | 5.27 / 2.55 / 11.09 / 11.09 | 9.38 / 11.09 / 11.09 / 11.09 | 7.4x, 14.0x | 13.2x, 14.0x |
| burst part, all | 285 | 0.14 / 0.01 / 0.80 / 0.80 | 0.63 / 0.01 / 0.80 / 11.09 | 1.64 / 0.01 / 11.09 / 11.09 | 4.7x, 1.0x | 12.2x, 14.0x |
| burst part, misses | 58 | 0.63 / 0.77 / 0.80 / 2.87 | 3.08 / 0.80 / 11.09 / 59.44 | 8.03 / 10.70 / 11.09 / 59.44 | 4.9x, 13.9x | 12.8x, 13.9x |
| misses, a burst on the node | 41 | 0.58 / 0.77 / 0.80 / 0.80 | 2.12 / 0.80 / 11.09 / 11.09 | 7.07 / 10.70 / 11.09 / 11.09 | 3.7x, 14.0x | 12.2x, 14.0x |
| misses, no burst on the node | 17 | 0.74 / 0.77 / 2.87 / 2.87 | 5.37 / 0.80 / 59.44 / 59.44 | 10.35 / 10.70 / 59.44 / 59.44 | 7.2x, 20.7x | 14.0x, 20.7x |
| misses, burst fired in flight | 29 | 0.71 / 0.77 / 0.80 / 2.87 | 3.78 / 0.80 / 11.09 / 59.44 | 9.67 / 10.70 / 11.09 / 59.44 | 5.3x, 13.9x | 13.7x, 13.9x |
| misses, dram on the node | 10 | 0.63 / 0.80 / 0.80 / 0.80 | 1.66 / 0.80 / 11.09 / 11.09 | 7.64 / 11.09 / 11.09 / 11.09 | 2.6x, 13.9x | 12.2x, 13.9x |
| misses, ib on the node | 16 | 0.57 / 0.77 / 0.80 / 0.80 | 2.60 / 0.80 / 11.09 / 11.09 | 6.99 / 10.70 / 11.09 / 11.09 | 4.6x, 13.9x | 12.3x, 13.9x |
| misses, ssd_rd on the node | 4 | 0.51 / 0.80 / 0.80 / 0.80 | 3.08 / 0.80 / 11.09 / 11.09 | 6.25 / 11.09 / 11.09 / 11.09 | 6.1x, 14.0x | 12.3x, 14.0x |
| misses, ssd_wr on the node | 3 | 0.80 / 0.80 / 0.80 / 0.80 | 4.23 / 0.80 / 11.09 / 11.09 | 11.09 / 11.09 / 11.09 / 11.09 | 5.3x, 14.0x | 14.0x, 14.0x |
| misses, hbm_idle on the node | 11 | 0.47 / 0.38 / 0.80 / 0.80 | 1.41 / 0.38 / 11.09 / 11.09 | 5.24 / 2.55 / 11.09 / 11.09 | 3.0x, 14.0x | 11.1x, 14.0x |
| misses, hbm_serve on the node | 12 | 0.65 / 0.80 / 0.80 / 0.80 | 1.51 / 0.80 / 11.09 / 11.09 | 8.18 / 11.09 / 11.09 / 11.09 | 2.3x, 14.0x | 12.6x, 14.0x |
| misses, h2d on the node | 6 | 0.66 / 0.80 / 0.80 / 0.80 | 0.66 / 0.80 / 0.80 / 0.80 | 8.24 / 11.09 / 11.09 / 11.09 | 1.0x, 1.0x | 12.6x, 13.9x |

### Cold bytes per activation (file + NVMe + peer staging) / all bytes, misses only

| system | mean | p50 | p95 | max | activations with any cold byte | cold GiB total |
|---|---|---|---|---|---|---|
| fluidpool_v2 | 0.000 | 0.000 | 0.000 | 0.027 | 1/63 | 19.2 |
| janus | 0.238 | 0.000 | 1.000 | 1.000 | 15/63 | 1411.1 |
| sglang | 1.000 | 1.000 | 1.000 | 1.000 | 63/63 | 3436.5 |

### How each request was served

| requests | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| all requests | fluid 63, ipc_hit 237 | cold 15, d2d_fork 46, h2d_wakeup 2, ipc_hit 237 | cold 63, ipc_hit 237 |
| calm, all | fluid 5, ipc_hit 10 | cold 3, d2d_fork 2, ipc_hit 10 | cold 5, ipc_hit 10 |
| burst part, all | fluid 58, ipc_hit 227 | cold 12, d2d_fork 44, h2d_wakeup 2, ipc_hit 227 | cold 58, ipc_hit 227 |

| | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| cold activations, burst part | 0 | 12 | 58 |
| requests that needed a fallback path | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool_v2 pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 0.667 / 0.5, 0.714 / 0.4248 | 0.176 / 0.1765, 0.35 / 0.1842 |
| burst | 0.966 / 0.6471, 0.898 / 0.3749 | 0.532 / 0.3824, 0.32 / 0.2037 | 0.28 / 0.1471, 0.168 / 0.0906 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool_v2: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|
| dram | 46 | 1.00, 0.00 / 0.01 | 1.00, 0.01 / 0.01 | 1.00, 0.00 / 0.00 |
| ib | 42 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_rd | 33 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_wr | 13 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| hbm_idle | 18 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| hbm_serve | 23 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| h2d | 21 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool_v2: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|
| dram | 34/46, 0.001 / 0.004, 3944.5 | 18/46, 0.001 / 0.002, 1533.9 | 0/46, None / None, 0.0 |
| hbm_idle | 17/18, 0.001 / 0.001, 1322.0 | 0/18, None / None, 0.0 | 0/18, None / None, 0.0 |
| hbm_serve | 0/23, None / None, 0.0 | 0/23, None / None, 0.0 | 0/23, None / None, 0.0 |

### Live graph and integrity

| | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| graph poll p50 / p99 (ms) | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 |
| pool events | {'evict': 182, 'repair': 227, 'demote': 6} | {'evict_whole': 21} | {} |
| requests / hits / misses / skipped | 300 / 237 / 63 / 14 | 300 / 237 / 63 / 14 | 300 / 237 / 63 / 14 |
| verified restores, bad chunks | 13, 0 | 13, 0 | 13, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none |
| bytes by source | {'net': 0.839, 'ldram': 0.155, 'file': 0.006} | {'net': 0.569, 'file': 0.411, 'ldram': 0.02} | {'file': 1.0} |
