# Stress campaign: 13-model catalog, normal and heavy bursts (2026-11-17)

Development tag `v0.1.23.3 / task2`. Results: `codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03/stress_r01/`
(`S1_big_default/`, `S2_big_heavy/`, each with `comparison.md`), orchestrator `pool/run_stress.sh`,
`campaign.log`. Preceding run: `formal_r400_v2` (fluidpool_v2 vs p1b, same schedule), comparison in
`pool_r03/formal_r400_cmp_v2_vs_p1b/comparison.md`.

## Setup

- **Catalog (`maas3_run.py --catalog big`):** 13 models, ~1.2 TiB of weights.
  - GLM-5.3 (705 GiB, two 8-card halves), Qwen3.6-35B, Clover-35B, 9g-8B, Qwen3-0.6B.
  - 7 byte-distinct variants standing in for full fine-tunes (`make_variants.py`: one byte per MiB
    XORed): qwen36-35b-v1/v2/v3, clover-35b-v1/v2, 9g-8b-v1/v2.
  - Variants share 0.03% of their bytes with the base (tiny tail chunks), so content addressing
    gains nothing.
  - Durable copies spread over node28/29/30.
- **Memory budget, the same for every system:** 30 GiB per NUMA node × 8 × 4 = 960 GiB of DRAM,
  which is less than the catalog. FluidPool may also use the idle HBM of node27/29 cards 4–7.
- **Workload:** 300 measured requests (trace order, ranks 10–16 mapped to the variants), seed 1;
  bursts start and end at request indices, and about a third fire 0.2 s into a restore wave.
  - **S1:** burst-load 2, amp 40 (the default): 123 burst events.
  - **S2:** burst-load 4, amp 60 — twice the concurrent bursts and larger DRAM grabs: 196 burst events.
- **Systems (one executor):** `fluidpool_v2` (relay + dynamic owner-first, HBM-first demotion with
  settle, idle-HBM prestage, volatile-HBM eviction, tier-ordered stealing with up-tier only after
  2 s when the fast lane measures slower), Janus-style (`within=` per-node warm copies, D2D fork
  from one peer, H2D wake-up), SGLang-style (HBM-resident only, HIP IPC re-attach).
- All six runs: rc=0, 0 tracebacks, identical request and hit/miss sequence per scenario.

## Results (misses only; "total" = making room on the cards + restore)

| | fluidpool_v2 mean / p50 / p95 / p99 | Janus-style | SGLang-style | Janus / v2 (mean, p95) | SGLang / v2 |
|---|---|---|---|---|---|
| S1 total (64 misses) | 1.05 / 0.83 / 3.94 / 8.55 s | 14.40 / 2.94 / 56.6 / 108.1 s | 13.41 / 9.94 / 32.2 / 88.6 s | 13.8×, 14.4× | 12.8×, 8.2× |
| S1 restore only | 0.84 / 0.68 / 1.51 / 8.38 s | 6.50 / 1.08 / 22.6 / 91.9 s | 13.41 / 9.94 / 32.2 / 88.6 s | 7.8×, 15.0× | 16.0×, 21.3× |
| S1 calm misses, total | 0.64 / 0.64 / 0.83 s | 26.45 / 24.9 / 55.1 s | 17.25 / 22.1 / 22.5 s | 41× | 27× |
| S2 total (63 misses) | 1.16 / 0.84 / 2.35 / 10.09 s | 12.96 / 8.41 / 39.6 / 83.8 s | 10.71 / 9.94 / 24.4 / 64.8 s | 11.2×, 16.8× | 9.3×, 10.4× |
| S2 restore only | 0.84 / 0.72 / 1.52 / 8.65 s | 4.84 / 1.14 / 19.5 / 67.7 s | 10.71 / 9.94 / 24.4 / 64.8 s | 5.8×, 12.8× | 12.7×, 16.0× |
| S2 misses with a burst on the node, total | 1.20 / 0.95 / 2.35 s | 11.00 / 8.94 / 36.5 s | 8.70 / 9.93 / 20.0 s | 9.2× | 7.2× |

