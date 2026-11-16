schedule: {"seed": 1, "requests": 40, "calm": 5, "burst_load": 2.0, "amp": 40, "kinds": ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"], "tenant_container": "6ebc62069627eabf9bf6659d9a78d228", "sha256": "6efee3fcc7a011b63d7e91676c640c92a2012a90f92921ff3f033b7d7a2509ed", "events": 20, "by_kind": {"ib": 5, "ssd_wr": 3, "h2d": 2, "dram": 3, "ssd_rd": 3, "hbm_idle": 2, "hbm_serve": 2}, "during": 6, "mean_concurrency": 2.23, "max_concurrency": 6, "requests_without_burst": 2}

same request sequence and hit/miss pattern in every system: True

### Activation latency: restore only

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | fluidpool_relay mean / p50 / p95 / p99 (s) | fluidpool_noplan mean / p50 / p95 / p99 (s) | fluidpool_static mean / p50 / p95 / p99 (s) | fluidpool_nodemote mean / p50 / p95 / p99 (s) | fluidpool_nostripe mean / p50 / p95 / p99 (s) | fluidpool_age mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool (mean, p95) | sglang / fluidpool (mean, p95) | fluidpool_relay / fluidpool (mean, p95) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all requests | 40 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 1.66 / 0.01 / 0.85 / 59.44 | 3.44 / 0.01 / 11.09 / 59.44 | 6.7x, 1.1x | 13.8x, 14.0x | 1.0x, 1.0x |
| misses only | 14 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 4.71 / 0.80 / 59.44 / 59.44 | 9.78 / 11.09 / 59.44 / 59.44 | 7.0x, 20.1x | 14.5x, 20.1x | 1.0x, 1.0x |
| calm, all | 5 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.50 / 0.80 / 0.85 / 0.85 | 6.66 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 13.8x, 14.0x | 1.0x, 1.0x |
| calm, misses | 3 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.83 / 0.85 / 0.85 / 0.85 | 11.09 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 14.0x, 14.0x | 1.0x, 1.0x |
| burst part, all | 35 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 1.83 / 0.01 / 0.80 / 59.44 | 2.98 / 0.01 / 11.09 / 59.44 | 8.5x, 1.0x | 13.8x, 14.0x | 1.0x, 1.0x |
| burst part, misses | 11 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 5.78 / 0.38 / 59.44 / 59.44 | 9.43 / 2.55 / 59.44 / 59.44 | 9.0x, 20.1x | 14.7x, 20.1x | 1.0x, 1.0x |
| misses, a burst on the node | 5 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 12.21 / 0.38 / 59.44 / 59.44 | 15.18 / 2.55 / 59.44 / 59.44 | 13.4x, 20.1x | 16.6x, 20.1x | 1.0x, 1.0x |
| misses, no burst on the node | 6 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 4.63 / 2.55 / 11.09 / 11.09 | 1.0x, 1.0x | 11.2x, 14.0x | 1.0x, 1.0x |
| misses, burst fired in flight | 2 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 2.55 / 2.55 / 2.55 / 2.55 | 1.0x, 1.0x | 6.7x, 6.7x | 1.0x, 1.0x |
| misses, dram on the node | 1 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 1.0x, 1.0x | 4.2x, 4.2x | 1.0x, 1.0x |
| misses, ib on the node | 3 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 20.20 / 0.80 / 59.44 / 59.44 | 24.36 / 11.09 / 59.44 / 59.44 | 14.7x, 20.1x | 17.7x, 20.1x | 1.0x, 1.0x |
| misses, ssd_wr on the node | 1 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 1.0x, 1.0x | 4.2x, 4.2x | 1.0x, 1.0x |
| misses, hbm_serve on the node | 2 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 6.82 / 11.09 / 11.09 / 11.09 | 1.0x, 1.0x | 11.6x, 14.0x | 1.0x, 1.0x |
| misses, h2d on the node | 1 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 1.0x, 1.0x | 4.2x, 4.2x | 1.0x, 1.0x |

### Activation latency: making room on the cards + restore

| requests | n | fluidpool mean / p50 / p95 / p99 (s) | fluidpool_relay mean / p50 / p95 / p99 (s) | fluidpool_noplan mean / p50 / p95 / p99 (s) | fluidpool_static mean / p50 / p95 / p99 (s) | fluidpool_nodemote mean / p50 / p95 / p99 (s) | fluidpool_nostripe mean / p50 / p95 / p99 (s) | fluidpool_age mean / p50 / p95 / p99 (s) | janus mean / p50 / p95 / p99 (s) | sglang mean / p50 / p95 / p99 (s) | janus / fluidpool (mean, p95) | sglang / fluidpool (mean, p95) | fluidpool_relay / fluidpool (mean, p95) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| all requests | 40 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 0.25 / 0.01 / 0.80 / 2.96 | 1.66 / 0.01 / 0.85 / 59.44 | 3.44 / 0.01 / 11.09 / 59.44 | 6.7x, 1.1x | 13.8x, 13.9x | 1.0x, 1.0x |
| misses only | 14 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 0.67 / 0.80 / 2.96 / 2.96 | 4.71 / 0.80 / 59.44 / 59.44 | 9.78 / 11.09 / 59.44 / 59.44 | 7.0x, 20.1x | 14.5x, 20.1x | 1.0x, 1.0x |
| calm, all | 5 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.48 / 0.80 / 0.80 / 0.80 | 0.50 / 0.80 / 0.85 / 0.85 | 6.66 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 13.8x, 14.0x | 1.0x, 1.0x |
| calm, misses | 3 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.80 / 0.80 / 0.80 / 0.80 | 0.83 / 0.85 / 0.85 / 0.85 | 11.09 / 11.09 / 11.09 / 11.09 | 1.0x, 1.1x | 14.0x, 14.0x | 1.0x, 1.0x |
| burst part, all | 35 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 0.22 / 0.01 / 0.80 / 2.96 | 1.83 / 0.01 / 0.80 / 59.44 | 2.98 / 0.01 / 11.09 / 59.44 | 8.5x, 1.0x | 13.8x, 13.9x | 1.0x, 1.0x |
| burst part, misses | 11 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 0.64 / 0.38 / 2.96 / 2.96 | 5.78 / 0.38 / 59.44 / 59.44 | 9.43 / 2.55 / 59.44 / 59.44 | 9.0x, 20.1x | 14.7x, 20.1x | 1.0x, 1.0x |
| misses, a burst on the node | 5 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 0.91 / 0.38 / 2.96 / 2.96 | 12.21 / 0.38 / 59.44 / 59.44 | 15.18 / 2.55 / 59.44 / 59.44 | 13.4x, 20.1x | 16.6x, 20.1x | 1.0x, 1.0x |
| misses, no burst on the node | 6 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 0.41 / 0.38 / 0.80 / 0.80 | 4.63 / 2.55 / 11.09 / 11.09 | 1.0x, 1.0x | 11.2x, 13.9x | 1.0x, 1.0x |
| misses, burst fired in flight | 2 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 0.38 / 0.38 / 0.38 / 0.38 | 2.55 / 2.55 / 2.55 / 2.55 | 1.0x, 1.0x | 6.7x, 6.7x | 1.0x, 1.0x |
| misses, dram on the node | 1 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 1.0x, 1.0x | 4.2x, 4.2x | 1.0x, 1.0x |
| misses, ib on the node | 3 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 1.38 / 0.80 / 2.96 / 2.96 | 20.20 / 0.80 / 59.44 / 59.44 | 24.36 / 11.09 / 59.44 / 59.44 | 14.7x, 20.1x | 17.7x, 20.1x | 1.0x, 1.0x |
| misses, ssd_wr on the node | 1 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 1.0x, 1.0x | 4.2x, 4.2x | 1.0x, 1.0x |
| misses, hbm_serve on the node | 2 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 0.59 / 0.80 / 0.80 / 0.80 | 6.82 / 11.09 / 11.09 / 11.09 | 1.0x, 1.0x | 11.6x, 14.0x | 1.0x, 1.0x |
| misses, h2d on the node | 1 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.06 / 0.06 / 0.06 / 0.06 | 0.26 / 0.26 / 0.26 / 0.26 | 1.0x, 1.0x | 4.2x, 4.2x | 1.0x, 1.0x |

### How each request was served

| requests | fluidpool | fluidpool_relay | fluidpool_noplan | fluidpool_static | fluidpool_nodemote | fluidpool_nostripe | fluidpool_age | janus | sglang |
|---|---|---|---|---|---|---|---|---|---|
| all requests | fluid 14, ipc_hit 26 | fluid 14, ipc_hit 26 | fluid 14, ipc_hit 26 | fluid 14, ipc_hit 26 | fluid 14, ipc_hit 26 | fluid 14, ipc_hit 26 | fluid 14, ipc_hit 26 | cold 1, d2d_fork 8, h2d_wakeup 5, ipc_hit 26 | cold 14, ipc_hit 26 |
| calm, all | fluid 3, ipc_hit 2 | fluid 3, ipc_hit 2 | fluid 3, ipc_hit 2 | fluid 3, ipc_hit 2 | fluid 3, ipc_hit 2 | fluid 3, ipc_hit 2 | fluid 3, ipc_hit 2 | d2d_fork 1, h2d_wakeup 2, ipc_hit 2 | cold 3, ipc_hit 2 |
| burst part, all | fluid 11, ipc_hit 24 | fluid 11, ipc_hit 24 | fluid 11, ipc_hit 24 | fluid 11, ipc_hit 24 | fluid 11, ipc_hit 24 | fluid 11, ipc_hit 24 | fluid 11, ipc_hit 24 | cold 1, d2d_fork 7, h2d_wakeup 3, ipc_hit 24 | cold 11, ipc_hit 24 |

| | fluidpool | fluidpool_relay | fluidpool_noplan | fluidpool_static | fluidpool_nodemote | fluidpool_nostripe | fluidpool_age | janus | sglang |
|---|---|---|---|---|---|---|---|---|---|
| cold activations, burst part | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 11 |
| requests that needed a fallback path | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### Share of (model, home) pairs that can be activated without a cold read

| part | fluidpool pairs mean / min, bytes mean / min | fluidpool_relay pairs mean / min, bytes mean / min | fluidpool_noplan pairs mean / min, bytes mean / min | fluidpool_static pairs mean / min, bytes mean / min | fluidpool_nodemote pairs mean / min, bytes mean / min | fluidpool_nostripe pairs mean / min, bytes mean / min | fluidpool_age pairs mean / min, bytes mean / min | janus pairs mean / min, bytes mean / min | sglang pairs mean / min, bytes mean / min |
|---|---|---|---|---|---|---|---|---|---|
| calm | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 0.4 / 0.4, 0.733 / 0.6799 |
| burst | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 1.0 / 1.0, 1.0 / 1.0 | 0.822 / 0.7333, 0.428 / 0.3224 | 0.43 / 0.3333, 0.287 / 0.2284 |

### What the tenants got (per burst kind)

| kind | bursts | fluidpool: obtained share, time to obtain mean / p95 (s) | fluidpool_relay: obtained share, time to obtain mean / p95 (s) | fluidpool_noplan: obtained share, time to obtain mean / p95 (s) | fluidpool_static: obtained share, time to obtain mean / p95 (s) | fluidpool_nodemote: obtained share, time to obtain mean / p95 (s) | fluidpool_nostripe: obtained share, time to obtain mean / p95 (s) | fluidpool_age: obtained share, time to obtain mean / p95 (s) | janus: obtained share, time to obtain mean / p95 (s) | sglang: obtained share, time to obtain mean / p95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| dram | 3 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.01 | 1.00, 0.00 / 0.00 |
| ib | 5 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_rd | 3 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| ssd_wr | 3 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |
| hbm_idle | 2 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| hbm_serve | 2 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 | 1.00, 0.00 / 0.00 |
| h2d | 2 | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s | traffic 0.0 GB/s |

### How the system reacted while a burst was on

| kind | fluidpool: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_relay: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_noplan: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_static: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_nodemote: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_nostripe: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | fluidpool_age: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | janus: bursts answered by a yield, first yield mean / p95 (s), GiB yielded | sglang: bursts answered by a yield, first yield mean / p95 (s), GiB yielded |
|---|---|---|---|---|---|---|---|---|---|
| dram | 1/3, 0.0 / 0.0, 100.8 | 1/3, 0.001 / 0.001, 100.8 | 1/3, 0.0 / 0.0, 100.8 | 1/3, 0.001 / 0.001, 100.8 | 1/3, 0.0 / 0.0, 100.8 | 1/3, 0.001 / 0.001, 100.8 | 1/3, 0.001 / 0.001, 100.8 | 1/3, 0.001 / 0.001, 101.5 | 0/3, None / None, 0.0 |
| hbm_idle | 2/2, 0.0 / 0.0, 73.0 | 2/2, 0.0 / 0.0, 73.0 | 2/2, 0.0 / 0.0, 73.0 | 2/2, 0.0 / 0.0, 73.0 | 2/2, 0.0 / 0.0, 73.0 | 2/2, 0.0 / 0.0, 73.0 | 2/2, 0.0 / 0.0, 73.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 |
| hbm_serve | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 | 0/2, None / None, 0.0 |

### Live graph and integrity

| | fluidpool | fluidpool_relay | fluidpool_noplan | fluidpool_static | fluidpool_nodemote | fluidpool_nostripe | fluidpool_age | janus | sglang |
|---|---|---|---|---|---|---|---|---|---|
| graph poll p50 / p99 (ms) | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 | 0.5 / 0.9 |
| pool events | {'evict': 10, 'repair': 3, 'demote': 1} | {'evict': 10, 'repair': 3, 'demote': 1} | {'evict': 10, 'repair': 3, 'demote': 1} | {'evict': 10, 'repair': 3, 'demote': 1} | {'evict': 10, 'repair': 4} | {'evict': 10, 'repair': 3, 'demote': 1} | {'evict': 10, 'repair': 3, 'demote': 1} | {'evict_whole': 1} | {} |
| requests / hits / misses / skipped | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 | 40 / 26 / 14 / 10 |
| verified restores, bad chunks | 5, 0 | 5, 0 | 5, 0 | 5, 0 | 5, 0 | 5, 0 | 5, 0 | 5, 0 | 5, 0 |
| shed fetches, failed waves, failed bursts | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 |
| adapter fallbacks | none | none | none | none | none | none | none | none | none |
| bytes by source | {'net': 0.676, 'ldram': 0.324} | {'net': 0.676, 'ldram': 0.324} | {'net': 0.676, 'ldram': 0.324} | {'net': 0.676, 'ldram': 0.324} | {'net': 0.676, 'ldram': 0.324} | {'net': 0.676, 'ldram': 0.324} | {'net': 0.676, 'ldram': 0.324} | {'file': 0.601, 'net': 0.269, 'ldram': 0.13} | {'file': 1.0} |
