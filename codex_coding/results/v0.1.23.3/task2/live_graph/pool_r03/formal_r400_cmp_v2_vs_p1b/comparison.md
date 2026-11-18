schedule: {"seed": 1, "requests": 400, "calm": 15, "burst_load": 2.0, "amp": 40, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "37c7ff81bbad259fb8a8f9317810f0e425bad26031b8d584100cb0ec6f2738b7", "events": 159, "by_kind": {"ib": 30, "ssd_wr": 15, "h2d": 11, "dram": 41, "ssd_rd": 27, "hbm_idle": 19, "hbm_serve": 16}, "during": 49, "mean_concurrency": 2.12, "max_concurrency": 7, "requests_without_burst": 55}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | fluidpool_relay mean / p50 / p95 / p99 (s) | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool (mean, p95) | sglang / fluidpool (mean, p95) | fluidpool_relay / fluidpool (mean, p95) | fluidpool_v2 / fluidpool (mean, p95) |
|---|---|---|---|---|---|---|---|---|---|---|
| all requests | 400 | 0.33 / 0.01 / 1.38 / 6.35 | 0.27 / 0.01 / 1.19 / 5.33 | 0.26 / 0.01 / 0.97 / 4.56 | 1.50 / 0.01 / 1.64 / 65.08 | 3.32 / 0.01 / 19.91 / 65.06 | 4.5x, 1.2x | 9.9x, 14.5x | 0.8x, 0.9x | 0.8x, 0.7x |
| misses only | 88 | 1.48 / 0.98 / 5.19 / 18.58 | 1.20 / 0.74 / 4.14 / 18.73 | 1.16 / 0.66 / 3.52 / 28.32 | 6.76 / 1.06 / 64.86 / 88.68 | 15.04 / 9.99 / 64.79 / 88.58 | 4.6x, 12.5x | 10.2x, 12.5x | 0.8x, 0.8x | 0.8x, 0.7x |
| calm, all | 15 | 0.30 / 0.01 / 1.06 / 1.06 | 0.25 / 0.01 / 0.87 / 0.87 | 0.20 / 0.01 / 0.74 / 0.74 | 0.41 / 0.01 / 1.64 / 1.64 | 6.21 / 0.01 / 25.91 / 25.91 | 1.4x, 1.5x | 20.9x, 24.5x | 0.8x, 0.8x | 0.7x, 0.7x |
| calm, misses | 5 | 0.87 / 0.96 / 1.06 / 1.06 | 0.73 / 0.80 / 0.87 / 0.87 | 0.57 / 0.66 / 0.74 / 0.74 | 1.20 / 1.24 / 1.64 / 1.64 | 18.61 / 22.13 / 25.91 / 25.91 | 1.4x, 1.5x | 21.4x, 24.5x | 0.8x, 0.8x | 0.7x, 0.7x |
| burst part, all | 385 | 0.34 / 0.01 / 1.38 / 6.35 | 0.27 / 0.01 / 1.19 / 5.33 | 0.27 / 0.01 / 0.97 / 4.56 | 1.54 / 0.01 / 1.62 / 65.08 | 3.20 / 0.01 / 16.87 / 65.06 | 4.6x, 1.2x | 9.5x, 12.3x | 0.8x, 0.9x | 0.8x, 0.7x |
| burst part, misses | 83 | 1.51 / 0.98 / 5.19 / 18.58 | 1.23 / 0.72 / 4.14 / 18.73 | 1.20 / 0.65 / 3.52 / 28.32 | 7.09 / 1.05 / 64.86 / 88.68 | 14.82 / 9.94 / 64.79 / 88.58 | 4.7x, 12.5x | 9.8x, 12.5x | 0.8x, 0.8x | 0.8x, 0.7x |
| misses, a burst on the node | 47 | 2.04 / 1.07 / 7.91 / 18.58 | 1.65 / 0.86 / 5.74 / 18.73 | 1.62 / 0.74 / 6.01 / 28.32 | 9.66 / 1.16 / 65.12 / 88.68 | 17.33 / 9.99 / 77.26 / 88.58 | 4.7x, 8.2x | 8.5x, 9.8x | 0.8x, 0.7x | 0.8x, 0.8x |
| misses, no burst on the node | 36 | 0.83 / 0.88 / 1.51 / 3.51 | 0.68 / 0.62 / 1.56 / 3.39 | 0.64 / 0.54 / 1.62 / 3.52 | 3.74 / 0.96 / 24.26 / 64.86 | 11.55 / 9.92 / 36.27 / 64.79 | 4.5x, 16.1x | 13.9x, 24.1x | 0.8x, 1.0x | 0.8x, 1.1x |
| misses, burst fired in flight | 30 | 2.18 / 1.05 / 17.18 / 18.58 | 1.57 / 0.87 / 4.14 / 18.73 | 1.66 / 0.67 / 3.52 / 28.32 | 9.01 / 1.17 / 75.63 / 88.68 | 17.66 / 11.36 / 77.26 / 88.58 | 4.1x, 4.4x | 8.1x, 4.5x | 0.7x, 0.2x | 0.8x, 0.2x |
| misses, dram on the node | 15 | 1.12 / 0.40 / 7.91 / 7.91 | 1.10 / 0.28 / 8.92 / 8.92 | 0.92 / 0.28 / 7.90 / 7.90 | 5.60 / 0.45 / 65.08 / 65.08 | 10.03 / 4.93 / 64.74 / 64.74 | 5.0x, 8.2x | 9.0x, 8.2x | 1.0x, 1.1x | 0.8x, 1.0x |
| misses, ib on the node | 23 | 1.80 / 1.30 / 6.35 / 7.91 | 1.73 / 1.14 / 5.74 / 8.92 | 1.38 / 0.78 / 6.01 / 7.90 | 10.32 / 1.32 / 65.08 / 65.12 | 20.51 / 11.04 / 65.06 / 79.04 | 5.7x, 10.2x | 11.4x, 10.2x | 1.0x, 0.9x | 0.8x, 0.9x |
| misses, ssd_rd on the node | 4 | 5.35 / 1.80 / 18.58 / 18.58 | 5.40 / 1.95 / 18.73 / 18.73 | 7.54 / 0.96 / 28.32 / 28.32 | 19.59 / 1.72 / 75.63 / 75.63 | 30.05 / 22.50 / 77.26 / 77.26 | 3.7x, 4.1x | 5.6x, 4.2x | 1.0x, 1.0x | 1.4x, 1.5x |
| misses, ssd_wr on the node | 2 | 2.21 / 3.43 / 3.43 / 3.43 | 2.51 / 4.14 / 4.14 / 4.14 | 2.37 / 3.52 / 3.52 / 3.52 | 44.83 / 88.68 / 88.68 / 88.68 | 55.52 / 88.58 / 88.58 / 88.58 | 20.3x, 25.8x | 25.1x, 25.8x | 1.1x, 1.2x | 1.1x, 1.0x |
| misses, hbm_idle on the node | 7 | 0.65 / 0.40 / 1.57 / 1.57 | 0.45 / 0.29 / 0.95 / 0.95 | 0.45 / 0.49 / 0.94 / 0.94 | 3.42 / 0.45 / 20.52 / 20.52 | 6.85 / 2.76 / 19.91 / 19.91 | 5.3x, 13.0x | 10.6x, 12.7x | 0.7x, 0.6x | 0.7x, 0.6x |
| misses, hbm_serve on the node | 7 | 3.16 / 0.80 / 17.18 / 17.18 | 0.94 / 0.86 / 2.05 / 2.05 | 0.68 / 0.88 / 1.23 / 1.23 | 4.68 / 1.05 / 18.20 / 18.20 | 8.07 / 8.50 / 16.87 / 16.87 | 1.5x, 1.1x | 2.6x, 1.0x | 0.3x, 0.1x | 0.2x, 0.1x |
| misses, h2d on the node | 1 | 17.18 / 17.18 / 17.18 / 17.18 | 2.05 / 2.05 / 2.05 / 2.05 | 1.23 / 1.23 / 1.23 / 1.23 | 18.20 / 18.20 / 18.20 / 18.20 | 11.06 / 11.06 / 11.06 / 11.06 | 1.1x, 1.1x | 0.6x, 0.6x | 0.1x, 0.1x | 0.1x, 0.1x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | fluidpool_relay mean / p50 / p95 / p99 (s) | fluidpool_v2 mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool (mean, p95) | sglang / fluidpool (mean, p95) | fluidpool_relay / fluidpool (mean, p95) | fluidpool_v2 / fluidpool (mean, p95) |
|---|---|---|---|---|---|---|---|---|---|---|
| all requests | 400 | 0.49 / 0.01 / 1.86 / 10.50 | 0.44 / 0.01 / 1.94 / 9.05 | 0.30 / 0.01 / 1.09 / 5.90 | 2.48 / 0.01 / 9.79 / 91.33 | 3.32 / 0.01 / 19.92 / 65.06 | 5.0x, 5.3x | 6.7x, 10.7x | 0.9x, 1.0x | 0.6x, 0.6x |
| misses only | 88 | 2.21 / 1.06 / 8.06 / 18.74 | 1.98 / 0.89 / 8.49 / 18.90 | 1.34 / 0.76 / 4.69 / 28.49 | 11.24 / 1.62 / 89.42 / 108.22 | 15.04 / 10.00 / 64.79 / 88.58 | 5.1x, 11.1x | 6.8x, 8.0x | 0.9x, 1.1x | 0.6x, 0.6x |
| calm, all | 15 | 0.32 / 0.01 / 1.08 / 1.08 | 0.27 / 0.01 / 0.97 / 0.97 | 0.22 / 0.01 / 0.83 / 0.83 | 0.74 / 0.01 / 4.46 / 4.46 | 6.21 / 0.01 / 25.91 / 25.91 | 2.3x, 4.1x | 19.7x, 24.0x | 0.9x, 0.9x | 0.7x, 0.8x |
| calm, misses | 5 | 0.92 / 1.03 / 1.08 / 1.08 | 0.78 / 0.89 / 0.97 / 0.97 | 0.63 / 0.68 / 0.83 / 0.83 | 2.18 / 1.25 / 4.46 / 4.46 | 18.61 / 22.13 / 25.91 / 25.91 | 2.4x, 4.1x | 20.2x, 24.0x | 0.9x, 0.9x | 0.7x, 0.8x |
| burst part, all | 385 | 0.50 / 0.01 / 1.86 / 10.50 | 0.45 / 0.01 / 1.94 / 9.05 | 0.31 / 0.01 / 1.09 / 5.90 | 2.55 / 0.01 / 9.79 / 91.33 | 3.20 / 0.01 / 16.87 / 65.06 | 5.1x, 5.3x | 6.4x, 9.1x | 0.9x, 1.0x | 0.6x, 0.6x |
| burst part, misses | 83 | 2.28 / 1.06 / 8.06 / 18.74 | 2.05 / 0.86 / 8.49 / 18.90 | 1.39 / 0.80 / 4.69 / 28.49 | 11.78 / 1.62 / 89.42 / 108.22 | 14.82 / 9.94 / 64.79 / 88.58 | 5.2x, 11.1x | 6.5x, 8.0x | 0.9x, 1.1x | 0.6x, 0.6x |
| misses, a burst on the node | 47 | 2.45 / 1.28 / 10.50 / 18.74 | 2.11 / 1.07 / 9.05 / 18.90 | 1.71 / 0.82 / 6.23 / 28.49 | 14.11 / 1.62 / 96.83 / 108.22 | 17.33 / 10.00 / 77.27 / 88.58 | 5.8x, 9.2x | 7.1x, 7.4x | 0.9x, 0.9x | 0.7x, 0.6x |
| misses, no burst on the node | 36 | 2.07 / 1.04 / 7.50 / 16.23 | 1.97 / 0.85 / 8.49 / 17.07 | 0.96 / 0.75 / 3.77 / 5.90 | 8.74 / 1.46 / 42.72 / 80.98 | 11.55 / 9.92 / 36.27 / 64.79 | 4.2x, 5.7x | 5.6x, 4.8x | 1.0x, 1.1x | 0.5x, 0.5x |
| misses, burst fired in flight | 30 | 3.26 / 1.06 / 17.22 / 18.74 | 2.77 / 0.92 / 17.07 / 18.90 | 1.91 / 0.72 / 5.90 / 28.49 | 14.76 / 1.73 / 105.42 / 108.22 | 17.66 / 11.36 / 77.27 / 88.58 | 4.5x, 6.1x | 5.4x, 4.5x | 0.8x, 1.0x | 0.6x, 0.3x |
| misses, dram on the node | 15 | 1.16 / 0.46 / 8.06 / 8.06 | 1.13 / 0.33 / 9.05 / 9.05 | 0.96 / 0.31 / 8.06 / 8.06 | 8.83 / 1.35 / 91.33 / 91.33 | 10.03 / 4.93 / 64.74 / 64.74 | 7.6x, 11.3x | 8.7x, 8.0x | 1.0x, 1.1x | 0.8x, 1.0x |
| misses, ib on the node | 23 | 2.38 / 1.38 / 8.06 / 10.50 | 2.39 / 1.26 / 9.05 / 12.14 | 1.47 / 0.94 / 6.23 / 8.06 | 15.07 / 1.64 / 91.33 / 96.83 | 20.52 / 11.05 / 65.06 / 79.04 | 6.3x, 11.3x | 8.6x, 8.1x | 1.0x, 1.1x | 0.6x, 0.8x |
| misses, ssd_rd on the node | 4 | 5.41 / 1.86 / 18.74 / 18.74 | 5.46 / 2.01 / 18.90 / 18.90 | 7.60 / 1.00 / 28.49 / 28.49 | 31.85 / 9.79 / 108.22 / 108.22 | 30.05 / 22.50 / 77.27 / 77.27 | 5.9x, 5.8x | 5.6x, 4.1x | 1.0x, 1.0x | 1.4x, 1.5x |
| misses, ssd_wr on the node | 2 | 4.91 / 6.31 / 6.31 / 6.31 | 5.43 / 6.65 / 6.65 / 6.65 | 3.26 / 3.59 / 3.59 / 3.59 | 54.10 / 105.42 / 105.42 / 105.42 | 55.53 / 88.58 / 88.58 / 88.58 | 11.0x, 16.7x | 11.3x, 14.0x | 1.1x, 1.1x | 0.7x, 0.6x |
| misses, hbm_idle on the node | 7 | 0.68 / 0.46 / 1.57 / 1.57 | 0.47 / 0.33 / 0.97 / 0.97 | 0.48 / 0.57 / 0.94 / 0.94 | 8.44 / 1.50 / 28.27 / 28.27 | 6.85 / 2.76 / 19.92 / 19.92 | 12.5x, 18.0x | 10.1x, 12.7x | 0.7x, 0.6x | 0.7x, 0.6x |
| misses, hbm_serve on the node | 7 | 3.16 / 0.80 / 17.22 / 17.22 | 0.95 / 0.86 / 2.08 / 2.08 | 0.69 / 0.88 / 1.26 / 1.26 | 5.17 / 1.05 / 21.64 / 21.64 | 8.07 / 8.50 / 16.87 / 16.87 | 1.6x, 1.3x | 2.6x, 1.0x | 0.3x, 0.1x | 0.2x, 0.1x |
| misses, h2d on the node | 1 | 17.22 / 17.22 / 17.22 / 17.22 | 2.08 / 2.08 / 2.08 / 2.08 | 1.26 / 1.26 / 1.26 / 1.26 | 21.64 / 21.64 / 21.64 / 21.64 | 11.06 / 11.06 / 11.06 / 11.06 | 1.3x, 1.3x | 0.6x, 0.6x | 0.1x, 0.1x | 0.1x, 0.1x |

