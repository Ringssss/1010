| phase | n | fluidpool mean / p95 (s) | janus mean / p95 (s) | sglang mean / p95 (s) | vs janus | vs sglang |
|---|---|---|---|---|---|---|
| calm | 10 | 3.65 / 21.70 | 16.73 / 72.77 | 19.47 / 72.78 | 4.6x | 5.3x |
| dram | 11 | 3.23 / 19.31 | 10.96 / 74.60 | 24.44 / 86.42 | 3.4x | 7.6x |
| ssd | 9 | 4.14 / 32.20 | 12.37 / 104.44 | 25.91 / 124.64 | 3.0x | 6.3x |
| ib | 9 | 3.77 / 6.89 | 6.82 / 19.93 | 18.37 / 37.99 | 1.8x | 4.9x |
| hbm | 11 | 3.69 / 20.76 | 11.50 / 63.35 | 19.68 / 74.63 | 3.1x | 5.3x |
| all | 9 | 1.44 / 2.86 | 1.25 / 2.42 | 20.56 / 42.53 | 0.9x | 14.3x |
| ALL | 59 | 3.34 / 20.76 | 10.14 / 72.77 | 21.42 / 74.63 | 3.0x | 6.4x |

| phase | fluidpool: how | janus: how | sglang: how |
|---|---|---|---|
| calm | fluid 10 | cold 4, d2d_fork 5, h2d_wakeup 1 | cold 9, resume 1 |
| dram | fluid 11 | cold 2, d2d_fork 7, h2d_wakeup 2 | cold 10, resume 1 |
| ssd | fluid 9 | cold 1, d2d_fork 7, h2d_wakeup 1 | cold 9 |
| ib | fluid 9 | cold 4, d2d_fork 2, h2d_wakeup 3 | cold 7, resume 2 |
| hbm | fluid 11 | cold 5, d2d_fork 1, h2d_wakeup 5 | cold 10, resume 1 |
| all | fluid 9 | cold 1, d2d_fork 4, h2d_wakeup 4 | cold 8, resume 1 |
| ALL | fluid 59 | cold 17, d2d_fork 26, h2d_wakeup 16 | cold 53, resume 6 |

| model | GiB | fluidpool n / mean / max (s) | janus n / mean / max (s) | sglang n / mean / max (s) |
|---|---|---|---|---|
| qwen3-0.6b | 1 | 7 / 0.33 / 0.62 | 7 / 0.24 / 0.74 | 7 / 0.86 / 1.98 |
| 9g-8b | 15 | 19 / 0.85 / 2.67 | 19 / 1.07 / 4.21 | 19 / 7.61 / 21.41 |
| clover-35b | 65 | 12 / 3.66 / 6.89 | 12 / 14.36 / 44.95 | 12 / 21.54 / 45.34 |
| qwen36-35b | 67 | 17 / 2.37 / 4.91 | 17 / 5.24 / 34.50 | 17 / 29.18 / 52.50 |
| glm53 | 704 | 4 / 23.49 / 32.20 | 4 / 78.79 / 104.44 | 4 / 89.62 / 124.64 |

integrity: {"fluidpool": {"requests": 59, "verified": 8, "bad_chunks": 0, "shed_fetches": 0, "concurrent_waves": 56, "bytes_by_source": {"net": 0.552, "ldram": 0.187, "file": 0.176, "rstage": 0.062, "lssd": 0.022}}, "janus": {"requests": 59, "verified": 8, "bad_chunks": 0, "shed_fetches": 0, "concurrent_waves": 56, "bytes_by_source": {"file": 0.602, "net": 0.195, "ldram": 0.121, "rstage": 0.082}}, "sglang": {"requests": 59, "verified": 8, "bad_chunks": 0, "shed_fetches": 0, "concurrent_waves": 56, "bytes_by_source": {"file": 0.667, "rstage": 0.311, "ldram": 0.022}}}
