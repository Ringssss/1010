# FluidPool runbook: every command, in order, and the core files (state of 2026-11-18)

Project overview and plan: `docx/plans/2026-11-16_fluidpool_overview_and_next_plan.md`.
Development tag `v0.1.23.3 / task2`. Everything below runs from the repository root
(`$REPO`) unless a container is named. Abbreviations used in commands:

```sh
REPO=<repository root>                                          # e.g. /home/<user>/zhujianian/fluidckpt_zjn
P=$REPO/codex_coding/src/v0.1.23.3/task2/live_graph/pool        # drivers and tools
R2=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02
R3=$REPO/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03
MAN=$R2/manifests                                               # chunk manifests (regenerated, not in git)
```

## 0. Cluster and containers

- **Nodes:** node27–30, 8 Hygon DCUs (gfx936) each with a full xGMI mesh, and 4 HCA ports each.
  Port subnets: `12/13/14/15.11.3.<27..30>`.
- **Containers:** `zjn-fluid-pool-<27..30>`. They see all cards under their host index and mount
  `/ssdpool` on the local NVMe. Required environment: `HSA_FORCE_FINE_GRAIN_PCIE=1` (the HCA then
  writes device memory directly) and `PYTHONPATH=$REPO/main_lib`.
- `/data/<node>/shared` (model checkpoints) is **read-only inside the containers**; write it from
  the host. node29's host does not see node30's `/data`.
- **Before any GPU experiment:** `ulimit -c 0`. A crash otherwise dumps every card's memory.
- **Status:** use `main_lib/fluidckpt/live/probe.py` (rocm_smi, ~0.5 ms for 8 cards), never
  `hy-smi` (8 s).

## 1. Chunk manifests and stress-catalog variants

```sh
# one process per model, on the node that holds the files (inside its pool container)
python3 $P/build_manifests.py $MAN 9g-8b=/data/node28/shared/models/9g_8b_thinking_llama \
        qwen36-35b=<dir> qwen3-0.6b=<dir> clover-35b=<dir> glm53-n28=<dir> glm53-n30=<dir>

# byte-distinct variants for the 13-model stress catalog (host side; one byte per MiB XORed)
ssh node28 "python3 -I $P/make_variants.py <src_dir> /data/node28/shared/zjn_fluidpool_models/qwen36-35b-v1 17"
# ...then build_manifests.py for each variant. run_stress.sh (section 6) does all of this.
```

The model directories are recorded in each manifest's `build.model_dir`.

## 2. Node daemons

```sh
NODED_ARGS="--arena-gib 48" bash $P/pool_ctl.sh start <log_dir>   # noded + tenantd on all four nodes
bash $P/pool_ctl.sh status
bash $P/pool_ctl.sh stop
```

Restart the daemons right before every comparison run: each run must use the daemon code it
recorded.

## 3. Microbenchmarks and smoke tests (hardware)

```sh
# xGMI relay go/no-go: sources export host regions, node28 lands into card 1
for n in node27 node29 node30; do ssh $n "docker exec zjn-fluid-pool-${n#node} bash -lc \
  'python3 $P/affinity_probe.py src $R3/relay_r01/src_$n.json 1200'" & done
ssh node28 "docker exec zjn-fluid-pool-28 bash -lc 'cd $REPO && python3 $P/relay_probe.py \
  $R3/relay_r01/src_node27.json,$R3/relay_r01/src_node29.json,$R3/relay_r01/src_node30.json \
  $R3/relay_r01/relay_node28_t1_pull.log --target 1 --warm 45 --copy pull'"
#   --copy push|pull, --cases tenant_alone,tenant_with_relay,... (tenant interference)

# planner only, no hardware: LP with / without relay on the real manifests
docker exec -e PYTHONPATH=$REPO/main_lib zjn-fluid-live-r01-27 python3 $P/relay_lp_check.py $MAN $R3/relay_r01/lp_check.jsonl

# restore-path smokes (sha-verified): relay on/off, concurrent remote-file puts, lhbm
docker exec -e WARM_S=40 zjn-fluid-pool-27 python3 $P/relay_smoke.py $MAN $R3/relay_r01/smoke3 3
# v2 mechanisms: prestage + lhbm restores, HBM-first demotion, owner-first mid-restore
docker exec -e WARM_S=40 zjn-fluid-pool-27 python3 $P/v2_smoke.py $MAN $R3/v2_smoke3
```

