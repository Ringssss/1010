schedule: {"seed": 1, "requests": 300, "calm": 15, "burst_load": 2.0, "amp": 40, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "d4e4bfad764b0170a992b6948c03d8c63c1eec78432976306c6ac87622051285", "events": 123, "by_kind": {"ib": 23, "ssd_wr": 12, "h2d": 9, "dram": 29, "ssd_rd": 20, "hbm_idle": 16, "hbm_serve": 14}, "during": 41, "mean_concurrency": 2.21, "max_concurrency": 7, "requests_without_burst": 42}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool_v2 (mean, p95) | sglang / fluidpool_v2 (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 300 | 0.19 / 0.01 / 0.92 / 3.88 | 1.40 / 0.01 / 2.35 / 65.31 | 2.87 / 0.01 / 16.92 / 64.66 | 7.4x, 2.6x | 15.3x, 18.5x |
| misses only | 64 | 0.84 / 0.68 / 1.51 / 8.38 | 6.50 / 1.08 / 22.64 / 91.91 | 13.41 / 9.94 / 32.15 / 88.64 | 7.8x, 15.0x | 16.0x, 21.3x |
| calm, all | 15 | 0.19 / 0.01 / 0.73 / 0.73 | 3.41 / 0.01 / 22.64 / 22.64 | 5.76 / 0.01 / 22.45 / 22.45 | 17.6x, 31.1x | 29.7x, 30.8x |
| calm, misses | 5 | 0.56 / 0.60 / 0.73 / 0.73 | 10.21 / 2.85 / 22.64 / 22.64 | 17.25 / 22.05 / 22.45 / 22.45 | 18.3x, 31.1x | 31.0x, 30.8x |
| burst part, all | 285 | 0.19 / 0.01 / 0.92 / 3.88 | 1.29 / 0.01 / 1.48 / 65.31 | 2.72 / 0.01 / 15.28 / 64.66 | 6.9x, 1.6x | 14.5x, 16.7x |
| burst part, misses | 59 | 0.86 / 0.68 / 3.88 / 8.38 | 6.19 / 1.04 / 65.31 / 91.91 | 13.08 / 9.93 / 64.66 / 88.64 | 7.2x, 16.8x | 15.2x, 16.7x |
| misses, a burst on the node | 27 | 1.11 / 0.84 / 3.88 / 8.38 | 8.53 / 1.04 / 85.59 / 91.91 | 16.22 / 9.94 / 87.23 / 88.64 | 7.7x, 22.0x | 14.6x, 22.5x |
| misses, no burst on the node | 32 | 0.65 / 0.54 / 1.48 / 3.92 | 4.21 / 1.06 / 11.06 / 65.31 | 10.44 / 9.93 / 22.25 / 64.66 | 6.5x, 7.5x | 16.1x, 15.0x |
| misses, burst fired in flight | 21 | 1.25 / 0.85 / 3.88 / 8.38 | 11.36 / 1.09 / 85.59 / 91.91 | 19.45 / 13.27 / 87.23 / 88.64 | 9.1x, 22.0x | 15.6x, 22.5x |
| misses, dram on the node | 8 | 1.70 / 0.85 / 8.38 / 8.38 | 13.84 / 2.01 / 85.59 / 85.59 | 18.95 / 9.94 / 87.23 / 87.23 | 8.2x, 10.2x | 11.2x, 10.4x |
| misses, ib on the node | 7 | 1.78 / 0.86 / 8.38 / 8.38 | 12.96 / 1.01 / 85.59 / 85.59 | 24.63 / 19.12 / 87.23 / 87.23 | 7.3x, 10.2x | 13.8x, 10.4x |
| misses, ssd_rd on the node | 2 | 0.80 / 0.92 / 0.92 / 0.92 | 7.61 / 14.06 / 14.06 / 14.06 | 14.60 / 15.93 / 15.93 / 15.93 | 9.5x, 15.4x | 18.2x, 17.4x |
| misses, ssd_wr on the node | 3 | 1.76 / 0.78 / 3.88 / 3.88 | 31.13 / 1.08 / 91.91 / 91.91 | 36.44 / 18.33 / 88.64 / 88.64 | 17.7x, 23.7x | 20.7x, 22.8x |
| misses, hbm_idle on the node | 4 | 0.56 / 0.84 / 1.12 / 1.12 | 0.66 / 0.72 / 1.17 / 1.17 | 5.63 / 5.54 / 11.02 / 11.02 | 1.2x, 1.0x | 10.0x, 9.9x |
| misses, hbm_serve on the node | 5 | 0.80 / 0.84 / 1.51 / 1.51 | 3.52 / 1.17 / 14.06 / 14.06 | 9.91 / 11.02 / 16.49 / 16.49 | 4.4x, 9.3x | 12.5x, 10.9x |
| misses, h2d on the node | 3 | 0.67 / 0.92 / 1.05 / 1.05 | 5.01 / 0.89 / 14.06 / 14.06 | 9.43 / 13.27 / 14.81 / 14.81 | 7.5x, 13.4x | 14.2x, 14.2x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool_v2 (mean, p95) | sglang / fluidpool_v2 (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 300 | 0.23 / 0.01 / 1.09 / 3.97 | 3.08 / 0.01 / 17.05 / 81.76 | 2.87 / 0.01 / 16.92 / 64.67 | 13.2x, 15.6x | 12.3x, 15.5x |
| misses only | 64 | 1.05 / 0.83 / 3.94 / 8.55 | 14.40 / 2.94 / 56.60 / 108.14 | 13.41 / 9.94 / 32.15 / 88.64 | 13.8x, 14.4x | 12.8x, 8.2x |
| calm, all | 15 | 0.22 / 0.01 / 0.83 / 0.83 | 8.82 / 0.01 / 55.11 / 55.11 | 5.76 / 0.01 / 22.45 / 22.45 | 39.9x, 66.2x | 26.0x, 27.0x |
| calm, misses | 5 | 0.64 / 0.64 / 0.83 / 0.83 | 26.45 / 24.93 / 55.11 / 55.11 | 17.25 / 22.05 / 22.45 / 22.45 | 41.3x, 66.2x | 27.0x, 27.0x |
| burst part, all | 285 | 0.23 / 0.01 / 1.09 / 3.97 | 2.78 / 0.01 / 12.96 / 81.76 | 2.72 / 0.01 / 15.28 / 64.67 | 11.9x, 11.8x | 11.6x, 14.0x |
| burst part, misses | 59 | 1.08 / 0.84 / 3.97 / 8.55 | 13.38 / 2.37 / 81.76 / 108.14 | 13.08 / 9.93 / 64.67 / 88.64 | 12.4x, 20.6x | 12.1x, 16.3x |
| misses, a burst on the node | 27 | 1.34 / 0.90 / 3.97 / 8.55 | 15.10 / 2.39 / 100.55 / 108.14 | 16.22 / 9.94 / 87.23 / 88.64 | 11.3x, 25.3x | 12.1x, 22.0x |
| misses, no burst on the node | 32 | 0.87 / 0.68 / 3.94 / 4.01 | 11.92 / 2.37 / 56.60 / 81.76 | 10.44 / 9.93 / 22.25 / 64.67 | 13.7x, 14.4x | 12.0x, 5.7x |
| misses, burst fired in flight | 21 | 1.64 / 0.92 / 3.97 / 8.55 | 21.01 / 2.39 / 100.55 / 108.14 | 19.45 / 13.27 / 87.23 / 88.64 | 12.8x, 25.3x | 11.8x, 22.0x |
| misses, dram on the node | 8 | 1.81 / 0.99 / 8.55 / 8.55 | 29.71 / 35.13 / 100.55 / 100.55 | 18.95 / 9.94 / 87.23 / 87.23 | 16.4x, 11.8x | 10.5x, 10.2x |
| misses, ib on the node | 7 | 1.92 / 0.90 / 8.55 / 8.55 | 17.12 / 2.94 / 100.55 / 100.55 | 24.63 / 19.12 / 87.23 / 87.23 | 8.9x, 11.8x | 12.8x, 10.2x |
| misses, ssd_rd on the node | 2 | 0.80 / 0.92 / 0.92 / 0.92 | 7.61 / 14.06 / 14.06 / 14.06 | 14.60 / 15.93 / 15.93 / 15.93 | 9.5x, 15.4x | 18.2x, 17.4x |
| misses, ssd_wr on the node | 3 | 2.82 / 3.89 / 3.97 / 3.97 | 36.98 / 2.39 / 108.14 / 108.14 | 36.44 / 18.34 / 88.64 / 88.64 | 13.1x, 27.2x | 12.9x, 22.3x |
| misses, hbm_idle on the node | 4 | 0.85 / 1.16 / 1.29 / 1.29 | 5.36 / 8.62 / 11.57 / 11.57 | 5.63 / 5.54 / 11.02 / 11.02 | 6.3x, 9.0x | 6.6x, 8.6x |
| misses, hbm_serve on the node | 5 | 0.80 / 0.87 / 1.51 / 1.51 | 3.53 / 1.19 / 14.06 / 14.06 | 9.91 / 11.02 / 16.49 / 16.49 | 4.4x, 9.3x | 12.4x, 10.9x |
| misses, h2d on the node | 3 | 0.67 / 0.92 / 1.05 / 1.05 | 5.01 / 0.89 / 14.06 / 14.06 | 9.43 / 13.27 / 14.81 / 14.81 | 7.5x, 13.4x | 14.2x, 14.2x |

### Cold bytes per activation (file + NVMe + peer staging) / all bytes, misses only

| system | mean | p50 | p95 | max | activations with any cold byte | cold GiB total |
|---|---|---|---|---|---|---|
| fluidpool_v2 | 0.002 | 0.000 | 0.000 | 0.140 | 1/64 | 98.6 |
| janus | 0.266 | 0.000 | 1.000 | 1.000 | 17/64 | 2755.3 |
| sglang | 1.000 | 1.000 | 1.000 | 1.000 | 64/64 | 4699.9 |

### How each request was served

| requests | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| all requests | fluid 64, ipc_hit 236 | cold 17, d2d_fork 43, h2d_wakeup 4, ipc_hit 236 | cold 64, ipc_hit 236 |
| calm, all | fluid 5, ipc_hit 10 | cold 3, d2d_fork 2, ipc_hit 10 | cold 5, ipc_hit 10 |
| burst part, all | fluid 59, ipc_hit 226 | cold 14, d2d_fork 41, h2d_wakeup 4, ipc_hit 226 | cold 59, ipc_hit 226 |

| | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| cold activations, burst part | 0 | 14 | 59 |
| requests that needed a fallback path | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool_v2 pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 0.667 / 0.5, 0.714 / 0.4248 | 0.176 / 0.1765, 0.35 / 0.1842 |
| burst | 0.972 / 0.3824, 0.942 / 0.173 | 0.5 / 0.3824, 0.351 / 0.2037 | 0.266 / 0.1471, 0.187 / 0.0669 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool_v2: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|
| dram | 29 | 1.00, 62.93 / 124.97 | 1.00, 63.24 / 122.08 | 1.00, 61.65 / 122.65 |
| ib | 23 | traffic 39.744 GB/s | traffic 35.278 GB/s | traffic 34.843 GB/s |
| ssd_rd | 20 | traffic 6.509 GB/s | traffic 5.941 GB/s | traffic 5.583 GB/s |
| ssd_wr | 12 | traffic 2.484 GB/s | traffic 2.65 GB/s | traffic 2.444 GB/s |
| hbm_idle | 16 | 1.00, 1.71 / 18.92 | 1.00, 0.30 / 0.30 | 1.00, 0.30 / 0.30 |
| hbm_serve | 14 | 1.00, 3.00 / 18.94 | 1.00, 3.36 / 9.15 | 1.00, 0.30 / 0.30 |
| h2d | 9 | traffic 45.968 GB/s | traffic 45.846 GB/s | traffic 45.914 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool_v2: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|
| dram | 28/29, 6.754 / 24.845, 3162.0 | 14/29, 50.513 / 116.367, 1083.0 | 0/29, None / None, 0.0 |
| hbm_idle | 14/16, 1.48 / 18.512, 849.4 | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 |
| hbm_serve | 0/14, None / None, 0.0 | 0/14, None / None, 0.0 | 0/14, None / None, 0.0 |

### Live graph and integrity

| | fluidpool_v2 | janus | sglang |
|---|---|---|---|
| graph poll p50 / p99 (ms) | 3.3752592280507088 / 6.575913168489933 | 3.6505768075585365 / 5.794310942292213 | 3.7253783084452152 / 4.913880955427885 |
| pool events | {'prestage': 48, 'settle': 124, 'demote': 90, 'evict': 2521, 'repair': 87} | {'evict_whole': 32} | {} |
| requests / hits / misses / skipped | 300 / 236 / 64 / 4 | 300 / 236 / 64 / 4 | 300 / 236 / 64 / 4 |
| verified restores, bad chunks | 16, 0 | 16, 0 | 16, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none |
| bytes by source | {'net': 0.531, 'relay': 0.204, 'ldram': 0.17, 'lhbm': 0.074, 'rstage': 0.011, 'file': 0.01} | {'file': 0.478, 'net': 0.385, 'rstage': 0.109, 'ldram': 0.029} | {'file': 0.587, 'rstage': 0.413} |
