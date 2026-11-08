# Model freeze — 2026-09-23 23:43 CST

Selected: **V9_links_hswio_numa_rw_conflict_tied** (benchmarks/results/model_fit_node27_frozen.json).

Structure and capacities were chosen using train + train2 experiments and validation set 0
(seed 0), which was inspected while revising the structure (so it is no longer held out).
Validation sets 2 (seed 1, general mixes) and 3 (seed 2, materialization-shaped mixes) were
generated and measured **after** this freeze; nothing is refit on them.

## After the freeze (2026-09-24), nothing refit

- validation4 (seed 3, placement-policy mixes) was generated and measured after the freeze:
  aggregate median 4.4%, max 8.9%.
- Real plan executions exposed a GPU0 slowdown next to the NVMe. `train3` experiments tested two
  structures: a shared PCIe-switch capacity (V10, **rejected**: the fit assigned it no capacity, and
  h2d n0->g0 + NVMe->n0 add up to 52 GB/s) versus "a root port touching two NUMA nodes collapses"
  (confirmed: the same h2d falls to 29.5 only when the NVMe writes to NUMA 2). V10 was removed from
  the code; its fit is kept as `model_fit_node27_v10_rejected.json`.
- The model is not extended with that rule. Instead the planner stays inside the regime where the
  frozen V9 is accurate (single-node root ports, no NUMA fan-out): see docs/findings_node27.md.
