"""Streaming a bucket plan across nodes with the collectives that work on this fabric.

Constraints measured on this cluster (docs/findings_multinode.md):

  * RCCL 2.22.3 cross-node `send`/`recv` is broken here ("ncclSocketInit: connecting to address
    with family 0", proxy Accept EPERM). Collectives work. So every stream is a `broadcast`.
  * Vendor plugin required: NCCL_NET_PLUGIN=librccl-net-shca.so, else TCP socket fallback
    (0.74 GB/s instead of 48.5).
  * Container needs memlock=-1 or ibv_reg_mr fails.

Rates this planner is calibrated on (node27<->node29, 8 ranks/node):
  one paired stream  25.7 GB/s     (SGLang's shape: TP rank i <-> TP rank i)
  N paired streams   205.8 GB/s aggregate
  one root fanning out to many   ~121 GB/s from the root

The interesting scheduling question is which of those three regimes a given materialization is
in, because they differ by 2x and the choice is ours to make (we are not stuck with rank i <-> i).
"""
from __future__ import annotations

import dataclasses
import time
from typing import Dict, List, Optional, Sequence

from .bucket import Bucket


@dataclasses.dataclass
class StreamPlan:
    """How a set of buckets is spread across (source rank, target rank) pairs."""
    assignments: List[tuple]   # (bucket_index, src_rank, dst_rank)
    regime: str                # "paired" | "fanout" | "multi_source"
    est_seconds: float


# Measured effective rates (GB/s), single-direction, this fabric, 8 ranks/node.
RATE_PAIRED_SINGLE = 25.7
RATE_PAIRED_AGGREGATE = 205.8
RATE_FANOUT_ROOT = 121.0
PER_BCAST_S = 99.3e-6


def rate_model(n_src: int, n_dst: int) -> tuple:
    """Return (regime, steady_rate_GBps) for a materialization shape.

    1 source  -> many targets : the root's egress is the wall (~121 GB/s), shared.
    k sources -> k targets    : k independent streams, aggregate ~205.8 when k is 8; each stream
                                gets aggregate/k but the aggregate is what bounds the transfer.
    """
    if n_src == 1 and n_dst > 1:
        return "fanout", RATE_FANOUT_ROOT
    if n_src == n_dst and n_src > 1:
        return "paired", RATE_PAIRED_AGGREGATE
    if n_src > 1 and n_dst > 1:
        return "multi_source", RATE_PAIRED_AGGREGATE
    return "paired", RATE_PAIRED_SINGLE


def plan_streams(buckets: Sequence[Bucket], n_src: int, n_dst: int) -> StreamPlan:
    """Assign buckets to source/target rank pairs. Round-robin the buckets over pairs so every
    stream carries a similar byte count -- a skewed assignment would leave links idle at the tail
    (this is the same reason the LP planner in fluidckpt/planner exists)."""
    n_pairs = min(n_src, n_dst)
    regime, rate = rate_model(n_src, n_dst)
    by_bytes = sorted(range(len(buckets)), key=lambda i: -buckets[i].bytes)
    load = [0] * n_pairs
    assignments = []
    for i in by_bytes:
        p = load.index(min(load))       # least-loaded pair, LPT scheduling
        load[p] += buckets[i].bytes
        assignments.append((i, p if n_src > 1 else 0, p if n_dst > 1 else 0))
    makespan_bytes = max(load) if load else 0
    # `rate` is the aggregate over all pairs; each pair runs at rate / n_pairs, and the slowest
    # pair (LPT makespan) bounds the transfer.
    per_stream = rate / n_pairs if regime != "fanout" else rate
    est = makespan_bytes / (per_stream * 1e9) if rate > 0 else float("inf")
    return StreamPlan(assignments, regime, est)


def estimate(buckets: Sequence[Bucket], n_src: int, n_dst: int) -> Dict:
    """Full cost estimate for one materialization: LPT makespan + the handshake term."""
    plan = plan_streams(buckets, n_src, n_dst)
    n_b = len(buckets)
    # per-broadcast fixed cost ~99 us (scripts/bucket_bw.py, 128 KiB tensors, paired subgroup).
    # Pairs run concurrently, so each pair pays it for its own share of the buckets.
    n_pairs = min(n_src, n_dst)
    handshake_s = (n_b / n_pairs) * PER_BCAST_S
    return {
        "regime": plan.regime,
        "n_buckets": n_b,
        "n_pairs": n_pairs,
        "transfer_s": plan.est_seconds,
        "handshake_s": handshake_s,
        "total_s": plan.est_seconds + handshake_s,
        "bytes": sum(b.bytes for b in buckets),
    }


def measure_stream(fn, repeats: int = 3) -> float:
    """Median wall time of a collective sequence, after a warm-up round."""
    import torch
    import torch.distributed as dist

    fn()
    torch.cuda.synchronize()
    dist.barrier()
    ts = []
    for _ in range(repeats):
        dist.barrier()
        t0 = time.perf_counter()
        fn()
        torch.cuda.synchronize()
        ts.append(time.perf_counter() - t0)
    ts.sort()
    return ts[len(ts) // 2]