## 4. Trace-driven comparisons (the main experiments)

The driver is `maas3_run.py`. All systems run on one executor and see the identical request
sequence and burst schedule (checksummed). `run_maas3_frozen.sh` copies the sources into
`<out>/src_snapshot` first, so edits made during a multi-hour run cannot change the systems.

```sh
# CPU dry run of the control flow (no hardware)
python3 $P/maas3_run.py fluidpool_v2 --dry --out <dir> --requests 40 --calm 5

# formal pass p1b (four systems, 400 requests, seed 1)
SYSTEMS="fluidpool fluidpool_relay janus sglang" bash $P/run_maas3_frozen.sh $R3/formal_r400_p1b \
  --requests 400 --calm 15 --seed 1 --warm-arenas 60

# fluidpool_v2 on the same schedule (then the v2 ablations, if not stopped)
SYSTEMS="fluidpool_v2 fluidpool_v2_noprestage fluidpool_v2_nohbmfirst fluidpool_v2_norelay fluidpool_v2_noplan fluidpool_v2_age" \
  bash $P/run_maas3_frozen.sh $R3/formal_r400_v2 --requests 400 --calm 15 --seed 1 --warm-arenas 60

# tables (any directory whose subdirectories are systems; symlinks are fine)
python3 $P/summarize_maas3.py <run_dir>          # -> comparison.md, comparison.json
python3 -I $P/tail_analysis.py <run_dir>         # cold bytes, outliers, relay effect
```

**Systems.**
- `fluidpool`, `fluidpool_relay`, `fluidpool_v2`.
- v2 ablations: `fluidpool_v2_{norelay,noprestage,nohbmfirst,noplan,nodemote,age}`.
- v1 ablations: `fluidpool_{noplan,static,nodemote,nostripe,age}`.
- Baselines: `janus` (Janus-style warm/D2D core) and `sglang` (SGLang-style HIP IPC keep-alive).

**Useful options.**
- `--catalog base|big`
- `--burst-load`, `--amp` (burst intensity)
- `--relay-policy idle|loose`
- `--janus-plan static|rr|live`
- `--requests`, `--calm`, `--seed`

## 5. Stress campaign (13 models, ~1.2 TiB; normal and heavy bursts)

```sh
bash $P/run_stress.sh $R3/stress_r01
#   0. (optional) waits for formal_r400_v2's fluidpool_v2, stops its queue, writes the v2-vs-p1b comparison
#   1. builds the 7 variants (host) and their manifests (containers); refuses 0-chunk manifests
#   2. S1_big_default: --catalog big --requests 300 --calm 15 --seed 1 --warm-arenas 60
#      S2_big_heavy:   same + --burst-load 4 --amp 60
#      fresh daemons and a frozen snapshot per scenario; systems fluidpool_v2 janus sglang
```

## 6. End-to-end through SGLang (W5, not yet working on hardware)

```sh
python3 codex_coding/src/v0.1.23.3/task2/e2e/attach_unit_check.py <tmp_dir>      # CPU: IPC views bit-exact
ssh node28 "docker exec zjn-fluid-pool-28 bash -lc 'cd $REPO && ulimit -c 0 && python3 \
  codex_coding/src/v0.1.23.3/task2/e2e/attach_e2e.py $MAN <out> --model qwen3-0.6b --node node28 --card 6 --reps 3'"
python3 codex_coding/src/v0.1.23.3/task2/e2e/replay_ttft.py $MAN <out> --node node28 --cards 4,5,6,7 --models qwen3-0.6b,9g-8b
```

Status is in `codex_coding/results/v0.1.23.3/task2/e2e/W5_STATUS.txt`: the disk-loaded
**reference** engine aborts on its first forward on gfx936. Next step: reuse the exact engine
arguments of `baseline_loading/native_loading.py`.

## 7. MaaS trace motivation (CPU)

```sh
bash codex_coding/src/v0.1.23.3/task2/maas_motivation/run_maas_motivation.sh   # downloads ~15 GB, streams the analysis
```

## 8. Core files

