| phase | n | fluidpool mean / p95 (s) | janus mean / p95 (s) | sglang mean / p95 (s) | vs janus | vs sglang |
|---|---|---|---|---|---|---|
| calm | 16 | 0.46 / 1.43 | 1.11 / 7.12 | 1.51 / 7.12 | 2.4x | 3.3x |
| dram_home | 16 | 0.65 / 2.30 | 1.49 / 10.94 | 2.09 / 10.94 | 2.3x | 3.2x |
| ssd_home | 16 | 0.99 / 2.27 | 6.08 / 17.35 | 6.96 / 16.82 | 6.2x | 7.1x |
| ib_home | 16 | 0.89 / 2.34 | 5.13 / 18.84 | 4.00 / 10.95 | 5.8x | 4.5x |
| dram_remote | 16 | 0.59 / 2.36 | 1.90 / 11.04 | 2.57 / 10.98 | 3.2x | 4.3x |
| peer_gone | 16 | 0.51 / 2.26 | 1.71 / 10.98 | 2.23 / 10.94 | 3.3x | 4.3x |
| all | 16 | 0.69 / 2.29 | 6.00 / 23.50 | 5.46 / 23.84 | 8.7x | 7.9x |
| ALL | 112 | 0.68 / 2.26 | 3.35 / 16.78 | 3.54 / 14.23 | 4.9x | 5.2x |

| phase | fluidpool: how restores were served | janus: how restores were served | sglang: how restores were served |
|---|---|---|---|
| calm | fluid 16 | cold 3, d2d_fork 3, h2d_wakeup 10 | cold 6, resume 10 |
| dram_home | fluid 16 | cold 3, d2d_fork 5, h2d_wakeup 8 | cold 7, resume 9 |
| ssd_home | fluid 16 | cold 9, d2d_fork 5, h2d_wakeup 2 | cold 13, resume 3 |
| ib_home | fluid 16 | cold 9, d2d_fork 5, h2d_wakeup 2 | cold 14, resume 2 |
| dram_remote | fluid 16 | cold 7, d2d_fork 2, h2d_wakeup 7 | cold 11, resume 5 |
| peer_gone | fluid 16 | cold 6, h2d_wakeup 10 | cold 12, resume 4 |
| all | fluid 16 | cold 10, h2d_wakeup 6 | cold 11, resume 5 |
| ALL | fluid 112 | cold 47, d2d_fork 20, h2d_wakeup 45 | cold 74, resume 38 |

| model | GiB | fluidpool n / mean / max (s) | janus n / mean / max (s) | sglang n / mean / max (s) |
|---|---|---|---|---|
| qwen3-0.6b | 1.4 | 17 / 0.05 / 0.05 | 17 / 0.16 / 0.53 | 17 / 0.20 / 0.55 |
| glm-a | 9.99 | 15 / 0.29 / 0.38 | 15 / 1.28 / 2.87 | 15 / 1.63 / 3.47 |
| glm-d | 14.98 | 2 / 0.46 / 0.48 | 2 / 2.86 / 5.33 | 2 / 2.96 / 5.53 |
| 9g-8b | 15.25 | 32 / 0.47 / 0.53 | 32 / 0.88 / 5.79 | 32 / 1.83 / 5.95 |
| glm-b | 19.98 | 14 / 0.58 / 0.67 | 14 / 1.07 / 6.88 | 14 / 3.00 / 6.89 |
| glm-f | 24.97 | 15 / 0.95 / 1.00 | 15 / 4.31 / 9.11 | 15 / 4.20 / 9.47 |
| glm-e | 39.96 | 7 / 1.43 / 1.55 | 7 / 11.02 / 14.51 | 7 / 7.88 / 14.23 |
| qwen36-35b | 66.97 | 10 / 2.28 / 2.36 | 10 / 16.20 / 23.50 | 10 / 14.45 / 23.84 |

integrity: {"fluidpool": {"restores": 112, "verified_from_HBM": 14, "bad_chunks": 0, "failed_fetches_shed": 0}, "janus": {"restores": 112, "verified_from_HBM": 14, "bad_chunks": 0, "failed_fetches_shed": 0}, "sglang": {"restores": 112, "verified_from_HBM": 14, "bad_chunks": 0, "failed_fetches_shed": 0}}
fluidpool bytes by source: {'remote dram': 0.487, 'ldram': 0.379, 'rstage': 0.082, 'file': 0.051}
