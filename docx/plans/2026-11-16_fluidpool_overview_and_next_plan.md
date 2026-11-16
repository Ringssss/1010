# FluidPool: project overview and next-step plan

Date: 2026-11-16. Branch `live-graph`, development tag `v0.1.23.3 / task2`.
Status words used below: **measured** = on the real cluster, with a result path; **projected** =
derived from measured link capacities but not run end-to-end; **open** = not verified.

---

## 1. What FluidPool is

### 1.1 Problem

In a multi-tenant inference cluster, many models are deployed and only a few are hot at any
moment. Every model that is not running still has to be brought back quickly when its next request
arrives. Today's keep-alive mechanisms are **per model and per node**:

- SGLang Weight Cache keeps an evicted model's weights in host memory or HBM of the same node
  and re-attaches by HIP/CUDA IPC.
- Janus (SOSP'26) keeps warm copies and forks an active instance D2D from one peer.
- BlitzScale (OSDI'25) multicasts parameters over RDMA from a host cache or a live instance.
- ServerlessLLM (OSDI'24) loads from a local multi-tier checkpoint store and places by locality.

All of them treat a model as one indivisible object that must sit, whole, in some memory that
belongs to it. In a shared cluster that memory is not reliably there. Other tenants take host
memory, HBM, NVMe bandwidth and NIC bandwidth at any moment, and the free capacity that remains
is fragmented across nodes and media. A whole-model cache then either does not fit, or gets
evicted under pressure and falls back to a cold read from the checkpoint (tens of seconds to
minutes for large models).

### 1.2 Thesis

> Use every node and every medium to keep every model's weights fast-restorable, by storing
> weights as small content-addressed fragments in whatever space is free right now, and restoring
> them as a flow over the live cluster graph.

The project is a **cluster resource manager for weights**, not an LLM serving engine. Its job ends
when a model's weights are in the right cards' HBM in the layout the engine expects.

### 1.3 One abstraction, two components

**Abstraction.** Weights are content-addressed chunks of 16–64 MiB. The cluster is one graph.
- Vertices: HBM of each card, DRAM of each NUMA node, NVMe of each node, durable checkpoint files.
- Edges: PCIe H2D/D2H, card-to-card xGMI, HCA ports (RDMA, including direct writes into HBM), and
  NVMe reads.
- Every vertex and edge has a live residual capacity.

Parking a model, restoring it and evicting it are all flows on this graph.

**Component 1: live resource graph.** It tracks the residual capacity of every vertex and edge
while other tenants come and go, and reacts within milliseconds. When a tenant needs memory, a
card or bandwidth, FluidPool yields at once: it drops or demotes the least valuable chunks first.

**Component 2: user-transparent chunking.** The checkpoint keeps its normal format (safetensors /
GGUF). A manifest maps tensors to chunks, and chunks are placed in fragments wherever the graph
has room. A restore fetches each chunk from several sources over a max-concurrent-flow plan
straight into the target cards.

### 1.4 What is new compared with the baselines

| | Unit kept | Where | Under tenant pressure | Restore |
|---|---|---|---|---|
| SGLang Weight Cache | whole model | its own node | model lost, cold read | single local source |
| Janus | whole model | its node + a peer instance | evicted whole | D2D from one peer, else H2D |
| BlitzScale | whole model | host cache / live instances | n/a (static pool) | RDMA multicast chain |
| ServerlessLLM | whole checkpoint | local DRAM / SSD tiers | LRU of whole models | local tiered loader |
| **FluidPool** | **chunk** | **any free HBM/DRAM/NVMe in the cluster** | **chunk-level yield, ms; demote, not drop** | **multi-source flow on the live graph, direct into HBM** |

---

## 2. Architecture as built

Code lives in `main_lib/fluidckpt/live/`. Drivers and tools are in
`codex_coding/src/v0.1.23.3/task2/live_graph/pool/`.

| Part | File | Role |
|---|---|---|
| Graph and probe | `graph.py`, `probe.py`, `types.py` | Vertices, edges, nominal capacities, live residuals (rocm_smi ~0.5 ms / 8 cards) |
| Chunk store / formats | `chunks.py`, `formats.py` | Content addressing, tensor-to-chunk manifest |
| RDMA data plane | `rdma/` (C + ctypes) | RDMA READ; with `HSA_FORCE_FINE_GRAIN_PCIE=1` the HCA writes straight into HBM |
| Node daemon | `noded.py` | Per node: DRAM slabs, NVMe pool, HBM arenas (long-lived, registered), pull-based restore executor with work stealing and source revocation |
| Coordinator | `pool.py` | `place` (water-filling, `within=` node sets), `park` / `stripe` / `retire` (demote), `flow_plan` (LP), `restore_wave`, `react` / `_victims` (value-ordered eviction), `rebalance` |
| Tenants | `pool/tenantd.py` | Seeded bursts on DRAM / HBM / NVMe read+write / RDMA / PCIe |
| Trace driver | `pool/maas3_run.py`, `summarize_maas3.py` | Three systems on one executor, identical burst schedule (checksummed), `--dry` CPU mode |

### 2.1 Restore planner (`Pool.flow_plan`)

The planner solves a max-concurrent-flow LP: it minimises T such that every chunk group is
delivered and every capacity × T holds. The capacities are:

- each destination HCA port, as its live share of time, with one pipe per port;
- each (source node, port) sending side, as a live residual;
- each port × card pair, from the measured capacity table (each port has two "near" cards at
  47 GB/s; others get about 24 or less);
- each card's PCIe ingress (47 GB/s, shared by H2D and direct RDMA);
- the node's host DMA budget (88 GB/s);
- the NVMe pools.

Chunks are grouped by (card, source set, has local copy, has slow copy). Several restores in one
wave are planned jointly through a reservation ledger.

### 2.2 Calibrated hardware facts (node27–30, identical)

| Fact | Value | Source |
|---|---|---|
| Card PCIe ingress (Gen5 x16) | ~47 GB/s | `graph.py` NOMINAL, pool_r02 |
| HCA port → near card, direct into HBM | 47 GB/s; into a pre-registered arena 150–190 GB/s aggregate | pool_r02 |
| One port writing several cards | collapses | pool_r02 |
| Host-staged (NIC→DRAM→H2D), whole node | ≤ ~35 GB/s | pool_r02 |
| Node H2D total | 85–100 GB/s | profile_node27 |
| **xGMI, 8 cards, full mesh** | **156 GB/s per pair, 165 GB/s fan-in to one card** | `graph.py:38-39` (RCCL era), sysfs io_link type 11 |
| NVMe pool | 6.5–7 GB/s per node | profile_node27 |
| Cards not near any port | none: every card is near exactly one port | capacity table |

---

## 3. Evidence so far

### 3.1 Data plane (pool_r02, measured)

67 GiB restored onto node28's eight cards:

| Configuration | Throughput |
|---|---|
| Local pinned DRAM only | 101 GB/s |
| Three remote nodes, direct into HBM, LP over the capacity table | 153 GB/s |
| Ports round-robin | 61 GB/s |
| LP that ignores the table | 47 GB/s |
| Local + remote | 188 GB/s |

So the max-flow plan is worth 2.5×, adding sources is worth 1.9×, and pure remote beats pure
local by 1.5×.

### 3.2 Trace pilot (pool_r03/maas3_pilot, measured, 60 requests, 24 bursts, 17 misses)

| System | miss mean | miss p50 | worst |
|---|---|---|---|
| FluidPool | 1.44 s | 0.85 s | 10.6 s (GLM 705 GiB during three simultaneous bursts) |
| Janus-style | 11.28 s | 1.05 s | 114.6 s (GLM cold from file) |
| SGLang-style | 19.38 s | 10.9 s | 122 s |

On FluidPool's side:
- 228 tenant evictions, with a median of 16 ms per event and a maximum of 107 ms. Tenants always
  received what they asked for.
- Fast coverage stayed at ≥ 86% and recovered to 100%.

**Correction.** The previous round's Janus-style 3.0× was inflated. `Pool.place` counted replicas
cluster-wide, so "keep one local copy" placed nothing on three of four nodes (local wake-ups:
node29 16, all other nodes 0). `place(within=...)` now fixes this, and every earlier Janus number
must be re-run.

### 3.3 Diagnosis: two regimes

Reading every restore event of the pilot shows that the two regimes have different bottlenecks:

1. **Models on one or two cards (most misses) are pinned at the per-card PCIe wall.** The planner
   predicts exactly B / (n_cards × 47): 0.35 s for 9g-8b and 0.77 s for qwen36-35b on two cards.
   - FluidPool measured 0.38–0.68 s and 0.85–1.07 s.
   - Janus-style local H2D measured 0.39 s and 1.2 s; its D2D fork measured 1.6 s.
   - The r400 dry run gives calm misses 1.0× against Janus, with an identical p50 of 0.77 s.

   **The calm-segment tie is structural.** No placement or source-side improvement can beat the
   wall, because the wall is the target card's own link.
2. **GLM and burst-degraded cases are pinned by SSD-only bytes.** In the 10.6 s restore, 27.9 GiB
   came from file at 3.8 GB/s and 39.4 GiB from node30's NVMe at 6.8 GB/s, while node30's DRAM
   had just been taken by a tenant. That is a coverage problem, not a bandwidth problem.

Hence the positioning. FluidPool's claims today are **capacity and robustness**, not single-restore
speed. Section 5.2 shows how to add a genuine speed claim as well.

### 3.4 Known modelling gap

`pool.py:813`: copies from local HBM (`lhbm`, e.g. the HBM parking pool on idle cards) are charged
to the host DMA budget and to the target card's PCIe ingress. Physically they travel over xGMI and
use neither, so the LP undervalues the HBM pool.

---

## 4. Review of the previous plan (Fable 5.2)

The previous plan had two parts: a build list, and an evaluation and paper list.

### 4.1 Build list: done, keep as is

| Item | State (2026-11-16) |
|---|---|
| Self-harm: noded's ~180 GiB, react() treating our own use as tenant pressure | Fixed (slab NUMA fix, own-usage accounting) |
| Demote instead of discard on release | Done (`retire(demote=True)`, D2H back-fill) |
| HBM and DRAM as one mutually exclusive pool (an active instance is a readable replica) | Done (`publish`, `rhbm` sources) |
| Equal-time placement + cold-block striping over all NVMe | Done (`place(local_frac)`, `stripe`) |
| Source side + joint planning of a wave | Done (`flow_plan` tx residuals, ledger, `restore_wave`) |
| Value-ordered eviction | Done (`_victims`: chunks with another fast replica go first, then the coldest model) |
| Whole-model baselines placing on their own node | Fixed by `place(within=)` |

### 4.2 Evaluation and paper list: agree, with five adjustments

**Agree.**
- The positioning: "keep all weights alive with all media of all nodes".
- The primary metrics:
  - retention ratio vs memory budget;
  - tail latency and non-cold ratio vs interference intensity;
  - memory needed to keep the full catalogue fast (853 GiB per copy for FluidPool vs about
    1229 GiB for Janus-style, per the driver's accounting).
- That BlitzScale and ServerlessLLM are the real competitors, and at least one must run as a real
  system.
- End-to-end TTFT with open-loop timestamped replay.
- MaaS-trace motivation, scale argument, repeats, ablations, failure behaviour, tenant impact.

**Adjustments.**

1. **Do not drop restore latency entirely; demote it to a secondary axis and win it.** The first
   question a reviewer asks about fragmentation is "is a fragmented model slower to load than a
   contiguous local copy?" Today the honest answer is "equal" (the card wall). With the xGMI relay
   in §5.2 it becomes "faster, ~3× at the median, and Janus cannot follow because it needs the
   live graph to borrow safely". That turns the main objection into a contribution.
2. **The retention-vs-budget curve needs a larger catalogue than 6 models.** Two to four budget
   points on the real cluster are enough to calibrate. The curve itself should come from the
   calibrated simulator with a catalogue drawn from the MaaS / GenAI traces (tens to hundreds of
   variants). Do not build the catalogue from copies of the same files under new names: content
   addressing would dedup them, and the result would be unfair to the baselines.
3. **Know what the MaaS trace can and cannot show.** It is per instance (allocated GPU memory, GPU
   memory utilisation, host memory use, SM utilisation, `zero_samples`). It has no node ID and no
   node capacity. It **can** show:
   - stranded HBM inside allocations (over-provisioning) and how fast it changes at 5-minute
     resolution;
   - the GPU-hours held by idle-resident (cold) models, which is the cost of whole-model
     keep-alive;
   - the long tail of model variants (catalogue size).

   It **cannot** directly show per-node fragmentation of free host memory. That must come from
   our own tenant trace (`fluidpool_trace/pod_memory_util_anon.csv`) or be stated as an
   assumption.
4. **Real competitor: run ServerlessLLM first, keep BlitzScale as a policy port.**
   ServerlessLLM Store's real C++ data plane already builds and loads on HIP
   (`baseline_loading/serverless_qwen_full_r03`). BlitzScale's real system is CUDA/RDMA-specific
   with WIP builds. Only its source-selection/TP-dispatch policy runs on our executor
   (`blitz_s3_r02`), so it stays a "BlitzScale-policy" comparison and must be labelled that way.
   Add **"Janus + relay"** as an ablation, so the relay gain is not credited to fragmentation alone.
5. **The scale argument rests on topology, not node count.** FuseLink (OSDI'25) itself shows that
   NVIDIA servers have the same structure: GPU–NIC affinity plus a fast intra-node fabric (NVLink)
   that can relay. Our graph is parameterised by exactly these capacities, so the simulator can be
   instantiated with DGX-class numbers. Measuring on one NVIDIA node with GPUDirect RDMA is
   desirable but depends on access.

---

## 5. Next plan

### 5.1 Workstreams

| # | Workstream | Needs hardware | Output | Acceptance |
|---|---|---|---|---|
| W1 | Formal robustness comparison v1 (FluidPool / Janus-style with `within=` / SGLang-style) | yes, ~3 h per pass | `pool_r03/formal_*` | 2 passes; frozen source snapshot; burst checksums equal; ≥ 40 bursts fire during restores |
| W2 | xGMI relay ("borrowed ingress") | microbench + run | LP + executor change, `relay_r01` | Go/no-go ≥ 120 GB/s into one card; ≥ 2× median restore for 1–2-card models; tenant slowdown on relay cards reported |
| W3 | Coverage and tail: repair after every request; demote over xGMI; SSD-tail rule for EC | partly | `pool.py`, `noded.py` | SSD-sourced share of miss time measured; EC go/no-go decided from data |
| W4 | Prediction and value placement (old items 1 + 3 merged) | run | predictor + periodic rebalance | Non-cold ratio under bursts up vs W1; background moves never slow foreground restores |
| W5 | Real competitor + E2E TTFT | yes | ServerlessLLM run; SGLang engine attach | TTFT and SLO attainment on open-loop replay, same engine for all systems |
| W6 | Motivation from MaaS trace | CPU only | `docx/experiments/…_maas_motivation.md` + figures | Stranded HBM, cold-model residency, variant tail, volatility |
| W7 | Calibrated simulator, scale and NVIDIA parameters | CPU only | simulator + validation | Within ±15% of real runs on 3+ configurations; curves at 64–512 nodes |
| W8 | Rigor: ablations, repeats, failures, tenant impact | yes | ablation table | Each component removed once; ≥ 2 passes; noded kill and coordinator restart tested |

### 5.2 W2: the xGMI relay, the core technical upgrade

**Insight (FuseLink → our hardware).** FuseLink observes that a GPU's communication is limited by
its affiliated NIC, and lets a GPU borrow idle NICs of neighbour GPUs through NVLink:
- buffer pages are remapped onto the GPU next to the borrowed NIC (cuMem VMM);
- a monitor marks a NIC idle after 160 µs without traffic;
- the owner's traffic always wins.

On our nodes the limiting link is the **target card's PCIe ingress (47 GB/s)**, and the idle fast
fabric is the **8-card xGMI mesh (156 GB/s per pair, 165 GB/s fan-in)**. The HCA ports are already
all used by the LP. What is not used is the neighbours' PCIe lanes and the xGMI mesh behind them.

**Insight (HydraServe → our hardware).** HydraServe fetches a model through several servers' NICs
at once, serves through pipeline parallelism, and later consolidates onto one server. Inside a
node, xGMI makes that consolidation nearly free and concurrent, so we get the bandwidth
aggregation without pipeline parallelism or engine changes. Across nodes, HydraServe's other idea
is that the **sink is a choice**: when a request does not fix the node, evaluate the LP for each
candidate node and card set (10–50 ms each) and start where T is smallest.

**Design.**

1. Graph and LP (`flow_plan`):
   - Add the relay path port p → slot on relay card r (r a near card of p, r ≠ c) → xGMI → target c.
     It consumes the port, PCIe ingress of r, xGMI out of r and xGMI in of c, but **not** c's PCIe.
   - Local DRAM via relay: H2D into r, then xGMI. This still counts against the host DMA budget.
   - Fix `lhbm`: a holder card h → c uses xGMI out of h and xGMI in of c, with no host DMA and no
     PCIe of c.
   - Give relay a small cost, so it is used only when it lowers T.
2. Executor (`noded`):
   - New lane class `relay:p:r`. It does an RDMA READ into a ring of 8 × 64 MiB slots carved from
     r's arena (already registered on all ports), then `copy_` r → c on a stream owned by c (the
     target pulls, so r's compute units stay free), then frees the slot.
   - Work stealing and `revoke` apply unchanged.
3. Owner first (FuseLink's rule):
   - `make_room` / `yield_card` on r revokes its relay lanes before anything else.
   - The probe adds per-card PCIe use (`pcie_bw` in sysfs).
   - If `rsmi_counter_*` exposes xGMI traffic on Hygon (`rsmi_topo_is_hylink` exists), the probe
     adds xGMI residuals too (**open**).
4. Go/no-go microbenchmark (≈ 30 min, runs ≥ 6 s):
   - Card c takes its near port directly while three other ports write into their near cards'
     slots and c pulls those over xGMI, all at once.
   - **Go if ≥ 120 GB/s lands in c.**
   - Also run an HBM-bound kernel on a relay card and report its slowdown.
   - The 156/165 GB/s figures are from the RCCL era and were never measured alongside direct NIC
     writes, so this test is the gate.

**Projected effect (not measured).**

| Restore | Today's floor | Projected with relay |
|---|---|---|
| 9g-8b, 1 card | 0.35 s | ~0.08–0.1 s |
| qwen36-35b, 2 cards | 0.77 s | ~0.26 s (bounded by node sources: 4 ports × 47 + host DMA) |
| GLM, 8 cards per node | — | unchanged (already uses all PCIe; its limit is coverage) |

**Spin-offs.**
- **Demote over xGMI.** When a tenant takes card c, its parked chunks move to neighbour arenas at
  ~156 GB/s before falling back to freshly faulted DRAM (13 GB/s today: 81 GiB took 6.4 s).
- **Prediction gets easier.** Every card of a node is ~165 GB/s from every other, so speculative
  pre-pull only has to predict the **node**, not the exact card.

### 5.3 W3: coverage and tail

- Repair after every request, not only after misses, run as a background borrower that yields to
  restores.
- Prefer SSD-only bytes first within a restore, and stripe them over all four NVMe pools.
- **Erasure coding stays parked until the data decides.** From W1 logs, compute the share of miss
  time spent on file, local-SSD and remote-SSD bytes. Arguments against doing it now:
  - EC only protects fast coverage against one node's DRAM loss.
  - Its parity eats the fast memory that GLM's 705 GiB already nearly fills.
  - Rebuilding a fragment pulls k fragments into the target, which multiplies ingress demand on
    exactly the bottleneck link.

  Go only if SSD-only bytes dominate the burst p95. GPU XOR throughput is not the question; it is
  far above any link rate.

### 5.4 W4: prediction and value placement

- One predictor per model, from inter-arrival histograms (hybrid-histogram style, as in Serverless
  in the Wild), giving P(request in the next window) per (model, node).
- One value function: seconds saved per GB = T_LP(without) − T_LP(with), using the LP as the
  oracle. The tier order after W2 is: same-node HBM (xGMI) > local DRAM ≈ remote DRAM/HBM (ports)
  > NVMe.
- A periodic greedy knapsack of moves under a background bandwidth budget. Moves are borrowers and
  yield to foreground restores and tenants.

### 5.5 W5: real competitor and end-to-end TTFT

- **Engine attach.** The SGLang version in `lib_cite/sglang` has `weight_cache/ipc_loader.py`, the
  dummy loader and `update_weights_from_tensor`. Start the engine once with dummy weights. A
  restore lands in FluidPool's arena and is handed to the engine through IPC handles, which is the
  same attach path SGLang Weight Cache uses. Every system then uses the same engine, and only the
  source of the IPC buffers differs.
- **Scope.** GLM-5.3 does not start in SGLang on gfx936 (its own NSA path crashes;
  `docx/articles/2026-09-27_glm53_startup_vmfault_forensics.md`). E2E therefore covers the
  Qwen / 9g / Clover models, and GLM stays at weight-ready latency, labelled as such.
- **Replay.** Open-loop replay with timestamps from `cluster-trace-v2026-GenAI`; report TTFT p50
  and p99 and SLO attainment.
- **Competitor.** ServerlessLLM Store as the real system (HIP build exists). BlitzScale as a policy
  port on our executor, labelled "BlitzScale-policy".

### 5.6 W6: motivation (CPU only, can start now)

Download `instance_week_5min` (4.5 GiB) first and `instance_daily` (9.8 GiB) second. From these,
measure:
1. Stranded HBM: gpu_mem_alloc × (1 − gpu_mem_util), its distribution, and its change between
   5-minute windows.
2. Cold-model residency: GPU-hours and GiB held by instances with mostly zero duty cycle.
3. The variant long tail: models per cluster, and the share of requests going to the top 10%.
4. Host-memory use per instance and its volatility. State clearly that node-level free host memory
   is not in the trace.

### 5.7 W7 and W8: scale and rigor

- **Simulator.**
  - Same graph and LP, with a discrete-event executor.
  - Calibrate on 3+ real configurations from W1/W2 and require ±15%.
  - Then run 64–512 nodes with MaaS-shaped catalogues and two topologies: Hygon (xGMI + 4×400G)
    and a DGX-like one (NVLink + 8×400G, GPU-NIC affinity).
- **Ablations**, each removed once:
  - live graph (static capacities);
  - LP → round-robin;
  - source side;
  - demote → drop;
  - striping;
  - value eviction → age;
  - relay;
  - prediction.
- **Failures:**
  - kill one noded during a wave (chunks must be re-fetched from other replicas or files);
  - restart the coordinator (state rebuilt from the daemons' inventories).
- **Tenant impact:** yield latency (already 16 ms median), tenant slowdown under relay borrowing,
  and our share of each burst.

### 5.8 Order

| When | What |
|---|---|
| Now, no hardware | W1 prep (frozen snapshot, `within=`, per-request repair); W2 LP extension + `lhbm` fix + offline replay of pilot restore sets through the new LP; W6 download and analysis; W5 engine-attach code |
| Next hardware slot | W1 formal pass 1 |
| After pass 1 | W2 go/no-go microbench (30 min) → relay lane → small pilot with relay |
| Then | W1 pass 2 with relay as a separate system variant (FluidPool vs FluidPool+relay, plus Janus+relay) |
| Then | W5 E2E runs; W3 EC decision from W1 data; W4 predictor |
| In parallel (CPU) | W7 simulator; W8 ablation scripts |

### 5.9 Paper storyline these results support

1. **Motivation.** Free memory in inference clusters is large but stranded and volatile (MaaS
   trace). Whole-model keep-alive wastes it and collapses under tenant pressure (W1).
2. **Insight.** Treat weights as fragments in a live cluster graph, and restore as a flow that
   borrows idle links and memory, yielding on demand.
3. **Claims.**
   - Retain the full catalogue with about 0.7× the memory of whole-model keep-alive.
   - Keep tail latency and non-cold ratio under any-medium interference.
   - Fragmentation costs no speed and, with fabric-aware relay, beats contiguous local copies by
     about 3× at the median.

---

## 6. Risks and open questions

- The relay gain depends on PCIe ingress and xGMI being independent while a NIC writes into the
  relay card at the same time (W2 gate). If the go/no-go fails, keep `lhbm`-over-xGMI (the HBM
  pool as the fastest same-node tier) and drop the NIC relay.
- Does `rsmi_counter_*` expose xGMI traffic on gfx936? If not, infer xGMI residuals from our own
  copy rates (FuseLink-style passive detection).
- Engine attach through IPC on DTK/HIP must keep the tensor layout the engine expects (TP shards,
  post-processing). Verify on Qwen TP2 first.
- NVIDIA evidence may stay argued rather than measured.
- Cross-node pipeline-parallel spreading (HydraServe) is not planned. After the relay, its
  remaining gain is under 0.2 s and it requires engine changes.

## 7. Key paths

- Code: `main_lib/fluidckpt/live/{pool,noded,graph,probe}.py`, `main_lib/fluidckpt/live/rdma/`
- Drivers: `codex_coding/src/v0.1.23.3/task2/live_graph/pool/{maas3_run,tenantd,summarize_maas3}.py`
- Results: `codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02/`, `pool_r03/{maas3_pilot,dry}/`
- Reports: `docx/experiments/2026-11-12_fluidpool_cluster_keepalive_report.md`
- Baselines: `lib_cite/README.md` (SGLang, Janus, BlitzScale(-hip), ServerlessLLM(-hip))
- Related work read for this plan: FuseLink (OSDI'25, `src/fuselink.h`, `src/monitor.h`);
  HydraServe (NSDI'26, `scripts/kubernetes/vllm/src/algs.py`)
- Traces: `/home/zhujianian/clusterdata/cluster-trace-v2026-{GenAI,maas}`
