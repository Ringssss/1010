# Cross-node keeper bandwidth: node27 -> node28, TP2 (2 RCCL pairs)

Synthetic shards, NUMA-pinned DRAM -> RCCL/IB -> remote NUMA-pinned DRAM.
`pass 0` allocates and first-touches the destination buffer inside the timed window;
`pass 1+` reuse it and the pair communicators created at `mesh_init`.

| GiB/rank | total GiB | pass | wall s | GB/s | aggregate GB/s | prep s | ib s |
|---|---|---|---|---|---|---|---|
| 1 | 2 | 0 cold | 0.420 | 5.12 |  5.12 | 0.356 | 0.407 |
| 1 | 2 | 1 warm | 0.086 | 24.95 | 24.95 | 0.000 | 0.083 |
| 2 | 4 | 0 cold | 0.787 | 5.46 |  5.46 | 0.701 | 0.776 |
| 2 | 4 | 1 warm | 0.130 | 33.05 | 33.05 | 0.000 | 0.126 |
| 4 | 8 | 0 cold | 1.539 | 5.58 |  5.58 | 1.392 | 1.529 |
| 4 | 8 | 1 warm | 0.148 | 58.07 | 58.07 | 0.000 | 0.144 |
| 8 | 16 | 0 cold | 3.586 | 4.79 |  4.79 | 3.362 | 3.576 |
| 8 | 16 | 1 warm | 0.229 | 74.97 | 74.97 | 0.000 | 0.226 |

Per pair, warm: 74.97 / 2 = 37.5 GB/s, 77% of the measured 48.5 GB/s single-link ceiling.

## Reading

- Cold is flat at ~5 GB/s across a 8x size range, and `prep_s` is 85-94% of it. The cost is
  destination allocation + first-touch page faults, not transport. A flow into an
  already-allocated buffer never pays it.
- The knee is size, not warm/cold per se: 1 GiB warm only reaches 25 GB/s because the
  transfer is short enough that launch and drain dominate. At 16 GiB the link saturates.
- Consequence for the ckpt experiments: pre-allocate destination shards before the timed
  phase, and move in >= 8 GiB units, or the measurement reports allocation not flow.

## Reproduce

    docker exec zjn-keep-v02-n27 bash -lc 'cd <ROOT> && PYTHONPATH=<ROOT>/main_lib \
      python3 codex_coding/src/task2/cluster/bw_sweep.py --gibs 1,2,4,8 --tps 2 \
      --src node27 --dst node28 --repeat 3'

Driver: `codex_coding/src/task2/cluster/bw_sweep.py`
