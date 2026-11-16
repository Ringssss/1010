# Borrowed ingress through xGMI: go/no-go, implementation, smoke (2026-11-16)

Development tag `v0.1.23.3 / task2`. Plan: `docx/plans/2026-11-16_fluidpool_overview_and_next_plan.md` §5.2 (W2).
Results directory: `codex_coding/results/v0.1.23.3/task2/live_graph/pool_r03/relay_r01/`.

## 1. Motivation

Every pilot miss of a 1–2-card model was pinned at the target card's PCIe ingress
(~47 GB/s/card), for FluidPool and Janus-style alike. That wall is why the calm segment ties.
The 8 DCUs of a node form a full xGMI mesh (sysfs io_link type 11). The idea is FuseLink's, on
this hardware: land data on idle neighbour cards through the ports near them, and forward it to
the target over xGMI.

## 2. Go/no-go microbenchmark (measured)

Script: `codex_coding/src/v0.1.23.3/task2/live_graph/pool/relay_probe.py`. Sources:
`affinity_probe.py src` on node27/29/30, with 8 × 2 GiB NUMA-bound host regions registered on
all 4 ports. Destination: node28, target card 1, whose near port is 1. Relays: port0→card0,
port2→card4, port3→card7, with 8 × 64 MiB slot rings per relay. Each window lasted 8 s after a
2 s ramp, following a 45 s warm-up. Daemons were stopped.

Commands (inside `zjn-fluid-pool-<nn>`):
```
python3 .../pool/affinity_probe.py src $R/src_nodeNN.json 1200                       # node27, 29, 30
python3 .../pool/relay_probe.py $R/src_node27.json,$R/src_node29.json,$R/src_node30.json \
        $R/relay_node28_t1_pull.log --target 1 --warm 45 --copy pull                  # node28
```

