schedule: {"seed": 1, "requests": 60, "calm": 10, "burst_load": 2.0, "amp": 40, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "18205a9967b008b86feef19e17322a7350237db83845d7880b3c2328023760a2", "events": 24, "by_kind": {"ib": 5, "ssd_wr": 3, "h2d": 2, "dram": 5, "ssd_rd": 4, "hbm_idle": 2, "hbm_serve": 3}, "during": 7, "mean_concurrency": 2.22, "max_concurrency": 6, "requests_without_burst": 4}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | vs janus (mean, p95) | vs sglang (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 60 | 0.42 / 0.01 / 1.31 / 10.62 | 3.21 / 0.01 / 1.63 / 114.60 | 5.50 / 0.01 / 22.05 / 121.96 | 7.7x, 1.2x | 13.1x, 16.8x |
| misses only | 17 | 1.44 / 0.85 / 10.62 / 10.62 | 11.28 / 1.05 / 114.60 / 114.60 | 19.38 / 10.89 / 121.96 / 121.96 | 7.8x, 10.8x | 13.5x, 11.5x |
| calm, all | 10 | 0.40 / 0.01 / 1.06 / 1.06 | 0.57 / 0.01 / 1.63 / 1.63 | 8.22 / 0.01 / 22.05 / 22.05 | 1.4x, 1.5x | 20.5x, 20.7x |
| calm, misses | 4 | 0.98 / 0.97 / 1.06 / 1.06 | 1.42 / 1.62 / 1.63 / 1.63 | 20.53 / 21.78 / 22.05 / 22.05 | 1.4x, 1.5x | 20.9x, 20.7x |
| burst part, all | 50 | 0.42 / 0.01 / 1.31 / 10.62 | 3.73 / 0.01 / 1.39 / 114.60 | 4.96 / 0.01 / 10.91 / 121.96 | 8.8x, 1.1x | 11.7x, 8.3x |
| burst part, misses | 13 | 1.58 / 0.57 / 10.62 / 10.62 | 14.31 / 1.04 / 114.60 / 114.60 | 19.02 / 8.53 / 121.96 / 121.96 | 9.1x, 10.8x | 12.1x, 11.5x |
| misses, a burst on the node | 6 | 2.84 / 1.31 / 10.62 / 10.62 | 30.18 / 1.39 / 114.60 / 114.60 | 34.60 / 10.91 / 121.96 / 121.96 | 10.6x, 10.8x | 12.2x, 11.5x |
| misses, no burst on the node | 7 | 0.50 / 0.56 / 0.96 / 0.96 | 0.71 / 1.01 / 1.38 / 1.38 | 5.68 / 5.97 / 10.89 / 10.89 | 1.4x, 1.4x | 11.4x, 11.3x |
| misses, burst fired in flight | 2 | 5.34 / 10.62 / 10.62 / 10.62 | 57.33 / 114.60 / 114.60 / 114.60 | 61.09 / 121.96 / 121.96 / 121.96 | 10.7x, 10.8x | 11.4x, 11.5x |
| misses, ib on the node | 4 | 4.00 / 3.97 / 10.62 / 10.62 | 44.91 / 63.60 / 114.60 / 114.60 | 49.07 / 62.99 / 121.96 / 121.96 | 11.2x, 10.8x | 12.3x, 11.5x |
| misses, ssd_wr on the node | 1 | 10.62 / 10.62 / 10.62 / 10.62 | 114.60 / 114.60 / 114.60 / 114.60 | 121.96 / 121.96 / 121.96 / 121.96 | 10.8x, 10.8x | 11.5x, 11.5x |
| misses, hbm_serve on the node | 2 | 0.53 / 0.68 / 0.68 / 0.68 | 0.74 / 1.05 / 1.05 / 1.05 | 5.65 / 8.53 / 8.53 / 8.53 | 1.4x, 1.5x | 10.7x, 12.5x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | vs janus (mean, p95) | vs sglang (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 60 | 0.63 / 0.01 / 4.93 / 10.69 | 3.80 / 0.01 / 5.87 / 131.47 | 5.50 / 0.01 / 22.05 / 121.96 | 6.1x, 1.2x | 8.8x, 4.5x |
| misses only | 17 | 2.17 / 0.98 / 10.69 / 10.69 | 13.36 / 1.22 / 131.47 / 131.47 | 19.38 / 10.89 / 121.96 / 121.96 | 6.1x, 12.3x | 8.9x, 11.4x |
| calm, all | 10 | 0.43 / 0.01 / 1.14 / 1.14 | 1.18 / 0.01 / 5.87 / 5.87 | 8.22 / 0.01 / 22.05 / 22.05 | 2.7x, 5.1x | 19.1x, 19.3x |
| calm, misses | 4 | 1.06 / 1.09 / 1.14 / 1.14 | 2.92 / 3.35 / 5.87 / 5.87 | 20.53 / 21.78 / 22.05 / 22.05 | 2.8x, 5.1x | 19.4x, 19.3x |
| burst part, all | 50 | 0.67 / 0.01 / 4.93 / 10.69 | 4.32 / 0.01 / 2.97 / 131.47 | 4.96 / 0.01 / 10.91 / 121.96 | 6.5x, 0.6x | 7.4x, 2.2x |
| burst part, misses | 13 | 2.52 / 0.68 / 10.69 / 10.69 | 16.57 / 1.05 / 131.47 / 131.47 | 19.03 / 8.53 / 121.96 / 121.96 | 6.6x, 12.3x | 7.6x, 11.4x |
| misses, a burst on the node | 6 | 3.78 / 4.01 / 10.69 / 10.69 | 34.59 / 2.97 / 131.47 / 131.47 | 34.60 / 10.91 / 121.96 / 121.96 | 9.2x, 12.3x | 9.2x, 11.4x |
| misses, no burst on the node | 7 | 1.43 / 0.57 / 7.38 / 7.38 | 1.12 / 1.01 / 2.79 / 2.79 | 5.68 / 5.97 / 10.89 / 10.89 | 0.8x, 0.4x | 4.0x, 1.5x |
| misses, burst fired in flight | 2 | 5.38 / 10.69 / 10.69 / 10.69 | 65.77 / 131.47 / 131.47 / 131.47 | 61.09 / 121.96 / 121.96 / 121.96 | 12.2x, 12.3x | 11.4x, 11.4x |
| misses, ib on the node | 4 | 5.40 / 4.93 / 10.69 / 10.69 | 51.52 / 71.58 / 131.47 / 131.47 | 49.07 / 62.99 / 121.96 / 121.96 | 9.5x, 12.3x | 9.1x, 11.4x |
| misses, ssd_wr on the node | 1 | 10.69 / 10.69 / 10.69 / 10.69 | 131.47 / 131.47 / 131.47 / 131.47 | 121.96 / 121.96 / 121.96 / 121.96 | 12.3x, 12.3x | 11.4x, 11.4x |
| misses, hbm_serve on the node | 2 | 0.53 / 0.68 / 0.68 / 0.68 | 0.74 / 1.05 / 1.05 / 1.05 | 5.65 / 8.53 / 8.53 / 8.53 | 1.4x, 1.5x | 10.7x, 12.5x |

### How each request was served

| requests | fluidpool | janus | sglang |
|---|---|---|---|
| all requests | fluid 17, ipc_hit 43 | cold 2, d2d_fork 10, h2d_wakeup 5, ipc_hit 43 | cold 17, ipc_hit 43 |
| calm, all | fluid 4, ipc_hit 6 | d2d_fork 2, h2d_wakeup 2, ipc_hit 6 | cold 4, ipc_hit 6 |
| burst part, all | fluid 13, ipc_hit 37 | cold 2, d2d_fork 8, h2d_wakeup 3, ipc_hit 37 | cold 13, ipc_hit 37 |

| | fluidpool | janus | sglang |
|---|---|---|---|
| cold activations, burst part | 0 | 2 | 13 |
| requests that needed a fallback path | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 0.4 / 0.4, 0.733 / 0.6799 |
| burst | 0.94 / 0.6667, 0.812 / 0.2698 | 0.86 / 0.7333, 0.594 / 0.3224 | 0.42 / 0.3333, 0.396 / 0.1485 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|
| dram | 5 | 1.00, 70.66 / 123.46 | 1.00, 73.95 / 119.01 | 1.00, 71.67 / 115.94 |
| ib | 5 | traffic 33.362 GB/s | traffic 33.868 GB/s | traffic 33.84 GB/s |
| ssd_rd | 4 | traffic 4.47 GB/s | traffic 5.98 GB/s | traffic 5.982 GB/s |
| ssd_wr | 3 | traffic 2.453 GB/s | traffic 2.383 GB/s | traffic 2.317 GB/s |
| hbm_idle | 2 | 1.00, 9.76 / 18.92 | 1.00, 0.30 / 0.30 | 1.00, 0.30 / 0.30 |
| hbm_serve | 3 | 1.00, 5.84 / 16.88 | 1.00, 4.17 / 9.21 | 1.00, 0.30 / 0.30 |
| h2d | 2 | traffic 45.875 GB/s | traffic 47.915 GB/s | traffic 47.915 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|
| dram | 4/5, 29.615 / 39.385, 285.2 | 1/5, 50.54 / 50.54, 110.1 | 0/5, None / None, 0.0 |
| hbm_idle | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 |
| hbm_serve | 0/3, None / None, 0.0 | 0/3, None / None, 0.0 | 0/3, None / None, 0.0 |

### Live graph and integrity

| | fluidpool | janus | sglang |
|---|---|---|---|
| graph poll p50 / p99 (ms) | 3.359195776283741 / 35.49661999568343 | 3.138704225420952 / 43.58985181897879 | 2.971711102873087 / 6.058802828192711 |
| pool events | {'demote': 24, 'evict': 228, 'repair': 4} | {'evict_whole': 3} | {} |
| requests / hits / misses / skipped | 60 / 43 / 17 / 9 | 60 / 43 / 17 / 9 | 60 / 43 / 17 / 9 |
| verified restores, bad chunks | 6, 0 | 6, 0 | 6, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none |
| bytes by source | {'net': 0.581, 'ldram': 0.256, 'rstage': 0.11, 'file': 0.044, 'lssd': 0.009} | {'file': 0.659, 'net': 0.246, 'ldram': 0.094} | {'file': 0.793, 'rstage': 0.207} |
