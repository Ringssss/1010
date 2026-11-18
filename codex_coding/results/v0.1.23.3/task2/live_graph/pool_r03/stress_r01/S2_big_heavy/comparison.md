schedule: {"seed": 1, "requests": 300, "calm": 15, "burst_load": 4.0, "amp": 60.0, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "fd69c9ab289e2ed79ed90f6dbfdf9f3488ed2a17351c29f2380f839a0588323c", "events": 196, "by_kind": {"ssd_rd": 33, "hbm_idle": 18, "hbm_serve": 23, "dram": 46, "h2d": 21, "ib": 42, "ssd_wr": 13}, "during": 57, "mean_concurrency": 3.31, "max_concurrency": 8, "requests_without_burst": 7}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool_v2 (mean, p95) | sglang / fluidpool_v2 (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 300 | 0.18 / 0.01 / 1.07 / 1.84 | 1.02 / 0.01 / 2.94 / 22.14 | 2.26 / 0.01 / 12.48 / 24.55 | 5.6x, 2.7x | 12.3x, 11.6x |
| misses only | 63 | 0.84 / 0.72 / 1.52 / 8.65 | 4.84 / 1.14 / 19.53 / 67.72 | 10.71 / 9.94 / 24.36 / 64.83 | 5.8x, 12.8x | 12.7x, 16.0x |
| calm, all | 15 | 0.19 / 0.01 / 0.74 / 0.74 | 3.38 / 0.01 / 22.23 / 22.23 | 5.76 / 0.01 / 22.48 / 22.48 | 17.8x, 30.0x | 30.3x, 30.3x |
| calm, misses | 5 | 0.55 / 0.57 / 0.74 / 0.74 | 10.12 / 2.94 / 22.23 / 22.23 | 17.26 / 22.02 / 22.48 / 22.48 | 18.6x, 30.0x | 31.7x, 30.3x |
| burst part, all | 285 | 0.18 / 0.01 / 1.07 / 1.84 | 0.90 / 0.01 / 1.97 / 19.50 | 2.07 / 0.01 / 11.03 / 24.55 | 4.9x, 1.8x | 11.3x, 10.3x |
| burst part, misses | 58 | 0.87 / 0.73 / 1.84 / 8.65 | 4.39 / 1.01 / 19.50 / 67.72 | 10.14 / 9.93 / 24.55 / 64.83 | 5.1x, 10.6x | 11.7x, 13.3x |
| misses, a burst on the node | 41 | 0.75 / 0.73 / 1.52 / 2.09 | 3.70 / 1.00 / 19.24 / 19.53 | 8.70 / 9.93 / 19.96 / 46.29 | 4.9x, 12.7x | 11.5x, 13.1x |
| misses, no burst on the node | 17 | 1.14 / 0.57 / 8.65 / 8.65 | 6.04 / 1.15 / 67.72 / 67.72 | 13.63 / 9.93 / 64.83 / 64.83 | 5.3x, 7.8x | 12.0x, 7.5x |
| misses, burst fired in flight | 29 | 1.00 / 0.75 / 2.09 / 8.65 | 5.27 / 1.18 / 19.50 / 67.72 | 11.45 / 10.24 / 24.55 / 64.83 | 5.2x, 9.3x | 11.4x, 11.7x |
| misses, dram on the node | 10 | 0.79 / 0.93 / 1.39 / 1.39 | 2.10 / 0.95 / 13.68 / 13.68 | 8.93 / 10.25 / 21.44 / 21.44 | 2.7x, 9.8x | 11.4x, 15.4x |
| misses, ib on the node | 16 | 0.75 / 0.73 / 1.84 / 1.84 | 4.35 / 1.30 / 19.50 / 19.50 | 9.40 / 9.92 / 46.29 / 46.29 | 5.8x, 10.6x | 12.5x, 25.1x |
| misses, ssd_rd on the node | 4 | 0.62 / 1.04 / 1.15 / 1.15 | 3.78 / 0.95 / 13.68 / 13.68 | 10.72 / 14.05 / 21.44 / 21.44 | 6.1x, 11.9x | 17.3x, 18.7x |
| misses, ssd_wr on the node | 3 | 0.92 / 0.89 / 1.15 / 1.15 | 4.89 / 1.14 / 12.58 / 12.58 | 14.98 / 12.48 / 21.44 / 21.44 | 5.3x, 11.0x | 16.3x, 18.7x |
| misses, hbm_idle on the node | 11 | 0.66 / 0.31 / 2.09 / 2.09 | 3.85 / 0.54 / 19.53 / 19.53 | 6.39 / 2.77 / 19.96 / 19.96 | 5.8x, 9.3x | 9.7x, 9.5x |
| misses, hbm_serve on the node | 12 | 0.91 / 0.99 / 2.09 / 2.09 | 2.62 / 1.12 / 19.53 / 19.53 | 11.83 / 10.28 / 46.29 / 46.29 | 2.9x, 9.3x | 13.1x, 22.1x |
| misses, h2d on the node | 6 | 1.04 / 1.18 / 1.84 / 1.84 | 6.90 / 1.96 / 19.24 / 19.24 | 8.11 / 11.02 / 11.03 / 11.03 | 6.6x, 10.4x | 7.8x, 6.0x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool_v2 (mean, p95) | sglang / fluidpool_v2 (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 300 | 0.25 / 0.01 / 1.19 / 4.75 | 2.73 / 0.01 / 19.24 / 40.20 | 2.26 / 0.01 / 12.48 / 24.56 | 10.9x, 16.2x | 9.0x, 10.5x |
| misses only | 63 | 1.16 / 0.84 / 2.35 / 10.09 | 12.96 / 8.41 / 39.62 / 83.79 | 10.71 / 9.94 / 24.36 / 64.83 | 11.2x, 16.8x | 9.3x, 10.4x |
| calm, all | 15 | 0.22 / 0.01 / 0.85 / 0.85 | 9.02 / 0.01 / 59.98 / 59.98 | 5.76 / 0.01 / 22.48 / 22.48 | 41.4x, 70.4x | 26.4x, 26.4x |
| calm, misses | 5 | 0.63 / 0.62 / 0.85 / 0.85 | 27.03 / 25.26 / 59.98 / 59.98 | 17.26 / 22.02 / 22.48 / 22.48 | 42.8x, 70.4x | 27.4x, 26.4x |
| burst part, all | 285 | 0.25 / 0.01 / 1.19 / 4.75 | 2.40 / 0.01 / 16.76 / 39.62 | 2.07 / 0.01 / 11.03 / 24.56 | 9.5x, 14.1x | 8.2x, 9.3x |
| burst part, misses | 58 | 1.20 / 0.88 / 4.75 / 10.09 | 11.74 / 8.41 / 39.62 / 83.79 | 10.14 / 9.93 / 24.56 / 64.83 | 9.8x, 8.3x | 8.5x, 5.2x |
| misses, a burst on the node | 41 | 1.20 / 0.95 / 2.35 / 10.09 | 11.00 / 8.94 / 36.47 / 40.20 | 8.70 / 9.93 / 19.96 / 46.30 | 9.2x, 15.5x | 7.2x, 8.5x |
| misses, no burst on the node | 17 | 1.20 / 0.64 / 8.79 / 8.79 | 13.54 / 3.44 / 83.79 / 83.79 | 13.63 / 9.93 / 64.83 / 64.83 | 11.3x, 9.5x | 11.4x, 7.4x |
| misses, burst fired in flight | 29 | 1.11 / 0.88 / 2.24 / 8.79 | 10.92 / 4.95 / 36.47 / 83.79 | 11.45 / 10.24 / 24.56 / 64.83 | 9.8x, 16.3x | 10.3x, 11.0x |
| misses, dram on the node | 10 | 1.73 / 0.98 / 10.09 / 10.09 | 13.10 / 13.68 / 39.62 / 39.62 | 8.93 / 10.25 / 21.44 / 21.44 | 7.6x, 3.9x | 5.2x, 2.1x |
| misses, ib on the node | 16 | 1.28 / 1.02 / 4.75 / 4.75 | 16.89 / 14.37 / 40.20 / 40.20 | 9.40 / 9.92 / 46.30 / 46.30 | 13.2x, 8.5x | 7.3x, 9.7x |
| misses, ssd_rd on the node | 4 | 0.64 / 1.04 / 1.15 / 1.15 | 5.77 / 8.41 / 13.68 / 13.68 | 10.72 / 14.05 / 21.44 / 21.44 | 9.0x, 11.9x | 16.8x, 18.7x |
| misses, ssd_wr on the node | 3 | 0.92 / 0.89 / 1.15 / 1.15 | 4.89 / 1.14 / 12.58 / 12.58 | 14.98 / 12.48 / 21.44 / 21.44 | 5.3x, 11.0x | 16.3x, 18.7x |
| misses, hbm_idle on the node | 11 | 0.78 / 0.44 / 2.24 / 2.24 | 8.72 / 8.41 / 19.56 / 19.56 | 6.39 / 2.77 / 19.96 / 19.96 | 11.2x, 8.7x | 8.2x, 8.9x |
| misses, hbm_serve on the node | 12 | 1.01 / 1.02 / 2.24 / 2.24 | 7.11 / 1.21 / 31.69 / 31.69 | 11.83 / 10.28 / 46.30 / 46.30 | 7.1x, 14.1x | 11.7x, 20.7x |
| misses, h2d on the node | 6 | 2.75 / 1.27 / 10.09 / 10.09 | 14.92 / 17.94 / 36.47 / 36.47 | 8.11 / 11.02 / 11.03 / 11.03 | 5.4x, 3.6x | 3.0x, 1.1x |

### Cold bytes per activation (file + NVMe + peer staging) / all bytes, misses only

| system | mean | p50 | p95 | max | activations with any cold byte | cold GiB total |
|---|---|---|---|---|---|---|
| fluidpool_v2 | 0.008 | 0.000 | 0.000 | 0.200 | 3/63 | 131.9 |
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
| burst | 0.913 / 0.3529, 0.832 / 0.1423 | 0.531 / 0.3824, 0.318 / 0.2037 | 0.28 / 0.1471, 0.168 / 0.0906 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool_v2: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|
| dram | 46 | 0.93, 95.96 / 186.56 | 0.93, 96.30 / 175.20 | 0.93, 95.78 / 186.53 |
| ib | 42 | traffic 28.936 GB/s | traffic 25.487 GB/s | traffic 25.804 GB/s |
| ssd_rd | 33 | traffic 6.081 GB/s | traffic 6.342 GB/s | traffic 6.319 GB/s |
| ssd_wr | 13 | traffic 2.536 GB/s | traffic 2.575 GB/s | traffic 2.56 GB/s |
| hbm_idle | 18 | 0.97, 3.09 / 18.93 | 1.00, 0.30 / 0.30 | 1.00, 0.30 / 0.31 |
| hbm_serve | 23 | 1.00, 1.29 / 0.97 | 1.00, 10.02 / 38.17 | 1.00, 0.30 / 0.30 |
| h2d | 21 | traffic 46.117 GB/s | traffic 45.385 GB/s | traffic 46.062 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool_v2: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|
| dram | 45/46, 13.637 / 58.941, 6332.7 | 20/46, 52.433 / 109.516, 1503.0 | 0/46, None / None, 0.0 |
| hbm_idle | 17/18, 2.281 / 18.475, 890.0 | 0/18, None / None, 0.0 | 0/18, None / None, 0.0 |
| hbm_serve | 0/23, None / None, 0.0 | 0/23, None / None, 0.0 | 0/23, None / None, 0.0 |

### Live graph and integrity

| | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| graph poll p50 / p99 (ms) | 3.983164206147194 / 6.717342883348465 | 3.824274055659771 / 5.552587099373341 | 3.391323145478964 / 4.557546228170395 |
| pool events | {'prestage': 62, 'demote': 88, 'evict': 5029, 'repair': 130, 'settle': 130} | {'evict_whole': 45} | {} |
| requests / hits / misses / skipped | 300 / 237 / 63 / 14 | 300 / 237 / 63 / 14 | 300 / 237 / 63 / 14 |
| verified restores, bad chunks | 13, 0 | 13, 0 | 13, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none |
| bytes by source | {'net': 0.448, 'relay': 0.261, 'lhbm': 0.137, 'ldram': 0.116, 'rstage': 0.021, 'file': 0.017, 'lssd': 0.001} | {'net': 0.569, 'file': 0.263, 'rstage': 0.148, 'ldram': 0.02} | {'rstage': 0.588, 'file': 0.412} |