| case | GB/s into card 1, push copy (`relay_node28_t1.log`) | pull copy (`relay_node28_t1_pull.log`) |
|---|---|---|
| direct (near port only, today's wall) | 48.3 | 48.3 |
| xGMI one pair (card0 → card1) | 168.0 | 172.6 |
| xGMI fan-in from 5 cards | 164.6 | 164.4 |
| relay only (3 ports → relay slots → xGMI) | 120.5 | 134.8 |
| **direct + relay (gate ≥ 120)** | **169.3** | **183.4** |
| direct + relay + 2 H2D relays | 210.8 | 210.8 |

**GO.** One card takes in 3.8× its PCIe wall. The ceiling is PCIe (48) plus xGMI fan-in (~165).
Pull (hipMemcpyAsync on the target's stream) beats push (torch `copy_`, which runs on the source
card's stream).

**Tenant impact** (HBM-bound copy loop on relay card 4, 1319–1323 GB/s alone; logs
`relay_node28_t1_tenant.log`):

| while card 4 is used for | tenant slowdown |
|---|---|
| full relay (NIC in + xGMI out), push | 19.8% |
| full relay, pull | 23.9–26.7% |
| xGMI read out only | 25.5–26.0% |
| NIC writes in only, at 48 GB/s | 55–75% |

Borrowing a card that runs an HBM-bound job therefore costs that job a quarter or more of its
throughput while the borrowing lasts. Policy: owner first. Only cards with no tenant are
borrowed (`Pool.relay_pick`).

## 3. Implementation

- `pool.py`:
  - `relay_pick(node, cards, policy, snap)` applies the owner-first rule. It excludes targets,
    cards of running or planned restores (`inflight_cards`), yielded cards, cards whose live HBM
    use exceeds our arena by more than 4 GiB, cards without 512 MiB of contiguous arena, and cards
    whose PCIe the live graph shows busy. Policy `idle` also excludes cards holding one of our
    restored models; `loose` allows them.
  - `flow_plan(..., relay)` adds relay paths (port → relay card PCIe → xGMI) with rows for relay
    PCIe ingress, xGMI out (170) and target xGMI fan-in (165), and these paths do not count
    against the target's PCIe.
  - **Fix:** parked same-node HBM (`lhbm`) is now an xGMI path. Before, it was charged to host
    DMA and target PCIe.
  - `relay_policy` defaults to `off`.
- `noded.py`:
  - `op_restore(relay={port: card})` carves slot rings from relay cards' arenas.
  - Lane `relay:<p>`: two workers per port. Each does an RDMA READ into a slot, then
    hipMemcpyAsync pulls it into the target on the target's stream.
  - A port that has a ring no longer offers far-card direct writes (see smoke2 below).
  - The lander pulls `lhbm` chunks on a separate stream, so they overlap H2D.
- Driver: system `fluidpool_relay` (`maas3_run.py`, `--relay-policy idle|loose`). The summarizer
  shows a `fluidpool_relay / fluidpool` ratio column.
- Offline LP check (`relay_lp_check.py`, `relay_r01/lp_check.jsonl`, nominal capacities):
  - 9g-8b on 1 card: 0.35 → 0.087 s.
  - 35B on 2 cards: 0.77 → 0.38 s.
  - With 30% of chunks parked in same-node HBM: 0.077 s and 0.26–0.27 s.

## 4. Bug found on the way: deadlock in remote-file puts (affects all systems)

The first formal attempt (`pool_r03/formal_r400_p1_hung/`, see `ABORTED.txt`) hung in setup
after parking one model:
- py-spy on node28 showed 24 `op_expose` handlers waiting in `_sget` for staging slots.
- The cause: peer RPC clients were cached per (ip, worker index), so concurrent puts shared a
  client. A thread's `unexpose`, which frees a slot, queued behind another thread's `expose`,
  which was waiting for a slot. The 124 slots were exhausted and nothing could free one.
- Restore's `rstage` path had the same pattern.
- **Fix:** `_rpc` clients are per thread, and closed when the worker exits (`_thread`,
  `_rpc_done`).
- Verified by smoke: three models parked concurrently from durable files in 16.1–16.5 s.

## 5. Hardware smoke (measured, all sha-verified, 0 failed fetches)

Script `relay_smoke.py`. Remote DRAM only, live planner, three repetitions alternating relay off
and relay on (`loose`).

- smoke1: the pull helper's name collided with an existing `_hip` list. The worker crashed and
  the restore hung. Two fixes: the rename, and any worker exception now fails its chunk.
- smoke2: relay worked but gained little. The far-card direct lane on a borrowed port shared the
  port lock and stole chunks (relay lanes moved only ~1.5 GiB each). Fixed: a port with a ring
  feeds only through the ring.
- smoke3 (final code, `relay_r01/smoke3/relay_smoke.log`):

| restore | relay off | relay on | gain |
|---|---|---|---|
| 9g-8b → node28 card 1 (15.25 GiB) | 0.386–0.397 s (41–42 GB/s) | 0.148–0.153 s (107–110 GB/s); first 0.229 | 2.6× |
| qwen36-35b → node28 cards 1,2 (67 GiB) | 0.876–0.884 s (81–82 GB/s) | 0.481–0.510 s (141–150 GB/s) | 1.8× |
| 9g-8b → node27 card 0, 80% parked on node27 cards 4–7 (lhbm) | 0.169–0.187 s (first 0.34) | 0.179–0.243 s | ≈ |

The LP predicts 0.089 s and 0.39 s, so the executor still reaches only 60–80% of the plan. The
remaining gap is per-chunk overhead and how work stealing balances lanes:
- With lhbm present, relay lanes steal from the xGMI lane, so turning relay on does not help
  there.
- To tune: lane priorities and the size of the first chunks.

## 6. Conclusions

1. The per-card PCIe wall can be broken. 184 GB/s lands in one card (3.8×) when direct writes,
   borrowed ports and xGMI are combined.
2. In the real executor today: 2.6× for a 1-card model and 1.8× for a 2-card model, with
   integrity verified.
3. The lhbm modelling fix alone brings a model mostly parked in same-node HBM to 0.17 s
   (~90–140 GB/s), against 0.35 s on the PCIe wall.
4. Borrowing costs a busy tenant 20–27% (55–75% for unpaced NIC writes). Owner first is
   required, not optional.
5. The remote-file put deadlock was a correctness bug that could hang any long run. It is fixed.

## 7. Next

- Formal pass 1b (`pool_r03/formal_r400_p1b/`) with fluidpool, fluidpool_relay (`idle`), janus,
  sglang: 400 requests, seed 1.
- Executor tuning toward the LP prediction: lane priority order lhbm > relay > far direct, and a
  first-chunk warm-up.
- Pace relay writes so that tenant slowdown can be bounded, if a looser policy is ever wanted.