### Cold bytes per activation (file + NVMe + peer staging) / all bytes, misses only

| system | mean | p50 | p95 | max | activations with any cold byte | cold GiB total |
|---|---|---|---|---|---|---|
| fluidpool | 0.229 | 0.222 | 0.379 | 0.429 | 86/88 | 1053.5 |
| fluidpool_relay | 0.176 | 0.166 | 0.309 | 0.385 | 88/88 | 931.4 |
| fluidpool_v2 | 0.001 | 0.000 | 0.000 | 0.034 | 2/88 | 24.9 |
| janus | 0.159 | 0.000 | 1.000 | 1.000 | 14/88 | 4565.6 |
| sglang | 1.000 | 1.000 | 1.000 | 1.000 | 88/88 | 7532.4 |

### How each request was served

| requests | fluidpool | fluidpool_relay | fluidpool_v2 | janus | sglang |
|---|---|---|---|---|---|
| all requests | fluid 88, ipc_hit 312 | fluid 88, ipc_hit 312 | fluid 88, ipc_hit 312 | cold 14, d2d_fork 62, h2d_wakeup 12, ipc_hit 312 | cold 88, ipc_hit 312 |
| calm, all | fluid 5, ipc_hit 10 | fluid 5, ipc_hit 10 | fluid 5, ipc_hit 10 | d2d_fork 2, h2d_wakeup 3, ipc_hit 10 | cold 5, ipc_hit 10 |
| burst part, all | fluid 83, ipc_hit 302 | fluid 83, ipc_hit 302 | fluid 83, ipc_hit 302 | cold 14, d2d_fork 60, h2d_wakeup 9, ipc_hit 302 | cold 83, ipc_hit 302 |

