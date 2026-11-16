# MaaS trace: stranded GPU memory and cold-model residency (motivation, W6)

Date: 2026-11-16. Workstream W6 of `docx/plans/2026-11-16_fluidpool_overview_and_next_plan.md`.
Data: Alibaba `cluster-trace-v2026-maas` (ATC'26, *Model-as-a-Service in the Wild*), six months,
56 clusters, 30 GPU types, ~300k applications, ~70k model variants.
Script: `codex_coding/src/v0.1.23.3/task2/maas_motivation/{maas_motivation.py,run_maas_motivation.sh}`.
Results: `codex_coding/results/v0.1.23.3/task2/maas_motivation/`.

## Why this measurement

FluidPool claims that whole-model keep-alive wastes cluster memory and that cold models hold
resources that fragment quickly. A motivation section has to show both: (a) how much allocated GPU
memory holds no tensors, (b) how much of the cluster's GPU-hours goes to instances that are
effectively idle, and (c) how fast that pool changes, because a live graph that follows it must
react at that speed.

## Method

All analysis streams one parquet shard at a time with column projection (`pyarrow`
`iter_batches`, 1M rows). Peak RSS was 3.6 GiB on a host that was also running a GPULive experiment
(the pipeline ran at `nice -n 19 ionice -c3`). Definitions:

- **stranded** = `gpu_mem_alloc * (1 - gpu_mem_util/100)` for an instance segment: GPU memory
  allocated to the instance but not used by it. This is *not* physically free device memory; the
  trace has no node id and no node capacity.
- **hour-weighted / allocated-memory-weighted** means weighted by segment duration or by
  allocated GiB-hours, i.e. the time-average view rather than one row per segment.
- **cold** = an instance segment whose `zero_samples / samples` (share of 20 s telemetry samples
  with zero duty cycle) is at or above a threshold.
- **weekly volatility**: `instance_week_5min` has no allocation column, so used HBM per window is
  `alloc_per_gpu * gpu_count * gpu_mem_util`, where `alloc_per_gpu` is the median daily
  `gpu_mem_alloc / gpu_count` of that instance over days 140–146. 1.46M of 140.9M windows (1.0%)
  have no daily allocation and are skipped. Shards were hash-partitioned by `instance_id` so that
  one instance's windows end up in the same file; the temporary 5.2 GB partition set was deleted
  after the run.

Exact commands:

```sh
# download (~15 GB, public OSS) into clusterdata/cluster-trace-v2026-maas/data/, then:
bash codex_coding/src/v0.1.23.3/task2/maas_motivation/run_maas_motivation.sh
# individual stages (python: the conda "sglang" env, the only one with pyarrow+matplotlib):
$PY maas_motivation.py split <data> <out>
$PY maas_motivation.py daily <data> <out>
$PY maas_motivation.py weekly <data> <out>
$PY maas_motivation.py figures <out>
```

## Results

### 1. Stranded memory inside allocations

| quantity | p50 | p90 | p99 | mean |
|---|---|---|---|---|
| unused share of an instance's own allocation, allocated-memory-weighted | 6.4% | 24.8% | 98.9% | 11.3% |
| stranded GiB per instance segment | 15.0 | 96.6 | 350.8 | 39.5 |
| stranded GiB per instance, hour-weighted | 4.3 | 45.2 | 160.3 | 16.3 |

- **11.3%** of all allocated GPU memory-hours sit in allocations that hold no tensors (4.37e9 of
  3.85e10 allocated GiB·h). A quarter of instance-time runs with at least 25% of its allocation
  unused.
- The two GiB rows are kept apart on purpose: allocations come in quanta (45, 95.6, 192 GiB per
  GPU), so a GiB level on a segment measures instance size as much as waste. The share row is the
  scale-free one; the capacity statement is made at cluster level below.

Cluster level (day average of the concurrent sum):

| | p10 | p50 | p90 | p99 |
|---|---|---|---|---|
| stranded GiB per cluster-day | 51 | 6,026 (5.9 TiB) | 54,222 (53 TiB) | 224,143 (219 TiB) |
| stranded share of allocation | 6.4% | 11.9% | 27.7% | — |

All clusters summed, averaged over a day: **1.0 PB stranded against 8.9 PB allocated**. Figure
`fig_stranded_hbm_cdf.png` panel (b) plots the per-cluster histogram with one 35B model (67 GiB)
and one GLM-class model (344 GiB) marked: the median cluster holds enough stranded memory for
~90 models of the first size.

### 2. Cold-model residency

Share of the total held by instance segments whose telemetry is idle (zero duty cycle) for at
least the given share of samples:

| threshold | GPU-hours | allocated GPU memory | avg GiB held fleet-wide |
|---|---|---|---|
| ≥ 50% | 16.7% | 14.6% | 1.29 PB |
| ≥ 90% | 10.5% | 9.0% | 0.78 PB |
| ≥ 99% | 8.0% | 6.9% | 0.61 PB |

By model class and workload (share of GPU-hours in the ≥90% bucket): offline `b_` 6.9%, online
`b_` 14.0%, offline `s_` 7.8%, online `s_` 13.1%, offline `o_` 28.7%, online `o_` 10.9%. Online
workloads are consistently colder than offline ones — the batch jobs keep their GPUs busy.

### 3. Variant long tail

232,180 models carry telemetry. Per cluster: p50 **66,333** distinct models, p90 140,649, max
167,900. The top 10% of models per cluster hold **96.7%** of GPU-hours (global: 97.8%; p10 of
clusters 80.5%). 119,098 models are ≥50% cold, 9,625 are ≥90% cold
— the long tail is real, and the tail is exactly what a whole-model keep-alive scheme cannot
afford to keep warm.

### 4. Volatility (5-min resolution, one week)

| quantity | p50 | p90 | p99 |
|---|---|---|---|
| cluster's total stranded memory, \|change\| in 5 min, % of its mean | 5.6% | 37.6% | 150% |
| same, 1 h apart | 11.9% | 63.1% | 251% |
| one instance's used HBM, \|change\| in 5 min, GiB | 0.001 (85% of windows: no change) | 0.19 | 59.6 |

Per-cluster p95/p5 ratio of stranded memory over the week: p50 3.2×, p90 91×. So the pool is not
only large, it is **lively at the timescale of a few minutes** on the clusters that matter, which
is the regime FluidPool's reactor is built for (16 ms median yield) and the regime in which a
static placement plan is stale within one 5-minute window.

### 5. Host memory

Per instance: hour-weighted p50 7.9 GiB, p90 28.2 GiB, p99 112 GiB. 5-min \|change\| p50 9 MiB,
p90 0.71 GiB, p99 15.8 GiB. The trace gives per-instance use only; node-level free host memory
(and therefore fragmentation of the host-memory free pool) is not in it.

## Figures

- `fig_stranded_hbm_cdf.png` — (a) CDF of the unused share of each allocation, allocated-memory
  weighted; (b) histogram of stranded GiB per cluster-day with model-size references.
- `fig_cold_residency.png` — cold residency share by threshold, GPU-hours vs allocated memory.
- `fig_stranded_volatility.png` — how fast a cluster's stranded memory changes, 5 min vs 1 h.

## Conclusions for the paper

1. The resource FluidPool targets is not marginal: **~1 PB of allocated GPU memory holds no
   tensors at any moment**, 11.3% of the fleet's allocation, and ~0.8 PB of that sits with
   instances that are ≥90% idle.
2. The catalogue is far larger than any warm set: ~66k models per cluster (median), 96.7% of
   GPU-hours in the top 10%. Keeping whole models warm cannot cover it; fragment-level placement
   is the only mode that can use leftover space at this cardinality.
3. The free capacity changes on a 5-minute scale on most clusters (p90: >1/3 of the cluster's
   stranded pool turns over per 5 minutes), so a keep-alive system needs a live view, not a plan.
4. Host-memory side: per-instance use is modest (7.9 GiB p50) but volatile; node-level
   fragmentation must be argued from our own tenant trace, not from this dataset.

## Limitations

- No node id, no node capacity: node-level free memory and per-node fragmentation cannot be
  measured here. All statements above are at instance and cluster level.
- `stranded` is allocation minus utilisation reported by the platform's own telemetry; if a
  framework over-reserves (KV pool, fragmentation inside the allocator), that shows up as
  stranded, which is the right notion for "memory held but not useful", but it is not the same as
  "free device memory an unrelated job could take".
- Weekly volatility needs a proxy allocation per instance (median daily value); 1.0% of windows
  are skipped. Instance-level 5-min deltas are quantized by the platform's 5-min averaging.
- The MaaS trace is a different operator from our cluster; it is used for scale and dynamics, not
  for absolute numbers of our four nodes.
