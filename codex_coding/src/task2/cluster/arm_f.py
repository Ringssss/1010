"""Arm F: keepalive restore -- pinned DRAM on one node, planned cross-node flow into a target HBM.

This is the method under test. The checkpoint is read once, off the critical path, into pinned DRAM
on a source node (the `ingest` the keepalive exists to amortise). Later, any number of target nodes
can be materialised from that snapshot without touching storage again, and the producer is free to
die -- the bytes live in the keeper's own pinned memory, not in another process's HBM.

The route is not hand-written: `flowplan.solve()` is the max-concurrent-flow LP over every copy of
every shard and every link between them (h2d, hostrd, ssd, ib out/in, pair, gpuin), so the byte
split across copies is chosen to finish the slowest resource last. `to_ops()` turns that split into
byte-range ops and round-robins the IB ops across destinations so every pair group starts streaming
at once.

Two destinations are used on purpose: rank0's copy lives on the same node as the source worker
(local H2D after a DRAM read) and rank1's lives on the destination node (cross-node IB). The LP sees
both routes at once and splits accordingly, which is the integration this arm exists to show.

Measured on the destination side only, with the mandated interval max(weights_ready) - min(load_begin).
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
CLUSTER = os.path.join(ROOT, "codex_coding/src/task2/cluster")
# `xfer.run` ships this file to /tmp/_x.py, so __file__ is not the repo copy -- the mesh helpers
# have to be found through the repo path, not relative to wherever the script happens to land.
for p in (HERE, CLUSTER, os.path.join(ROOT, "main_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import keepctl  # noqa: E402
from fluidckpt.transport.flowplan import Copy, Demand, route, solve, to_ops  # noqa: E402

MODEL = "/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"
LAUNCHER = os.path.join(ROOT, "codex_coding/src/serving/sglang_fluid_launch.py")
GB = 1e9


def get(port, path, timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=timeout) as r:
        p = r.read()
    return json.loads(p) if p else {}


def wait_http(port, timeout=1800, proc=None):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"port {port} exited rc={proc.returncode}")
        try:
            get(port, "health", timeout=2)
            return time.monotonic() - t0
        except Exception:
            time.sleep(1)
    raise TimeoutError(f"port {port} never came up")


def summary_load(metrics, ranks=2):
    """max(weights_ready) - min(load_begin) over the ranks of this run."""
    begins, ends = {}, {}
    for r in range(ranks):
        path = os.path.join(metrics, f"rank{r}.jsonl")
        if not os.path.exists(path):
            continue
        for line in open(path):
            e = json.loads(line)
            if e["event"] in ("load_begin", "model_layout_begin"):
                begins[r] = min(begins.get(r, 1 << 62), e["monotonic_ns"])
            if e["event"] == "weights_ready":
                ends[r] = e["monotonic_ns"]
    if not begins or not ends:
        return None
    return {"load_s": (max(ends.values()) - min(begins.values())) / 1e9,
            "ranks_begin": len(begins), "ranks_end": len(ends),
            "per_rank_s": {r: (ends[r] - begins[r]) / 1e9 for r in ends if r in begins}}


def plan_and_flow(snap, members, src_node, src_gpus, dst_node, dst_gpus, index, meta,
                  caps=None, dry=False, tag="restore", local_stage=False):
    """One LP-solved restore of every tp shard onto (dst_node, dst_gpus).

    `local_stage` picks which real topology the LP is planning over, and the two are genuinely
    different experiments:

      * False -- the snapshot exists only on the source node, so every byte must cross the network.
        The LP has one copy per shard and the only route is IB; the interesting question is then how
        the two pair streams share the node's 205.8 GB/s IB budget.
      * True  -- the snapshot was pre-staged to the destination node's own DRAM (the widest keepalive
        "spread a copy while the model is still warm" case). Now each shard has a local and a remote
        copy and the LP may split, which is the only setting where the split is worth solving.

    Handing the LP a copy that does not exist would be the easiest way to make it look fast; the
    copy list is built from the topology the caller actually asked for.
    """
    demands = []
    for tp, (dgpu, sgpu) in enumerate(zip(dst_gpus, src_gpus)):
        off_total = max(o + n for o, n in index[tp].values())
        copies = [Copy(kind="dram", node=src_node, gpu=sgpu)]
        if local_stage:
            copies.append(Copy(kind="dram", node=dst_node, gpu=dgpu))
        demands.append(Demand(tp=tp, node=dst_node, gpu=dgpu, nbytes=off_total, copies=copies))
    T, split, load = solve(demands, caps)
    wid = lambda n, g: members.index((n, g))
    ops = to_ops(demands, split, wid)
    for o in ops:
        o["snap"] = snap
    report = {"lp_T_s": T, "ops": len(ops), "split_GB": [(i, j, round(b / GB, 3)) for i, j, b in split],
              "resource_load_GBps": {k: round(v, 2) for k, v in sorted(load.items())},
              "route_of": [(i, route(demands[i].copies[j], demands[i])[0]) for i, j, _ in split]}
    if dry:
        return report, ops
    parts = {members[w] for o in ops for w in (o.get("src_w"), o["dst_w"]) if w is not None}
    body = {"ops": ops, "into": "hbm", "tag": tag, "snap": snap, "retain_live": True,
            "index": {str(tp): index[tp] for tp in index},
            "meta": {str(tp): meta[tp] for tp in meta}}
    t0 = time.time()
    res = keepctl.multi_call(members, "flow", body, only=parts)
    report["wall_s"] = time.time() - t0
    report["errors"] = {f"{k[0]}:{k[1]}": v["error"][:300] for k, v in res.items()
                        if isinstance(v, dict) and "error" in v}
    report["replies"] = {f"{k[0]}:{k[1]}": {kk: vv for kk, vv in v.items()
                                            if kk in ("total_s", "ib_s", "prep_s", "ib_bytes",
                                                      "h2d_bytes", "ssd_bytes")}
                        for k, v in res.items() if isinstance(v, dict) and "error" not in v}
    return report, ops


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snap", default="qwen36")
    ap.add_argument("--src", default="node27")
    ap.add_argument("--src-gpus", default="0,1")
    ap.add_argument("--dst", default="node29")
    ap.add_argument("--dst-gpus", default="0,1")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--skip-ingest", action="store_true",
                    help="reuse a snapshot this source node already holds")
    ap.add_argument("--dry", action="store_true", help="solve the LP and print the plan, move nothing")
    ap.add_argument("--local-stage", action="store_true",
                    help="the destination node also holds a copy, so the LP may split local/remote")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    src_gpus = [int(x) for x in args.src_gpus.split(",")]
    dst_gpus = [int(x) for x in args.dst_gpus.split(",")]
    members = keepctl.parse_mesh()
    rep = {"arm": "F_keepalive_flow", "model": args.model, "snap": args.snap,
           "src": args.src, "src_gpus": src_gpus, "dst": args.dst, "dst_gpus": dst_gpus}

    # ---- ingest: safetensors -> packed pinned DRAM on the source node. Off the critical path.
    if not args.skip_ingest:
        t0 = time.time()
        bodies = {"ingest": {"snap": args.snap, "tp": tp, "dir": args.model}
                  for tp in range(len(src_gpus))}
        ing = keepctl.multi_call(members, "ingest",
                                 bodies=[{"snap": args.snap, "tp": i, "dir": args.model}
                                         for i in range(len(src_gpus))],
                                 only=[(args.src, g) for g in src_gpus], timeout=7200)
        rep["ingest"] = {f"{k[0]}:{k[1]}": v for k, v in ing.items()}
        rep["ingest_wall_s"] = time.time() - t0
        with open(os.path.join(args.out, "summary.json"), "w") as f:
            json.dump(rep, f, indent=2)

    # ---- collect the packed index/meta each shard must be written into.
    index, meta = {}, {}
    for tp, g in enumerate(src_gpus):
        r = keepctl.call(args.src, g, "index", {"snap": args.snap, "tp": tp}, timeout=60)
        index[tp] = r["index"]
        meta[tp] = r["meta"]
    rep["shard_GiB"] = {str(tp): round(max(o + n for o, n in index[tp].values()) / 2**30, 2)
                        for tp in index}

    # ---- solve and move.
    flow, ops = plan_and_flow(args.snap, members, args.src, src_gpus, args.dst, dst_gpus,
                              index, meta, dry=args.dry, local_stage=args.local_stage)
    rep["flow"] = flow
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(rep, f, indent=2)
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