| | fluidpool | fluidpool_relay | fluidpool_v2 | janus | sglang |
|---|---|---|---|---|---|
| cold activations, burst part | 0 | 0 | 0 | 14 | 83 |
| requests that needed a fallback path | 0 | 0 | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool pairs mean / min, bytes mean / min | fluidpool_relay pairs mean / min, bytes mean / min | fluidpool_v2 pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 0.911 / 0.7333, 0.801 / 0.4026 | 0.4 / 0.4, 0.596 / 0.323 |
| burst | 0.986 / 0.8, 0.951 / 0.4274 | 0.99 / 0.8667, 0.957 / 0.4274 | 0.988 / 0.8667, 0.949 / 0.4274 | 0.82 / 0.6667, 0.46 / 0.3185 | 0.544 / 0.3333, 0.32 / 0.1485 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool: obtained share, time to obtain mean / p95 (s) | fluidpool_relay: obtained share, time to obtain mean / p95 (s) | fluidpool_v2: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|---|---|
| dram | 41 | 1.00, 64.61 / 124.06 | 1.00, 63.33 / 125.85 | 1.00, 61.54 / 124.38 | 1.00, 63.06 / 121.43 | 1.00, 63.31 / 124.96 |
| ib | 30 | traffic 26.429 GB/s | traffic 26.61 GB/s | traffic 27.973 GB/s | traffic 24.976 GB/s | traffic 24.979 GB/s |
| ssd_rd | 27 | traffic 6.146 GB/s | traffic 6.223 GB/s | traffic 6.593 GB/s | traffic 6.508 GB/s | traffic 6.015 GB/s |
| ssd_wr | 15 | traffic 2.249 GB/s | traffic 2.229 GB/s | traffic 2.373 GB/s | traffic 2.394 GB/s | traffic 2.223 GB/s |
| hbm_idle | 19 | 1.00, 1.50 / 18.92 | 1.00, 0.51 / 0.60 | 1.00, 1.47 / 18.92 | 1.00, 0.30 / 0.30 | 1.00, 0.30 / 0.30 |
| hbm_serve | 16 | 1.00, 2.36 / 16.63 | 1.00, 0.35 / 0.55 | 1.00, 2.42 / 17.70 | 1.00, 2.63 / 9.17 | 1.00, 0.30 / 0.30 |
| h2d | 11 | traffic 44.828 GB/s | traffic 45.397 GB/s | traffic 45.822 GB/s | traffic 44.98 GB/s | traffic 45.415 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_relay: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_v2: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|---|---|
| dram | 33/41, 34.599 / 64.896, 2119.3 | 35/41, 30.118 / 54.075, 2204.8 | 33/41, 29.134 / 57.396, 2109.3 | 13/41, 72.285 / 111.363, 889.6 | 0/41, None / None, 0.0 |
| hbm_idle | 0/19, None / None, 0.0 | 0/19, None / None, 0.0 | 17/19, 1.246 / 18.495, 727.5 | 0/19, None / None, 0.0 | 0/19, None / None, 0.0 |
| hbm_serve | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 | 0/16, None / None, 0.0 |

