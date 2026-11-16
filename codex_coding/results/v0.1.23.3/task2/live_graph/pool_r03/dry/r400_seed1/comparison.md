schedule: {"seed": 1, "requests": 400, "calm": 15, "burst_load": 2.0, "amp": 40, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "37c7ff81bbad259fb8a8f9317810f0e425bad26031b8d584100cb0ec6f2738b7", "events": 159, "by_kind": {"ib": 30, "ssd_wr": 15, "h2d": 11, "dram": 41, "ssd_rd": 27, "hbm_idle": 19, "hbm_serve": 16}, "during": 49, "mean_concurrency": 2.12, "max_concurrency": 7, "requests_without_burst": 55}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | vs janus (mean, p95) | vs sglang (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 400 | 0.16 / 0.01 / 0.80 / 2.13 | 1.14 / 0.01 / 0.80 / 59.44 | 2.27 / 0.01 / 11.09 / 59.44 | 7.1x, 1.0x | 14.1x, 14.0x |
| misses only | 88 | 0.69 / 0.77 / 2.13 / 3.42 | 5.16 / 0.77 / 59.44 / 59.44 | 10.29 / 10.70 / 59.44 / 59.44 | 7.5x, 27.9x | 14.9x, 27.9x |
| calm, all | 15 | 0.24 / 0.01 / 0.80 / 0.80 | 0.25 / 0.01 / 0.85 / 0.85 | 3.14 / 0.01 / 11.09 / 11.09 | 1.0x, 1.1x | 12.8x, 14.0x |
| calm, misses | 5 | 0.71 / 0.80 / 0.80 / 0.80 | 0.73 / 0.80 / 0.85 / 0.85 | 9.38 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 13.2x, 14.0x |
| burst part, all | 385 | 0.16 / 0.01 / 0.80 / 2.13 | 1.18 / 0.01 / 0.80 / 59.44 | 2.24 / 0.01 / 11.09 / 59.44 | 7.5x, 1.0x | 14.2x, 14.0x |
| burst part, misses | 83 | 0.69 / 0.77 / 2.13 / 3.42 | 5.42 / 0.77 / 59.44 / 59.44 | 10.35 / 10.70 / 59.44 / 59.44 | 7.9x, 27.9x | 15.0x, 27.9x |
| misses, a burst on the node | 47 | 0.75 / 0.77 / 2.13 / 3.42 | 7.45 / 0.77 / 59.44 / 59.44 | 12.28 / 10.70 / 59.44 / 59.44 | 9.9x, 27.9x | 16.4x, 27.9x |
| misses, no burst on the node | 36 | 0.61 / 0.77 / 0.80 / 2.96 | 2.79 / 0.77 / 10.70 / 59.44 | 7.83 / 10.70 / 11.09 / 59.44 | 4.6x, 13.5x | 12.9x, 14.0x |
| misses, burst fired in flight | 30 | 0.76 / 0.77 / 2.96 / 3.42 | 5.58 / 0.80 / 59.44 / 59.44 | 11.01 / 10.70 / 59.44 / 59.44 | 7.4x, 20.1x | 14.5x, 20.1x |
| misses, dram on the node | 15 | 0.55 / 0.38 / 2.13 / 2.13 | 5.03 / 0.38 / 59.44 / 59.44 | 8.89 / 2.55 / 59.44 / 59.44 | 9.2x, 27.9x | 16.2x, 27.9x |
| misses, ib on the node | 23 | 0.78 / 0.80 / 2.13 / 2.13 | 8.69 / 0.80 / 59.44 / 59.44 | 14.33 / 11.09 / 59.44 / 59.44 | 11.1x, 27.9x | 18.3x, 27.9x |
| misses, ssd_rd on the node | 4 | 1.26 / 0.80 / 3.42 / 3.42 | 15.28 / 0.85 / 59.44 / 59.44 | 20.37 / 11.09 / 59.44 / 59.44 | 12.1x, 17.4x | 16.1x, 17.4x |
| misses, ssd_wr on the node | 2 | 1.88 / 2.96 / 2.96 / 2.96 | 30.12 / 59.44 / 59.44 / 59.44 | 35.27 / 59.44 / 59.44 / 59.44 | 16.0x, 20.1x | 18.8x, 20.1x |
| misses, hbm_idle on the node | 7 | 0.46 / 0.38 / 0.80 / 0.80 | 1.88 / 0.38 / 10.70 / 10.70 | 5.50 / 2.55 / 11.09 / 11.09 | 4.1x, 13.5x | 11.9x, 14.0x |
| misses, hbm_serve on the node | 7 | 0.51 / 0.38 / 0.80 / 0.80 | 1.98 / 0.38 / 11.09 / 11.09 | 5.88 / 2.55 / 11.09 / 11.09 | 3.9x, 14.0x | 11.5x, 14.0x |
| misses, h2d on the node | 1 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 11.09 / 11.09 / 11.09 / 11.09 | 1.0x, 1.0x | 14.0x, 14.0x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | vs janus (mean, p95) | vs sglang (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 400 | 0.16 / 0.01 / 0.80 / 2.13 | 1.14 / 0.01 / 0.80 / 59.44 | 2.27 / 0.01 / 11.09 / 59.44 | 7.1x, 1.0x | 14.1x, 14.0x |
| misses only | 88 | 0.69 / 0.77 / 2.13 / 3.42 | 5.16 / 0.77 / 59.44 / 59.44 | 10.29 / 10.70 / 59.44 / 59.44 | 7.5x, 27.9x | 14.9x, 27.9x |
| calm, all | 15 | 0.24 / 0.01 / 0.80 / 0.80 | 0.25 / 0.01 / 0.85 / 0.85 | 3.14 / 0.01 / 11.09 / 11.09 | 1.0x, 1.1x | 12.8x, 14.0x |
| calm, misses | 5 | 0.71 / 0.80 / 0.80 / 0.80 | 0.73 / 0.80 / 0.85 / 0.85 | 9.38 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 13.2x, 14.0x |
| burst part, all | 385 | 0.16 / 0.01 / 0.80 / 2.13 | 1.18 / 0.01 / 0.80 / 59.44 | 2.24 / 0.01 / 11.09 / 59.44 | 7.5x, 1.0x | 14.2x, 14.0x |
| burst part, misses | 83 | 0.69 / 0.77 / 2.13 / 3.42 | 5.42 / 0.77 / 59.44 / 59.44 | 10.35 / 10.70 / 59.44 / 59.44 | 7.9x, 27.9x | 15.0x, 27.9x |
| misses, a burst on the node | 47 | 0.75 / 0.77 / 2.13 / 3.42 | 7.45 / 0.77 / 59.44 / 59.44 | 12.28 / 10.70 / 59.44 / 59.44 | 9.9x, 27.9x | 16.4x, 27.9x |
| misses, no burst on the node | 36 | 0.61 / 0.77 / 0.80 / 2.96 | 2.79 / 0.77 / 10.70 / 59.44 | 7.83 / 10.70 / 11.09 / 59.44 | 4.6x, 13.5x | 12.9x, 14.0x |
| misses, burst fired in flight | 30 | 0.76 / 0.77 / 2.96 / 3.42 | 5.58 / 0.80 / 59.44 / 59.44 | 11.01 / 10.70 / 59.44 / 59.44 | 7.4x, 20.1x | 14.5x, 20.1x |
| misses, dram on the node | 15 | 0.55 / 0.38 / 2.13 / 2.13 | 5.03 / 0.38 / 59.44 / 59.44 | 8.89 / 2.55 / 59.44 / 59.44 | 9.2x, 27.9x | 16.2x, 27.9x |
| misses, ib on the node | 23 | 0.78 / 0.80 / 2.13 / 2.13 | 8.69 / 0.80 / 59.44 / 59.44 | 14.33 / 11.09 / 59.44 / 59.44 | 11.1x, 27.9x | 18.3x, 27.9x |
| misses, ssd_rd on the node | 4 | 1.26 / 0.80 / 3.42 / 3.42 | 15.28 / 0.85 / 59.44 / 59.44 | 20.37 / 11.09 / 59.44 / 59.44 | 12.1x, 17.4x | 16.1x, 17.4x |
| misses, ssd_wr on the node | 2 | 1.88 / 2.96 / 2.96 / 2.96 | 30.12 / 59.44 / 59.44 / 59.44 | 35.27 / 59.44 / 59.44 / 59.44 | 16.0x, 20.1x | 18.8x, 20.1x |
| misses, hbm_idle on the node | 7 | 0.46 / 0.38 / 0.80 / 0.80 | 1.88 / 0.38 / 10.70 / 10.70 | 5.50 / 2.55 / 11.09 / 11.09 | 4.1x, 13.5x | 11.9x, 14.0x |
| misses, hbm_serve on the node | 7 | 0.51 / 0.38 / 0.80 / 0.80 | 1.98 / 0.38 / 11.09 / 11.09 | 5.88 / 2.55 / 11.09 / 11.09 | 3.9x, 14.0x | 11.5x, 14.0x |
| misses, h2d on the node | 1 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 11.09 / 11.09 / 11.09 / 11.09 | 1.0x, 1.0x | 14.0x, 14.0x |

### How each request was served

| requests | fluidpool | janus | sglang |
|---|---|---|---|
| all requests | fluid 88, ipc_hit 312 | cold 12, d2d_fork 62, h2d_wakeup 14, ipc_hit 312 | cold 88, ipc_hit 312 |
| calm, all | fluid 5, ipc_hit 10 | d2d_fork 2, h2d_wakeup 3, ipc_hit 10 | cold 5, ipc_hit 10 |
| burst part, all | fluid 83, ipc_hit 302 | cold 12, d2d_fork 60, h2d_wakeup 11, ipc_hit 302 | cold 83, ipc_hit 302 |

| | fluidpool | janus | sglang |
|---|---|---|---|
| cold activations, burst part | 0 | 12 | 83 |
| requests that needed a fallback path | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 0.911 / 0.7333, 0.801 / 0.4026 | 0.4 / 0.4, 0.596 / 0.323 |
| burst | 0.973 / 0.6667, 0.931 / 0.2699 | 0.834 / 0.7333, 0.461 / 0.3185 | 0.544 / 0.3333, 0.32 / 0.1485 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|
| dram | 41 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.01 | 1.00, 0.00 / 0.00 |
| ib | 30 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_rd | 27 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_wr | 15 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| hbm_idle | 19 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| hbm_serve | 16 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| h2d | 11 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|
| dram | 8/41, 0.001 / 0.008, 256.0 | 13/41, 0.001 / 0.001, 884.4 | 0/41, None / None, 0.0 |
| hbm_idle | 18/19, 0.0 / 0.001, 879.2 | 0/19, None / None, 0.0 | 0/19, None / None, 0.0 |
| hbm_serve | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 |

### Live graph and integrity

| | fluidpool | janus | sglang |
|---|---|---|---|
| graph poll p50 / p99 (ms) | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 |
| pool events | {'evict': 45, 'demote': 11, 'repair': 19} | {'evict_whole': 13} | {} |
| requests / hits / misses / skipped | 400 / 312 / 88 / 5 | 400 / 312 / 88 / 5 | 400 / 312 / 88 / 5 |
| verified restores, bad chunks | 14, 0 | 14, 0 | 14, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none |
| bytes by source | {'net': 0.702, 'ldram': 0.29, 'file': 0.009} | {'file': 0.606, 'net': 0.336, 'ldram': 0.058} | {'file': 1.0} |
