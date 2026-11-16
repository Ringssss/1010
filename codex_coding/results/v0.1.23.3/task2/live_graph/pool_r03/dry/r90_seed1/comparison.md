schedule: {"seed": 1, "requests": 90, "calm": 15, "burst_load": 2.0, "amp": 40, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "53c6da3b3a50dfd41ee6183353776e674245ec1553595830e07ecc891b090743", "events": 33, "by_kind": {"ib": 8, "ssd_wr": 3, "h2d": 2, "dram": 7, "ssd_rd": 6, "hbm_idle": 2, "hbm_serve": 5}, "during": 10, "mean_concurrency": 1.97, "max_concurrency": 6, "requests_without_burst": 12}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | vs janus (mean, p95) | vs sglang (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 90 | 0.17 / 0.01 / 0.80 / 2.96 | 1.42 / 0.01 / 0.80 / 59.44 | 2.35 / 0.01 / 11.09 / 59.44 | 8.5x, 1.0x | 14.1x, 14.0x |
| misses only | 18 | 0.76 / 0.80 / 2.96 / 2.96 | 7.04 / 0.80 / 59.44 / 59.44 | 11.69 / 11.09 / 59.44 / 59.44 | 9.2x, 20.1x | 15.3x, 20.1x |
| calm, all | 15 | 0.24 / 0.01 / 0.80 / 0.80 | 0.25 / 0.01 / 0.85 / 0.85 | 3.14 / 0.01 / 11.09 / 11.09 | 1.0x, 1.1x | 12.8x, 14.0x |
| calm, misses | 5 | 0.71 / 0.80 / 0.80 / 0.80 | 0.73 / 0.80 / 0.85 / 0.85 | 9.38 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 13.2x, 14.0x |
| burst part, all | 75 | 0.15 / 0.01 / 0.80 / 2.96 | 1.66 / 0.01 / 0.80 / 59.44 | 2.19 / 0.01 / 11.09 / 59.44 | 11.0x, 1.0x | 14.5x, 14.0x |
| burst part, misses | 13 | 0.78 / 0.38 / 2.96 / 2.96 | 9.46 / 0.38 / 59.44 / 59.44 | 12.57 / 2.55 / 59.44 / 59.44 | 12.1x, 20.1x | 16.1x, 20.1x |
| misses, a burst on the node | 6 | 0.78 / 0.38 / 2.96 / 2.96 | 10.19 / 0.38 / 59.44 / 59.44 | 12.69 / 2.55 / 59.44 / 59.44 | 13.1x, 20.1x | 16.3x, 20.1x |
| misses, no burst on the node | 7 | 0.78 / 0.38 / 2.96 / 2.96 | 8.84 / 0.38 / 59.44 / 59.44 | 12.46 / 2.55 / 59.44 / 59.44 | 11.3x, 20.1x | 15.9x, 20.1x |
| misses, burst fired in flight | 5 | 0.93 / 0.80 / 2.96 / 2.96 | 12.23 / 0.80 / 59.44 / 59.44 | 16.43 / 11.09 / 59.44 / 59.44 | 13.1x, 20.1x | 17.6x, 20.1x |
| misses, ib on the node | 2 | 0.08 / 0.11 / 0.11 / 0.11 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 0.7x, 0.6x | 3.1x, 2.4x |
| misses, ssd_wr on the node | 2 | 1.88 / 2.96 / 2.96 / 2.96 | 30.12 / 59.44 / 59.44 / 59.44 | 35.27 / 59.44 / 59.44 / 59.44 | 16.0x, 20.1x | 18.8x, 20.1x |
| misses, hbm_serve on the node | 3 | 0.27 / 0.38 / 0.38 / 0.38 | 0.27 / 0.38 / 0.38 / 0.38 | 1.79 / 2.55 / 2.55 / 2.55 | 1.0x, 1.0x | 6.5x, 6.7x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | vs janus (mean, p95) | vs sglang (mean, p95) |
|---|---|---|---|---|---|---|
| all requests | 90 | 0.17 / 0.01 / 0.80 / 2.96 | 1.42 / 0.01 / 0.80 / 59.44 | 2.35 / 0.01 / 11.09 / 59.44 | 8.5x, 1.0x | 14.1x, 14.0x |
| misses only | 18 | 0.76 / 0.80 / 2.96 / 2.96 | 7.04 / 0.80 / 59.44 / 59.44 | 11.69 / 11.09 / 59.44 / 59.44 | 9.2x, 20.1x | 15.3x, 20.1x |
| calm, all | 15 | 0.24 / 0.01 / 0.80 / 0.80 | 0.25 / 0.01 / 0.85 / 0.85 | 3.14 / 0.01 / 11.09 / 11.09 | 1.0x, 1.1x | 12.8x, 14.0x |
| calm, misses | 5 | 0.71 / 0.80 / 0.80 / 0.80 | 0.73 / 0.80 / 0.85 / 0.85 | 9.38 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 13.2x, 14.0x |
| burst part, all | 75 | 0.15 / 0.01 / 0.80 / 2.96 | 1.66 / 0.01 / 0.80 / 59.44 | 2.19 / 0.01 / 11.09 / 59.44 | 11.0x, 1.0x | 14.5x, 14.0x |
| burst part, misses | 13 | 0.78 / 0.38 / 2.96 / 2.96 | 9.46 / 0.38 / 59.44 / 59.44 | 12.57 / 2.55 / 59.44 / 59.44 | 12.1x, 20.1x | 16.1x, 20.1x |
| misses, a burst on the node | 6 | 0.78 / 0.38 / 2.96 / 2.96 | 10.19 / 0.38 / 59.44 / 59.44 | 12.69 / 2.55 / 59.44 / 59.44 | 13.1x, 20.1x | 16.3x, 20.1x |
| misses, no burst on the node | 7 | 0.78 / 0.38 / 2.96 / 2.96 | 8.84 / 0.38 / 59.44 / 59.44 | 12.46 / 2.55 / 59.44 / 59.44 | 11.3x, 20.1x | 15.9x, 20.1x |
| misses, burst fired in flight | 5 | 0.93 / 0.80 / 2.96 / 2.96 | 12.23 / 0.80 / 59.44 / 59.44 | 16.43 / 11.09 / 59.44 / 59.44 | 13.1x, 20.1x | 17.6x, 20.1x |
| misses, ib on the node | 2 | 0.08 / 0.11 / 0.11 / 0.11 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 0.7x, 0.6x | 3.1x, 2.4x |
| misses, ssd_wr on the node | 2 | 1.88 / 2.96 / 2.96 / 2.96 | 30.12 / 59.44 / 59.44 / 59.44 | 35.27 / 59.44 / 59.44 / 59.44 | 16.0x, 20.1x | 18.8x, 20.1x |
| misses, hbm_serve on the node | 3 | 0.27 / 0.38 / 0.38 / 0.38 | 0.27 / 0.38 / 0.38 / 0.38 | 1.79 / 2.55 / 2.55 / 2.55 | 1.0x, 1.0x | 6.5x, 6.7x |

### How each request was served

| requests | fluidpool | janus | sglang |
|---|---|---|---|
| all requests | fluid 18, ipc_hit 72 | cold 2, d2d_fork 11, h2d_wakeup 5, ipc_hit 72 | cold 18, ipc_hit 72 |
| calm, all | fluid 5, ipc_hit 10 | d2d_fork 2, h2d_wakeup 3, ipc_hit 10 | cold 5, ipc_hit 10 |
| burst part, all | fluid 13, ipc_hit 62 | cold 2, d2d_fork 9, h2d_wakeup 2, ipc_hit 62 | cold 13, ipc_hit 62 |

| | fluidpool | janus | sglang |
|---|---|---|---|
| cold activations, burst part | 0 | 2 | 13 |
| requests that needed a fallback path | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 0.911 / 0.7333, 0.801 / 0.4026 | 0.4 / 0.4, 0.596 / 0.323 |
| burst | 0.987 / 0.8667, 1.0 / 0.9977 | 0.847 / 0.7333, 0.529 / 0.3224 | 0.427 / 0.3333, 0.333 / 0.1485 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|
| dram | 7 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.01 | 1.00, 0.00 / 0.00 |
| ib | 8 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_rd | 6 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_wr | 3 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| hbm_idle | 2 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| hbm_serve | 5 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| h2d | 2 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|
| dram | 1/7, 0.0 / 0.0, 100.8 | 2/7, 0.001 / 0.001, 168.5 | 0/7, None / None, 0.0 |
| hbm_idle | 2/2, 0.0 / 0.0, 73.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 |
| hbm_serve | 0/5, None / None, 0.0 | 0/5, None / None, 0.0 | 0/5, None / None, 0.0 |

### Live graph and integrity

| | fluidpool | janus | sglang |
|---|---|---|---|
| graph poll p50 / p99 (ms) | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 |
| pool events | {'evict': 10, 'demote': 3} | {'evict_whole': 2} | {} |
| requests / hits / misses / skipped | 90 / 72 / 18 / 4 | 90 / 72 / 18 / 4 | 90 / 72 / 18 / 4 |
| verified restores, bad chunks | 6, 0 | 6, 0 | 6, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none |
| bytes by source | {'net': 0.56, 'ldram': 0.439, 'file': 0.001} | {'file': 0.719, 'net': 0.204, 'ldram': 0.078} | {'file': 1.0} |