### Live graph and integrity

| | fluidpool | fluidpool_relay | fluidpool_v2 | janus | sglang |
|---|---|---|---|---|---|
| graph poll p50 / p99 (ms) | 3.9126938208937645 / 9.704281110316515 | 3.863945137709379 / 11.06829708442092 | 3.931201994419098 / 32.92565979063511 | 3.4680101089179516 / 5.949988029897213 | 3.7295683287084103 / 4.887411370873451 |
| pool events | {'demote': 98, 'evict': 1660, 'repair': 60} | {'demote': 98, 'evict': 1720, 'repair': 58} | {'prestage': 57, 'demote': 98, 'evict': 1671, 'repair': 62, 'settle': 99} | {'evict_whole': 24} | {} |
| requests / hits / misses / skipped | 400 / 312 / 88 / 5 | 400 / 312 / 88 / 5 | 400 / 312 / 88 / 5 | 400 / 312 / 88 / 5 | 400 / 312 / 88 / 5 |
| verified restores, bad chunks | 14, 0 | 14, 0 | 14, 0 | 14, 0 | 14, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none | none | none |
| bytes by source | {'net': 0.64, 'ldram': 0.22, 'rstage': 0.097, 'file': 0.029, 'lssd': 0.014} | {'net': 0.505, 'ldram': 0.214, 'relay': 0.157, 'rstage': 0.084, 'file': 0.029, 'lssd': 0.011} | {'net': 0.547, 'ldram': 0.205, 'relay': 0.171, 'lhbm': 0.074, 'rstage': 0.002, 'file': 0.001} | {'file': 0.589, 'net': 0.336, 'ldram': 0.058, 'rstage': 0.018} | {'file': 0.668, 'rstage': 0.332} |