| file | role |
|---|---|
| `main_lib/fluidckpt/live/pool.py` | Coordinator. `place` (water-filling, `within=`, `hbm_first`), `park` / `stripe` / `prestage` / `settle`, `retire` (demotion, HBM-first), `flow_plan` (max-concurrent-flow LP: ports × cards, sources, host DMA, disks, relay paths, lhbm over xGMI), `relay_pick` (owner-first), `restore` / `restore_wave` (ledger), `react` / `_victims` (value or age eviction, volatile HBM), `rebalance` |
| `main_lib/fluidckpt/live/noded.py` | Node daemon. DRAM slabs, NVMe pool, HBM arenas; `op_restore` executor with lanes `net:p` (direct RDMA into HBM), `relay:p` (slot ring on a borrowed card, then a target-side hipMemcpyAsync pull), `ldram`/`lhbm` landers (separate xGMI lander), disk lanes; `Sched` (work stealing that never goes up a tier unless the faster lane has collapsed); `op_relay_release` (owner first mid-restore); per-thread peer RPC clients (fix for the expose/unexpose deadlock) |
| `main_lib/fluidckpt/live/graph.py`, `probe.py`, `types.py` | live resource graph: vertices, edges, nominal capacities, rocm_smi probe |
| `main_lib/fluidckpt/live/rdma/` | RDMA READ data plane (C library + ctypes) |
| `main_lib/fluidckpt/live/chunks.py`, `formats.py` | content-addressed chunks, tensor → chunk manifests |
| `main_lib/fluidckpt/serve/sglang_attach.py` | W5: attach restored job buffers to a running SGLang engine (`update_weights_from_tensor` + custom loader) |
| `$P/maas3_run.py` | trace driver: catalogs (`VARIANTS`, `--catalog big`), burst schedule, systems `Fluid` / `FluidRelay` / `FluidV2` / ablations / `Janus` / `SGLang` |
| `$P/summarize_maas3.py`, `$P/tail_analysis.py` | tables (latency, cold bytes, keep-alive, tenants, integrity); tail analysis |
| `$P/run_maas3_frozen.sh`, `$P/run_stress.sh`, `$P/pool_ctl.sh` | frozen-source runs, stress orchestrator, daemon control |
| `$P/tenantd.py` | tenant bursts: DRAM / HBM / NVMe read+write / RDMA / PCIe |
| `$P/relay_probe.py`, `relay_lp_check.py`, `relay_smoke.py`, `v2_smoke.py`, `affinity_probe.py` | microbenchmarks and smokes |
| `$P/make_variants.py`, `$P/build_manifests.py` | stress-catalog variants, manifests |

## 9. Results and reports

| run | directory | report |
|---|---|---|
| relay go/no-go and smokes | `$R3/relay_r01/` | `docx/experiments/2026-11-16_relay_borrowed_ingress.md` |
| formal p1b (4 systems) | `$R3/formal_r400_p1b/` | `docx/experiments/2026-11-16_p1b_tail_analysis.md` |
| fluidpool_v2 vs p1b | `$R3/formal_r400_v2/`, `$R3/formal_r400_cmp_v2_vs_p1b/` | the comparison.md there |
| stress, 13 models | `$R3/stress_r01/S1_big_default/`, `S2_big_heavy/` | `docx/experiments/2026-11-17_stress_big_catalog.md` |
| MaaS motivation | `codex_coding/results/v0.1.23.3/task2/maas_motivation/` | `docx/experiments/2026-11-16_maas_motivation.md` |
| failed / aborted attempts | `formal_r400_p1_hung/ABORTED.txt`, `relay_r01/smoke1_fail_*`, `e2e/attach_r0*_fail_*` | in the reports above |

**Headline numbers** (misses, making room + restore, mean):
- **p1b, 6 models:** fluidpool 2.21 s, fluidpool_v2 1.34 s, Janus-style 11.24 s, SGLang-style 15.04 s.
- **Stress, 13 models:**
  - fluidpool_v2: 1.05 s (S1) / 1.16 s (S2).
  - Janus-style: 14.40 / 12.96 s.
  - SGLang-style: 13.41 / 10.71 s.
- **Model, node pairs warm during bursts:** v2 97% / 91%, Janus-style 50% / 53%, SGLang-style 27% / 28%.
- **Cold activations:** v2 0 / 0, Janus-style 17 / 15, SGLang-style 64 / 63.