How the misses were served, and how much of them came from disk:

| | fluidpool_v2 | Janus-style | SGLang-style |
|---|---|---|---|
| S1 cold activations | 0 | 17 | 64 |
| S1 activations reading any disk byte | 1/64 | 17/64 | 64/64 |
| S1 bytes from disk/file | 2.1% (99 of 4700 GiB) | 58.6% | 100% |
| S2 cold activations | 0 | 15 | 63 |
| S2 activations reading any disk byte | 3/63 | 15/63 | 63/63 |
| S2 bytes from disk/file | 3.8% (132 of 3437 GiB) | 41.1% | 100% |

Keep-alive: share of (model, home) pairs that can be activated without a cold read (mean / min
over the run), and the same weighted by bytes:

| | fluidpool_v2 | Janus-style | SGLang-style |
|---|---|---|---|
| calm, pairs / bytes | 1.00 / 1.00 | 0.67 / 0.71 | 0.18 / 0.35 |
| S1 bursts, pairs (mean / min) | 0.972 / 0.38 | 0.50 / 0.38 | 0.27 / 0.15 |
| S1 bursts, bytes | 0.942 | 0.351 | 0.187 |
| S2 bursts, pairs (mean / min) | 0.913 / 0.35 | 0.53 / 0.38 | 0.28 / 0.15 |
| S2 bursts, bytes | 0.832 | 0.318 | 0.168 |

Mean total activation time by model family (S1 → S2):

| family | fluidpool_v2 | Janus-style | SGLang-style |
|---|---|---|---|
| 35B (Qwen + variants) | 1.11 → 1.45 s | 10.2 → 12.4 s | 16.6 → 16.0 s |
| 35B (Clover + variants) | 0.99 → 0.99 s | 17.0 → 11.8 s | 10.9 → 10.8 s |
| 8B (9g + variants) | 0.62 → 0.48 s | 10.8 → 12.8 s | 4.3 → 3.3 s |
| GLM-5.3 705 GiB | 5.5 → 8.8 s | 96.8 → 83.8 s | 80.2 → 64.8 s |

Tenants:
- DRAM tenants obtained the same share under every system: 1.00 in S1, 0.93 in S2. In S2 the
  asks exceed what the nodes have, so no system can give more.
- HBM tenants obtained 0.97–1.00 under every system.

## Conclusions

1. **With more models than memory, whole-model keep-alive collapses and FluidPool does not.**
   - At the 13-model catalog, FluidPool keeps 97% (S1) / 91% (S2) of (model, home) pairs warm
     during bursts.
   - Janus-style keeps 50–53%, SGLang-style 27–28%.
   - FluidPool had 0 cold activations, against 15–17 for Janus-style and every miss for
     SGLang-style.
2. **Latency (activation total, misses):**
   - FluidPool 1.05 s (S1) and 1.16 s (S2) mean.
   - Janus-style is 11–14× slower in the mean and 14–17× in the p95; SGLang-style 9–13×.
   - Even calm-segment misses are 27–41× faster, because the baselines cannot keep the larger
     catalog warm in the first place.
3. **Heavier bursts barely move FluidPool:** mean 1.05 → 1.16 s, p95 3.94 → 2.35 s. Disk bytes
   stay at 2–4% because the pool keeps a fast copy almost everywhere and demotes instead of
   dropping.

## Caveats

- The variants carry meaningless weights; the experiment is restore-only (weights ready in HBM).
  Engine first-token is not measured: W5 is blocked by an SGLang abort on gfx936 (`e2e/W5_STATUS.txt`).
- One run per scenario; a second pass is pending, as are the fluidpool_v2 ablations (postponed
  for this campaign).
- The minimum keep-alive (0.35–0.38 of pairs) occurs during the heaviest DRAM squeezes for both
  FluidPool and Janus-style. The means differ, not the worst instant.
- The adaptive up-tier stealing rule (after 2 s, only if the fast lane measures slower) went into
  these runs. It was not in `formal_r400_v2`, whose GLM p99 was 28.5 s; in S1/S2 the worst activation of
  any model was 8.6–10.1 s.
