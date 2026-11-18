# Formal pass 1b: what drives the slow misses (tail analysis)

Date: 2026-11-16. Run: `codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03/formal_r400_p1b/`
(fluidpool, fluidpool_relay, janus, sglang; 400 requests, seed 1, 88 misses each, identical burst
schedule). Script: `codex_coding/src/v0.1.23.3/task2/live_graph/pool/tail_analysis.py`.
Output: `formal_r400_p1b/tail_analysis.json`.

```
python3 -I codex_coding/src/v0.1.23.3/task2/live_graph/pool/tail_analysis.py codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03/formal_r400_p1b
```

## Method

- **Join.** Each measured miss (`event=request`, `phase!=warmup`, `kind!=ipc_hit`) is joined to
  the restore record(s) of its wave. GLM is two restores: glm53-n28 and glm53-n30.
- **Cold bytes.** Cold bytes = file + lssd + rstage (durable file, local NVMe, a peer's NVMe or
  file through its staging). cold_frac = cold bytes / all bytes.
- **slow_s.** slow_s = max over slow classes of bytes / that class's observed rate (the executor's
  per-class rate). A miss is *slow-bound* when slow_s ≥ 0.5 × its restore time. This is an
  estimate: per-class rates are EWMAs.
- **Planned vs stolen.** Slow bytes are split by the executor's per-class `stolen` chunk counts:
  - *planned* bytes were assigned to a slow class by the LP;
  - *stolen* bytes were pulled by a slow lane from a chunk the LP had planned on a fast lane. A
    stolen chunk therefore had a fast copy.

## 1. Cold bytes and time

| system | miss mean / p95 / p99 (s) | cold_frac mean / p50 / p90 / max | slow GiB | share of miss time explained by slow bytes | slow-bound misses | burst-p95 tail: slow-bound share / time explained |
|---|---|---|---|---|---|---|
| fluidpool | 1.48 / 5.19 / 18.58 | 0.230 / 0.222 / 0.364 / 0.447 | 1053 | 0.71 | 82 / 88 | 0.80 / 0.50 |
| fluidpool_relay | 1.20 / 4.14 / 18.73 | 0.176 / 0.167 / 0.265 / 0.394 | 931 | 0.77 | 73 / 88 | 1.00 / 0.79 |
| janus | 6.76 / 64.86 / 88.68 | 0.159 / 0 / 1.0 / 1.0 | 4566 | 0.83 | 13 / 88 | 1.00 / 0.99 |
| sglang | 15.04 / 64.79 / 88.58 | 1.0 (every miss cold) | 7532 | 0.88 | 84 / 88 | 1.00 / 0.96 |

FluidPool's misses are mostly slow-bound. **Most of those slow bytes, however, had a fast copy.**
Work stealing lets the NVMe/staging lanes take chunks the LP had placed on fast lanes. GLM, where
the tail lives:

| i | fluidpool s | slow GiB | planned (no fast copy, or LP choice) | stolen (had a fast copy) | fast-copy chunks lost since previous GLM activation (n28 / n30) |
|---|---|---|---|---|---|
| 22 | 3.43 | 39.3 | 8.7 | 30.5 | 1436 / 574 |
| 31 | 3.51 | 40.8 | 8.7 | 32.1 | 0 / 0 |
| 270 | 18.58 | 75.7 | 21.0 | 54.7 | 16283 / 5563 |
| 284 | 7.91 | 110.3 | 69.5 | 40.8 | 1153 / 406 |
| 307 | 6.35 | 65.0 | 24.2 | 40.8 | 0 / 0 |
| 361 | 5.19 | 59.1 | 35.9 | 23.2 | 1873 / 546 |

fluidpool_relay has the same pattern; at i=270 it took 18.73 s with 79.1 GiB slow, 52.7 of them
stolen. Only at i=284 was GLM in the state's cold list (coverage < 0.999 at that wave). It is
also the one case where planned slow bytes dominate (69.5 GiB).

**Erasure coding: no-go for now.**
- In 5 of 6 GLM activations, stolen bytes, which had fast copies, are the larger part of the
  slow bytes.
- Erasure coding would only add redundancy for bytes that already have it.
- The bytes that genuinely lacked a fast copy are 9–36 GiB per activation (69.5 at i=284). They
  follow bursts that evicted fast copies (lost_fast_copy) and that the repair had not yet
  replaced.
- Two cheaper fixes come first: (a) slow lanes must not steal chunks that have a fast copy, or
  may steal only when they can finish them before the fast lanes would; (b) faster repair after
  coverage loss.
- Re-evaluate after (a) and (b).

## 2. Outlier: fluidpool i=110, qwen36-35b@node29, cards 0,1 — 17.18 s

| quantity | value |
|---|---|
| predicted | 0.77 s (`cap_card` 47 for both cards: the live graph saw no contention) |
| bytes | net 61.5 GiB (net:0 36.1, net:1 21.3, net:2 2.0, net:3 2.0), ldram 2.3, lssd 3.2, rstage 5.0 |
| observed rates (GB/s) | net:0 0.21, net:1 1.28, net:2 0.17, net:3 0.11; lssd 7.7, rstage 5.0–8.5 |
| prof | land_s 17.0 over 14 batches; slot_wait_s 194.8 (summed over workers); fetch_s:net 66.5 |
| slow_s | 0.49 (the slow tier is not the cause) |

Bursts active at i=110:

| burst | kind, node | detail | during |
|---|---|---|---|
| b038 | **h2d on node29 card 1, the target card** | from i=109 | no |
| b039 | hbm_serve, node29 cards 2,3 | | no |
| b037 | dram, node29 6 NUMAs × 37 GiB | | yes |
| b036 | ib, node27 ← node28, ports 0,3 | | yes |
| b040 | dram, node27 | | no |

**Cause.** A tenant drove host→device copies into the very card being restored (b038, ~45 GB/s of
H2D), and that card's PCIe ingress was saturated:
- direct RDMA writes into card 1 crawled (net lanes 0.1–1.3 GB/s);
- the H2D lander for local/slow chunks took 17 s;
- staging slots stayed held while waiting (slot_wait).

The planner kept `cap_card` at 47 GB/s, so **the live graph did not see a tenant's H2D traffic on
`pcie_in/<card>`**. That is a probe gap.

fluidpool_relay, same request: **2.05 s.** Its restore moved 39.1 GiB through relay lanes. That
path enters the target over xGMI, not its congested PCIe link, so the relay went around the
saturated link.

## 3. Relay effectiveness (fluidpool_relay vs fluidpool, same request index)

| | value |
|---|---|
| misses with relay bytes | 82 / 88 |
| relay GiB total | 1185 |
| faster than fluidpool | 65 of 82 |
| median Δ with relay bytes | −0.119 s |
| median Δ without relay bytes | +0.154 s |
| by model, median Δ | qwen36-35b −0.144 s (29); 9g-8b −0.113 s (25); clover-35b −0.267 s (15); qwen3-0.6b +0.005 s (13, too small to matter) |

The GLM tail is unchanged (18.73 vs 18.58 s at i=270) because it is slow-bytes bound, not ingress
bound.

## Conclusions

1. The FluidPool tail is driven by NVMe/file bytes (71–77% of miss time). Most of those bytes had a
   fast copy and were pulled by stealing slow lanes. Fix stealing before considering erasure
   coding.
2. The single non-GLM outlier is a probe gap: tenant H2D on the target card is invisible to
   `pcie_in`. The relay variant routes around it (17.2 s → 2.1 s).
3. The relay helps 65 of 82 activations that used it, with a median of −0.12 s; it cannot help
   slow-bound GLM.
